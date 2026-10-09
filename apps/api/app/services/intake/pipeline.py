"""Intake evaluation and report pipeline (design contract sections 8.1-8.10 and 9.1).

``evaluate_session`` (job ``intake.evaluate``)
    1. the session must have ended and be an intake session (else a permanent error);
    2. first delivery: ``open_link.round_finished`` (open-link progression, as ``scoring.finalize`` does);
    3. waits for settled activity transcripts (``final`` / ``live_only`` / ``unavailable``): otherwise it re-enqueues
       itself (10 s, at most 12 times) and returns ``{deferred: true}`` — it never writes activity rows;
    4. reserves the evaluation row (``job_id`` retries reuse it; a ``session_completed`` duplicate with the same
       input digest is a no-op);
    5. deterministic reading metrics (Drill A alignment + Drill B cue coverage), checkpointed;
    6. model evaluators with bounded concurrency (``INTAKE_EVAL_CONCURRENCY``), each checkpointed in
       ``intake_evaluator_runs`` (a ``succeeded`` / ``fallback`` run is never repeated); ``technical`` runs after
       ``resume_evidence``; a retryable failure retries the job, the job's last attempt turns it into ``fallback``;
    7. deterministic drill scores, validator (V1-V7), F0 ``summarize_scores``, review items;
    8. one transaction: seven competency rows, the evaluation row ``ready``, the generic ``scores`` rows; then the
       ``intake.evaluated`` bus event and the ``intake.report`` job.

``generate_intake_report`` (job ``intake.report``)
    training plan (checkpointed, one model call, deterministic fallback), a ``reports`` row (``type = recruiter``,
    ``content.kind = "intake"``, retries reuse the row), the HTML artifact, session ``COMPLETING -> COMPLETED`` and
    ``report.ready`` (``candidate_report_available: false``). No candidate report is ever created for intake.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import Conflict
from app.core.logging import get_logger
from app.core.queue import PermanentJobError, enqueue, publish_session_event
from app.db.models import (
    Candidate,
    CandidateProfile,
    IntegrityEvent,
    InterviewSession,
    Job,
    MediaAsset,
    Report,
    Score,
)
from app.db.models.intake import (
    IntakeCompetencyScore,
    IntakeDecision,
    IntakeEvaluation,
    IntakeEvaluatorRun,
    IntakeInterview,
)
from app.db.session import session_scope
from app.domain.enums import (
    IntegrityReviewStatus,
    MediaKind,
    Recommendation,
    ReportStatus,
    ReportType,
    ScoreCategory,
    SessionStatus,
)
from app.services.intake import evidence as ev
from app.services.intake.eval_schemas import (
    EVALUATOR_SCHEMAS,
    CompetencyJudgement,
    RelayOutput,
    ResumeEvidenceOutput,
    StructuredOutput,
    TechnicalOutput,
    TrainabilityOutput,
)
from app.services.intake.evaluators import (
    EvaluatorBackend,
    EvaluatorCall,
    EvaluatorOutcome,
    ModelBackend,
    build_call,
    run_call,
)
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.reading import ReadingMetrics, RelayCoverage, relay_coverage
from app.services.intake.relay import DrillScore, capture_problem, drill_a_score, reading_metrics, relay_score
from app.services.intake.report import (
    SCORING_VERSION,
    ReportInputs,
    assemble_intake_report,
    excerpt,
    report_meta,
)
from app.services.intake.report_html import render_intake_html
from app.services.intake.review_items import build_review_items
from app.services.intake.scoring import INTAKE_COMPETENCY_KEYS, INTAKE_LABELS, INTAKE_WEIGHTS, DecisionBand
from app.services.intake.training import generate_training_plan
from app.services.intake.validator import (
    ValidationResult,
    check_arithmetic,
    claim_results,
    concept_evidence,
    validate_cites,
    validate_evaluation,
)
from app.services.integrity.queries import count_unreviewed_flags
from app.services.integrity.rules import label_for
from app.services.reports.queries import report_retention_days
from app.services.scoring.persistence import advisory_lock_key
from app.services.storage.media import delete_media, store_media

log = get_logger(__name__)

INTAKE_ROUND_TYPE = "intake"
EVALUATE_JOB = "intake.evaluate"
REPORT_JOB = "intake.report"
MAX_DEFERRALS = 12
DEFER_SECONDS = 10.0
STALE_RUNNING = timedelta(minutes=15)
ENDED_STATUSES = frozenset(
    {
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
        SessionStatus.ABORTED.value,
        SessionStatus.FAILED.value,
    }
)
SETTLED_TRANSCRIPTS = frozenset({"final", "live_only", "unavailable"})
PARALLEL_EVALUATORS = ("communication", "resume_evidence", "relay", "structured", "trainability", "composure")
DONE_RUN_STATUSES = frozenset({"succeeded", "fallback"})
BAND_RECOMMENDATION: dict[str, Recommendation] = {
    DecisionBand.STRONG_ACCEPT.value: Recommendation.STRONG_PROCEED,
    DecisionBand.ACCEPT_WITH_NOTES.value: Recommendation.PROCEED,
    DecisionBand.BORDERLINE.value: Recommendation.BORDERLINE_EVIDENCE,
    DecisionBand.REJECT.value: Recommendation.HUMAN_REVIEW,
    DecisionBand.INSUFFICIENT_EVIDENCE.value: Recommendation.INSUFFICIENT_EVIDENCE,
}


class RetryEvaluation(RuntimeError):
    """A retryable evaluator failure before the job's last attempt (checkpoints are kept; the job retries)."""

    retryable = True


class EvaluationRunning(Conflict):
    code = "evaluation_running"


# ============================================================================================== helpers


def is_intake_round_config(round_config: Mapping[str, Any] | None) -> bool:
    return str((round_config or {}).get("round_type") or "") == INTAKE_ROUND_TYPE


def is_intake_session(session: Any) -> bool:
    """Whether a session runs an intake round (frozen ``round_config.round_type``)."""
    return is_intake_round_config(getattr(session, "round_config", None))


def recommendation_for(band: str | None) -> Recommendation | None:
    return BAND_RECOMMENDATION.get(band or "")


def _now() -> datetime:
    return datetime.now(UTC)


def evaluation_is_stale(row: IntakeEvaluation, *, now: datetime | None = None) -> bool:
    started = row.started_at or row.updated_at or row.created_at
    return started is not None and (now or _now()) - started > STALE_RUNNING


async def running_evaluation(db: AsyncSession, session_id: uuid.UUID) -> IntakeEvaluation | None:
    """A non-stale ``running`` evaluation of the session (blocks decisions and re-evaluation)."""
    rows = (
        await db.scalars(
            select(IntakeEvaluation).where(
                IntakeEvaluation.session_id == session_id, IntakeEvaluation.status == "running"
            )
        )
    ).all()
    now = _now()
    return next((row for row in rows if not evaluation_is_stale(row, now=now)), None)


async def latest_ready_evaluation(db: AsyncSession, session_id: uuid.UUID) -> IntakeEvaluation | None:
    return await db.scalar(
        select(IntakeEvaluation)
        .where(IntakeEvaluation.session_id == session_id, IntakeEvaluation.status == "ready")
        .order_by(IntakeEvaluation.version.desc())
        .limit(1)
    )


async def intake_for_session(db: AsyncSession, session: InterviewSession) -> IntakeInterview | None:
    return await db.scalar(
        select(IntakeInterview).where(IntakeInterview.interview_id == session.interview_id)
    )


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return _uuid(value) if value else None
    except ValueError:
        return None


# ============================================================================================== result


@dataclass(slots=True)
class EvaluationResult:
    session_id: uuid.UUID
    evaluation_id: uuid.UUID | None = None
    version: int | None = None
    status: str = "ready"
    band: str | None = None
    weighted_total: Decimal | None = None
    deferred: bool = False
    duplicate: bool = False
    fallbacks: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    deferrals: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "evaluation_id": str(self.evaluation_id) if self.evaluation_id else None,
            "version": self.version,
            "status": self.status,
            "band": self.band,
            "weighted_total": format(self.weighted_total, "f") if self.weighted_total is not None else None,
            "deferred": self.deferred,
            "duplicate": self.duplicate,
            "fallbacks": list(self.fallbacks),
            "duration_ms": round(self.duration_ms, 1),
            "deferrals": self.deferrals,
        }


# ============================================================================================== evaluation


async def _load_session(db: AsyncSession, session_id: uuid.UUID) -> InterviewSession:
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise PermanentJobError(f"session {session_id} not found")
    if not is_intake_session(session):
        raise PermanentJobError(f"session {session_id} is not an intake session")
    if session.status not in ENDED_STATUSES:
        raise PermanentJobError(
            f"session {session_id} is {session.status}; it is evaluated once it has ended"
        )
    return session


async def _transcripts_pending(db: AsyncSession, session_id: uuid.UUID) -> list[str]:
    """Activities whose capture exists but whose final transcript has not settled yet (contract 7.7). Activities that
    never captured anything (not reached, skipped) are not waited for."""
    from app.db.models.intake import IntakeActivityResult

    rows = (
        await db.execute(
            select(
                IntakeActivityResult.kind,
                IntakeActivityResult.transcript_status,
                IntakeActivityResult.status,
                IntakeActivityResult.capture,
            ).where(IntakeActivityResult.session_id == session_id)
        )
    ).all()
    pending = []
    for kind, transcript_status, status, capture in rows:
        if transcript_status in SETTLED_TRANSCRIPTS:
            continue
        captured = bool((capture or {}).get("end_reason")) or status in ("completed", "interrupted")
        if captured and transcript_status in ("pending", "pending_worker"):
            pending.append(kind)
    return pending


async def _reserve_evaluation(
    db: AsyncSession,
    *,
    session: InterviewSession,
    intake: IntakeInterview,
    reason: str,
    job_id: str | None,
    requested_by: uuid.UUID | None,
    digest: str,
    kit: IntakeKit,
) -> tuple[IntakeEvaluation, bool]:
    """``(row, duplicate)``: the evaluation row this run writes (reused by retries of the same job)."""
    await db.execute(select(func.pg_advisory_xact_lock(advisory_lock_key("intake.evaluate", session.id))))
    if job_id:
        existing = await db.scalar(
            select(IntakeEvaluation)
            .where(IntakeEvaluation.session_id == session.id, IntakeEvaluation.job_id == job_id)
            .order_by(IntakeEvaluation.version.desc())
            .limit(1)
        )
        if existing is not None:
            if existing.status == "ready":
                return existing, True
            existing.status = "running"
            existing.error = None
            existing.started_at = existing.started_at or _now()
            existing.input_digest = digest
            await db.flush()
            return existing, False
    if reason == "session_completed":
        duplicate = await db.scalar(
            select(IntakeEvaluation)
            .where(
                IntakeEvaluation.session_id == session.id,
                IntakeEvaluation.status == "ready",
                IntakeEvaluation.input_digest == digest,
            )
            .limit(1)
        )
        if duplicate is not None:
            return duplicate, True
    version = int(
        await db.scalar(
            select(func.coalesce(func.max(IntakeEvaluation.version), 0)).where(
                IntakeEvaluation.session_id == session.id
            )
        )
        or 0
    )
    row = IntakeEvaluation(
        id=uuid.uuid4(),
        organization_id=session.organization_id,
        intake_id=intake.id,
        session_id=session.id,
        version=version + 1,
        status="running",
        reason=reason,
        requested_by_id=requested_by,
        job_id=job_id,
        input_digest=digest,
        kit_version=kit.version,
        scoring_version=SCORING_VERSION,
        started_at=_now(),
    )
    db.add(row)
    await db.flush()
    return row, False


async def _runs(db: AsyncSession, evaluation_id: uuid.UUID) -> dict[str, IntakeEvaluatorRun]:
    rows = (
        await db.scalars(select(IntakeEvaluatorRun).where(IntakeEvaluatorRun.evaluation_id == evaluation_id))
    ).all()
    return {row.evaluator: row for row in rows}


async def _save_run(
    *,
    evaluation: IntakeEvaluation,
    evaluator: str,
    status: str,
    input_digest: str | None,
    output: Mapping[str, Any] | None,
    model_call_id: str | None = None,
    latency_ms: float | None = None,
    error: str | None = None,
    attempts_delta: int = 1,
) -> None:
    """Upsert one evaluator checkpoint in its own transaction (survives a later failure of the job)."""
    async with session_scope() as db:
        row = await db.scalar(
            select(IntakeEvaluatorRun).where(
                IntakeEvaluatorRun.evaluation_id == evaluation.id, IntakeEvaluatorRun.evaluator == evaluator
            )
        )
        if row is None:
            row = IntakeEvaluatorRun(
                id=uuid.uuid4(),
                evaluation_id=evaluation.id,
                organization_id=evaluation.organization_id,
                session_id=evaluation.session_id,
                evaluator=evaluator,
                attempts=0,
            )
            db.add(row)
        row.status = status
        row.attempts = int(row.attempts or 0) + attempts_delta
        row.input_digest = input_digest
        row.output = dict(output) if output is not None else None
        row.model_call_id = _uuid_or_none(model_call_id)
        row.latency_ms = latency_ms
        row.error = error[:2000] if error else None


def _parse_output(evaluator: str, data: Mapping[str, Any] | None) -> BaseModel | None:
    if not data:
        return None
    try:
        return EVALUATOR_SCHEMAS[evaluator].model_validate(data)
    except ValidationError:
        return None


def empty_output(evaluator: str) -> BaseModel:
    """A valid "insufficient" output for an evaluator that has no exchanges at all (no model call is made)."""

    def judgement(key: str) -> dict[str, Any]:
        return CompetencyJudgement(
            competency=key,
            sufficient=False,
            score=None,
            reasoning="No answers were recorded for this part of the interview.",
            evidence=[],
            missing_evidence=["no answers in this part of the interview"],
            strengths=[],
            improvements=[],
        ).model_dump()

    payloads: dict[str, dict[str, Any]] = {
        "communication": {"judgement": judgement("communication"), "review_items": []},
        "resume_evidence": {"claims": [], "review_items": []},
        "technical": {
            "judgement": judgement("technical"),
            "verification": {"answer": None, "quality": "not_asked", "matched_key_points": [], "note": ""},
            "rating_supported": "unknown",
            "review_items": [],
        },
        "relay": {
            "concepts": [],
            "paraphrase": "insufficient",
            "misstatements": [],
            "followups": [],
            "notes": "",
        },
        "structured": {
            "judgement": judgement("structured_answer"),
            "bullets": [],
            "flow": "disjointed",
            "examples_used": False,
            "gap_handling": "not_needed",
            "followup_analysis": "",
        },
        "trainability": {
            "trainability": judgement("trainability"),
            "adaptability": judgement("adaptability"),
            "learning_strategy": "",
            "teach_back": "",
            "ai_ml_openness": "unknown",
            "review_items": [],
        },
        "composure": {"judgement": judgement("composure"), "observations": []},
    }
    return EVALUATOR_SCHEMAS[evaluator].model_validate(payloads[evaluator])


@dataclass(slots=True)
class _EvaluatorState:
    outputs: dict[str, BaseModel | None] = field(default_factory=dict)
    allowed: dict[str, frozenset[str]] = field(default_factory=dict)
    fallbacks: set[str] = field(default_factory=set)
    failed_retryable: set[str] = field(default_factory=set)
    model_call_ids: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


async def _run_evaluator(
    call: EvaluatorCall,
    *,
    evaluation: IntakeEvaluation,
    inputs: ev.EvaluationInputs,
    runs: Mapping[str, IntakeEvaluatorRun],
    backend: EvaluatorBackend,
    kit: IntakeKit,
    state: _EvaluatorState,
    is_last_attempt: bool,
    privacy_country: str | None,
    semaphore: asyncio.Semaphore,
) -> None:
    key = call.key
    state.allowed[key] = call.allowed_answers
    run = runs.get(key)
    if run is not None and run.status in DONE_RUN_STATUSES:
        output = _parse_output(key, run.output) if run.status == "succeeded" else None
        state.outputs[key] = output
        if run.status == "fallback" or output is None:
            state.fallbacks.add(key)
        if run.model_call_id:
            state.model_call_ids.append(str(run.model_call_id))
        return
    if call.empty:
        output = empty_output(key)
        state.outputs[key] = output
        await _save_run(
            evaluation=evaluation,
            evaluator=key,
            status="succeeded",
            input_digest=call.input_digest,
            output=output.model_dump(mode="json"),
            attempts_delta=0,
        )
        log.info("intake.evaluator_no_evidence", evaluator=key, session_id=str(inputs.session_id))
        return
    async with semaphore:
        outcome: EvaluatorOutcome = await run_call(
            call, inputs, backend=backend, kit=kit, country=privacy_country
        )
    if outcome.output is not None:
        state.outputs[key] = outcome.output
        if outcome.model_call_id:
            state.model_call_ids.append(str(outcome.model_call_id))
        await _save_run(
            evaluation=evaluation,
            evaluator=key,
            status="succeeded",
            input_digest=call.input_digest,
            output=outcome.output.model_dump(mode="json"),
            model_call_id=outcome.model_call_id,
            latency_ms=outcome.latency_ms,
        )
        return
    state.errors[key] = outcome.error or "unknown error"
    final = is_last_attempt or not outcome.retryable
    await _save_run(
        evaluation=evaluation,
        evaluator=key,
        status="fallback" if final else "failed",
        input_digest=call.input_digest,
        output=None,
        latency_ms=outcome.latency_ms,
        error=outcome.error,
    )
    state.outputs[key] = None
    if final:
        state.fallbacks.add(key)
        log.warning("intake.evaluator_fallback", evaluator=key, session_id=str(inputs.session_id))
    else:
        state.failed_retryable.add(key)


def _reading_section(
    inputs: ev.EvaluationInputs, kit: IntakeKit
) -> tuple[ReadingMetrics | None, RelayCoverage | None, dict[str, Any]]:
    reading = inputs.activities.get("reading_aloud")
    relay = inputs.activities.get("read_relay")
    metrics_value: ReadingMetrics | None = None
    if reading is not None and reading.material_key and capture_problem(reading) is None:
        metrics_value = reading_metrics(reading, kit.passage(reading.material_key))
    coverage: RelayCoverage | None = None
    if relay is not None and relay.material_key and capture_problem(relay) is None:
        coverage = relay_coverage(kit.passage(relay.material_key), relay.transcript)
    output = {
        "drill_a": metrics_value.model_dump(mode="json") if metrics_value is not None else None,
        "drill_a_transcript_source": reading.transcript_origin if reading and reading.transcript else None,
        "drill_b_coverage": coverage.model_dump(mode="json") if coverage is not None else None,
        "drill_b_transcript_source": relay.transcript_origin if relay and relay.transcript else None,
        "transcript_status": {kind: act.transcript_status for kind, act in inputs.activities.items()},
    }
    return metrics_value, coverage, output


def typed_output[M: BaseModel](outputs: Mapping[str, Any], key: str, schema: type[M]) -> M | None:
    """The evaluator output of ``key`` when it is an instance of ``schema`` (``None`` for fallbacks)."""
    value = outputs.get(key)
    return value if isinstance(value, schema) else None


def _dedupe(items: Sequence[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join(str(item).split())
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
        if len(out) >= limit:
            break
    return out


def build_summary(
    inputs: ev.EvaluationInputs,
    aliases: ev.AliasMap,
    *,
    result: ValidationResult,
    outputs: Mapping[str, BaseModel | None],
    allowed: Mapping[str, frozenset[str]],
    drill_a: DrillScore,
    drill_b: DrillScore,
    metrics_value: ReadingMetrics | None,
    coverage: RelayCoverage | None,
    kit: IntakeKit,
) -> dict[str, Any]:
    """The evaluation ``summary`` (contract 8.6): every alias resolved to ids / ``EvidenceRef``."""
    strengths = [s for key in INTAKE_COMPETENCY_KEYS for s in result.competencies[key].strengths]
    improvements = [s for key in INTAKE_COMPETENCY_KEYS for s in result.competencies[key].improvements]
    reading = inputs.activities.get("reading_aloud")
    relay_activity = inputs.activities.get("read_relay")
    relay = typed_output(outputs, "relay", RelayOutput)
    technical = typed_output(outputs, "technical", TechnicalOutput)
    structured = typed_output(outputs, "structured", StructuredOutput)
    trainability = typed_output(outputs, "trainability", TrainabilityOutput)
    metrics_dump = metrics_value.model_dump(mode="json") if metrics_value is not None else None
    events = metrics_dump.pop("events", []) if metrics_dump is not None else []
    drill_a_summary = {
        "passage": reading.material_key if reading else None,
        "passage_title": kit.passage(reading.material_key).title
        if reading and reading.material_key
        else None,
        "metrics": metrics_dump,
        "events": events,
        "score": drill_a.score,
        "basis": list(drill_a.basis),
        "status": drill_a.status,
        "reason": drill_a.reason,
        "transcript_source": reading.transcript_origin if reading and reading.transcript else None,
    }
    misstatements: list[dict[str, Any]] = []
    followups: list[dict[str, Any]] = []
    if relay is not None:
        refs, _ = validate_cites(
            relay.misstatements, aliases=aliases, allowed=allowed.get("relay", frozenset())
        )
        misstatements = refs
        for item in relay.followups:
            question = aliases.get(item.question)
            answer = aliases.get(item.answer) if item.answer else None
            if question is None or question.kind != "question":
                continue
            followups.append(
                {
                    "question_id": str(question.question_id) if question.question_id else None,
                    "question": question.text,
                    "answer_id": str(answer.answer_id) if answer and answer.answer_id else None,
                    "answer_excerpt": excerpt(answer.text) if answer else None,
                    "note": item.note,
                }
            )
    reveal = relay_activity.reveal if relay_activity else {}
    drill_b_summary = {
        "passage": relay_activity.material_key if relay_activity else None,
        "passage_title": kit.passage(relay_activity.material_key).title
        if relay_activity and relay_activity.material_key
        else None,
        "coverage": drill_b.coverage,
        "cue_coverage": coverage.model_dump(mode="json") if coverage is not None else None,
        "concepts": list(drill_b.concepts),
        "paraphrase": relay.paraphrase if relay is not None else None,
        "misstatements": misstatements,
        "followups": followups,
        "score": drill_b.score,
        "basis": list(drill_b.basis),
        "status": drill_b.status,
        "reason": drill_b.reason,
        "shown_ms": reveal.get("shown_ms") if isinstance(reveal, Mapping) else None,
    }
    verification: dict[str, Any] = {"quality": "not_asked", "matched_key_points": [], "note": None}
    if technical is not None:
        target = aliases.get(technical.verification.answer) if technical.verification.answer else None
        valid = (
            target is not None and target.kind == "answer" and target.alias in allowed.get("technical", ())
        )
        verification = {
            "language": inputs.language,
            "answer_id": str(target.answer_id) if valid and target and target.answer_id else None,
            "evidence": (
                {
                    "source": "answer",
                    "stage": target.stage,
                    "item_key": target.item_key,
                    "question_id": str(target.question_id) if target.question_id else None,
                    "answer_id": str(target.answer_id) if target.answer_id else None,
                    "activity_id": None,
                    "start_ms": target.start_ms,
                    "end_ms": target.end_ms,
                    "quote": excerpt(target.text, 300),
                    "supports": "context",
                }
                if valid and target
                else None
            ),
            "quality": technical.verification.quality,
            "matched_key_points": list(technical.verification.matched_key_points),
            "note": technical.verification.note,
        }
    elif not inputs.item_answered("mandatory.mq3.verification"):
        verification["quality"] = "not_asked"
    composure = outputs.get("composure")
    observations = []
    for item in getattr(composure, "observations", None) or []:
        target = aliases.get(item.answer)
        if target is None or target.kind != "answer" or target.alias not in allowed.get("composure", ()):
            continue
        observations.append(
            {
                "behaviour": item.behaviour,
                "note": item.note,
                "answer_id": str(target.answer_id) if target.answer_id else None,
                "start_ms": target.start_ms,
                "stage": target.stage,
            }
        )
    notes = []
    for key in ("communication", "resume_evidence", "technical", "trainability"):
        for draft in getattr(outputs.get(key), "review_items", None) or []:
            if draft.kind == "evaluator_note" and draft.note.strip():
                notes.append({"evaluator": key, "note": draft.note.strip()})
    return {
        "language": inputs.language,
        "strengths": _dedupe(strengths, 5),
        "improvements": _dedupe(improvements, 5),
        "drill_a": drill_a_summary,
        "drill_b": drill_b_summary,
        "claims": list(result.claims),
        "verification": verification,
        "rating_supported": technical.rating_supported if technical is not None else "unknown",
        "bullets": list(result.bullets),
        "flow": structured.flow if structured is not None else None,
        "examples_used": structured.examples_used if structured is not None else None,
        "gap_handling": structured.gap_handling if structured is not None else None,
        "followup_analysis": structured.followup_analysis if structured is not None else None,
        "learning_strategy": trainability.learning_strategy if trainability is not None else None,
        "teach_back": trainability.teach_back if trainability is not None else None,
        "ai_ml_openness": trainability.ai_ml_openness if trainability is not None else "unknown",
        "composure_observations": observations,
        "evaluator_notes": notes,
    }


def evidence_sufficiency(result: ValidationResult) -> str:
    scored = sum(1 for c in result.competencies.values() if c.status == "scored")
    if scored == len(result.competencies):
        return "sufficient"
    return "insufficient" if scored == 0 else "partial"


INSUFFICIENT_OVERALL_LABEL = "Intake weighted total — not enough evidence"


def intake_generic_score_rows(
    session_id: uuid.UUID,
    evaluation: IntakeEvaluation,
    competencies: Mapping[str, Any],
    created_at: datetime,
) -> list[Score]:
    """Generic ``scores`` rows (contract 8.10) so the sessions list, session detail and analytics show intake
    sessions.

    ``competencies`` maps each intake competency key to an object with ``status``, ``score`` (Decimal 1-5 or
    None), ``evidence`` and ``missing_evidence`` (a validated competency or a stored
    ``IntakeCompetencyScore``); totals come from the evaluation row. Every competency gets a row: an
    unscored one (``insufficient`` / ``not_assessed``) is stored with ``score = NULL`` and
    ``sufficient_evidence = false`` — never a made-up number. The overall row is always written: the
    converted total when the session is banded, else ``score = NULL`` with ``insufficient_evidence``.
    """
    rows: list[Score] = []
    for key in INTAKE_WEIGHTS:
        comp = competencies.get(key)
        status = str(getattr(comp, "status", None) or "insufficient")
        raw = getattr(comp, "score", None)
        scored = status == "scored" and raw is not None
        refs = list(getattr(comp, "evidence", None) or [])
        missing = list(getattr(comp, "missing_evidence", None) or [])
        rows.append(
            Score(
                id=uuid.uuid4(),
                session_id=session_id,
                category=ScoreCategory.COMPETENCY.value,
                key=key,
                label=INTAKE_LABELS[key],
                score=float(Decimal(str(raw)) * 2) if scored else None,
                confidence=1.0 if scored else 0.0,
                weight=float(INTAKE_WEIGHTS[key] * 10),
                evidence_count=len(refs),
                positive_count=sum(1 for r in refs if r.get("supports") == "positive"),
                negative_count=sum(1 for r in refs if r.get("supports") == "negative"),
                sufficient_evidence=scored,
                unanswered_areas=missing[:10],
                supporting_question_ids=sorted({str(r["question_id"]) for r in refs if r.get("question_id")}),
                transcript_refs=[
                    {
                        "answer_id": r.get("answer_id"),
                        "start_ms": r.get("start_ms"),
                        "end_ms": r.get("end_ms"),
                        "quote": r.get("quote"),
                    }
                    for r in refs
                ][:25],
                details={
                    "source": "intake",
                    "evaluation_id": str(evaluation.id),
                    "status": status,
                    "assessed": scored,
                    "intake_score": format(Decimal(str(raw)), "f") if scored else None,
                    "intake_weight": format(INTAKE_WEIGHTS[key], "f"),
                },
                is_final=True,
                created_at=created_at,
            )
        )
    banded = evaluation.weighted_total is not None and evaluation.normalized_100 is not None
    band = evaluation.band or DecisionBand.INSUFFICIENT_EVIDENCE.value
    recommendation = recommendation_for(band) if banded else Recommendation.INSUFFICIENT_EVIDENCE
    scored_count = sum(1 for r in rows if r.score is not None)
    if banded:
        rationale = None
    elif scored_count == 0:
        rationale = (
            "Insufficient evidence: no intake competency could be scored (the interview was too short or the "
            "candidate gave no answers), so no score is given."
        )
    else:
        rationale = (
            f"Insufficient evidence: {scored_count} of {len(rows)} intake competencies could be scored, so the "
            "weighted total is not banded."
        )
    rows.append(
        Score(
            id=uuid.uuid4(),
            session_id=session_id,
            category=ScoreCategory.OVERALL.value,
            key="overall",
            label="Intake weighted total (out of 100, conversion)" if banded else INSUFFICIENT_OVERALL_LABEL,
            score=float(evaluation.normalized_100)
            if banded and evaluation.normalized_100 is not None
            else None,
            confidence=1.0 if banded else 0.0,
            weight=None,
            evidence_count=sum(r.evidence_count for r in rows),
            sufficient_evidence=banded,
            details={
                "source": "intake",
                "evaluation_id": str(evaluation.id),
                "recommendation": recommendation.value if recommendation else None,
                "rationale": rationale,
                "band": band,
                "weighted_total": format(evaluation.weighted_total, "f") if banded else None,
                "partial_total": format(evaluation.partial_total, "f")
                if evaluation.partial_total is not None
                else None,
                "scored_competencies": scored_count,
                "insufficient_reason": None
                if banded
                else ("no_evidence" if scored_count == 0 else "partial"),
            },
            is_final=True,
            created_at=created_at,
        )
    )
    return rows


def _score_rows(
    session_id: uuid.UUID,
    evaluation: IntakeEvaluation,
    result: ValidationResult,
    created_at: datetime,
) -> list[Score]:
    return intake_generic_score_rows(session_id, evaluation, result.competencies, created_at)


async def _eligibility(
    db: AsyncSession, intake: IntakeInterview
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """``(enabled, eligibility record, country)`` — used for the review item severity and the V8 markers only."""
    settings = intake.settings or {}
    policy = settings.get("work_authorization") or {}
    enabled = bool(policy.get("enabled"))
    record = dict(intake.eligibility) if intake.eligibility else None
    country = (record or {}).get("country") or policy.get("country")
    return enabled, record, country


async def evaluate_session(
    session_id: uuid.UUID | str,
    *,
    reason: str = "session_completed",
    job_id: str | None = None,
    requested_by: uuid.UUID | str | None = None,
    deferrals: int = 0,
    is_last_attempt: bool = True,
    backend: EvaluatorBackend | None = None,
    enqueue_followups: bool = True,
    wait_for_transcripts: bool = True,
    kit: IntakeKit | None = None,
) -> EvaluationResult:
    """Run (or resume) the evaluation of one ended intake session. See the module docstring for the steps."""
    started = time.perf_counter()
    sid = _uuid(session_id)
    requester = _uuid_or_none(requested_by)
    kit = kit or load_intake_kit()
    backend = backend or ModelBackend()
    result = EvaluationResult(session_id=sid, deferrals=deferrals)

    async with session_scope() as db:
        session = await _load_session(db, sid)
        pending = await _transcripts_pending(db, sid) if wait_for_transcripts else []
    if reason == "session_completed" and deferrals == 0 and enqueue_followups:
        try:
            await enqueue(
                "open_link.round_finished",
                {"session_id": str(sid)},
                dedupe_key=f"open_link.round_finished:{sid}",
            )
        except Exception as exc:  # noqa: BLE001 - never blocks the evaluation
            log.warning("intake.open_link_enqueue_failed", session_id=str(sid), error=type(exc).__name__)
    if pending and deferrals < MAX_DEFERRALS:
        await enqueue(
            EVALUATE_JOB,
            {
                "session_id": str(sid),
                "reason": reason,
                "requested_by": str(requester) if requester else None,
                "deferrals": deferrals + 1,
            },
            dedupe_key=f"{EVALUATE_JOB}:{sid}:defer:{deferrals + 1}",
            delay_seconds=DEFER_SECONDS,
        )
        log.info("intake.evaluate_deferred", session_id=str(sid), pending=pending, deferrals=deferrals + 1)
        result.deferred, result.status, result.deferrals = True, "deferred", deferrals + 1
        return result
    wait_exhausted = bool(pending)

    async with session_scope() as db:
        session = await _load_session(db, sid)
        intake = await intake_for_session(db, session)
        if intake is None:
            raise PermanentJobError(f"session {sid} has no intake row")
        inputs = await ev.load_evaluation_inputs(db, sid)
        digest = ev.input_digest(inputs)
        enabled, eligibility, country = await _eligibility(db, intake)
        onboarding = dict(intake.onboarding or {})
        evaluation, duplicate = await _reserve_evaluation(
            db,
            session=session,
            intake=intake,
            reason=reason,
            job_id=job_id,
            requested_by=requester,
            digest=digest,
            kit=kit,
        )
        runs = await _runs(db, evaluation.id)
        unreviewed = await count_unreviewed_flags(db, sid)
        db.expunge(evaluation)
    result.evaluation_id, result.version = evaluation.id, evaluation.version
    if duplicate:
        result.duplicate, result.status, result.band = True, evaluation.status, evaluation.band
        result.weighted_total = evaluation.weighted_total
        return result

    try:
        aliases = ev.assign_aliases(inputs)
        # ---- deterministic reading metrics (checkpointed)
        metrics_value, coverage, reading_output = _reading_section(inputs, kit)
        reading_output["transcript_wait_exhausted"] = wait_exhausted
        if wait_exhausted:
            reading_output["transcript_source_note"] = "live_segments"
        if "reading_metrics" not in runs or runs["reading_metrics"].status not in DONE_RUN_STATUSES:
            await _save_run(
                evaluation=evaluation,
                evaluator="reading_metrics",
                status="succeeded",
                input_digest=digest,
                output=reading_output,
                attempts_delta=1,
            )

        # ---- model evaluators
        state = _EvaluatorState()
        semaphore = asyncio.Semaphore(max(1, int(getattr(get_settings(), "intake_eval_concurrency", 2) or 2)))
        calls = {
            key: build_call(key, inputs, aliases, kit=kit, relay_coverage=coverage)
            for key in PARALLEL_EVALUATORS
        }
        await asyncio.gather(
            *(
                _run_evaluator(
                    call,
                    evaluation=evaluation,
                    inputs=inputs,
                    runs=runs,
                    backend=backend,
                    kit=kit,
                    state=state,
                    is_last_attempt=is_last_attempt,
                    privacy_country=country,
                    semaphore=semaphore,
                )
                for call in calls.values()
            )
        )
        verdicts = claim_results(
            typed_output(state.outputs, "resume_evidence", ResumeEvidenceOutput),
            inputs,
            aliases=aliases,
            allowed=state.allowed.get("resume_evidence", frozenset()),
            issues=[],
        )
        technical_call = build_call("technical", inputs, aliases, kit=kit, resume_verdicts=verdicts)
        await _run_evaluator(
            technical_call,
            evaluation=evaluation,
            inputs=inputs,
            runs=runs,
            backend=backend,
            kit=kit,
            state=state,
            is_last_attempt=is_last_attempt,
            privacy_country=country,
            semaphore=semaphore,
        )
        if state.failed_retryable:
            raise RetryEvaluation(
                "evaluators failed (retryable): "
                + ", ".join(f"{k}: {state.errors.get(k, '')[:120]}" for k in sorted(state.failed_retryable))
            )

        # ---- deterministic drills, validator, scores, review items
        drill_a = drill_a_score(inputs.activities.get("reading_aloud"), metrics_value)
        relay_activity = inputs.activities.get("read_relay")
        relay_problem = capture_problem(relay_activity)
        relay_answer = inputs.activity_answer("read_relay")
        relay_output = typed_output(state.outputs, "relay", RelayOutput)
        misstatements = 0
        if relay_output is not None:
            refs, _ = validate_cites(
                relay_output.misstatements, aliases=aliases, allowed=state.allowed.get("relay", frozenset())
            )
            misstatements = len(refs)
        drill_b = relay_score(
            coverage,
            concepts=concept_evidence(
                relay_output, aliases=aliases, relay_alias=relay_answer.alias if relay_answer else None
            ),
            paraphrase=relay_output.paraphrase if relay_output is not None else None,
            misstatements=misstatements,
            captured=relay_problem is None and coverage is not None,
            problem=relay_problem,
        )
        topic = inputs.activities.get("topic_card")
        card = kit.topic_card(topic.material_key) if topic is not None and topic.material_key else None
        validation = validate_evaluation(
            inputs,
            aliases,
            outputs=state.outputs,
            allowed=state.allowed,
            drill_a=drill_a,
            drill_b=drill_b,
            card_bullets=card.bullets if card else (),
            fallbacks=state.fallbacks,
        )
        summary = build_summary(
            inputs,
            aliases,
            result=validation,
            outputs=state.outputs,
            allowed=state.allowed,
            drill_a=drill_a,
            drill_b=drill_b,
            metrics_value=metrics_value,
            coverage=coverage,
            kit=kit,
        )
        drafts = {
            key: list(getattr(state.outputs.get(key), "review_items", None) or [])
            for key in ("communication", "resume_evidence", "technical", "trainability")
        }
        items = build_review_items(
            inputs,
            aliases,
            competencies=validation.competencies,
            claims=validation.claims,
            findings=validation.findings,
            drill_a=drill_a,
            drill_b=drill_b,
            verification=summary.get("verification"),
            drafts=drafts,
            allowed=state.allowed,
            eligibility=eligibility,
            eligibility_enabled=enabled,
            onboarding=onboarding,
            integrity_unreviewed=unreviewed,
            kit=kit,
        )
        await _store_evaluation(
            evaluation_id=evaluation.id,
            session_id=sid,
            validation=validation,
            summary=summary,
            items=items,
            model_call_ids=state.model_call_ids,
        )
    except RetryEvaluation:
        raise
    except Exception as exc:
        if is_last_attempt:
            await _mark_failed(evaluation.id, f"{type(exc).__name__}: {exc}")
        raise

    duration = time.perf_counter() - started
    metrics.INTAKE_EVALUATIONS.labels(status="ready").inc()
    metrics.INTAKE_EVALUATION_SECONDS.observe(duration)
    band = validation.summary.band.value
    result.status, result.band = "ready", band
    result.weighted_total = validation.summary.total
    result.fallbacks = sorted(state.fallbacks)
    result.duration_ms = duration * 1000.0
    if enqueue_followups:
        await publish_session_event(
            sid,
            {
                "type": "intake.evaluated",
                "evaluation_id": str(evaluation.id),
                "band": band,
                "status": "ready",
            },
        )
        await enqueue(
            REPORT_JOB,
            {"session_id": str(sid), "evaluation_id": str(evaluation.id), "reason": reason},
            dedupe_key=f"{REPORT_JOB}:{evaluation.id}",
        )
    log.info(
        "intake.evaluated",
        session_id=str(sid),
        evaluation_id=str(evaluation.id),
        version=evaluation.version,
        band=band,
        fallbacks=result.fallbacks,
        duration_ms=round(result.duration_ms, 1),
    )
    return result


async def _store_evaluation(
    *,
    evaluation_id: uuid.UUID,
    session_id: uuid.UUID,
    validation: ValidationResult,
    summary: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    model_call_ids: Sequence[str],
) -> None:
    """One transaction: competency rows, the ready evaluation row, the generic score rows (+ rule V7)."""
    async with session_scope() as db:
        await db.execute(select(func.pg_advisory_xact_lock(advisory_lock_key("intake.evaluate", session_id))))
        evaluation = await db.get(IntakeEvaluation, evaluation_id, with_for_update=True)
        if evaluation is None:
            raise PermanentJobError(f"evaluation {evaluation_id} disappeared")
        await db.execute(
            delete(IntakeCompetencyScore).where(IntakeCompetencyScore.evaluation_id == evaluation_id)
        )
        for key, comp in validation.competencies.items():
            weight = INTAKE_WEIGHTS[key]
            db.add(
                IntakeCompetencyScore(
                    id=uuid.uuid4(),
                    evaluation_id=evaluation_id,
                    organization_id=evaluation.organization_id,
                    session_id=session_id,
                    competency_key=key,
                    status=comp.status,
                    score=comp.score,
                    weight=weight,
                    weighted=(comp.score * weight) if comp.score is not None else None,
                    evaluator=comp.evaluator[:32],
                    reasoning=comp.reasoning,
                    evidence=list(comp.evidence),
                    missing_evidence=list(comp.missing_evidence),
                    strengths=list(comp.strengths),
                    improvements=list(comp.improvements),
                    sub_scores=dict(comp.sub_scores),
                )
            )
        totals = validation.summary
        evaluation.status = "ready"
        evaluation.weighted_total = totals.total
        evaluation.partial_total = totals.partial_total
        evaluation.normalized_100 = totals.normalized_100
        evaluation.band = totals.band.value
        evaluation.evidence_sufficiency = evidence_sufficiency(validation)
        evaluation.summary = dict(summary)
        evaluation.validator = {"issues": [i.as_dict() for i in validation.issues]}
        evaluation.review_items = [dict(i) for i in items]
        evaluation.model_call_ids = list(dict.fromkeys(model_call_ids))
        evaluation.error = None
        evaluation.finished_at = _now()
        await db.flush()
        stored = (
            await db.execute(
                select(IntakeCompetencyScore.competency_key, IntakeCompetencyScore.score).where(
                    IntakeCompetencyScore.evaluation_id == evaluation_id
                )
            )
        ).all()
        check_arithmetic(
            [{"competency_key": k, "score": s} for k, s in stored],
            total=evaluation.weighted_total,
            partial_total=evaluation.partial_total,
            band=evaluation.band,
        )
        newer = await db.scalar(
            select(func.count())
            .select_from(IntakeEvaluation)
            .where(
                IntakeEvaluation.session_id == session_id,
                IntakeEvaluation.status == "ready",
                IntakeEvaluation.version > evaluation.version,
            )
        )
        if not newer:  # a newer ready evaluation (finished first) keeps its generic score set
            await db.execute(delete(Score).where(Score.session_id == session_id))
            db.add_all(_score_rows(session_id, evaluation, validation, _now()))


async def _mark_failed(evaluation_id: uuid.UUID, error: str) -> None:
    async with session_scope() as db:
        await db.execute(
            update(IntakeEvaluation)
            .where(IntakeEvaluation.id == evaluation_id, IntakeEvaluation.status != "ready")
            .values(status="failed", error=error[:2000], finished_at=_now())
        )
    metrics.INTAKE_EVALUATIONS.labels(status="failed").inc()


# ============================================================================================== report


@dataclass(slots=True)
class ReportResult:
    session_id: uuid.UUID
    evaluation_id: uuid.UUID
    report_id: uuid.UUID | None = None
    version: int | None = None
    artifact_id: uuid.UUID | None = None
    training_plan_source: str | None = None
    duration_ms: float = 0.0
    completed: bool = False
    superseded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "evaluation_id": str(self.evaluation_id),
            "report_id": str(self.report_id) if self.report_id else None,
            "version": self.version,
            "artifact_id": str(self.artifact_id) if self.artifact_id else None,
            "training_plan_source": self.training_plan_source,
            "duration_ms": round(self.duration_ms, 1),
            "session_completed": self.completed,
            "superseded": self.superseded,
        }


def decision_out(decision: IntakeDecision, reviewer: Any | None) -> dict[str, Any]:
    """``IntakeDecisionOut`` (contract 15.13) as a JSON-ready dict."""
    from app.services.intake.report import OUTCOME_LABELS

    return {
        "id": str(decision.id),
        "outcome": decision.outcome,
        "outcome_label": OUTCOME_LABELS.get(decision.outcome, decision.outcome),
        "rationale": decision.rationale,
        "reviewer": {
            "id": str(decision.reviewer_id),
            "full_name": getattr(reviewer, "full_name", None) or "Reviewer",
            "email": getattr(reviewer, "email", None),
        },
        "ai_band_at_decision": decision.ai_band_at_decision,
        "ai_total_at_decision": format(decision.ai_total_at_decision, "f")
        if decision.ai_total_at_decision is not None
        else None,
        "overrides_ai": bool(decision.overrides_ai),
        "review_items_resolution": list(decision.review_items_resolution or []),
        "competency_notes": dict(decision.competency_notes or {}),
        "requested_evidence": list(decision.requested_evidence or []),
        "review_decision_id": str(decision.review_decision_id),
        "evaluation_id": str(decision.evaluation_id) if decision.evaluation_id else None,
        "created_at": decision.created_at.isoformat() if decision.created_at else None,
    }


async def decisions_out(db: AsyncSession, intake_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.db.models import User

    rows = (
        await db.execute(
            select(IntakeDecision, User)
            .join(User, User.id == IntakeDecision.reviewer_id, isouter=True)
            .where(IntakeDecision.intake_id == intake_id)
            .order_by(IntakeDecision.created_at.desc(), IntakeDecision.id.desc())
        )
    ).all()
    return [decision_out(decision, user) for decision, user in rows]


async def integrity_observations(db: AsyncSession, session_id: uuid.UUID) -> tuple[list[dict[str, Any]], int]:
    """Objective integrity counts per signal type (observations, never determinations)."""
    rows = (
        await db.execute(
            select(
                IntegrityEvent.type,
                func.coalesce(func.sum(IntegrityEvent.count), 0),
                func.min(IntegrityEvent.occurred_at),
                func.count().filter(IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value),
                func.count(),
            )
            .where(IntegrityEvent.session_id == session_id)
            .group_by(IntegrityEvent.type)
            .order_by(func.min(IntegrityEvent.occurred_at))
        )
    ).all()
    observations = [
        {
            "type": event_type,
            "title": label_for(event_type),
            "count": int(count or 0),
            "first_at": first.isoformat() if first else None,
            "review_status": "unreviewed" if int(unreviewed or 0) else "reviewed",
        }
        for event_type, count, first, unreviewed, _events in rows
    ]
    return observations, await count_unreviewed_flags(db, session_id)


async def _profile_summary(
    db: AsyncSession, session: InterviewSession, intake: IntakeInterview
) -> dict[str, Any]:
    review = intake.resume_review or {}
    document = review.get("document") or {}
    out: dict[str, Any] = {
        "headline": document.get("headline"),
        "summary": None,
        "experience_years": document.get("experience_years"),
    }
    profile = await db.get(CandidateProfile, session.profile_id) if session.profile_id else None
    if profile is None:
        profile = await db.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.candidate_id == session.candidate_id)
            .order_by(CandidateProfile.created_at.desc())
            .limit(1)
        )
    if profile is not None:
        out["summary"] = profile.summary or (profile.profile or {}).get("summary")
        out["headline"] = out["headline"] or (profile.profile or {}).get("headline")
        if out["experience_years"] is None:
            out["experience_years"] = profile.experience_years
    return out


async def load_report_inputs(db: AsyncSession, evaluation: IntakeEvaluation) -> ReportInputs:
    session = await db.get(InterviewSession, evaluation.session_id)
    if session is None:
        raise PermanentJobError("session not found")
    intake = await db.get(IntakeInterview, evaluation.intake_id)
    candidate = await db.get(Candidate, session.candidate_id)
    job = await db.get(Job, session.job_id)
    competency_rows = (
        await db.scalars(
            select(IntakeCompetencyScore).where(IntakeCompetencyScore.evaluation_id == evaluation.id)
        )
    ).all()
    reading_run = await db.scalar(
        select(IntakeEvaluatorRun).where(
            IntakeEvaluatorRun.evaluation_id == evaluation.id,
            IntakeEvaluatorRun.evaluator == "reading_metrics",
        )
    )
    inputs = await ev.load_evaluation_inputs(db, session.id)
    observations, unreviewed = await integrity_observations(db, session.id)
    return ReportInputs(
        session=session,
        intake=intake,
        candidate_name=candidate.full_name if candidate else None,
        job_title=job.title if job else None,
        evaluation=evaluation,
        competency_rows=list(competency_rows),
        inputs=inputs,
        reading_output=(reading_run.output or {}).get("drill_a")
        if reading_run and reading_run.output
        else None,
        profile=await _profile_summary(db, session, intake) if intake is not None else {},
        integrity=observations,
        integrity_unreviewed=unreviewed,
        decisions=await decisions_out(db, intake.id) if intake is not None else [],
        training_plan=evaluation.training_plan,
    )


async def _training_plan(
    evaluation: IntakeEvaluation, *, allow_fallback: bool, use_model: bool, services: Any | None
) -> tuple[dict[str, Any], str | None]:
    """The evaluation's training plan (checkpointed as evaluator run ``training_plan``)."""
    async with session_scope() as db:
        run = await db.scalar(
            select(IntakeEvaluatorRun).where(
                IntakeEvaluatorRun.evaluation_id == evaluation.id,
                IntakeEvaluatorRun.evaluator == "training_plan",
            )
        )
        rows = (
            await db.scalars(
                select(IntakeCompetencyScore).where(IntakeCompetencyScore.evaluation_id == evaluation.id)
            )
        ).all()
        inputs_language = (evaluation.summary or {}).get("language")
    if run is not None and run.status in DONE_RUN_STATUSES and run.output:
        return dict(run.output), str(run.model_call_id) if run.model_call_id else None
    scores = {r.competency_key: (Decimal(str(r.score)) if r.score is not None else None) for r in rows}
    strengths = {r.competency_key: list(r.strengths or []) for r in rows}
    improvements = {r.competency_key: list(r.improvements or []) for r in rows}
    drill_b = ((evaluation.summary or {}).get("drill_b") or {}).get("score")
    outcome = await generate_training_plan(
        scores=scores,
        strengths=strengths,
        improvements=improvements,
        drill_b=drill_b,
        language=inputs_language,
        session_id=str(evaluation.session_id),
        organization_id=str(evaluation.organization_id),
        use_model=use_model,
        services=services,
    )
    if outcome.error and outcome.retryable and not allow_fallback:
        raise RetryEvaluation(f"training plan: {outcome.error}")
    status = "succeeded" if outcome.error is None else "fallback"
    await _save_run(
        evaluation=evaluation,
        evaluator="training_plan",
        status=status,
        input_digest=None,
        output=outcome.plan,
        model_call_id=outcome.model_call_id,
        error=outcome.error,
    )
    async with session_scope() as db:
        await db.execute(
            update(IntakeEvaluation)
            .where(IntakeEvaluation.id == evaluation.id)
            .values(training_plan=outcome.plan)
        )
    return outcome.plan, outcome.model_call_id


async def complete_intake_session(session_id: uuid.UUID) -> bool:
    """``COMPLETING -> COMPLETED`` once the report exists (or the evaluation gave up), in its own transaction."""
    from app.services.interview.sessions import mark_session_completed

    async with session_scope() as db:
        return await mark_session_completed(db, session_id)


def artifact_key(organization_id: uuid.UUID, session_id: uuid.UUID, version: int) -> str:
    return f"orgs/{organization_id}/sessions/{session_id}/reports/recruiter-v{version}.html"


async def _store_artifact(
    db: AsyncSession, *, row: Report, session: InterviewSession, html: str
) -> MediaAsset:
    key = artifact_key(session.organization_id, session.id, row.version)
    kwargs: dict[str, Any] = {
        "organization_id": session.organization_id,
        "kind": MediaKind.REPORT_ARTIFACT,
        "key": key,
        "data": html.encode("utf-8"),
        "content_type": "text/html",
        "session_id": session.id,
        "candidate_id": session.candidate_id,
        "retention_days": await report_retention_days(db, session),
        "details": {
            "report_id": str(row.id),
            "report_type": row.type,
            "version": row.version,
            "kind": "intake",
        },
    }
    try:
        return await store_media(db, **kwargs)
    except Conflict:
        existing = await db.scalar(select(MediaAsset).where(MediaAsset.storage_key == key))
        if existing is not None:
            await delete_media(db, existing, reason="report_regenerated")
        return await store_media(db, **kwargs)


async def _reserve_report(
    db: AsyncSession, session_id: uuid.UUID, *, job_id: str | None, reason: str
) -> Report:
    await db.execute(select(func.pg_advisory_xact_lock(advisory_lock_key("reports", session_id))))
    if job_id:
        existing = await db.scalar(
            select(Report).where(
                Report.session_id == session_id,
                Report.type == ReportType.RECRUITER.value,
                Report.content["job_id"].astext == job_id,
                Report.status != ReportStatus.READY.value,
            )
        )
        if existing is not None:
            existing.status = ReportStatus.GENERATING.value
            existing.error = None
            await db.flush()
            return existing
    version = int(
        await db.scalar(
            select(func.coalesce(func.max(Report.version), 0)).where(
                Report.session_id == session_id, Report.type == ReportType.RECRUITER.value
            )
        )
        or 0
    )
    row = Report(
        id=uuid.uuid4(),
        session_id=session_id,
        type=ReportType.RECRUITER.value,
        status=ReportStatus.GENERATING.value,
        version=version + 1,
        content={"kind": "intake", "job_id": job_id, "reason": reason}
        if job_id
        else {"kind": "intake", "reason": reason},
    )
    db.add(row)
    await db.flush()
    return row


async def generate_intake_report(
    session_id: uuid.UUID | str,
    evaluation_id: uuid.UUID | str | None = None,
    *,
    reason: str = "session_completed",
    job_id: str | None = None,
    allow_fallback: bool = True,
    use_model: bool = True,
    services: Any | None = None,
    complete_session: bool = True,
) -> ReportResult:
    """Training plan + ``reports`` row + HTML artifact for one ready evaluation (the newest when not given)."""
    started = time.perf_counter()
    sid = _uuid(session_id)
    async with session_scope() as db:
        session = await db.get(InterviewSession, sid)
        if session is None or not is_intake_session(session):
            raise PermanentJobError(f"session {sid} is not an intake session")
        if evaluation_id is not None:
            evaluation = await db.get(IntakeEvaluation, _uuid(evaluation_id))
        else:
            evaluation = await latest_ready_evaluation(db, sid)
        if evaluation is None or evaluation.session_id != sid:
            raise PermanentJobError(f"no evaluation to report for session {sid}")
        if evaluation.status != "ready":
            raise PermanentJobError(f"evaluation {evaluation.id} is {evaluation.status}")
        newest = await latest_ready_evaluation(db, sid)
        if evaluation_id is not None and newest is not None and newest.version > evaluation.version:
            # Superseded: the newer evaluation's own report job renders the current report.
            log.info("intake.report_superseded", session_id=str(sid), evaluation_id=str(evaluation.id))
            return ReportResult(session_id=sid, evaluation_id=evaluation.id, superseded=True)
        db.expunge(evaluation)
    plan, training_call_id = await _training_plan(
        evaluation, allow_fallback=allow_fallback, use_model=use_model, services=services
    )
    async with session_scope() as db:
        reserved = await _reserve_report(db, sid, job_id=job_id, reason=reason)
        report_id, version = reserved.id, reserved.version
    try:
        async with session_scope() as db:
            session = await db.get(InterviewSession, sid)
            evaluation = await db.get(IntakeEvaluation, evaluation.id)
            assert session is not None and evaluation is not None
            data = await load_report_inputs(db, evaluation)
            now = _now()
            data.generated_at = now
            data.training_plan = plan
            content = assemble_intake_report(data)
            content["version"] = version
            content["generation"] = {"job_id": job_id, "reason": reason}
            row = await db.get(Report, report_id, with_for_update=True)
            if row is None:
                raise PermanentJobError(f"reserved report {report_id} disappeared")
            html = render_intake_html(content, report_meta(report_id, version, now))
            asset = await _store_artifact(db, row=row, session=session, html=html)
            recommendation = recommendation_for(evaluation.band)
            scored_weight = sum(
                (
                    INTAKE_WEIGHTS[c.competency_key]
                    for c in data.competency_rows
                    if c.status == "scored" and c.competency_key in INTAKE_WEIGHTS
                ),
                Decimal(0),
            )
            row.content = content
            row.status = ReportStatus.READY.value
            row.recommendation = recommendation.value if recommendation else None
            row.overall_score = (
                float(evaluation.normalized_100) if evaluation.normalized_100 is not None else None
            )
            row.confidence = float(scored_weight)
            row.artifact_storage_key = asset.storage_key
            row.model_call_ids = list(
                dict.fromkeys(
                    [*(evaluation.model_call_ids or []), *([training_call_id] if training_call_id else [])]
                )
            )
            row.generated_at = now
            row.error = None
            await audit(
                db,
                None,
                "report.generate",
                resource_type="session",
                resource_id=str(sid),
                organization_id=session.organization_id,
                details={
                    "kind": "intake",
                    "version": version,
                    "reason": reason,
                    "evaluation_id": str(evaluation.id),
                    "evaluation_version": evaluation.version,
                    "band": evaluation.band,
                    "training_plan": plan.get("generated_by"),
                },
            )
            artifact_id = asset.id
    except Exception as exc:
        if allow_fallback:
            async with session_scope() as db:
                await db.execute(
                    update(Report)
                    .where(Report.id == report_id, Report.status != ReportStatus.READY.value)
                    .values(status=ReportStatus.FAILED.value, error=f"{type(exc).__name__}: {exc}"[:2000])
                )
        raise
    completed = False
    if complete_session:
        completed = await complete_intake_session(sid)
    await publish_session_event(
        sid,
        {
            "type": "report.ready",
            "session_id": str(sid),
            "versions": {ReportType.RECRUITER.value: version},
            "candidate_released": False,
            "candidate_report_available": False,
            "kind": "intake",
        },
    )
    result = ReportResult(
        session_id=sid,
        evaluation_id=evaluation.id,
        report_id=report_id,
        version=version,
        artifact_id=artifact_id,
        training_plan_source=plan.get("generated_by"),
        duration_ms=(time.perf_counter() - started) * 1000.0,
        completed=completed,
    )
    log.info("intake.report_generated", **result.as_dict())
    return result


__all__ = [
    "BAND_RECOMMENDATION",
    "ENDED_STATUSES",
    "EVALUATE_JOB",
    "MAX_DEFERRALS",
    "REPORT_JOB",
    "EvaluationResult",
    "EvaluationRunning",
    "ReportResult",
    "RetryEvaluation",
    "artifact_key",
    "build_summary",
    "complete_intake_session",
    "decision_out",
    "decisions_out",
    "empty_output",
    "evaluate_session",
    "evaluation_is_stale",
    "generate_intake_report",
    "integrity_observations",
    "intake_for_session",
    "is_intake_round_config",
    "is_intake_session",
    "latest_ready_evaluation",
    "load_report_inputs",
    "recommendation_for",
    "running_evaluation",
]
