"""Conversation memory: a bounded verbatim window plus a rolling summary.

* The prompt carries the last ``memory_window_exchanges`` (8) exchanges
  verbatim (``recent_window``).
* ``state.conversation`` itself is bounded (``conversation_max_entries``).
* When more than ``memory_summarize_after`` (12) exchanges exist and the
  summary lags behind the window, ``memory.summarize`` runs on the 8B model
  (``ModelTask.MEMORY_SUMMARY`` routes to the vision endpoint). The job writes
  ``session:{id}:memory`` and the live engine applies it before its next
  decision (:func:`apply_memory`), the same pattern as evaluation results.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from app.ai.base import AIError, CallContext
from app.ai.registry import get_ai
from app.ai.structured import describe_schema
from app.core.logging import get_logger
from app.core.queue import publish_session_event
from app.db.models import Transcript
from app.db.session import session_scope
from app.domain.enums import ModelTask
from app.schemas.ai import MemorySummary
from app.services.interview.config import EngineConfig, get_engine_config
from app.services.interview.prompts import MEMORY_SUMMARY
from app.services.interview.state import ExchangeTurn, InterviewState

log = get_logger(__name__)


def memory_key(session_id: str) -> str:
    return f"session:{session_id}:memory"


def append_exchange(state: InterviewState, turn: ExchangeTurn, *, max_entries: int | None = None) -> None:
    limit = max_entries or get_engine_config().conversation_max_entries
    state.conversation.append(turn)
    if len(state.conversation) > limit:
        del state.conversation[: len(state.conversation) - limit]


def recent_window(state: InterviewState, *, exchanges: int | None = None) -> list[ExchangeTurn]:
    """The last ``exchanges`` candidate turns and everything said after the
    first of them (interviewer turns included)."""
    n = exchanges or get_engine_config().memory_window_exchanges
    conv = state.conversation
    seen = 0
    start = 0
    for i in range(len(conv) - 1, -1, -1):
        if conv[i].speaker == "candidate":
            seen += 1
            if seen == n:
                start = max(0, i - 1)  # include the interviewer question before it
                break
    return list(conv[start:])


def answered_count(state: InterviewState) -> int:
    return len(state.answers)


def needs_summary(state: InterviewState, config: EngineConfig | None = None) -> bool:
    cfg = config or get_engine_config()
    total = answered_count(state)
    if total <= cfg.memory_summarize_after:
        return False
    return state.memory_upto < total - cfg.memory_window_exchanges


async def apply_memory(state: InterviewState, redis: Redis) -> bool:
    """Apply a newer summary written by the memory.summarize job."""
    try:
        raw = await redis.get(memory_key(state.session_id))
    except (RedisError, OSError) as exc:
        log.warning("engine.memory_read_failed", session_id=state.session_id, error=type(exc).__name__)
        return False
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return False
    upto = int(data.get("upto", 0))
    if upto <= state.memory_upto:
        return False
    state.memory_summary = str(data.get("summary", ""))[:1500]
    state.memory_key_facts = [str(f)[:200] for f in data.get("key_facts", [])][:12]
    state.memory_upto = upto
    return True


def _turns_view(rows: list[Transcript]) -> list[dict[str, Any]]:
    return [
        {
            "speaker": "Candidate" if r.speaker == "candidate" else "Interviewer",
            "text": r.text,
            "candidate": r.speaker == "candidate",
        }
        for r in rows
    ]


async def summarize_session_memory(
    session_id: str,
    *,
    previous_summary: str = "",
    previous_upto: int = 0,
    organization_id: str | None = None,
    redis: Redis | None = None,
    config: EngineConfig | None = None,
) -> dict[str, Any] | None:
    """Summarise everything older than the verbatim window (memory.summarize job)."""
    cfg = config or get_engine_config()
    from app.core.redis import get_redis

    client = redis or get_redis()
    async with session_scope() as db:
        rows = list(
            (
                await db.execute(
                    select(Transcript)
                    .where(Transcript.session_id == uuid.UUID(session_id), Transcript.kind == "final")
                    .order_by(Transcript.seq)
                )
            )
            .scalars()
            .all()
        )
    candidate_positions = [i for i, r in enumerate(rows) if r.speaker == "candidate"]
    total = len(candidate_positions)
    keep = cfg.memory_window_exchanges
    if total <= keep:
        return None
    cutoff_answer = total - keep  # summarise answers [0, cutoff_answer)
    if cutoff_answer <= previous_upto:
        return None
    # Only the part not yet covered by the previous summary is sent.
    start_index = candidate_positions[previous_upto - 1] + 1 if previous_upto > 0 else 0
    end_index = candidate_positions[cutoff_answer] if cutoff_answer < total else len(rows)
    chunk = rows[start_index:end_index]
    if not chunk:
        return None
    prompt = MEMORY_SUMMARY.render(
        previous=previous_summary or None,
        conversation=_turns_view(chunk),
        schema=describe_schema(MemorySummary),
    )
    try:
        result = await get_ai().llm.structured(
            MemorySummary,
            [{"role": "user", "content": prompt}],
            context=CallContext(
                task=ModelTask.MEMORY_SUMMARY,
                session_id=session_id,
                organization_id=organization_id,
                prompt_version=MEMORY_SUMMARY.version,
            ),
            temperature=0.1,
            max_tokens=900,
            timeout=cfg.memory_timeout_s,
            max_attempts=2,
        )
    except AIError as exc:
        log.warning(
            "engine.memory_summary_failed", session_id=session_id, error=f"{type(exc).__name__}: {exc}"[:200]
        )
        raise
    payload = {
        "summary": result.value.summary,
        "key_facts": list(result.value.key_facts),
        "upto": cutoff_answer,
    }
    try:
        await client.set(memory_key(session_id), json.dumps(payload), ex=cfg.state_ttl_active_s)
    except (RedisError, OSError) as exc:
        log.warning("engine.memory_write_failed", session_id=session_id, error=type(exc).__name__)
    await publish_session_event(
        session_id, {"type": "memory.updated", "session_id": session_id, "upto": cutoff_answer}
    )
    log.info("engine.memory_summarized", session_id=session_id, upto=cutoff_answer, chunk_turns=len(chunk))
    return payload


__all__ = [
    "answered_count",
    "append_exchange",
    "apply_memory",
    "memory_key",
    "needs_summary",
    "recent_window",
    "summarize_session_memory",
]
