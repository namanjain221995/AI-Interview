"""Session lifecycle jobs (plan package S1).

``session.lifecycle_sweep`` (every 300 s)
    Ends live/paused sessions idle past SESSION_ABANDON_AFTER_SECONDS as ``disconnect_timeout``
    (partial-coverage scoring and report), batch-limited; SESSION_SWEEP_DRY_RUN only reports.
``session.recovery_sweep`` (every 300 s)
    Sessions COMPLETING past SESSION_COMPLETING_STALE_SECONDS with no report job alive: complete them
    when a report is READY, otherwise enqueue one last ``report.generate`` (fallback narrative).
``session.end`` {session_id, reason, actor}
    Ends one session offline (staff cancel, the candidate's REST end). A session another process is
    driving (lock held) is retried; the periodic sweep is the backstop.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import bind_context, get_logger
from app.core.queue import JobContext, PermanentJobError, job_handler, register_periodic_job
from app.domain.enums import SessionEndReason
from app.services.interview.lifecycle import (
    end_session_offline,
    recover_stuck_completing,
    sweep_abandoned_sessions,
)

log = get_logger(__name__)

SWEEP_JOB = "session.lifecycle_sweep"
END_JOB = "session.end"
RECOVERY_JOB = "session.recovery_sweep"
SWEEP_INTERVAL_SECONDS = 300

register_periodic_job(SWEEP_JOB, SWEEP_INTERVAL_SECONDS)
register_periodic_job(RECOVERY_JOB, SWEEP_INTERVAL_SECONDS)


class SessionBusy(Exception):
    """Another process holds the session lock right now; the job is retried."""

    retryable = True


@job_handler(SWEEP_JOB, concurrency=1, timeout_seconds=900)
async def lifecycle_sweep(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    summary = await sweep_abandoned_sessions()
    return summary.as_dict()


# A runtime that never heard the end request (e.g. created by a reconnect right after it) holds the lock through
# its disconnect grace (ws_disconnect_grace_seconds, 180 s) plus the lock TTL: wait that out (~8 min in all) instead
# of giving up after ~30 s and leaving the end to the abandonment sweep (S6 review).
END_RETRY_SCHEDULE: tuple[float, ...] = (10.0, 30.0, 60.0, 120.0, 240.0)


@job_handler(
    END_JOB,
    concurrency=4,
    timeout_seconds=120,
    max_attempts=len(END_RETRY_SCHEDULE) + 1,
    retry_schedule=END_RETRY_SCHEDULE,
)
async def end_session(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise PermanentJobError("payload.session_id is required")
    try:
        reason = SessionEndReason(str(payload.get("reason") or SessionEndReason.DISCONNECT_TIMEOUT.value))
    except ValueError as exc:
        raise PermanentJobError(f"unknown end reason {payload.get('reason')!r}") from exc
    actor = str(payload.get("actor") or "job")[:40]
    bind_context(session_id=session_id, job_id=ctx.job_id)
    outcome = await end_session_offline(session_id, reason, actor=actor)
    if outcome.action == "skipped" and outcome.reason == "locked" and not ctx.is_last_attempt:
        raise SessionBusy(f"session {session_id} is held by {outcome.detail}")
    return outcome.as_dict()


@job_handler(RECOVERY_JOB, concurrency=1, timeout_seconds=600)
async def recovery_sweep(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    summary = await recover_stuck_completing()
    return summary.as_dict()
