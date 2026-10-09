"""Server-Sent Events stream of a session for the staff live monitor.

Forwards every event published on ``session:{id}:bus`` (evaluation.ready,
integrity.event, coding.evaluated, report.ready, ...) and emits a ``state``
digest periodically (current question, recent conversation, competency
estimates, integrity counters). The stream ends with ``end`` once the
session reaches a terminal status, when the client disconnects, or after
``max_seconds``. A database session is opened per digest (request sessions
are closed before streaming bodies run).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.core.queue import subscribe_session_events
from app.db.models import InterviewSession
from app.db.session import session_scope
from app.services.reports.queries import TERMINAL_STATUSES, load_live_state, state_digest

log = get_logger(__name__)

HEARTBEAT_SECONDS = 15.0
DEFAULT_MAX_SECONDS = 4 * 3600.0
CONVERSATION_TURNS = 12


def sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    payload = json.dumps(data, default=str, separators=(",", ":"))
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


async def load_digest(session_id: uuid.UUID) -> dict[str, Any] | None:
    async with session_scope() as db:
        session = await db.get(InterviewSession, session_id)
        if session is None:
            return None
        state = await load_live_state(session)
        digest = state_digest(state, session, conversation_turns=CONVERSATION_TURNS) or {}
        digest.update(
            {
                "session_id": str(session.id),
                "session_status": session.status,
                "end_reason": session.end_reason,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        return digest


async def live_events(
    session_id: uuid.UUID,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    interval: float = 5.0,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> AsyncIterator[str]:
    started = time.monotonic()
    yield "retry: 3000\n\n"
    digest = await load_digest(session_id)
    if digest is None:
        yield sse("end", {"reason": "not_found"})
        return
    yield sse("state", digest)
    if digest["session_status"] in TERMINAL_STATUSES:
        yield sse("end", {"reason": "session_ended", "status": digest["session_status"]})
        return
    counter = 0
    async with subscribe_session_events(session_id) as subscription:
        last_digest = last_beat = time.monotonic()
        while True:
            if await is_disconnected():
                break
            now = time.monotonic()
            if now - started >= max_seconds:
                yield sse("end", {"reason": "max_duration"})
                break
            event = await subscription.get(timeout=min(1.0, interval))
            if event is not None:
                counter += 1
                yield sse(str(event.get("type") or "message"), event, event_id=str(counter))
                last_beat = time.monotonic()
            now = time.monotonic()
            if now - last_digest >= interval:
                digest = await load_digest(session_id)
                last_digest = last_beat = now
                if digest is None:
                    yield sse("end", {"reason": "not_found"})
                    break
                yield sse("state", digest)
                if digest["session_status"] in TERMINAL_STATUSES:
                    yield sse("end", {"reason": "session_ended", "status": digest["session_status"]})
                    break
            elif now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                yield ": keep-alive\n\n"
    log.info("sessions.live_stream_closed", session_id=str(session_id), events=counter)
