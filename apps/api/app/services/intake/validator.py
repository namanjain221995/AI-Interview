"""Deterministic validator of the intake evaluation (design contract section 8.7).

| Rule | Check | Effect |
|---|---|---|
| V1 schema | evaluator output validates (after the structured retries) | otherwise the evaluator falls back: its competencies are insufficient + finding ``evaluator_fallback`` |
| V2 evidence | the cited alias belongs to the evaluator's own inputs and the quote matches that answer (rapidfuzz ``partial_ratio >= 85`` after whitespace/case normalisation, quote not longer than the answer) | invalid citations are dropped (issue ``evidence_dropped``) |
| V3 support | a score needs >= 1 valid citation; >= 4 needs a positive one; <= 2 needs a negative or context one | otherwise insufficient (issue ``score_unsupported``) |
| V4 required evidence | per competency (:func:`required_evidence`) | otherwise insufficient (issue ``required_evidence_missing``) |
| V5 mandatory coverage | stage results: mandatory items not answered / prefilled | finding ``mandatory_item_missing`` per item, ``stage_incomplete`` per incomplete stage |
| V6 consistency | technical >= 4 with >= 2 unsupported claims; structured >= 4 with < 3 bullets covered; structured 5 with < 4 covered; communication and structured >= 3 apart | finding ``evaluator_disagreement`` (scores unchanged, except structured 5 with < 4 covered bullets is lowered to 4) |
| V7 arithmetic | the stored competency rows recomputed with ``summarize_scores`` equal the stored totals | :class:`ArithmeticMismatch` (a bug: the job retries) |
| V8 privacy | no MQ1 / eligibility text and no ``kit_original`` text in any prompt | :class:`~app.services.intake.evidence.PromptPrivacyError` (raised before the call) |

Findings become human review items in :mod:`app.services.intake.review_items`; nothing here decides an outcome.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from rapidfuzz import fuzz

from app.services.intake.eval_schemas import (
    BulletJudgement,
    ClaimVerdict,
    CommunicationOutput,
    CompetencyJudgement,
    ComposureOutput,
    EvidenceCite,
    RelayOutput,
    ResumeEvidenceOutput,
    StructuredOutput,
    TechnicalOutput,
    TrainabilityOutput,
)
from app.services.intake.evidence import (
    CHALLENGE_ITEM,
    CHALLENGE_KINDS,
    LADDER_RUNG,
    AliasMap,
    AliasTarget,
    EvaluationInputs,
    PromptPrivacyError,
    assert_prompt_safe,
)
from app.services.intake.relay import DrillScore
from app.services.intake.scoring import (
    INTAKE_COMPETENCY_KEYS,
    IntakeScoreSummary,
    summarize_scores,
    technical_reading_score,
)

QUOTE_MIN_RATIO = 85.0
QUOTE_LENGTH_SLACK = 12  # characters a quote may exceed the answer by (punctuation, ellipses)
COMMUNICATION_MIN_WORDS = 25
COMMUNICATION_MIN_ANSWERS = 3
COMMUNICATION_MIN_STAGES = 2
STRUCTURED_MIN_WORDS = 30
COMPOSURE_MIN_ITEMS = 4
TECHNICAL_MIN_RUNGS = 2
DISAGREEMENT_GAP = 3

#: Item statuses / skip reasons that do not make a mandatory item "missing" (rule V5).
COVERED_STATUSES = frozenset({"answered", "prefilled", "not_applicable"})
DESIGNED_SKIPS = frozenset({"not_hands_on", "policy_disabled", "prefilled"})
#: Interviewer statements that expect no answer: delivered (``asked``) is complete.
NO_ANSWER_ITEMS = frozenset({"close.closing", "close.answer"})

COMPETENCY_EVALUATOR: dict[str, str] = {
    "communication": "communication",
    "technical_reading": "relay",
    "structured_answer": "structured",
    "technical": "technical",
    "trainability": "trainability",
    "composure": "composure",
    "adaptability": "trainability",
}


class ArithmeticMismatch(RuntimeError):
    """Rule V7: stored competency rows do not reproduce the stored totals (a bug; the job retries)."""


__all__ = [
    "COMPETENCY_EVALUATOR",
    "ArithmeticMismatch",
    "CompetencyResult",
    "Finding",
    "PromptPrivacyError",
    "ValidationResult",
    "ValidatorIssue",
    "assert_prompt_safe",
    "bullet_results",
    "check_arithmetic",
    "claim_results",
    "concept_evidence",
    "consistency_findings",
    "mandatory_findings",
    "normalize_quote",
    "quote_matches",
    "required_evidence",
    "validate_cites",
    "validate_evaluation",
]


# ============================================================================================== types


@dataclass(slots=True)
class ValidatorIssue:
    code: str
    competency: str | None
    detail: str
    evaluator: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "competency": self.competency,
            "detail": self.detail,
            "evaluator": self.evaluator,
        }


@dataclass(slots=True)
class Finding:
    """A validator observation for a human reviewer (turned into a review item)."""

    kind: Literal[
        "mandatory_item_missing", "stage_incomplete", "evaluator_disagreement", "evaluator_fallback"
    ]
    detail: str
    competency_keys: tuple[str, ...] = ()
    evidence: list[dict[str, Any]] = field(default_factory=list)
    key: str = ""  # stable discriminator for the review item id (stage key, item key, rule)


@dataclass(slots=True)
class CompetencyResult:
    key: str
    status: Literal["scored", "insufficient"]
    score: Decimal | None
    evaluator: str
    reasoning: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    sub_scores: dict[str, Any] = field(default_factory=dict)
    model_score: int | None = None  # the evaluator's own score before validation

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "score": None if self.score is None else format(self.score, "f"),
            "evaluator": self.evaluator,
            "reasoning": self.reasoning,
            "evidence": list(self.evidence),
            "missing_evidence": list(self.missing_evidence),
            "strengths": list(self.strengths),
            "improvements": list(self.improvements),
            "sub_scores": dict(self.sub_scores),
            "model_score": self.model_score,
        }


@dataclass(slots=True)
class ValidationResult:
    competencies: dict[str, CompetencyResult]
    issues: list[ValidatorIssue]
    findings: list[Finding]
    claims: list[dict[str, Any]]
    bullets: list[dict[str, Any]]
    summary: IntakeScoreSummary

    def scores(self) -> dict[str, Decimal | None]:
        return {key: self.competencies[key].score for key in INTAKE_COMPETENCY_KEYS}


# ============================================================================================== V2 quotes

_QUOTE_CHARS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "…": " ", "—": " ", "–": " "})
_EDGE = re.compile(r"^[\s\"'.,;:!?()\[\]-]+|[\s\"'.,;:!?()\[\]-]+$")


def normalize_quote(text: str | None) -> str:
    """Lower case, NFKC, straight quotes, collapsed whitespace, no surrounding quotes/punctuation/ellipses."""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).translate(_QUOTE_CHARS).lower()
    value = value.replace("...", " ")
    value = " ".join(value.split())
    return _EDGE.sub("", value)


def quote_matches(quote: str | None, text: str | None) -> bool:
    """Whether ``quote`` is (nearly) verbatim from ``text`` (rule V2)."""
    q = normalize_quote(quote)
    t = normalize_quote(text)
    if len(q) < 3 or not t:
        return False
    if q in t:
        return True
    if len(q) > len(t) + QUOTE_LENGTH_SLACK:
        return False
    return fuzz.partial_ratio(q, t) >= QUOTE_MIN_RATIO


def evidence_ref(target: AliasTarget, *, quote: str | None, supports: str) -> dict[str, Any]:
    """``EvidenceRef`` (contract 15.7) for a validated citation."""
    return {
        "source": "activity" if target.activity_id else "answer",
        "stage": target.stage,
        "item_key": target.item_key,
        "question_id": str(target.question_id) if target.question_id else None,
        "answer_id": str(target.answer_id) if target.answer_id else None,
        "activity_id": target.activity_id,
        "start_ms": target.start_ms,
        "end_ms": target.end_ms,
        "quote": (quote or "").strip()[:300] or None,
        "supports": supports,
    }


def validate_cites(
    cites: Iterable[EvidenceCite],
    *,
    aliases: AliasMap,
    allowed: frozenset[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Valid citations as ``EvidenceRef`` dicts, plus the reasons of the dropped ones."""
    refs: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for cite in cites:
        alias = cite.answer.strip().upper()
        target = aliases.get(alias)
        if target is None or target.kind != "answer":
            dropped.append(f"{alias}: unknown answer")
            continue
        if alias not in allowed:
            dropped.append(f"{alias}: not among this evaluator's answers")
            continue
        if not quote_matches(cite.quote, target.text):
            dropped.append(f"{alias}: quote not found in the answer")
            continue
        key = (alias, normalize_quote(cite.quote))
        if key in seen:
            continue
        seen.add(key)
        refs.append(evidence_ref(target, quote=cite.quote, supports=cite.supports))
    return refs, dropped


def _alias_ok(alias: str | None, aliases: AliasMap, allowed: frozenset[str]) -> AliasTarget | None:
    if not alias:
        return None
    target = aliases.get(alias)
    if target is None or target.kind != "answer" or target.alias not in allowed:
        return None
    return target


# ============================================================================================== V4 required evidence


def _answered_items(inputs: EvaluationInputs) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    by_id = {q.id: q for q in inputs.questions}
    for answer in inputs.answers:
        question = by_id.get(answer.question_id) if answer.question_id else None
        if question is None or not question.item_key or not answer.text.strip():
            continue
        out.setdefault(question.item_key, []).append(answer)
    return out


def required_evidence(
    inputs: EvaluationInputs, drill_a: DrillScore, drill_b: DrillScore
) -> dict[str, str | None]:
    """Rule V4: per competency ``None`` when the required evidence exists, else why it does not."""
    by_id = {q.id: q for q in inputs.questions}
    answered = _answered_items(inputs)
    out: dict[str, str | None] = {}

    # communication: >= 3 answers of >= 25 words across >= 2 of greeting/resume/topic/learning
    long_answers = []
    for answer in inputs.answers:
        question = by_id.get(answer.question_id) if answer.question_id else None
        stage = question.stage if question else None
        if answer.activity_kind == "topic_card":
            stage = "topic"
        elif answer.activity_kind is not None:
            continue
        if stage in ("greeting", "resume", "topic", "learning") and answer.words >= COMMUNICATION_MIN_WORDS:
            long_answers.append(stage)
    out["communication"] = (
        None
        if len(long_answers) >= COMMUNICATION_MIN_ANSWERS
        and len(set(long_answers)) >= COMMUNICATION_MIN_STAGES
        else f"needs {COMMUNICATION_MIN_ANSWERS} answers of {COMMUNICATION_MIN_WORDS}+ words across "
        f"{COMMUNICATION_MIN_STAGES} stages (found {len(long_answers)} across {len(set(long_answers))})"
    )

    # technical_reading: both drills scored
    missing = [name for name, drill in (("Drill A", drill_a), ("Drill B", drill_b)) if drill.score is None]
    out["technical_reading"] = None if not missing else f"{' and '.join(missing)} not scored"

    # structured_answer: a speak capture of >= 30 words (or typed)
    speak = inputs.activity_answer("topic_card")
    activity = inputs.activities.get("topic_card")
    typed = bool(activity and activity.capture.get("typed")) or bool(speak and speak.typed)
    words = speak.words if speak else 0
    out["structured_answer"] = (
        None
        if speak is not None and (words >= STRUCTURED_MIN_WORDS or (typed and words > 0))
        else f"needs a topic-card answer of {STRUCTURED_MIN_WORDS}+ words (found {words})"
    )

    # technical: >= 2 answered ladder rungs or an answered verification question
    rungs = [key for key in answered if LADDER_RUNG.match(key)]
    verification = any(
        k in answered for k in ("mandatory.mq3.verification", "mandatory.mq3.verification_retry")
    )
    out["technical"] = (
        None
        if len(rungs) >= TECHNICAL_MIN_RUNGS or verification
        else f"needs {TECHNICAL_MIN_RUNGS} answered résumé ladder questions or the language check "
        f"(found {len(rungs)} rungs, verification {'answered' if verification else 'not answered'})"
    )

    # trainability: both learning.three_weeks and learning.teach answered
    need = [k for k in ("learning.three_weeks", "learning.teach") if k not in answered]
    out["trainability"] = None if not need else f"not answered: {', '.join(need)}"

    # composure: >= 4 answered items including >= 1 follow-up, clarification or challenge
    answered_questions = {
        a.question_id for a in inputs.answers if a.question_id and a.text.strip() and a.question_id in by_id
    }
    challenges = [
        qid
        for qid in answered_questions
        if by_id[qid].item_kind in CHALLENGE_KINDS
        or (by_id[qid].item_key and CHALLENGE_ITEM.search(by_id[qid].item_key or ""))
    ]
    out["composure"] = (
        None
        if len(answered_questions) >= COMPOSURE_MIN_ITEMS and challenges
        else f"needs {COMPOSURE_MIN_ITEMS} answered items including a follow-up or challenge "
        f"(found {len(answered_questions)} items, {len(challenges)} follow-ups/challenges)"
    )

    # adaptability: MQ2 or learning.three_weeks answered
    out["adaptability"] = (
        None
        if "mandatory.mq2" in answered or "learning.three_weeks" in answered
        else "neither MQ2 nor the three-weeks question was answered"
    )
    return out


# ============================================================================================== V5 mandatory coverage


def mandatory_findings(inputs: EvaluationInputs) -> list[Finding]:
    """Rule V5 from the stage results (T1's checkpoint): missing mandatory items and incomplete stages."""
    findings: list[Finding] = []
    for stage in sorted(inputs.stages.values(), key=lambda s: s.stage_number):
        if stage.status == "incomplete":
            findings.append(
                Finding(
                    kind="stage_incomplete",
                    detail=f"Stage {stage.stage_number} ({stage.stage_key}) did not finish.",
                    key=f"stage:{stage.stage_key}",
                )
            )
        for item in stage.items:
            if not isinstance(item, Mapping) or not item.get("mandatory"):
                continue
            status = str(item.get("status") or "pending")
            skip = item.get("skip_reason")
            if status in COVERED_STATUSES or (status == "skipped" and skip in DESIGNED_SKIPS):
                continue
            if item.get("item_key") in NO_ANSWER_ITEMS and status == "asked":
                continue
            reason = f" ({skip.replace('_', ' ')})" if isinstance(skip, str) and skip else ""
            findings.append(
                Finding(
                    kind="mandatory_item_missing",
                    detail=f"Mandatory item {item.get('item_key')} in stage {stage.stage_key}: {status}{reason}.",
                    key=f"item:{item.get('item_key')}",
                )
            )
    return findings


# ============================================================================================== judgements


def _judgement_result(
    key: str,
    evaluator: str,
    judgement: CompetencyJudgement | None,
    *,
    aliases: AliasMap,
    allowed: frozenset[str],
    issues: list[ValidatorIssue],
) -> CompetencyResult:
    if judgement is None:
        issues.append(ValidatorIssue("evaluator_fallback", key, "no valid evaluator output", evaluator))
        return CompetencyResult(
            key=key,
            status="insufficient",
            score=None,
            evaluator=evaluator,
            reasoning="The evaluator did not return a valid judgement; a reviewer should assess this competency.",
            missing_evidence=["evaluator unavailable"],
        )
    if judgement.competency != key:
        issues.append(
            ValidatorIssue(
                "competency_mismatch", key, f"judgement labelled {judgement.competency}", evaluator
            )
        )
    refs, dropped = validate_cites(judgement.evidence, aliases=aliases, allowed=allowed)
    if dropped:
        issues.append(ValidatorIssue("evidence_dropped", key, "; ".join(dropped)[:500], evaluator))
    result = CompetencyResult(
        key=key,
        status="scored",
        score=Decimal(judgement.score) if judgement.score is not None else None,
        evaluator=evaluator,
        reasoning=judgement.reasoning.strip(),
        evidence=refs,
        missing_evidence=[m for m in judgement.missing_evidence if m.strip()],
        strengths=[s for s in judgement.strengths if s.strip()],
        improvements=[i for i in judgement.improvements if i.strip()],
        model_score=judgement.score,
    )
    if not judgement.sufficient or judgement.score is None:
        if judgement.sufficient and judgement.score is None:
            issues.append(
                ValidatorIssue("score_missing", key, "marked sufficient without a score", evaluator)
            )
        result.status, result.score = "insufficient", None
        return result
    # V3 support
    supports = {ref["supports"] for ref in refs}
    problem = None
    if not refs:
        problem = "no valid evidence citation"
    elif judgement.score >= 4 and "positive" not in supports:
        problem = f"score {judgement.score} without a positive citation"
    elif judgement.score <= 2 and not supports & {"negative", "context"}:
        problem = f"score {judgement.score} without a negative or context citation"
    if problem:
        issues.append(ValidatorIssue("score_unsupported", key, problem, evaluator))
        result.status, result.score = "insufficient", None
        result.missing_evidence.append("validated evidence supporting the score")
    return result


def claim_results(
    output: ResumeEvidenceOutput | None,
    inputs: EvaluationInputs,
    *,
    aliases: AliasMap,
    allowed: frozenset[str],
    issues: list[ValidatorIssue],
) -> list[dict[str, Any]]:
    """Claim verdicts with validated evidence (unknown claim refs dropped)."""
    known = {c["ref"]: c for c in inputs.claims}
    out: list[dict[str, Any]] = []
    if output is None:
        return out
    seen: set[str] = set()
    for verdict in output.claims:
        ref = verdict.claim.strip().upper()
        if ref not in known or ref in seen:
            issues.append(
                ValidatorIssue("claim_dropped", None, f"{ref}: unknown or repeated claim", "resume_evidence")
            )
            continue
        seen.add(ref)
        refs, dropped = validate_cites(verdict.evidence, aliases=aliases, allowed=allowed)
        if dropped:
            issues.append(
                ValidatorIssue("evidence_dropped", None, f"{ref}: " + "; ".join(dropped), "resume_evidence")
            )
        rungs: dict[str, str | None] = {}
        for rung in ("ownership", "mechanics", "stress"):
            alias = getattr(verdict.rung_answers, rung)
            target = _alias_ok(alias, aliases, allowed)
            rungs[rung] = target.alias if target else None
        out.append(_claim_dict(known[ref], verdict, refs, rungs, aliases))
    return out


def _claim_dict(
    claim: Mapping[str, Any],
    verdict: ClaimVerdict,
    refs: list[dict[str, Any]],
    rungs: Mapping[str, str | None],
    aliases: AliasMap,
) -> dict[str, Any]:
    rung_refs = {}
    for rung, alias in rungs.items():
        target = aliases.get(alias) if alias else None
        rung_refs[rung] = str(target.answer_id) if target and target.answer_id else None
    return {
        "claim_ref": claim["ref"],
        "claim": claim.get("text"),
        "probe": claim.get("probe"),
        "source": claim.get("source"),
        "verdict": verdict.verdict,
        "rung_answers": rung_refs,
        "evidence": refs,
        "note": verdict.note.strip(),
        "unresolved": [u for u in verdict.unresolved if u.strip()],
    }


def bullet_results(
    output: StructuredOutput | None,
    bullets: Sequence[str],
    *,
    aliases: AliasMap,
    allowed: frozenset[str],
) -> list[dict[str, Any]]:
    """The four topic-card bullets with the model status and whether its quote validated."""
    judged: dict[int, BulletJudgement] = {}
    for bullet in output.bullets if output is not None else ():
        judged.setdefault(bullet.index, bullet)
    out: list[dict[str, Any]] = []
    for index, text in enumerate(bullets, start=1):
        item = judged.get(index)
        target = _alias_ok(item.answer, aliases, allowed) if item is not None else None
        valid = bool(item and target and item.quote and quote_matches(item.quote, target.text))
        status = item.status if item is not None else "missing"
        out.append(
            {
                "index": index,
                "text": text,
                "status": status,
                "quote_valid": valid,
                "quote": item.quote if (item is not None and valid) else None,
                "answer_id": str(target.answer_id) if (valid and target and target.answer_id) else None,
                "start_ms": target.start_ms if (valid and target) else None,
            }
        )
    return out


def concept_evidence(
    output: RelayOutput | None,
    *,
    aliases: AliasMap,
    relay_alias: str | None,
) -> list[Any] | None:
    """Relay concept judgements with quote validation against the relay answer only (``None`` = no model output)."""
    from app.services.intake.relay import ConceptEvidence

    if output is None:
        return None
    target = aliases.get(relay_alias) if relay_alias else None
    out = []
    seen: set[str] = set()
    for concept in output.concepts:
        if concept.key in seen:
            continue
        seen.add(concept.key)
        valid = bool(
            target is not None
            and concept.quote
            and (concept.answer or "").strip().upper() == target.alias
            and quote_matches(concept.quote, target.text)
        )
        out.append(
            ConceptEvidence(
                key=concept.key,
                status=concept.status,
                quote_valid=valid,
                answer=target.alias if valid and target else None,
                quote=concept.quote if valid else None,
            )
        )
    return out


# ============================================================================================== V6 consistency


def consistency_findings(
    competencies: Mapping[str, CompetencyResult],
    claims: Sequence[Mapping[str, Any]],
    bullets: Sequence[Mapping[str, Any]],
    issues: list[ValidatorIssue],
) -> list[Finding]:
    findings: list[Finding] = []
    technical = competencies.get("technical")
    structured = competencies.get("structured_answer")
    communication = competencies.get("communication")
    unsupported = [c for c in claims if c.get("verdict") in ("unsupported", "inconsistent")]
    if technical and technical.score is not None and technical.score >= 4 and len(unsupported) >= 2:
        findings.append(
            Finding(
                kind="evaluator_disagreement",
                detail=f"Technical ability scored {technical.score} while {len(unsupported)} résumé claims were "
                "not supported by the answers.",
                competency_keys=("technical",),
                evidence=[e for c in unsupported for e in c.get("evidence", [])][:4],
                key="v6:technical_claims",
            )
        )
    verified = [b for b in bullets if b.get("quote_valid") and b.get("status") in ("covered", "partial")]
    covered = [b for b in bullets if b.get("quote_valid") and b.get("status") == "covered"]
    if structured and structured.score is not None:
        if structured.score >= 4 and len(verified) < 3:
            findings.append(
                Finding(
                    kind="evaluator_disagreement",
                    detail=f"Structured answer scored {structured.score} with only {len(verified)} of 4 points "
                    "covered with a verified quote.",
                    competency_keys=("structured_answer",),
                    key="v6:structured_bullets",
                )
            )
        if structured.score == 5 and len(covered) < 4:
            structured.score = Decimal(4)
            issues.append(
                ValidatorIssue(
                    "score_lowered",
                    "structured_answer",
                    f"5 needs all four points covered; {len(covered)} verified -> 4",
                    "structured",
                )
            )
    if (
        communication
        and structured
        and communication.score is not None
        and structured.score is not None
        and abs(communication.score - structured.score) >= DISAGREEMENT_GAP
    ):
        findings.append(
            Finding(
                kind="evaluator_disagreement",
                detail=f"Communication ({communication.score}) and structured answer ({structured.score}) differ "
                f"by {abs(communication.score - structured.score)} points.",
                competency_keys=("communication", "structured_answer"),
                key="v6:communication_structured",
            )
        )
    return findings


# ============================================================================================== V7 arithmetic


def check_arithmetic(
    rows: Iterable[Mapping[str, Any]],
    *,
    total: Decimal | None,
    partial_total: Decimal | None,
    band: str | None,
) -> IntakeScoreSummary:
    """Rule V7: recompute the summary from stored ``(competency_key, score)`` rows and compare (partial totals at the
    column's 4-decimal precision)."""
    scores = {str(r["competency_key"]): r.get("score") for r in rows}
    if sorted(scores) != sorted(INTAKE_COMPETENCY_KEYS):
        raise ArithmeticMismatch(f"expected 7 competency rows, found {sorted(scores)}")
    summary = summarize_scores({k: (Decimal(str(v)) if v is not None else None) for k, v in scores.items()})
    if summary.total != total:
        raise ArithmeticMismatch(f"weighted total {summary.total} != stored {total}")
    q = Decimal("0.0001")
    recomputed_partial = summary.partial_total.quantize(q) if summary.partial_total is not None else None
    stored_partial = partial_total.quantize(q) if partial_total is not None else None
    if recomputed_partial != stored_partial:
        raise ArithmeticMismatch(f"partial total {recomputed_partial} != stored {stored_partial}")
    if band is not None and summary.band.value != band:
        raise ArithmeticMismatch(f"band {summary.band.value} != stored {band}")
    return summary


# ============================================================================================== orchestration


def validate_evaluation(
    inputs: EvaluationInputs,
    aliases: AliasMap,
    *,
    outputs: Mapping[str, Any],
    allowed: Mapping[str, frozenset[str]],
    drill_a: DrillScore,
    drill_b: DrillScore,
    card_bullets: Sequence[str] = (),
    fallbacks: Iterable[str] = (),
) -> ValidationResult:
    """Apply V1-V6 to the evaluator outputs and the deterministic drills; returns adjusted competency results, issues,
    findings, validated claims and bullets, and the F0 score summary. ``outputs`` maps evaluator -> validated model
    output (``None`` / missing = fallback)."""
    issues: list[ValidatorIssue] = []
    findings: list[Finding] = []
    fallback_set = set(fallbacks)

    def output(name: str, schema: type) -> Any:
        value = outputs.get(name)
        return value if isinstance(value, schema) else None

    communication = output("communication", CommunicationOutput)
    technical = output("technical", TechnicalOutput)
    structured = output("structured", StructuredOutput)
    trainability = output("trainability", TrainabilityOutput)
    composure = output("composure", ComposureOutput)
    resume = output("resume_evidence", ResumeEvidenceOutput)

    def empty(name: str) -> frozenset[str]:
        return allowed.get(name, frozenset())

    results: dict[str, CompetencyResult] = {
        "communication": _judgement_result(
            "communication",
            "communication",
            communication.judgement if communication else None,
            aliases=aliases,
            allowed=empty("communication"),
            issues=issues,
        ),
        "structured_answer": _judgement_result(
            "structured_answer",
            "structured",
            structured.judgement if structured else None,
            aliases=aliases,
            allowed=empty("structured"),
            issues=issues,
        ),
        "technical": _judgement_result(
            "technical",
            "technical",
            technical.judgement if technical else None,
            aliases=aliases,
            allowed=empty("technical"),
            issues=issues,
        ),
        "trainability": _judgement_result(
            "trainability",
            "trainability",
            trainability.trainability if trainability else None,
            aliases=aliases,
            allowed=empty("trainability"),
            issues=issues,
        ),
        "adaptability": _judgement_result(
            "adaptability",
            "trainability",
            trainability.adaptability if trainability else None,
            aliases=aliases,
            allowed=empty("trainability"),
            issues=issues,
        ),
        "composure": _judgement_result(
            "composure",
            "composure",
            composure.judgement if composure else None,
            aliases=aliases,
            allowed=empty("composure"),
            issues=issues,
        ),
    }
    for evaluator in sorted(fallback_set):
        keys = tuple(
            k for k, ev in COMPETENCY_EVALUATOR.items() if ev == evaluator and k != "technical_reading"
        )
        findings.append(
            Finding(
                kind="evaluator_fallback",
                detail=f"The {evaluator.replace('_', ' ')} evaluator did not return a valid result after retries.",
                competency_keys=keys,
                key=f"fallback:{evaluator}",
            )
        )

    # technical_reading: deterministic drills
    reading = technical_reading_score(drill_a.score, drill_b.score)
    results["technical_reading"] = CompetencyResult(
        key="technical_reading",
        status="scored" if reading is not None else "insufficient",
        score=reading,
        evaluator="relay+reading_metrics",
        reasoning=_reading_reasoning(drill_a, drill_b, reading),
        evidence=_drill_evidence(inputs, aliases),
        missing_evidence=[
            f"{name}: {drill.reason.replace('_', ' ') if drill.reason else 'not scored'}"
            for name, drill in (("Drill A", drill_a), ("Drill B", drill_b))
            if drill.score is None
        ],
        sub_scores={
            "drill_a": {"score": drill_a.score, "basis": list(drill_a.basis)},
            "drill_b": {"score": drill_b.score, "basis": list(drill_b.basis)},
            "formula": "0.4 × Drill A + 0.6 × Drill B",
        },
    )

    # V4 required evidence
    required = required_evidence(inputs, drill_a, drill_b)
    for key, problem in required.items():
        result = results[key]
        if problem is None:
            continue
        if result.status == "scored":
            issues.append(ValidatorIssue("required_evidence_missing", key, problem, result.evaluator))
        result.status, result.score = "insufficient", None
        if problem not in result.missing_evidence:
            result.missing_evidence.append(problem)

    claims = claim_results(resume, inputs, aliases=aliases, allowed=empty("resume_evidence"), issues=issues)
    bullets = bullet_results(structured, card_bullets, aliases=aliases, allowed=empty("structured"))

    # V6 consistency (may lower structured 5 -> 4)
    findings.extend(consistency_findings(results, claims, bullets, issues))
    # V5 mandatory coverage
    findings.extend(mandatory_findings(inputs))

    ordered = {key: results[key] for key in INTAKE_COMPETENCY_KEYS}
    summary = summarize_scores({key: r.score for key, r in ordered.items()})
    return ValidationResult(
        competencies=ordered,
        issues=issues,
        findings=findings,
        claims=claims,
        bullets=bullets,
        summary=summary,
    )


def _reading_reasoning(drill_a: DrillScore, drill_b: DrillScore, reading: Decimal | None) -> str:
    a = f"Drill A (read aloud) {drill_a.score}" if drill_a.score is not None else "Drill A not scored"
    b = f"Drill B (relay) {drill_b.score}" if drill_b.score is not None else "Drill B not scored"
    if reading is None:
        return f"{a}; {b}. Both drills are needed for this competency (0.4 × Drill A + 0.6 × Drill B)."
    return f"{a}; {b}. Combined as 0.4 × Drill A + 0.6 × Drill B = {format(reading, 'f')}."


def _drill_evidence(inputs: EvaluationInputs, aliases: AliasMap) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for kind in ("reading_aloud", "read_relay"):
        answer = inputs.activity_answer(kind)
        if answer is None or not answer.alias:
            continue
        target = aliases.get(answer.alias)
        if target is None:
            continue
        excerpt = " ".join(target.text.split()[:40]) or None
        refs.append(evidence_ref(target, quote=excerpt, supports="context"))
    return refs
