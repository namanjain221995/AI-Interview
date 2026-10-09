"""Open interview link jobs (OPEN_LINKS.md sections 5.3, 8.4).

``open_link.round_finished`` {session_id}
    Enqueued from ``scoring.finalize`` (seconds after a session closes). Maps
    session -> interview -> registration round and runs ``sync_registration``
    (next round for ``immediate`` links, ``awaiting_review`` otherwise), then
    publishes ``{"type": "link.progress"}`` on the session bus. Not a link
    interview -> no-op. Idempotent.
``open_link.sweep`` (every 300 s)
    Stale syncs, signal-hash retention, purge of old ``blocked`` rows.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.core.queue import (
    JobContext,
    PermanentJobError,
    job_handler,
    publish_session_event,
    register_periodic_job,
)
from app.db.session import session_scope
from app.services.open_links.hooks import on_session_finished
from app.services.open_links.retention import sweep

log = get_logger(__name__)

ROUND_FINISHED_JOB = "open_link.round_finished"
SWEEP_JOB = "open_link.sweep"
SWEEP_INTERVAL_SECONDS = 300

register_periodic_job(SWEEP_JOB, SWEEP_INTERVAL_SECONDS)


@job_handler(ROUND_FINISHED_JOB, concurrency=2, timeout_seconds=60)
async def round_finished(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    try:
        sid = uuid.UUID(str(payload["session_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PermanentJobError("payload needs a valid session_id") from exc
    async with session_scope() as db:
        result = await on_session_finished(db, sid)
    if result is None or result.registration is None:
        return {"session_id": str(sid), "link": False}
    await publish_session_event(sid, {"type": "link.progress"})
    return {
        "session_id": str(sid),
        "link": True,
        "changed": result.changed,
        "status": result.registration.status,
        "unlocked": bool(result.unlocked_interview_id),
    }


@job_handler(SWEEP_JOB, concurrency=1, timeout_seconds=600)
async def open_link_sweep(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    async with session_scope() as db:
        summary = await sweep(db)
    return summary.as_dict()
