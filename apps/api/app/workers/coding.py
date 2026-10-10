"""Coding jobs.

``coding.evaluate`` {coding_session_id}: deep evaluation of a finished coding
exercise (:func:`app.services.coding.evaluation.evaluate_coding_session`).
Idempotent: a completed evaluation is skipped unless ``force`` is set; the
session's coding evidence rows are replaced, never duplicated. Transient
model/sandbox outages retry; on the last attempt a deterministic fallback
evaluation (flagged for human review) is stored instead of failing.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.queue import OUTAGE_RETRY_SCHEDULE, JobContext, PermanentJobError, job_handler
from app.services.coding.evaluation import evaluate_coding_session


@job_handler(
    "coding.evaluate",
    concurrency=2,
    max_attempts=len(OUTAGE_RETRY_SCHEDULE) + 1,
    timeout_seconds=420,
    retry_schedule=OUTAGE_RETRY_SCHEDULE,  # S11: an outage delays, the last attempt falls back
)
async def evaluate(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    raw = payload.get("coding_session_id")
    try:
        coding_session_id = uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError(f"invalid coding_session_id: {raw!r}") from exc
    return await evaluate_coding_session(
        coding_session_id,
        force=bool(payload.get("force", False)),
        is_last_attempt=ctx.is_last_attempt,
    )
