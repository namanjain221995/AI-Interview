"""Interview engine jobs (CONTRACTS.md section 5).

* ``session.prepare`` {session_id} - plan + opening + prepared first question +
  initial bank candidates; status preparing -> ready (idempotent).
* ``answer.evaluate`` {session_id, answer_id} - deep evaluation -> evidence ->
  competency recompute -> snapshots -> ``evaluation.ready`` (idempotent by
  answer id; neutral fallback flagged for human review on the last attempt).
* ``memory.summarize`` {session_id} - rolling conversation summary on the 8B
  model (``ModelTask.MEMORY_SUMMARY``).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import bind_context, get_logger
from app.core.queue import OUTAGE_RETRY_SCHEDULE, JobContext, PermanentJobError, job_handler
from app.services.interview.engine import SessionNotFound, get_engine
from app.services.interview.evaluation import EvaluationService
from app.services.interview.memory import summarize_session_memory

log = get_logger(__name__)


def _session_id(payload: dict[str, Any]) -> str:
    sid = str(payload.get("session_id") or "").strip()
    if not sid:
        raise PermanentJobError("payload.session_id is required")
    return sid


@job_handler("session.prepare", concurrency=4, timeout_seconds=180)
async def prepare_session(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = _session_id(payload)
    bind_context(session_id=session_id, job_id=ctx.job_id)
    try:
        await get_engine().prepare_session(session_id)
    except SessionNotFound as exc:
        raise PermanentJobError(str(exc)) from exc
    return {"session_id": session_id}


# An LLM outage delays evaluations (up to ~34 min) instead of storing fallbacks; the last attempt falls back (S11).
@job_handler(
    "answer.evaluate",
    concurrency=4,
    timeout_seconds=240,
    max_attempts=len(OUTAGE_RETRY_SCHEDULE) + 1,
    retry_schedule=OUTAGE_RETRY_SCHEDULE,
)
async def evaluate_answer(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = _session_id(payload)
    answer_id = str(payload.get("answer_id") or "").strip()
    if not answer_id:
        raise PermanentJobError("payload.answer_id is required")
    bind_context(session_id=session_id, job_id=ctx.job_id)
    return await EvaluationService().evaluate(session_id, answer_id, is_last_attempt=ctx.is_last_attempt)


@job_handler("memory.summarize", concurrency=2, timeout_seconds=180)
async def summarize_memory(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = _session_id(payload)
    bind_context(session_id=session_id, job_id=ctx.job_id)
    engine = get_engine()
    state = await engine.store.load(session_id)
    if state is None:
        raise PermanentJobError(f"session {session_id} not found")
    result = await summarize_session_memory(
        session_id,
        previous_summary=state.memory_summary,
        previous_upto=state.memory_upto,
        organization_id=state.organization_id,
    )
    return {"session_id": session_id, "summarized_upto": (result or {}).get("upto")}
