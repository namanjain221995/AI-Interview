"""Scoring and report jobs.

``scoring.finalize`` {session_id}
    Final score recompute (``scores.is_final``, final timeline snapshots).
``report.generate`` {session_id, reason?, requested_by?}
    Candidate + recruiter reports (new version every run), HTML artifacts,
    automatic candidate release in ``immediate`` mode, ``report.ready`` on
    the session bus.

Both wait for the session's in-flight ``answer.evaluate`` /
``coding.evaluate`` jobs: while any is unfinished they re-enqueue
themselves with a short delay (bounded), so the last answers of an
interview are part of the final scores. Both are idempotent.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import update

from app.core.logging import get_logger
from app.core.queue import JobContext, PermanentJobError, enqueue, job_handler, publish_session_event
from app.db.models import InterviewSession, Report
from app.db.session import session_scope
from app.domain.enums import ReportStatus, SessionStatus
from app.services.interview.records import log_event
from app.services.interview.sessions import mark_session_completed
from app.services.reports.service import generate_reports
from app.services.scoring.persistence import pending_evaluation_jobs, recompute_session_scores

log = get_logger(__name__)

# A report waits for the session's evaluations (S11): first after 10 s, then 1.5x longer each time (at most 2 min
# apart), for at most 40 min in total - longer than the evaluation retry schedule, so an LLM outage of up to
# ~30 min still yields a report built on real evaluations.
REPORT_MAX_WAIT_SECONDS = 2400.0
DEFER_FIRST_SECONDS = 10.0
DEFER_GROWTH = 1.5
DEFER_MAX_SECONDS = 120.0
REPORTABLE = frozenset(
    {
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
        SessionStatus.ABORTED.value,
        SessionStatus.FAILED.value,
    }
)


def _session_id(payload: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload["session_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentJobError("payload needs a valid session_id") from exc


def defer_delay(deferrals: int) -> float:
    return min(DEFER_MAX_SECONDS, DEFER_FIRST_SECONDS * DEFER_GROWTH ** max(0, deferrals))


async def _maybe_defer(job_type: str, sid: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Re-enqueue when evaluations of the session are still running (until REPORT_MAX_WAIT_SECONDS)."""
    deferrals = int(payload.get("deferrals", 0) or 0)
    now = time.time()
    try:
        first_deferred = float(payload.get("first_deferred_at") or now)
    except (TypeError, ValueError):
        first_deferred = now
    waited = max(0.0, now - first_deferred)
    async with session_scope() as db:
        session = await db.get(InterviewSession, sid)
        if session is None:
            raise PermanentJobError(f"session {sid} not found")
        status = session.status
        pending = await pending_evaluation_jobs(db, sid)
    if job_type == "report.generate" and status not in REPORTABLE:
        raise PermanentJobError(f"session {sid} is {status}; reports are generated once it has ended")
    if pending and waited < REPORT_MAX_WAIT_SECONDS:
        delay = defer_delay(deferrals)
        next_payload = {**payload, "deferrals": deferrals + 1, "first_deferred_at": first_deferred}
        await enqueue(
            job_type,
            next_payload,
            dedupe_key=f"{job_type}:{sid}:defer:{deferrals + 1}",
            delay_seconds=delay,
        )
        log.info(
            "reports.deferred",
            job_type=job_type,
            session_id=str(sid),
            pending=pending,
            deferrals=deferrals + 1,
            waited_s=round(waited, 1),
            next_in_s=round(delay, 1),
        )
        return {"deferred": True, "pending_evaluations": pending, "deferrals": deferrals + 1}
    if pending:
        log.warning(
            "reports.pending_evaluations_ignored",
            job_type=job_type,
            session_id=str(sid),
            pending=pending,
            waited_s=round(waited, 1),
        )
    return None


async def _intake_guard(
    sid: uuid.UUID, *, job_id: str | None = None, report: bool = False
) -> dict[str, Any] | None:
    """Intake sessions are scored and reported by the intake pipeline (``app.workers.intake``): generic jobs skip
    them; ``report.generate`` hands over to ``intake.report`` for the newest ready evaluation."""
    async with session_scope() as db:
        session = await db.get(InterviewSession, sid)
        if session is None or (session.round_config or {}).get("round_type") != "intake":
            return None
        evaluation_id = None
        if report:
            from app.services.intake.pipeline import latest_ready_evaluation

            evaluation = await latest_ready_evaluation(db, sid)
            evaluation_id = evaluation.id if evaluation is not None else None
    if evaluation_id is not None:
        await enqueue(
            "intake.report",
            {"session_id": str(sid), "evaluation_id": str(evaluation_id), "reason": "regenerate"},
            dedupe_key=f"intake.report:{evaluation_id}:generic:{job_id or 'none'}",
        )
    log.info("reports.intake_skipped", session_id=str(sid), handed_over=evaluation_id is not None)
    return {"skipped": "intake", "intake_report_enqueued": evaluation_id is not None}


@job_handler("scoring.finalize", concurrency=2, timeout_seconds=120)
async def finalize_scores(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    sid = _session_id(payload)
    skipped = await _intake_guard(sid)
    if skipped is not None:
        return skipped
    # Open interview links: unlock the next round / route to review as soon as
    # the session has closed (does not wait for scores or reports).
    try:
        await enqueue(
            "open_link.round_finished",
            {"session_id": str(sid)},
            dedupe_key=f"open_link.round_finished:{sid}",
        )
    except Exception as exc:  # noqa: BLE001 - never fails scoring
        log.warning("reports.open_link_enqueue_failed", session_id=str(sid), error=type(exc).__name__)
    deferred = await _maybe_defer("scoring.finalize", sid, payload)
    if deferred is not None:
        return deferred
    async with session_scope() as db:
        result = await recompute_session_scores(db, sid, final=True, trigger="final")
    summary = {
        "session_id": str(sid),
        "overall_score": result.rubric.overall_score,
        "overall_confidence": result.rubric.overall_confidence,
        "recommendation": result.recommendation.recommendation.value,
        "competencies": len(result.competencies),
    }
    await publish_session_event(sid, {"type": "scores.finalized", **summary})
    return summary


async def on_report_exhausted(ctx: JobContext, exc: BaseException) -> None:
    """The report job will not run again (non-model error on the last attempt, or a worker killed
    through every attempt): never leave the session in COMPLETING, outside the review queue (S2)."""
    try:
        sid = _session_id(ctx.payload)
    except PermanentJobError:
        return
    async with session_scope() as db:
        await db.execute(
            update(Report)
            .where(
                Report.session_id == sid,
                Report.status.in_([ReportStatus.PENDING.value, ReportStatus.GENERATING.value]),
            )
            .values(status=ReportStatus.FAILED.value)
        )
        completed = await mark_session_completed(db, sid)
        await log_event(
            db, sid, "report.failed", {"job_id": ctx.job_id, "error": f"{type(exc).__name__}: {exc}"[:200]}
        )
    log.error(
        "reports.job_exhausted",
        session_id=str(sid),
        job_id=ctx.job_id,
        completed=completed,
        error=type(exc).__name__,
    )
    await publish_session_event(sid, {"type": "report.failed", "session_id": str(sid)})


@job_handler("report.generate", concurrency=2, timeout_seconds=600, on_exhausted=on_report_exhausted)
async def generate_report(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    sid = _session_id(payload)
    skipped = await _intake_guard(sid, job_id=ctx.job_id, report=True)
    if skipped is not None:
        return skipped
    deferred = await _maybe_defer("report.generate", sid, payload)
    if deferred is not None:
        return deferred
    result = await generate_reports(
        sid,
        reason=str(payload.get("reason") or "session_completed")[:64],
        job_id=ctx.job_id,
        requested_by=payload.get("requested_by"),
        # Model outages retry the job; the last attempt falls back to a
        # deterministic narrative so a report is always produced.
        allow_fallback=ctx.is_last_attempt,
    )
    # Reports are done: the session moves from COMPLETING to COMPLETED (and its
    # interview invitation is marked completed by the session service).
    from app.services.interview.sessions import mark_session_completed

    async with session_scope() as db:
        await mark_session_completed(db, sid)
    return result.as_dict()
