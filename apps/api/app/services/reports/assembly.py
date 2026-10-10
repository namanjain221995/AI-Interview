"""Deterministic report assembly (ENGINE_DESIGN.md section 11).

:func:`assemble_report_data` reads everything a report needs from Postgres
and returns one JSON-safe document (the *recruiter* view: scores, rubric,
recommendation, evidence, questions with evaluations, coding, claims,
contradictions, integrity summary, timeline). :func:`candidate_view`
projects it to the candidate-safe subset: no integrity details, no
recommendation, no recruiter notes, no claim verification, no internal
interviewer reasoning, no hidden-test data.

Model-written text read from stored records (evidence, contradiction
descriptions, claim notes, evaluator notes, coding reviews) is put into
neutral wording in the assembled document (:mod:`app.services.reports.wording`);
the records themselves are never modified, so reports written before a
wording rule existed read neutrally when they are regenerated.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Candidate,
    CodeRun,
    CodingHint,
    IntegrityEvent,
    Job,
    Round,
    ScoreSnapshot,
)
from app.domain.enums import EvidenceSignal
from app.schemas.ai import AnswerEvaluation as AnswerEvaluationDoc
from app.services.integrity.queries import NOTICE as INTEGRITY_NOTICE
from app.services.integrity.queries import integrity_summary
from app.services.integrity.rules import label_for
from app.services.reports.wording import candidate_voice, neutral_recruiter_document
from app.services.scoring.compute import assistance_factor, is_assessed
from app.services.scoring.persistence import (
    FLAG_ACTIONS,
    ScoringComputation,
    coding_evaluation,
    coding_extras,
    evaluation_document,
    humanize_key,
)
from app.services.scoring.timeline import TimelinePoint, bucket_timeline

SCHEMA_VERSION = 1
MAX_ANSWER_CHARS = 4000
MAX_EXCERPT_CHARS = 600
MAX_INTEGRITY_EVENTS = 100
MAX_CODE_CHARS = 20000
INTEGRITY_NOTE = INTEGRITY_NOTICE  # one wording, owned by the integrity module
CANDIDATE_NOTE = (
    "This feedback was generated from your interview answers to help you prepare and improve. "
    "Hiring decisions are made by people at the hiring organization, not by the AI."
)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def truncate(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _duration_seconds(started: datetime | None, ended: datetime | None) -> float | None:
    if started is None or ended is None:
        return None
    return max(0.0, (ended - started).total_seconds())


def _platform_fault(run: Any) -> bool:
    return bool((run.sandbox_meta or {}).get("platform_fault"))


@dataclass(slots=True)
class ReportData:
    """Assembled report document plus lookups used by the narrative step."""

    document: dict[str, Any]
    evidence_ids: set[str] = field(default_factory=set)
    question_ids: set[str] = field(default_factory=set)
    competency_keys: set[str] = field(default_factory=set)
    organization_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


def _evidence_items(computation: ScoringComputation) -> list[dict[str, Any]]:
    out = []
    for row in computation.inputs.evidence_rows:
        ref = dict(row.transcript_ref or {})
        out.append(
            {
                "id": str(row.id),
                "competency_key": row.competency_key,
                "signal": row.signal,
                "dimension": row.dimension,
                "evidence": row.evidence,
                "quote": ref.get("quote"),
                "strength": round(float(row.strength), 3),
                "confidence": round(float(row.confidence), 3),
                "source": row.source,
                "difficulty": row.difficulty,
                "question_id": _s(row.question_id),
                "answer_id": _s(row.answer_id),
                "coding_session_id": _s(row.coding_session_id),
                "elapsed_seconds": row.elapsed_seconds,
                "transcript_ref": ref,
            }
        )
    return out


def _evaluation_summary(doc: AnswerEvaluationDoc | None, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    dims = {k: v for k, v in (doc.scores.model_dump() if doc else {}).items() if v is not None}
    return {
        "id": str(row.id),
        "overall_quality": row.overall_quality,
        "dimensions": dims,
        "summary": doc.summary if doc else "",
        "technical_evidence": list(doc.technical_evidence) if doc else [],
        "measurable_outcomes": list(doc.measurable_outcomes) if doc else [],
        "missing_details": list(doc.missing_details) if doc else [],
        "unsupported_claims": list(doc.unsupported_claims) if doc else [],
        "contradictions": [c.model_dump() for c in doc.contradictions] if doc else [],
        "confidence": round(float(row.confidence or 0.0), 3),
        "is_fallback": bool(row.is_fallback),
    }


def _questions(computation: ScoringComputation) -> list[dict[str, Any]]:
    inputs = computation.inputs
    evals_by_answer = {e.answer_id: e for e in inputs.evaluations}
    evidence_by_answer: dict[uuid.UUID, list[str]] = {}
    for row in inputs.evidence_rows:
        if row.answer_id is not None:
            evidence_by_answer.setdefault(row.answer_id, []).append(str(row.id))
    answers_by_question: dict[uuid.UUID | None, list[Any]] = {}
    for answer in inputs.answers:
        answers_by_question.setdefault(answer.question_id, []).append(answer)
    depth: dict[uuid.UUID, int] = {}
    by_id = {q.id: q for q in inputs.questions}
    children: dict[uuid.UUID, list[str]] = {}
    for q in inputs.questions:
        if q.parent_question_id:
            children.setdefault(q.parent_question_id, []).append(str(q.id))

    def _depth(q_id: uuid.UUID, guard: int = 0) -> int:
        if q_id in depth:
            return depth[q_id]
        q = by_id.get(q_id)
        if q is None or q.parent_question_id is None or q.parent_question_id not in by_id or guard > 50:
            depth[q_id] = 0
        else:
            depth[q_id] = _depth(q.parent_question_id, guard + 1) + 1
        return depth[q_id]

    out = []
    for q in inputs.questions:
        answers = []
        for answer in answers_by_question.get(q.id, []):
            eval_row = evals_by_answer.get(answer.id)
            doc = None
            if eval_row is not None:
                try:
                    doc = evaluation_document(eval_row)
                except Exception:  # noqa: BLE001 - a malformed evaluation must not break the report
                    doc = None
            answers.append(
                {
                    "id": str(answer.id),
                    "sequence": answer.sequence,
                    "transcript_text": truncate(answer.transcript_text, MAX_ANSWER_CHARS),
                    "typed": answer.typed,
                    "started_ms": answer.started_ms,
                    "ended_ms": answer.ended_ms,
                    "duration_ms": answer.duration_ms,
                    "word_count": answer.word_count,
                    "interrupted_ai": answer.interrupted_ai,
                    "evaluation": _evaluation_summary(doc, eval_row),
                    "evidence_ids": evidence_by_answer.get(answer.id, []),
                }
            )
        out.append(
            {
                "id": str(q.id),
                "sequence": q.sequence,
                "text": q.spoken_text or q.text,
                "focus": q.question_focus,
                "kind": q.kind,
                "action": q.action,
                "source": q.source,
                "segment": q.segment,
                "topic": q.topic,
                "competency_keys": list(q.competency_keys or []),
                "difficulty": q.difficulty,
                "expected_evidence": list(q.expected_evidence or []),
                "parent_question_id": _s(q.parent_question_id),
                "followup_ids": children.get(q.id, []),
                "followup_depth": _depth(q.id),
                "claim_id": q.claim_id,
                "reason": q.reason,
                "interrupted": q.interrupted,
                "elapsed_seconds": round(float(q.elapsed_seconds or 0.0), 1),
                "answers": answers,
            }
        )
    return out


def _competencies(computation: ScoringComputation, supporting: dict[str, list[str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, state in computation.states.items():
        calc = computation.calculations.get(key)
        out.append(
            {
                "key": key,
                "name": state.name or computation.inputs.competency_names.get(key) or humanize_key(key),
                "weight": state.weight,
                "required": state.required,
                # Unassessed (no observation, no evidence): no score, never the neutral starting estimate.
                "score": round(state.score_estimate, 2) if is_assessed(state) else None,
                "assessed": is_assessed(state),
                "confidence": round(state.confidence, 3),
                "evidence_count": state.evidence_count,
                "positive_count": state.positive_evidence,
                "negative_count": state.negative_evidence,
                "sufficient_evidence": state.sufficient_evidence,
                "unanswered_areas": list(state.unanswered_questions),
                "dimensions_covered": list(state.dimensions_covered),
                "difficulty_seen": dict(state.difficulty_seen),
                "positive_evidence_ids": list(state.positive_evidence_ids),
                "negative_evidence_ids": list(state.negative_evidence_ids),
                "supporting_question_ids": supporting.get(key, []),
                "calculation": calc.as_dict() if calc is not None else None,
            }
        )
    out.sort(key=lambda c: (not c["required"], -float(c["weight"]), c["key"]))
    return out


def _supporting_questions(computation: ScoringComputation) -> dict[str, list[str]]:
    order = {str(q.id): q.sequence for q in computation.inputs.questions}
    ids: dict[str, set[str]] = {}
    for obs in computation.inputs.observations:
        if obs.question_id:
            ids.setdefault(obs.competency_key, set()).add(obs.question_id)
    for ev in computation.inputs.evidence:
        if ev.question_id:
            ids.setdefault(ev.competency_key, set()).add(ev.question_id)
    return {k: sorted(v, key=lambda q: (order.get(q, 10**6), q)) for k, v in ids.items()}


def _claims_and_contradictions(
    computation: ScoringComputation,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    state = computation.inputs.session.state or {}
    claims: list[dict[str, Any]] = []
    raw_claims = state.get("claims")
    if isinstance(raw_claims, dict) and raw_claims:
        for claim_id, claim in raw_claims.items():
            if not isinstance(claim, dict):
                continue
            inner = claim.get("claim") or {}
            claims.append(
                {
                    "id": claim_id,
                    "claim": inner.get("claim", ""),
                    "category": inner.get("category"),
                    "priority": inner.get("priority"),
                    "competency_keys": list(inner.get("competency_keys") or []),
                    "status": claim.get("status", "unverified"),
                    "note": claim.get("note"),
                    "question_ids": list(claim.get("question_ids") or []),
                    "evidence_ids": list(claim.get("evidence_ids") or []),
                }
            )
    else:
        latest: dict[str, dict[str, Any]] = {}
        for row in computation.inputs.evaluations:
            for verification in (row.evaluation or {}).get("claim_verifications", []) or []:
                if isinstance(verification, dict) and verification.get("claim_id"):
                    latest[str(verification["claim_id"])] = {
                        "id": str(verification["claim_id"]),
                        "claim": "",
                        "category": None,
                        "priority": None,
                        "competency_keys": [],
                        "status": verification.get("status", "unverified"),
                        "note": verification.get("note"),
                        "question_ids": [_s(row.question_id)] if row.question_id else [],
                        "evidence_ids": [],
                    }
        claims = list(latest.values())
    contradictions: list[dict[str, Any]] = []
    raw = state.get("contradictions")
    if isinstance(raw, list) and raw:
        for c in raw:
            if isinstance(c, dict):
                contradictions.append(
                    {
                        "id": c.get("id"),
                        "description": c.get("description", ""),
                        "severity": c.get("severity", "low"),
                        "status": c.get("status", "open"),
                        "refs": list(c.get("refs") or []),
                    }
                )
    else:
        for row in computation.inputs.evaluations:
            for i, finding in enumerate((row.evaluation or {}).get("contradictions", []) or []):
                if isinstance(finding, dict):
                    contradictions.append(
                        {
                            "id": f"{row.answer_id}:{i}",
                            "description": f"{finding.get('statement', '')} — conflicts with: {finding.get('conflicts_with', '')}",
                            "severity": finding.get("severity", "low"),
                            "status": "open",
                            "refs": [str(row.answer_id)],
                        }
                    )
    return claims, contradictions


async def _coding(db: AsyncSession, computation: ScoringComputation) -> list[dict[str, Any]]:
    sessions = computation.inputs.coding_sessions
    if not sessions:
        return []
    ids = [c.id for c in sessions]
    runs = list(
        (
            await db.scalars(
                select(CodeRun).where(CodeRun.coding_session_id.in_(ids)).order_by(CodeRun.created_at)
            )
        ).all()
    )
    hints = list(
        (
            await db.scalars(
                select(CodingHint).where(CodingHint.coding_session_id.in_(ids)).order_by(CodingHint.level)
            )
        ).all()
    )
    out = []
    for cs in sessions:
        cq = computation.inputs.coding_questions.get(cs.coding_question_id)
        doc = coding_evaluation(cs)
        cs_runs = [r for r in runs if r.coding_session_id == cs.id]
        # Runs the code runner failed say nothing about the candidate's code: never the final result (S20).
        real_runs = [r for r in cs_runs if not _platform_fault(r)]
        submit = next((r for r in reversed(real_runs) if r.kind == "submit"), None)
        final_run = next(
            (r for r in real_runs if cs.final_run_id and r.id == cs.final_run_id),
            submit or (real_runs[-1] if real_runs else None),
        )
        out.append(
            {
                "id": str(cs.id),
                "question_id": _s(cs.question_id),
                "coding_question_id": str(cs.coding_question_id),
                "title": cq.title if cq else "Coding problem",
                "slug": cq.slug if cq else None,
                "difficulty": cq.difficulty if cq else None,
                "competency_keys": list(cq.competency_keys or []) if cq else [],
                "language": cs.language,
                "status": cs.status,
                "started_at": iso(cs.started_at),
                "ended_at": iso(cs.ended_at),
                "duration_seconds": _duration_seconds(cs.started_at, cs.ended_at),
                "time_limit_seconds": cs.time_limit_seconds,
                "hints_used": cs.hints_used,
                "hints_available": cs.hints_available,
                "assistance_factor": assistance_factor(cs.hints_used),
                "paste_event_count": cs.paste_event_count,
                "final_code": truncate(cs.final_code, MAX_CODE_CHARS) if cs.final_code else None,
                "final_result": (
                    {
                        "status": final_run.status,
                        "kind": final_run.kind,
                        "passed": final_run.passed_count,
                        "total": final_run.total_count,
                        "runtime_ms": final_run.runtime_ms,
                    }
                    if final_run is not None
                    else None
                ),
                "runs": [
                    {
                        "id": str(r.id),
                        "kind": r.kind,
                        "status": r.status,
                        "passed": r.passed_count,
                        "total": r.total_count,
                        "runtime_ms": r.runtime_ms,
                        "elapsed_seconds": r.elapsed_seconds,
                        "platform_fault": _platform_fault(r),
                    }
                    for r in cs_runs
                ],
                "hints": [
                    {
                        "level": h.level,
                        "requested_via": h.requested_via,
                        "elapsed_seconds": h.elapsed_seconds,
                        "source": h.source,
                    }
                    for h in hints
                    if h.coding_session_id == cs.id
                ],
                "evaluation": doc.model_dump(mode="json") if doc is not None else None,
                "evaluation_meta": coding_extras(cs),
            }
        )
    return out


async def _integrity(db: AsyncSession, session_id: uuid.UUID) -> dict[str, Any]:
    """Objective integrity section (recruiter report only): the integrity
    module's summary plus the individual events for the reviewer."""
    summary = await integrity_summary(db, session_id)
    events = list(
        (
            await db.scalars(
                select(IntegrityEvent)
                .where(IntegrityEvent.session_id == session_id)
                .order_by(
                    IntegrityEvent.occurred_at,
                    IntegrityEvent.elapsed_seconds.nulls_last(),
                    IntegrityEvent.created_at,
                    IntegrityEvent.id,
                )
                .limit(MAX_INTEGRITY_EVENTS)
            )
        ).all()
    )
    by_type = summary.get("by_type") or {}
    return {
        **summary,
        "note": summary.get("notice") or INTEGRITY_NOTE,
        "occurrences": sum(int(entry.get("occurrences") or 0) for entry in by_type.values()),
        "events": [
            {
                "id": str(e.id),
                "type": e.type,
                "label": label_for(e.type),
                "source": e.source,
                "severity": e.severity,
                "confidence": e.confidence,
                "elapsed_seconds": e.elapsed_seconds,
                "count": e.count,
                "action": e.action,
                "flagged": e.action in FLAG_ACTIONS,
                "review_status": e.review_status,
                "review_note": e.review_note,
                "evidence_media_id": _s(e.evidence_media_id),
            }
            for e in events
        ],
    }


async def _timeline(
    db: AsyncSession, computation: ScoringComputation, names: dict[str, str]
) -> dict[str, Any]:
    session = computation.inputs.session
    rows = list(
        (
            await db.scalars(
                select(ScoreSnapshot)
                .where(ScoreSnapshot.session_id == session.id)
                .order_by(ScoreSnapshot.elapsed_seconds)
            )
        ).all()
    )
    if rows:
        points = [
            TimelinePoint(
                r.elapsed_seconds, r.competency_key, r.score, r.confidence, r.evidence_count, r.trigger
            )
            for r in rows
        ]
    else:
        points = [
            TimelinePoint(p.elapsed_s, key, p.score, p.confidence, p.evidence_count, "replay")
            for key, replay_points in computation.replays.items()
            for p in replay_points
        ]
    windows = bucket_timeline(
        points, window_minutes=10, duration_seconds=float(session.elapsed_seconds or 0.0), names=names
    )
    return {"window_minutes": 10, "windows": windows}


def _excerpts(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for q in questions:
        for a in q["answers"]:
            if not a["transcript_text"]:
                continue
            out.append(
                {
                    "question_id": q["id"],
                    "sequence": q["sequence"],
                    "question": truncate(q["text"], 400),
                    "answer_id": a["id"],
                    "answer_excerpt": truncate(a["transcript_text"], MAX_EXCERPT_CHARS),
                    "start_ms": a["started_ms"],
                    "end_ms": a["ended_ms"],
                }
            )
    return out


async def assemble_report_data(db: AsyncSession, computation: ScoringComputation) -> ReportData:
    """Everything deterministic about a session's report (recruiter view)."""
    inputs = computation.inputs
    session = inputs.session
    candidate = await db.get(Candidate, session.candidate_id)
    job = await db.get(Job, session.job_id)
    round_row = await db.get(Round, session.round_id)
    supporting = _supporting_questions(computation)
    competencies = _competencies(computation, supporting)
    names = {c["key"]: c["name"] for c in competencies}
    questions = _questions(computation)
    claims, contradictions = _claims_and_contradictions(computation)
    rec = computation.recommendation
    evidence = _evidence_items(computation)
    config = inputs.round_config
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "recruiter",
        "session": {
            "id": str(session.id),
            "interview_id": str(session.interview_id),
            "status": session.status,
            "started_at": iso(session.started_at),
            "ended_at": iso(session.ended_at),
            "end_reason": session.end_reason,
            "elapsed_seconds": round(float(session.elapsed_seconds or 0.0), 1),
            "wall_seconds": _duration_seconds(session.started_at, session.ended_at),
            "reconnect_count": session.reconnect_count,
        },
        "candidate": {
            "id": str(session.candidate_id),
            "full_name": candidate.full_name if candidate else None,
            "email": candidate.email if candidate else None,
        },
        "job": {
            "id": str(session.job_id),
            "title": job.title if job else None,
            "department": job.department if job else None,
            "seniority": job.seniority if job else None,
        },
        "round": {
            "id": str(session.round_id),
            "name": config.name or (round_row.name if round_row else None),
            "round_type": config.round_type.value,
            "duration_minutes": config.duration_minutes,
            "expected_difficulty": config.expected_difficulty.value,
        },
        "scores": {
            "overall": {
                "score": computation.rubric.overall_score,
                "confidence": computation.rubric.overall_confidence,
                "required_coverage": computation.rubric.required_coverage,
                "evidence_coverage": round(computation.evidence_coverage, 3),
                "assessed_weight_share": computation.rubric.assessed_weight_share,
            },
            "rubric": [
                {
                    "key": c.key,
                    "label": c.label,
                    "weight": c.weight,
                    "score": c.score,
                    "confidence": c.confidence,
                    "contributing_competencies": c.contributing_competencies,
                }
                for c in computation.rubric.categories
            ],
            "competencies": competencies,
            "dimension_averages": dict(inputs.dimension_averages),
        },
        "recommendation": {
            "value": rec.recommendation.value,
            "rationale": rec.rationale,
            "factors": dict(rec.factors),
            "human_decision_note": rec.human_decision_note,
            "passing_guidance": config.passing_guidance.model_dump(mode="json"),
        },
        "evidence": evidence,
        "questions": questions,
        "transcript_excerpts": _excerpts(questions),
        "coding": await _coding(db, computation),
        "claims": claims,
        "claims_summary": dict(Counter(c["status"] for c in claims)),
        "contradictions": contradictions,
        "integrity": await _integrity(db, session.id),
        "timeline": await _timeline(db, computation, names),
        "pending_evaluation_jobs": inputs.pending_jobs,
        "unevaluated_answers": len(inputs.unevaluated_answer_ids),
        "interviewer_notes": config.interviewer_notes,
    }
    # Neutral wording for stored model-written text (a new document; the records stay as they are).
    document = neutral_recruiter_document(document)
    return ReportData(
        document=document,
        evidence_ids={e["id"] for e in evidence},
        question_ids={q["id"] for q in questions},
        competency_keys={c["key"] for c in competencies},
        organization_id=session.organization_id,
        session_id=session.id,
    )


# --------------------------------------------------------------------------
# Candidate projection
# --------------------------------------------------------------------------

# Resume-claim verification is recruiter-only (claims, contradictions, the
# resume-consistency rubric category): candidate-facing text that comes from
# staff-facing sources (evaluator notes, the engine's open areas) is filtered.
_CLAIM_VERIFICATION = re.compile(
    r"\b(?:r[eé]sum[eé]s?|cv|claim(?:s|ed)?|employment history|contradict\w*|inconsisten\w*"
    r"|discrepanc\w*|mismatch\w*)\b",
    re.IGNORECASE,
)
RECRUITER_ONLY_RUBRIC_KEYS = frozenset({"resume_consistency"})
# Questions that probe a contradiction: their evaluator notes are about the
# contradiction itself, whatever the wording, so none reach the candidate.
_CONTRADICTION_ACTIONS = frozenset({"verify_contradiction"})


def candidate_safe(text: str | None) -> bool:
    """False for text about resume-claim verification (never shown to candidates)."""
    return bool(text) and not _CLAIM_VERIFICATION.search(str(text))


def _safe_items(values: Iterable[Any] | None) -> list[Any]:
    return [v for v in values or [] if candidate_safe(v)]


def _question_notes(narrative: dict[str, Any] | None) -> dict[str, str]:
    """Candidate-voice note per question from the candidate narrative."""
    notes: dict[str, str] = {}
    for section in ("incomplete_answers", "strong_answers"):
        for item in (narrative or {}).get(section) or []:
            note = item.get("note") if isinstance(item, dict) else None
            qid = str(item.get("question_id")) if isinstance(item, dict) else ""
            if qid and note and candidate_safe(note):
                notes.setdefault(qid, note)
    return notes


def _candidate_feedback(
    evaluation: dict[str, Any], note: str | None, *, probes_contradiction: bool = False
) -> dict[str, Any]:
    """Per-question feedback for the candidate. The evaluator's ``summary`` is
    written for reviewers ("The candidate ..."), so the candidate sees the
    narrative's note for that question instead (or nothing)."""
    if probes_contradiction:
        return {
            "overall_quality": None,
            "summary": note,
            "missing_details": [],
            "measurable_outcomes": [],
            "technical_evidence": [],
        }
    return {
        "overall_quality": evaluation.get("overall_quality"),
        "summary": note,
        "missing_details": _voice(_safe_items(evaluation.get("missing_details"))),
        "measurable_outcomes": _voice(_safe_items(evaluation.get("measurable_outcomes"))),
        "technical_evidence": _voice(_safe_items(evaluation.get("technical_evidence"))),
    }


def _voice(values: Iterable[Any]) -> list[Any]:
    """Evaluator notes are written for reviewers: neutral wording, addressed to the candidate."""
    return [candidate_voice(v) for v in values]


def _candidate_evidence(items: Iterable[dict[str, Any]], referenced: set[str]) -> list[dict[str, Any]]:
    out = []
    for e in items:
        if e["id"] not in referenced or not candidate_safe(e.get("evidence")):
            continue
        out.append(
            {
                "id": e["id"],
                "competency_key": e["competency_key"],
                "signal": e["signal"],
                "dimension": e["dimension"],
                "evidence": candidate_voice(e["evidence"]),
                "quote": e.get("quote"),
                "question_id": e.get("question_id"),
            }
        )
    return out


def candidate_view(document: dict[str, Any], narrative: dict[str, Any] | None) -> dict[str, Any]:
    """Candidate-safe projection of an assembled recruiter document."""
    referenced: set[str] = set()
    for point in (narrative or {}).get("technical_strengths", []) or []:
        referenced.update(point.get("evidence_ids") or [])
    for area in (narrative or {}).get("areas_to_improve", []) or []:
        referenced.update(area.get("evidence_ids") or [])
    notes = _question_notes(narrative)
    questions = []
    for q in document["questions"]:
        feedback = None
        for a in q["answers"]:
            ev = a.get("evaluation")
            if ev:
                feedback = _candidate_feedback(
                    ev,
                    notes.get(str(q["id"])),
                    probes_contradiction=q.get("action") in _CONTRADICTION_ACTIONS,
                )
        questions.append(
            {
                "id": q["id"],
                "sequence": q["sequence"],
                "text": q["text"],
                "focus": q["focus"],
                "competency_keys": q["competency_keys"],
                "difficulty": q["difficulty"],
                "parent_question_id": q["parent_question_id"],
                "feedback": feedback,
            }
        )
    coding = []
    for c in document["coding"]:
        evaluation = c.get("evaluation") or {}
        coding.append(
            {
                "id": c["id"],
                "title": c["title"],
                "difficulty": c["difficulty"],
                "language": c["language"],
                "status": c["status"],
                "duration_seconds": c["duration_seconds"],
                "hints_used": c["hints_used"],
                "tests_passed": (c.get("final_result") or {}).get("passed"),
                "tests_total": (c.get("final_result") or {}).get("total"),
                "time_complexity": evaluation.get("time_complexity"),
                "space_complexity": evaluation.get("space_complexity"),
                "strengths": _voice(evaluation.get("strengths") or []),
                "weaknesses": _voice(evaluation.get("weaknesses") or []),
                "correctness_summary": candidate_voice(evaluation.get("correctness_summary")),
            }
        )
    scores = document["scores"]
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "candidate",
        "session": {
            "id": document["session"]["id"],
            "started_at": document["session"]["started_at"],
            "ended_at": document["session"]["ended_at"],
            "elapsed_seconds": document["session"]["elapsed_seconds"],
        },
        "candidate": {"full_name": document["candidate"]["full_name"]},
        "job": {"title": document["job"]["title"]},
        "round": {"name": document["round"]["name"], "round_type": document["round"]["round_type"]},
        "overall_score": scores["overall"]["score"],
        "overall_confidence": scores["overall"]["confidence"],
        "rubric": [
            {"key": c["key"], "label": c["label"], "weight": c["weight"], "score": c["score"]}
            for c in scores["rubric"]
            if c["key"] not in RECRUITER_ONLY_RUBRIC_KEYS
        ],
        "competencies": [
            {
                "key": c["key"],
                "name": c["name"],
                "score": c["score"],
                "confidence": c["confidence"],
                "evidence_count": c["evidence_count"],
                "unanswered_areas": _safe_items(c["unanswered_areas"]),
            }
            for c in scores["competencies"]
            if c["evidence_count"] > 0 or c["required"]
        ],
        "questions": questions,
        "coding": coding,
        "evidence": _candidate_evidence(document["evidence"], referenced),
        "narrative": narrative,
        "note": CANDIDATE_NOTE,
    }


def positive_negative(
    document: dict[str, Any], key: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = [e for e in document["evidence"] if e["competency_key"] == key]
    pos = sorted(
        (e for e in items if e["signal"] == EvidenceSignal.POSITIVE.value),
        key=lambda e: -(e["strength"] * e["confidence"]),
    )
    neg = sorted(
        (e for e in items if e["signal"] == EvidenceSignal.NEGATIVE.value),
        key=lambda e: -(e["strength"] * e["confidence"]),
    )
    return pos, neg
