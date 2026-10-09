"""Integrity jobs.

``integrity.analyze_frame`` (payload ``session_id``, ``media_id``; optional
``kind``): vision observation of one camera/screen frame (fused with the face
detection made when the frame was received) -> sustained-signal rules ->
integrity events. Vision outages raise and the job retries; on the last
attempt camera frames fall back to the face detection alone. Frames that
disappeared (expired transient frame, deleted media) finish as no-ops.

``retention.sweep`` (scheduled hourly by the worker): deletes expired media
files, marks ``media_assets.deleted_at``, purges stale transient frames and
writes a ``system_notes`` summary.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.queue import JobContext, PermanentJobError, job_handler
from app.services.integrity.retention import sweep_expired_media
from app.services.integrity.service import ANALYZE_FRAME_JOB, get_integrity_service


def _require_uuid(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise PermanentJobError(f"payload.{key} must be a UUID") from None


# Measured 2026-10-07: one frame = ~99 output tokens on the 8B VL at ~9.5 tok/s
# (~10.5 s); concurrent requests batch (3 in parallel finished in 10.7 s), so
# the worker runs as many analyses as the vision client allows.
@job_handler(
    ANALYZE_FRAME_JOB,
    concurrency=max(1, get_settings().vision_llm_max_concurrency),
    max_attempts=3,
    timeout_seconds=150,
)
async def analyze_frame(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = _require_uuid(payload, "session_id")
    media_id = _require_uuid(payload, "media_id")
    try:
        # Last attempt: a vision outage no longer fails the frame - camera frames are
        # then analysed with the face detection made when they were received.
        outcomes = await get_integrity_service().analyze_frame(
            session_id, media_id, vision_optional=ctx.is_last_attempt
        )
    except NotFound as exc:
        raise PermanentJobError(str(exc)) from None
    return {
        "media_id": media_id,
        "events": [
            {
                "event_id": o.event_id,
                "type": o.event_type.value,
                "severity": o.severity.value,
                "action": o.action.value,
                "warned": o.candidate_message is not None,
            }
            for o in outcomes
        ],
        "attempt": ctx.attempt,
    }


@job_handler("retention.sweep", concurrency=1, max_attempts=2, timeout_seconds=900)
async def retention_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    summary = await sweep_expired_media()
    return summary.to_dict()
