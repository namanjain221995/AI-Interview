"""Intake evaluation and report jobs (INTAKE_INTERVIEW.md section 8.1).

``intake.evaluate`` {session_id, reason: session_completed|reevaluate, requested_by?, deferrals?}
    Enqueued by the engine at closing (``intake.evaluate:{sid}:initial``, delay ``INTAKE_EVAL_START_DELAY_SECONDS``)
    and by staff re-evaluation (``intake.evaluate:{sid}:v{n}``). Waits for settled activity transcripts (re-enqueues
    itself, ``…:defer:{k}``), reserves/reuses the evaluation row, runs the checkpointed evaluators, validates,
    scores and stores the evaluation, then enqueues ``intake.report``. Idempotent: retries reuse the evaluation row
    and every finished evaluator; a duplicate delivery with the same inputs is a no-op. A retryable evaluator
    failure retries the job; on the last attempt it falls back (competencies insufficient + a review item). When
    the evaluation fails for good, the session still moves to COMPLETED (reviewers decide without AI fields).

``intake.report`` {session_id, evaluation_id, reason?}
    Training plan (checkpointed), the ``reports`` row (``content.kind = "intake"``; retries reuse the row), the HTML
    artifact, session ``COMPLETING -> COMPLETED`` and ``report.ready``. The last attempt falls back to the
    deterministic training plan, so a report is always produced.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.core.queue import JobContext, PermanentJobError, job_handler
from app.services.intake.pipeline import (
    EVALUATE_JOB,
    REPORT_JOB,
    RetryEvaluation,
    complete_intake_session,
    evaluate_session,
    generate_intake_report,
)

log = get_logger(__name__)

REASONS = frozenset({"session_completed", "reevaluate"})


def _uuid(payload: dict[str, Any], key: str, *, required: bool = True) -> uuid.UUID | None:
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise PermanentJobError(f"payload needs a valid {key}")
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError) as exc:
        raise PermanentJobError(f"payload needs a valid {key}") from exc


async def on_intake_job_exhausted(ctx: JobContext, exc: BaseException) -> None:
    """Reviewers still decide without AI fields: a dead evaluation or report job never leaves the
    session in COMPLETING (S2; covers the worker-killed path the handlers' own except cannot see)."""
    sid = _uuid(ctx.payload, "session_id", required=False)
    if sid is None:
        return
    completed = await complete_intake_session(sid)
    log.error(
        "intake.job_exhausted",
        session_id=str(sid),
        job_type=ctx.job_type,
        job_id=ctx.job_id,
        completed=completed,
    )


@job_handler(EVALUATE_JOB, concurrency=2, timeout_seconds=900, on_exhausted=on_intake_job_exhausted)
async def evaluate(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    sid = _uuid(payload, "session_id")
    assert sid is not None
    reason = str(payload.get("reason") or "session_completed")
    if reason not in REASONS:
        raise PermanentJobError(f"unknown evaluation reason {reason!r}")
    try:
        result = await evaluate_session(
            sid,
            reason=reason,
            job_id=ctx.job_id,
            requested_by=payload.get("requested_by"),
            deferrals=int(payload.get("deferrals", 0) or 0),
            is_last_attempt=ctx.is_last_attempt,
        )
    except RetryEvaluation:
        if ctx.is_last_attempt:  # cannot happen (last attempts fall back), kept as a guard
            await complete_intake_session(sid)
        raise
    except PermanentJobError:
        raise
    except Exception:
        if ctx.is_last_attempt:
            # Reviewers still decide without AI fields: never leave the session in COMPLETING.
            await complete_intake_session(sid)
            log.error("intake.evaluation_failed", session_id=str(sid), job_id=ctx.job_id)
        raise
    return result.as_dict()


@job_handler(REPORT_JOB, concurrency=2, timeout_seconds=600, on_exhausted=on_intake_job_exhausted)
async def report(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    sid = _uuid(payload, "session_id")
    assert sid is not None
    evaluation_id = _uuid(payload, "evaluation_id", required=False)
    result = await generate_intake_report(
        sid,
        evaluation_id,
        reason=str(payload.get("reason") or "session_completed")[:64],
        job_id=ctx.job_id,
        allow_fallback=ctx.is_last_attempt,
    )
    return result.as_dict()
