"""Recording jobs.

``recording.finalize`` (payload ``session_id``; optional ``part_id``,
``close`` = the browser finished that part, ``force`` = staff asked, retry a
part the sweep gave up on): joins each part's segments and remuxes them into
one seekable file (``app.services.recordings.finalize``).

``recording.sweep`` (every two minutes): queues ``recording.finalize`` for
parts that are over but not processed or not cleaned up yet.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.queue import JobContext, PermanentJobError, job_handler, register_periodic_job
from app.services.recordings.constants import FINALIZE_JOB, SWEEP_INTERVAL_SECONDS, SWEEP_JOB
from app.services.recordings.finalize import finalize_session
from app.services.recordings.sweep import sweep_recordings

register_periodic_job(SWEEP_JOB, SWEEP_INTERVAL_SECONDS)


def _session_id(payload: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload.get("session_id")))
    except (ValueError, TypeError, AttributeError):
        raise PermanentJobError("payload.session_id must be a UUID") from None


@job_handler(FINALIZE_JOB, concurrency=2, max_attempts=2, timeout_seconds=1200)
async def recording_finalize(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = _session_id(payload)
    part = payload.get("part_id")
    results = await finalize_session(
        session_id,
        part_id=str(part)[:80] if part else None,
        requested_close=bool(payload.get("close")),
        force=bool(payload.get("force")),
    )
    return {"session_id": str(session_id), "attempt": ctx.attempt, "parts": [r.to_dict() for r in results]}


@job_handler(SWEEP_JOB, concurrency=1, max_attempts=1, timeout_seconds=300)
async def recording_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    summary = await sweep_recordings()
    return summary.to_dict()
