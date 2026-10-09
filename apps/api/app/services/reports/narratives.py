"""Report narratives: two structured calls on the main model, validated
against the real evidence, with deterministic fallbacks.

* Evidence and questions are shown to the model under short aliases (``E1``,
  ``Q1``). Returned ids are mapped back; anything that is not an alias or a
  real id of *this* session is dropped (and counted).
* The candidate narrative only ever sees candidate-safe data (no integrity,
  no recommendation, no claims/inconsistencies, no resume-consistency probes),
  and its text is scrubbed of sentences that mention integrity monitoring,
  hiring outcomes or resume verification anyway; third-person wording ("The
  candidate ...") is turned into "you".
* Judgemental verbs ("failed to", "failing to", "ignored", "admitted") are
  rewritten into neutral, evidence-based wording in both narratives (rules in
  :mod:`app.services.reports.wording`, which also neutralises stored evidence
  and contradiction text when reports are assembled and rendered), and
  internal competency keys written into prose ("ci_cd") become their names.
* When the model is unavailable on the last attempt, or its output stays
  invalid, a deterministic narrative built from the scores is used, so a
  report is always produced.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field
from rapidfuzz import fuzz

from app.ai.base import AIError, AIOutputInvalid, CallContext, StructuredResult
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.domain.enums import ModelTask, Recommendation
from app.schemas.ai import (
    CandidateReportNarrative,
    CompetencyFinding,
    EvidenceBackedPoint,
    ImprovementArea,
    QuestionHighlight,
    RecruiterReportNarrative,
    StudyArea,
)
from app.services.reports.assembly import ReportData, candidate_safe, positive_negative, truncate
from app.services.reports.prompts import CANDIDATE_SYSTEM, CANDIDATE_USER, RECRUITER_SYSTEM, RECRUITER_USER
from app.services.reports.wording import NEUTRAL_REWRITES as NEUTRAL_REWRITES  # re-exported
from app.services.reports.wording import candidate_voice, neutral_wording, second_person
from app.services.scoring.persistence import FLAG_ACTIONS
from app.services.scoring.topics import humanize_topic

log = get_logger(__name__)

NARRATIVE_TIMEOUT_SECONDS = 150.0
NARRATIVE_MAX_TOKENS = 4096
MAX_RECORD_EVIDENCE = 150
MAX_RECORD_ANSWER_CHARS = 700
_ALIAS = re.compile(r"^\s*([EQ])\s*-?\s*(\d+)\s*$", re.IGNORECASE)
_ID_SPLIT = re.compile(r"[\s,;()\[\]]+")
# "(E1, E2)", "[Q3]", "(E4 and E5)" written into prose by the model.
_ALIAS_GROUP = re.compile(
    r"\s*[(\[]\s*(?:(?:evidence|question)s?\s*:?\s*)?[EQ]-?\d{1,4}(?:\s*(?:,|;|/|&|and)?\s*[EQ]-?\d{1,4})*\s*[)\]]",
    re.IGNORECASE,
)
_BARE_EVIDENCE = re.compile(r"\s*\b(?:evidence\s+)?E-?\d{1,4}\b")
_BARE_QUESTION = re.compile(r"\bQ-?(\d{1,4})\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
CANDIDATE_BANNED = re.compile(
    r"\b(integrity|proctor\w*|cheat\w*|suspicious|flagged|misconduct|dishonest\w*|recommendation\w*|"
    r"hiring decision\w*|reject\w*|strong[_ ]proceed|human[_ ]review|insufficient[_ ]evidence)\b",
    re.IGNORECASE,
)
# Resume verification is recruiter-only: sentences about it never reach the candidate (same vocabulary
# as ``assembly.candidate_safe``, plus third-person judgements of the background).
CANDIDATE_RESUME_CHECK = re.compile(
    r"\b(?:r[eé]sum[eé]s?|cv|claim(?:s|ed)?|employment history|contradict\w*|inconsisten\w*|discrepanc\w*"
    r"|mismatch\w*|disconnect between|professional (?:narrative|background|history))\b",
    re.IGNORECASE,
)
# Internal numbers stay out of candidate prose (the report shows what the release policy allows).
CANDIDATE_INTERNAL = re.compile(
    r"\b(?:overall |competency |your )?scor(?:e|es|ed|ing)\b|\bconfidence (?:of|level|score|value)\b"
    r"|\b\d{1,3}(?:\.\d+)?\s*(?:/|out of)\s*100\b|\b\d{1,2}\.\d\s*(?:/|out of)\s*10\b",
    re.IGNORECASE,
)
#: Engine actions whose questions probe a resume/answer inconsistency (recruiter-only).
CONTRADICTION_ACTIONS = frozenset({"verify_contradiction"})
QUALITY_STRONG = {"strong"}
QUALITY_INCOMPLETE = {"partial", "vague", "no_answer", "incorrect", "off_topic"}


# --------------------------------------------------------------------------
# Guided-decoding variants of the report schemas
# --------------------------------------------------------------------------
# Same fields as app.schemas.ai (and validated against those afterwards), but
# with minItems so guided JSON cannot return a narrative without
# evidence-backed points (measured: the main model occasionally emits empty
# arrays for these sections).


class _CitedPoint(EvidenceBackedPoint):
    evidence_ids: list[str] = Field(..., min_length=1, max_length=8)


class GuidedCandidateNarrative(CandidateReportNarrative):
    technical_strengths: list[_CitedPoint] = Field(..., min_length=1, max_length=8)  # type: ignore[assignment]
    areas_to_improve: list[ImprovementArea] = Field(..., min_length=1, max_length=8)


class _CitedFinding(CompetencyFinding):
    evidence_ids: list[str] = Field(..., min_length=1, max_length=10)


class GuidedRecruiterNarrative(RecruiterReportNarrative):
    competency_findings: list[_CitedFinding] = Field(..., min_length=1, max_length=20)  # type: ignore[assignment]
    areas_for_human_followup: list[str] = Field(..., min_length=1, max_length=8)


# --------------------------------------------------------------------------
# Aliases and the interview record
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Aliases:
    evidence: dict[str, str] = field(default_factory=dict)  # "E1" -> uuid
    questions: dict[str, str] = field(default_factory=dict)  # "Q1" -> uuid
    evidence_alias: dict[str, str] = field(default_factory=dict)  # uuid -> "E1"
    question_alias: dict[str, str] = field(default_factory=dict)

    def _resolve(self, value: str, table: dict[str, str], valid: set[str], prefix: str) -> str | None:
        text = str(value or "").strip()
        if text in valid:
            return text
        match = _ALIAS.match(text)
        if match and match.group(1).upper() == prefix:
            return table.get(f"{prefix}{int(match.group(2))}")
        return None

    def evidence_ids(self, values: Iterable[str], valid: set[str]) -> tuple[list[str], int]:
        kept: list[str] = []
        dropped = 0
        for value in values or []:
            text = str(value or "").strip()
            whole = self._resolve(text, self.evidence, valid, "E")
            # The model sometimes packs several ids into one string ("E1, E2").
            tokens = [text] if whole is not None else [t for t in _ID_SPLIT.split(text) if t] or [text]
            for token in tokens:
                resolved = whole if whole is not None else self._resolve(token, self.evidence, valid, "E")
                if resolved is None or resolved not in valid:
                    dropped += 1
                elif resolved not in kept:
                    kept.append(resolved)
        return kept, dropped

    def strip_references(self, text: str | None, question_numbers: dict[str, int]) -> str | None:
        """Remove alias ids the model wrote into prose ("... (E1, E2)")."""
        if not text:
            return text
        cleaned = _ALIAS_GROUP.sub("", text)
        cleaned = _BARE_EVIDENCE.sub("", cleaned)

        def _question(match: re.Match[str]) -> str:
            qid = self.questions.get(f"Q{int(match.group(1))}")
            number = question_numbers.get(qid or "")
            return f"question {number}" if number else "an earlier question"

        cleaned = _BARE_QUESTION.sub(_question, cleaned)
        cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    def question_id(self, value: str, valid: set[str]) -> str | None:
        resolved = self._resolve(value, self.questions, valid, "Q")
        return resolved if resolved in valid else None


def build_aliases(document: dict[str, Any]) -> Aliases:
    aliases = Aliases()
    for i, q in enumerate(document["questions"], start=1):
        aliases.questions[f"Q{i}"] = q["id"]
        aliases.question_alias[q["id"]] = f"Q{i}"
    for i, e in enumerate(document["evidence"], start=1):
        aliases.evidence[f"E{i}"] = e["id"]
        aliases.evidence_alias[e["id"]] = f"E{i}"
    return aliases


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def record_text(document: dict[str, Any], aliases: Aliases, *, audience: str) -> str:
    """Plain-text interview record for the narrative prompt."""
    lines: list[str] = ["QUESTIONS AND ANSWERS"]
    candidate = audience == "candidate"
    skipped: set[str] = set()
    for q in document["questions"]:
        if candidate and (q.get("action") in CONTRADICTION_ACTIONS or not candidate_safe(q["text"])):
            # Resume-consistency probes are recruiter-only; so is everything said in answer to them.
            skipped.add(q["id"])
            continue
        alias = aliases.question_alias[q["id"]]
        comps = ", ".join(q["competency_keys"]) or "general"
        parent = aliases.question_alias.get(q["parent_question_id"] or "", "")
        follow = f"; follow-up to {parent}" if parent else ""
        lines.append(f"{alias} [{comps}; {q['difficulty']}{follow}] {truncate(q['text'], 500)}")
        for a in q["answers"]:
            lines.append(
                f"  Answer: {truncate(a['transcript_text'], MAX_RECORD_ANSWER_CHARS) or '(no answer)'}"
            )
            ev = a.get("evaluation")
            if ev:
                details = ev.get("missing_details") or []
                summary = ev.get("summary")
                if candidate:
                    details = [d for d in details if candidate_safe(d)]
                    summary = summary if candidate_safe(summary) else None
                missing = "; ".join(details)
                lines.append(
                    f"  Evaluation: quality={ev['overall_quality']}"
                    + (f"; summary={truncate(summary, 300)}" if summary else "")
                    + (f"; missing={truncate(missing, 200)}" if missing else "")
                )
                if audience == "recruiter" and ev.get("unsupported_claims"):
                    lines.append(
                        f"  Unsupported claims: {truncate('; '.join(ev['unsupported_claims']), 300)}"
                    )
    evidence = list(document["evidence"])
    if len(evidence) > MAX_RECORD_EVIDENCE:
        evidence = sorted(evidence, key=lambda e: -(e["strength"] * e["confidence"]))[:MAX_RECORD_EVIDENCE]
    lines.append("")
    lines.append("EVIDENCE")
    for e in evidence:
        if candidate and (
            e["signal"] == "neutral" or e.get("question_id") in skipped or not candidate_safe(e["evidence"])
        ):
            continue
        quote = f' | quote: "{truncate(e.get("quote"), 200)}"' if e.get("quote") else ""
        question = aliases.question_alias.get(e.get("question_id") or "", "-")
        lines.append(
            f"{aliases.evidence_alias[e['id']]} [{e['competency_key']}; {e['signal']}; {e['dimension']}; "
            f"strength {_fmt(e['strength'])}; confidence {_fmt(e['confidence'])}; question {question}] "
            f"{truncate(e['evidence'], 300)}{quote}"
        )
    if document["coding"]:
        lines.append("")
        lines.append("CODING")
        for c in document["coding"]:
            ev = c.get("evaluation") or {}
            result = c.get("final_result") or {}
            lines.append(
                f"{c['title']} ({c['difficulty']}, {c['language']}): status {c['status']}, "
                f"tests {result.get('passed', '?')}/{result.get('total', '?')}, hints used {c['hints_used']}"
                + (f", pastes {c['paste_event_count']}" if audience == "recruiter" else "")
            )
            if ev:
                lines.append(
                    f"  Evaluation: {truncate(ev.get('correctness_summary'), 300)}; complexity "
                    f"{ev.get('time_complexity') or '?'} time / {ev.get('space_complexity') or '?'} space; "
                    f"strengths: {truncate('; '.join(ev.get('strengths') or []), 300)}; "
                    f"weaknesses: {truncate('; '.join(ev.get('weaknesses') or []), 300)}; "
                    f"hints impact: {truncate(ev.get('hints_impact'), 200)}"
                )
    return "\n".join(lines)


def _competency_lines(document: dict[str, Any], *, recruiter: bool) -> str:
    rows = []
    for c in document["scores"]["competencies"]:
        unanswered = ", ".join(c["unanswered_areas"][:6]) or "none"
        if recruiter:
            rows.append(
                f"- {c['key']} ({c['name']}): {c['score']:.1f}, confidence {c['confidence']:.2f}, "
                f"evidence {c['evidence_count']}, sufficient {c['sufficient_evidence']}, "
                f"required {c['required']}; unanswered: {unanswered}"
            )
        else:
            safe = (
                ", ".join(humanize_topic(u) for u in c["unanswered_areas"][:6] if candidate_safe(u)) or "none"
            )
            rows.append(f"- {_name(c)} [{c['key']}]: {c['score']:.1f}; not covered yet: {safe}")
    return "\n".join(rows) or "- none"


def candidate_messages(document: dict[str, Any], aliases: Aliases) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": CANDIDATE_SYSTEM.render(
                job_title=document["job"]["title"] or "the role", round_name=document["round"]["name"]
            ),
        },
        {
            "role": "user",
            "content": CANDIDATE_USER.render(
                overall_score=f"{document['scores']['overall']['score']:.0f}",
                competencies=_competency_lines(document, recruiter=False),
                record=record_text(document, aliases, audience="candidate"),
            ),
        },
    ]


def recruiter_messages(document: dict[str, Any], aliases: Aliases) -> list[dict[str, str]]:
    integrity = document["integrity"]
    flagged = [e for e in integrity.get("events", []) if e.get("action") in FLAG_ACTIONS]
    flagged_text = (
        "; ".join(
            f"{e['type']} (confidence {_fmt(e.get('confidence') or 0.0)}, review status {e['review_status']})"
            for e in flagged
        )
        or "none"
    )
    by_type = ", ".join(
        f"{kind}: {entry.get('occurrences', 0) if isinstance(entry, dict) else entry}"
        for kind, entry in (integrity.get("by_type") or {}).items()
    )
    integrity_text = (
        f"{integrity['total_events']} signals recorded ({by_type or 'none'}). "
        f"Flagged for human review: {flagged_text}. Other signals were logged only."
    )
    claims = (
        "; ".join(f"{c['id']}: {truncate(c['claim'], 160)} -> {c['status']}" for c in document["claims"])
        or "none"
    )
    open_items = [c for c in document["contradictions"] if str(c.get("status")) in ("open", "verifying")]
    resolved = [c for c in document["contradictions"] if c not in open_items]
    contradictions = (
        "open: "
        + (
            "; ".join(f"{truncate(c['description'], 200)} (severity {c['severity']})" for c in open_items)
            or "none"
        )
        + "; already resolved (no follow-up needed): "
        + ("; ".join(truncate(c["description"], 120) for c in resolved) or "none")
    )
    rec = document["recommendation"]
    overall = document["scores"]["overall"]
    return [
        {
            "role": "system",
            "content": RECRUITER_SYSTEM.render(
                job_title=document["job"]["title"] or "the role",
                round_name=document["round"]["name"],
                recommendation=rec["value"],
            ),
        },
        {
            "role": "user",
            "content": RECRUITER_USER.render(
                overall_score=f"{overall['score']:.0f}",
                overall_confidence=f"{overall['confidence']:.2f}",
                coverage=f"{overall['required_coverage']:.0%}",
                recommendation=rec["value"],
                recommendation_rationale=rec["rationale"],
                competencies=_competency_lines(document, recruiter=True),
                claims=claims,
                contradictions=contradictions,
                integrity=integrity_text,
                record=record_text(document, aliases, audience="recruiter"),
            ),
        },
    ]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def scrub_candidate_text(text: str | None) -> tuple[str | None, int]:
    """Drop sentences mentioning integrity monitoring, hiring outcomes, resume verification or internal
    scores."""
    if not text:
        return text, 0
    sentences = _SENTENCE.split(text)
    kept = [
        s
        for s in sentences
        if not CANDIDATE_BANNED.search(s)
        and not CANDIDATE_RESUME_CHECK.search(s)
        and not CANDIDATE_INTERNAL.search(s)
    ]
    return " ".join(kept).strip(), len(sentences) - len(kept)


def humanize_keys(text: str | None, names: dict[str, str]) -> str | None:
    """Internal competency keys written into prose ("ci_cd") become their names ("CI/CD")."""
    if not text or not names:
        return text
    for key, name in names.items():
        text = re.sub(rf"(?<![\w/]){re.escape(key)}(?![\w/])", name, text)
    return text


def _key_names(data: ReportData) -> dict[str, str]:
    return {
        c["key"]: _name(c)
        for c in data.document["scores"]["competencies"]
        if "_" in c["key"] and _name(c) != c["key"]
    }


LINK_MIN_SIMILARITY = 45  # rapidfuzz token_set_ratio (0-100)
MAX_LINKED_EVIDENCE = 3


def link_evidence(
    text: str, document: dict[str, Any], *, signal: str, competency_key: str | None
) -> tuple[list[str], str | None]:
    """Evidence ids supporting an uncited narrative point.

    Candidates are the session's evidence items with the matching signal
    (restricted to ``competency_key`` when known), ranked by textual
    similarity to the point; returns ``([], None)`` when nothing is close.
    """
    pool = [e for e in document["evidence"] if e["signal"] == signal]
    if competency_key:
        pool = [e for e in pool if e["competency_key"] == competency_key]
    scored = []
    for item in pool:
        haystack = " ".join(
            str(v or "")
            for v in (item["evidence"], item.get("quote"), item["dimension"], item["competency_key"])
        )
        similarity = fuzz.token_set_ratio(text.lower(), haystack.lower().replace("_", " "))
        scored.append((similarity, item["strength"] * item["confidence"], item))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    best = [t for t in scored if t[0] >= LINK_MIN_SIMILARITY][:MAX_LINKED_EVIDENCE]
    if not best and competency_key and scored:
        best = sorted(scored, key=lambda t: -t[1])[:MAX_LINKED_EVIDENCE]
    if not best:
        return [], None
    keys = Counter(t[2]["competency_key"] for t in best)
    return [t[2]["id"] for t in best], competency_key or keys.most_common(1)[0][0]


def validate_candidate_narrative(
    narrative: CandidateReportNarrative, aliases: Aliases, data: ReportData
) -> tuple[dict[str, Any], dict[str, int]]:
    stats = {
        "dropped_evidence_ids": 0,
        "dropped_question_highlights": 0,
        "scrubbed_sentences": 0,
        "cleared_competency_keys": 0,
    }
    doc = narrative.model_dump(mode="json")

    numbers = {q["id"]: q["sequence"] for q in data.document["questions"]}
    names = _key_names(data)
    rewritten = 0

    def _scrub(value: str | None) -> str | None:
        nonlocal rewritten
        cleaned, removed = scrub_candidate_text(aliases.strip_references(value, numbers))
        stats["scrubbed_sentences"] += removed
        cleaned, n_person = second_person(cleaned)
        cleaned, n_neutral = neutral_wording(cleaned)
        rewritten += n_person + n_neutral
        return humanize_keys(cleaned, names)

    def _key(value: str | None) -> str | None:
        if value and value not in data.competency_keys:
            stats["cleared_competency_keys"] += 1
            return None
        return value

    stats["linked_points"] = 0
    stats["dropped_unsupported_points"] = 0
    for list_name, signal in (("technical_strengths", "positive"), ("areas_to_improve", "negative")):
        items = []
        for point in doc.get(list_name) or []:
            ids, dropped = aliases.evidence_ids(point.get("evidence_ids") or [], data.evidence_ids)
            stats["dropped_evidence_ids"] += dropped
            point["evidence_ids"] = ids
            point["competency_key"] = _key(point.get("competency_key"))
            for text_field in ("title", "detail", "learning_guidance"):
                if text_field in point:
                    point[text_field] = _scrub(point[text_field]) or ""
            if not (point.get("detail") or point.get("title")):
                continue
            if not ids:
                text = " ".join(str(point.get(f) or "") for f in ("title", "detail"))
                linked, key = link_evidence(
                    text, data.document, signal=signal, competency_key=point["competency_key"]
                )
                if linked:
                    point["evidence_ids"] = linked
                    point["competency_key"] = point["competency_key"] or key
                    stats["linked_points"] += 1
                elif signal == "positive":
                    # A strength nobody can point at is not reported.
                    stats["dropped_unsupported_points"] += 1
                    continue
                # An improvement area may rest on missing evidence (unanswered topics): kept without ids.
            items.append(point)
        doc[list_name] = items
    for list_name in ("strong_answers", "incomplete_answers"):
        highlights = []
        for item in doc.get(list_name) or []:
            qid = aliases.question_id(item.get("question_id", ""), data.question_ids)
            if qid is None:
                stats["dropped_question_highlights"] += 1
                continue
            highlights.append({"question_id": qid, "note": _scrub(item.get("note")) or ""})
        doc[list_name] = highlights
    for text_field in (
        "overall_summary",
        "coding_feedback",
        "problem_solving_feedback",
        "communication_feedback",
    ):
        doc[text_field] = _scrub(doc.get(text_field))
    # An empty summary is filled from the deterministic narrative by generate_narratives.
    doc["overall_summary"] = doc.get("overall_summary") or ""
    for area in doc.get("recommended_study_areas") or []:
        area["topic"] = humanize_keys(area.get("topic"), names) or ""
        area["why"] = _scrub(area.get("why")) or ""
        area["next_steps"] = [s for s in (_scrub(x) for x in area.get("next_steps") or []) if s]
    doc["next_steps"] = [s for s in (_scrub(x) for x in doc.get("next_steps") or []) if s]
    if rewritten:
        stats["rewritten_phrases"] = rewritten
    _fit_lengths(doc, CandidateReportNarrative)
    return CandidateReportNarrative.model_validate(doc).model_dump(mode="json"), stats


def validate_recruiter_narrative(
    narrative: RecruiterReportNarrative, aliases: Aliases, data: ReportData
) -> tuple[dict[str, Any], dict[str, int]]:
    stats = {"dropped_evidence_ids": 0, "dropped_findings": 0}
    doc = narrative.model_dump(mode="json")
    numbers = {q["id"]: q["sequence"] for q in data.document["questions"]}
    names = _key_names(data)
    rewritten = 0

    def _clean(value: Any) -> Any:
        nonlocal rewritten
        if isinstance(value, str):
            text, n = neutral_wording(aliases.strip_references(value, numbers))
            rewritten += n
            return humanize_keys(text, names)
        if isinstance(value, list):
            return [_clean(v) for v in value]
        return value

    for text_field in (
        "executive_summary",
        "claims_summary",
        "contradictions_summary",
        "coding_summary",
        "recommendation_rationale",
        "areas_for_human_followup",
        "risks",
    ):
        doc[text_field] = _clean(doc.get(text_field))
    for finding in doc.get("competency_findings") or []:
        for text_field in ("summary", "strengths", "weaknesses", "unanswered_areas"):
            finding[text_field] = _clean(finding.get(text_field))
    findings = []
    seen: set[str] = set()
    for finding in doc.get("competency_findings") or []:
        key = finding.get("competency_key")
        if key not in data.competency_keys or key in seen:
            stats["dropped_findings"] += 1
            continue
        seen.add(key)
        ids, dropped = aliases.evidence_ids(finding.get("evidence_ids") or [], data.evidence_ids)
        stats["dropped_evidence_ids"] += dropped
        if not ids:
            pos, neg = positive_negative(data.document, key)
            ids = [e["id"] for e in (pos[:5] + neg[:5])][:10]
            if ids:
                stats["linked_findings"] = stats.get("linked_findings", 0) + 1
        finding["evidence_ids"] = ids
        findings.append(finding)
    doc["competency_findings"] = findings
    if rewritten:
        stats["rewritten_phrases"] = rewritten
    _fit_lengths(doc, RecruiterReportNarrative)
    return RecruiterReportNarrative.model_validate(doc).model_dump(mode="json"), stats


def _fit_lengths(doc: dict[str, Any], schema: type[Any]) -> None:
    """Rewrites can lengthen text slightly ("ignored" -> "did not address"): clip top-level and nested
    string fields to their schema's max_length so validation never fails on a rewrite."""

    def _limits(model: type[Any]) -> dict[str, int]:
        out = {}
        for name, info in model.model_fields.items():
            for meta in info.metadata:
                limit = getattr(meta, "max_length", None)
                if limit is not None and info.annotation in (str, str | None):
                    out[name] = int(limit)
        return out

    nested = {
        "technical_strengths": EvidenceBackedPoint,
        "areas_to_improve": ImprovementArea,
        "strong_answers": QuestionHighlight,
        "incomplete_answers": QuestionHighlight,
        "recommended_study_areas": StudyArea,
        "competency_findings": CompetencyFinding,
    }
    for name, limit in _limits(schema).items():
        if isinstance(doc.get(name), str) and len(doc[name]) > limit:
            doc[name] = truncate(doc[name], limit)
    for name, model in nested.items():
        limits = _limits(model)
        for item in doc.get(name) or []:
            for field_name, limit in limits.items():
                if isinstance(item.get(field_name), str) and len(item[field_name]) > limit:
                    item[field_name] = truncate(item[field_name], limit)


# --------------------------------------------------------------------------
# Deterministic fallbacks
# --------------------------------------------------------------------------


def _name(c: dict[str, Any]) -> str:
    return str(c.get("name") or c["key"])


def _join(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def fallback_candidate_narrative(document: dict[str, Any]) -> dict[str, Any]:
    comps = [c for c in document["scores"]["competencies"] if c["evidence_count"] > 0]
    ranked = sorted(comps, key=lambda c: -c["score"])
    strengths: list[EvidenceBackedPoint] = []
    for c in ranked:
        if c["score"] < 6.0 or len(strengths) >= 3:
            continue
        pos, _ = positive_negative(document, c["key"])
        if not pos:
            continue
        strengths.append(
            EvidenceBackedPoint(
                title=truncate(_name(c), 120),
                detail=truncate(candidate_voice(pos[0]["evidence"]), 600),
                evidence_ids=[e["id"] for e in pos[:3]],
                competency_key=c["key"],
            )
        )
    improvements: list[ImprovementArea] = []
    study: list[StudyArea] = []
    for c in sorted(comps, key=lambda c: c["score"]):
        if len(improvements) >= 3:
            break
        _, neg = positive_negative(document, c["key"])
        open_areas = [m for m in c["unanswered_areas"] if candidate_safe(m)]
        neg = [e for e in neg if candidate_safe(e["evidence"])]
        if c["score"] >= 7.0 and not open_areas:
            continue
        missing = [humanize_topic(m) for m in open_areas[:3]]
        detail = (
            candidate_voice(neg[0]["evidence"])
            if neg
            else f"There was not yet room in your answers for {_join(missing)}."
            if missing
            else ""
        )
        if not detail:
            continue
        guidance = (
            f"Practise explaining {_join(missing) or _name(c)} with a concrete example: what you built or changed, "
            "the scale involved, the trade-offs you weighed and how you measured the result."
        )
        improvements.append(
            ImprovementArea(
                title=truncate(_name(c), 120),
                detail=truncate(detail, 600),
                learning_guidance=truncate(guidance, 600),
                evidence_ids=[e["id"] for e in neg[:3]],
                competency_key=c["key"],
            )
        )
        if missing:
            study.append(
                StudyArea(
                    topic=truncate(_name(c), 120),
                    why=truncate(f"These topics did not come up in detail yet: {_join(missing)}.", 300),
                    next_steps=[
                        truncate(f"Prepare a short, specific example about {m}.", 200) for m in missing[:3]
                    ],
                )
            )
    strong: list[QuestionHighlight] = []
    incomplete: list[QuestionHighlight] = []
    for q in document["questions"]:
        if q.get("action") in CONTRADICTION_ACTIONS:
            continue
        for a in q["answers"]:
            ev = a.get("evaluation") or {}
            quality = ev.get("overall_quality")
            # The evaluator's summary is written for reviewers ("The candidate ..."): candidate voice only.
            summary_text = candidate_voice(ev.get("summary")) if candidate_safe(ev.get("summary")) else None
            if quality in QUALITY_STRONG and len(strong) < 4:
                strong.append(
                    QuestionHighlight(
                        question_id=q["id"],
                        note=truncate(summary_text or "A specific, well-supported answer.", 400),
                    )
                )
            elif quality in QUALITY_INCOMPLETE and len(incomplete) < 4:
                missing_text = "; ".join(
                    candidate_voice(m) for m in ev.get("missing_details") or [] if candidate_safe(m)
                )
                note = (
                    f"This answer could go further on: {missing_text}."
                    if missing_text
                    else (summary_text or "This answer could include a more specific example.")
                )
                incomplete.append(QuestionHighlight(question_id=q["id"], note=truncate(note, 400)))
    best = _join([_name(c) for c in ranked[:2]])
    job = document["job"].get("title") or "the role"
    summary = (
        f"Thank you for interviewing for {job}. You answered {len(document['questions'])} questions "
        f"across {len(comps)} areas."
    )
    if best:
        summary += f" Your clearest examples were in {best}."
    if improvements:
        summary += f" The biggest opportunities to grow are in {_join([i.title for i in improvements[:2]])}."
    coding_feedback = None
    if document["coding"]:
        c = document["coding"][0]
        ev = c.get("evaluation") or {}
        coding_feedback = truncate(
            " ".join(
                x
                for x in [
                    ev.get("correctness_summary") or "",
                    ("Strengths: " + "; ".join(ev.get("strengths") or []) + ".")
                    if ev.get("strengths")
                    else "",
                    ("To improve: " + "; ".join(ev.get("weaknesses") or []) + ".")
                    if ev.get("weaknesses")
                    else "",
                ]
                if x
            )
            or f"You worked on {c['title']} in {c['language']}.",
            800,
        )
    narrative = CandidateReportNarrative(
        overall_summary=truncate(summary, 1200),
        technical_strengths=strengths,
        areas_to_improve=improvements,
        coding_feedback=coding_feedback,
        strong_answers=strong,
        incomplete_answers=incomplete,
        recommended_study_areas=study[:8],
        next_steps=[
            "Structure answers as context, your actions, the result and how you measured it.",
            "Quantify scale and impact (users, requests, nodes, cost, latency) when you describe your work.",
        ],
    )
    return narrative.model_dump(mode="json")


CLAIM_STATUS_WORDS = {
    "verified": "verified",
    "partially_verified": "partially verified",
    "unverified": "not yet verified",
    "in_progress": "still being discussed",
    "contradicted": "with an open question for the reviewer",
}
CONTRADICTION_STATUS_WORDS = {
    "open": "still open",
    "verifying": "being clarified",
    "resolved": "clarified in the interview",
    "dismissed": "no follow-up needed",
}


def _min_confidence_target(document: dict[str, Any]) -> float:
    """The overall confidence target the recommendation used (round-scope target; configured minimum
    for reports assembled before the calibration existed)."""
    rec = document.get("recommendation") or {}
    target = (rec.get("factors") or {}).get("min_confidence_target")
    if isinstance(target, (int, float)):
        return float(target)
    return float((rec.get("passing_guidance") or {}).get("min_confidence", 0.6))


def fallback_recruiter_narrative(document: dict[str, Any]) -> dict[str, Any]:
    overall = document["scores"]["overall"]
    rec = document["recommendation"]
    findings: list[CompetencyFinding] = []
    followups: list[str] = []
    open_areas: list[str] = []
    # A short or crowded round judges sufficiency against its own (scaled) targets.
    scoped = float((rec.get("factors") or {}).get("round_scope", 1.0) or 1.0) < 1.0
    enough = "enough evidence for this round's scope." if scoped else "enough evidence to judge."
    for c in document["scores"]["competencies"][:20]:
        pos, neg = positive_negative(document, c["key"])
        findings.append(
            CompetencyFinding(
                competency_key=c["key"],
                summary=truncate(
                    (
                        f"{c['score']:.1f} out of 10 from {c['evidence_count']} piece"
                        f"{'' if c['evidence_count'] == 1 else 's'} of evidence (confidence {c['confidence']:.2f}); "
                        + (enough if c["sufficient_evidence"] else "more evidence is needed.")
                    )
                    if c["evidence_count"]
                    else "Not assessed in this interview: no evidence was collected.",
                    600,
                ),
                strengths=[truncate(e["evidence"], 200) for e in pos[:3]],
                weaknesses=[truncate(e["evidence"], 200) for e in neg[:3]],
                unanswered_areas=[truncate(humanize_topic(u), 120) for u in c["unanswered_areas"][:5]],
                evidence_ids=[e["id"] for e in (pos[:5] + neg[:5])][:10],
            )
        )
        areas = _join([humanize_topic(u) for u in c["unanswered_areas"][:3]])
        if c["required"] and not c["sufficient_evidence"]:
            followups.append(truncate(f"Probe {_name(c)}" + (f": {areas}" if areas else ""), 200))
        elif c["required"] and c["evidence_count"] and areas:
            # Sufficient for the round, with areas a human interviewer can still probe.
            open_areas.append(truncate(f"Follow up on {_name(c)}: {areas}", 200))
    contradictions = document["contradictions"]
    for c in contradictions[:3]:
        followups.append(truncate(f"Discuss: {c['description']}", 200))
    followups += open_areas
    integrity = document["integrity"]
    claims = document["claims"]
    claims_summary = (
        "Resume claims discussed: "
        + _join(
            [
                f"{count} {CLAIM_STATUS_WORDS.get(status, status.replace('_', ' '))}"
                for status, count in document["claims_summary"].items()
            ]
        )
        + "."
        if claims
        else "No resume claims were tracked in this interview."
    )
    coding_summary = None
    if document["coding"]:
        parts = []
        for c in document["coding"]:
            result = c.get("final_result") or {}
            ev = c.get("evaluation") or {}
            hints = c["hints_used"]
            estimate = ev.get("score_estimate")
            parts.append(
                f"{c['title']} in {c['language']}: passed {result.get('passed', '?')} of "
                f"{result.get('total', '?')} tests with {hints or 'no'} hint{'' if hints == 1 else 's'}"
                + (f"; evaluator estimate {estimate} out of 10." if estimate is not None else ".")
            )
        coding_summary = truncate(" ".join(parts), 800)
    assessed = sorted(
        (c for c in document["scores"]["competencies"] if c["evidence_count"] > 0), key=lambda c: -c["score"]
    )
    strongest = _join([_name(c) for c in assessed[:3] if c["score"] >= 6.5])
    weakest = _join([_name(c) for c in sorted(assessed, key=lambda c: c["score"])[:2] if c["score"] < 6.5])
    unassessed = _join([_name(c) for c in document["scores"]["competencies"] if c["evidence_count"] == 0][:4])
    required = [c for c in document["scores"]["competencies"] if c["required"]]
    covered = sum(1 for c in required if c["sufficient_evidence"])
    executive_summary = (
        f"Overall {overall['score']:.0f} out of 100 (confidence {overall['confidence']:.2f}). "
        + (
            f"{covered} of {len(required)} required competencies have enough evidence to judge."
            if required
            else f"{overall['required_coverage']:.0%} of required competencies have enough evidence."
        )
        + (f" Clearest evidence: {strongest}." if strongest else "")
        + (f" Weakest evidence: {weakest}." if weakest else "")
        + (f" Not assessed: {unassessed}." if unassessed else "")
    )
    risks = []
    if overall["confidence"] < _min_confidence_target(document):
        risks.append("Overall confidence is below the round's minimum; scores may move with more evidence.")
    if integrity["unreviewed_flags"]:
        risks.append(
            f"{integrity['unreviewed_flags']} integrity signal(s) await human review (observations only)."
        )
    narrative = RecruiterReportNarrative(
        executive_summary=truncate(executive_summary, 1500),
        competency_findings=findings,
        claims_summary=truncate(claims_summary, 800),
        contradictions_summary=truncate(
            (
                "Questions for the reviewer: "
                + "; ".join(
                    f"{c['description']} ({c['severity']} severity, "
                    f"{CONTRADICTION_STATUS_WORDS.get(str(c['status']), str(c['status']))})"
                    for c in contradictions
                )
            )
            if contradictions
            else "No inconsistencies were recorded.",
            600,
        ),
        coding_summary=coding_summary,
        areas_for_human_followup=followups[:8],
        recommendation_rationale=truncate(rec["rationale"], 800),
        suggested_recommendation=Recommendation(rec["value"]),
        risks=risks[:6],
    )
    return narrative.model_dump(mode="json")


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


@dataclass(slots=True)
class NarrativeOutcome:
    candidate: dict[str, Any]
    recruiter: dict[str, Any]
    source: dict[str, str]
    model_call_ids: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


async def _call(
    schema: type[GuidedCandidateNarrative] | type[GuidedRecruiterNarrative],
    messages: list[dict[str, str]],
    *,
    data: ReportData,
    prompt_version: str,
) -> tuple[StructuredResult[Any], float]:
    started = time.perf_counter()
    result = await get_ai().llm.structured(
        schema,
        messages,
        context=CallContext(
            task=ModelTask.REPORT_GENERATION,
            session_id=str(data.session_id) if data.session_id else None,
            organization_id=str(data.organization_id) if data.organization_id else None,
            prompt_version=prompt_version,
        ),
        temperature=0.2,
        max_tokens=NARRATIVE_MAX_TOKENS,
        timeout=NARRATIVE_TIMEOUT_SECONDS,
        max_attempts=2,
    )
    return result, (time.perf_counter() - started) * 1000.0


async def generate_narratives(
    data: ReportData, *, allow_fallback: bool = True, use_model: bool = True
) -> NarrativeOutcome:
    """Both narratives (concurrently). With ``allow_fallback=False`` a
    retryable model failure is raised so the job can retry; invalid output
    always falls back (retrying the same prompt rarely fixes it)."""
    document = data.document
    aliases = build_aliases(document)
    outcome = NarrativeOutcome(candidate={}, recruiter={}, source={})
    if not any(e["signal"] != "neutral" for e in document["evidence"]):
        use_model = False  # nothing evidence-backed to write about: deterministic text only
    if not use_model:
        outcome.candidate = fallback_candidate_narrative(document)
        outcome.recruiter = fallback_recruiter_narrative(document)
        outcome.source = {"candidate": "fallback", "recruiter": "fallback"}
        return outcome
    results = await asyncio.gather(
        _call(
            GuidedCandidateNarrative,
            candidate_messages(document, aliases),
            data=data,
            prompt_version=CANDIDATE_USER.version,
        ),
        _call(
            GuidedRecruiterNarrative,
            recruiter_messages(document, aliases),
            data=data,
            prompt_version=RECRUITER_USER.version,
        ),
        return_exceptions=True,
    )
    for kind, result in zip(("candidate", "recruiter"), results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, AIError):
                raise result
            retryable = getattr(result, "retryable", False) and not isinstance(result, AIOutputInvalid)
            if retryable and not allow_fallback:
                raise result
            log.warning("reports.narrative_fallback", kind=kind, error=type(result).__name__)
            outcome.errors[kind] = f"{type(result).__name__}: {str(result)[:300]}"
            outcome.source[kind] = "fallback"
            if kind == "candidate":
                outcome.candidate = fallback_candidate_narrative(document)
            else:
                outcome.recruiter = fallback_recruiter_narrative(document)
            continue
        structured, latency = result
        outcome.latency_ms[kind] = round(latency, 1)
        if structured.model_call_id:
            outcome.model_call_ids.append(structured.model_call_id)
        outcome.source[kind] = "fallback" if structured.used_fallback else "model"
        if kind == "candidate":
            outcome.candidate, outcome.stats["candidate"] = validate_candidate_narrative(
                structured.value, aliases, data
            )
            fallback = fallback_candidate_narrative(document)
            filled = _fill_gaps(outcome.candidate, fallback, CANDIDATE_REQUIRED_SECTIONS)
            filled_text = _fill_gaps(outcome.candidate, fallback, CANDIDATE_TEXT_SECTIONS)
            if _blank(outcome.candidate.get("overall_summary")):
                outcome.candidate["overall_summary"] = "Thank you for completing the interview."
        else:
            outcome.recruiter, outcome.stats["recruiter"] = validate_recruiter_narrative(
                structured.value, aliases, data
            )
            fallback = fallback_recruiter_narrative(document)
            filled = _fill_gaps(outcome.recruiter, fallback, RECRUITER_REQUIRED_SECTIONS)
            filled_text = _fill_gaps(outcome.recruiter, fallback, RECRUITER_TEXT_SECTIONS)
        if filled:
            outcome.stats[kind]["filled_from_deterministic"] = filled
        if filled_text:
            outcome.stats[kind]["filled_text_from_deterministic"] = filled_text
    return outcome


CANDIDATE_REQUIRED_SECTIONS = ("technical_strengths", "areas_to_improve")
RECRUITER_REQUIRED_SECTIONS = ("competency_findings",)
# Text sections the model sometimes returns as "" or null while the deterministic narrative has content
# (e.g. no coding feedback although the candidate did the coding exercise).
CANDIDATE_TEXT_SECTIONS = ("overall_summary", "coding_feedback")
RECRUITER_TEXT_SECTIONS = (
    "executive_summary",
    "claims_summary",
    "contradictions_summary",
    "coding_summary",
    "recommendation_rationale",
)


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return not value
    return False


def _fill_gaps(narrative: dict[str, Any], fallback: dict[str, Any], sections: Sequence[str]) -> list[str]:
    """Sections left empty or null by the model (or emptied by validation) are filled from the
    deterministic narrative."""
    filled = []
    for section in sections:
        if _blank(narrative.get(section)) and not _blank(fallback.get(section)):
            narrative[section] = fallback[section]
            filled.append(section)
    return filled
