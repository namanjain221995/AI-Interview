"""Resume pipeline jobs.

``resume.process`` {resume_id, job_id?}
    extraction (text / OCR, or the text of an identical earlier upload) ->
    normalisation -> sections -> deterministic quick profile (row with status
    ``quick``, visible at once) -> enqueues ``profile.generate``.
``profile.generate`` {candidate_id, resume_id, job_id?}
    small concurrent model calls (cached per part) + chunks/embeddings and
    semantic requirement evidence beside them -> deterministic merge,
    experience / skills / job match -> versioned ``candidate_profiles`` row.

Both are idempotent and record failures on the resume (candidate-safe
message) when they give up.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.queue import JobContext, PermanentJobError, job_handler
from app.services.resume.pipeline import process_resume, run_with_failure_handling
from app.services.resume.profile import ProfileInputError, generate_profile

# Worker timeouts are above the internal budgets so our own handler records
# the failure (instead of a bare cancellation).
PROCESS_TIMEOUT_SECONDS = 900
PROFILE_TIMEOUT_SECONDS = 900


def _uuid(value: Any, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError(f"invalid {name}: {value!r}") from exc


def _optional_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, "", "None"):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


@job_handler("resume.process", concurrency=3, timeout_seconds=PROCESS_TIMEOUT_SECONDS)
async def resume_process(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    resume_id = _uuid(payload.get("resume_id"), "resume_id")
    job_id = _optional_uuid(payload.get("job_id"))
    return await run_with_failure_handling(
        resume_id,
        lambda: process_resume(resume_id, job_id, attempt=ctx.attempt),
        attempt=ctx.attempt,
        max_attempts=ctx.max_attempts,
        budget_seconds=PROCESS_TIMEOUT_SECONDS - 60,
    )


@job_handler("profile.generate", concurrency=3, timeout_seconds=PROFILE_TIMEOUT_SECONDS)
async def profile_generate(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    resume_id = _uuid(payload.get("resume_id"), "resume_id")
    candidate_id = _uuid(payload.get("candidate_id"), "candidate_id")
    job_id = _optional_uuid(payload.get("job_id"))

    async def work() -> dict[str, Any]:
        try:
            outcome = await generate_profile(
                candidate_id, resume_id, job_id, last_attempt=ctx.is_last_attempt
            )
        except ProfileInputError as exc:
            raise PermanentJobError(str(exc)) from exc
        return outcome.as_dict()

    return await run_with_failure_handling(
        resume_id,
        work,
        attempt=ctx.attempt,
        max_attempts=ctx.max_attempts,
        budget_seconds=PROFILE_TIMEOUT_SECONDS - 60,
    )
