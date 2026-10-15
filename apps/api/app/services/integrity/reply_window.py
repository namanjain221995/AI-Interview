"""Camera-frame analysis yields to the interviewer's reply (Tier 1, owner-approved).

Each camera frame costs ~7-8 s of the vision model on the head node's GPU, which the main LLM shares. While a reply
is starting - the candidate has finished, the first audio of the interviewer's answer is not out yet - a new
frame analysis waits (bounded) and then runs while the interviewer speaks. Frames are still captured and every
frame is still analysed; only the start of its analysis moves by a few seconds.

The runtime sets a short-lived Redis key at the end of a candidate turn and removes it when the first reply audio
is sent; the key expires by itself if a reply never comes. Redis trouble never blocks anything.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

HOLD_TTL_MS = 8000  # a hold never outlives a slow reply
MAX_WAIT_S = 6.0
POLL_S = 0.2


def hold_key(session_id: str) -> str:
    return f"integrity:reply_hold:{session_id}"


async def hold(redis: Any, session_id: str) -> None:
    try:
        await redis.set(hold_key(session_id), "1", px=HOLD_TTL_MS)
    except Exception as exc:  # noqa: BLE001 - telemetry-grade: never disturbs the interview
        log.debug("integrity.reply_hold_failed", session_id=session_id, error=type(exc).__name__)


async def release(redis: Any, session_id: str) -> None:
    try:
        await redis.delete(hold_key(session_id))
    except Exception as exc:  # noqa: BLE001
        log.debug("integrity.reply_release_failed", session_id=session_id, error=type(exc).__name__)


async def wait_for_reply_start(
    redis: Any, session_id: str, *, max_wait_s: float = MAX_WAIT_S, poll_s: float = POLL_S
) -> float:
    """Wait while the session's reply is starting (at most ``max_wait_s``); returns the seconds waited."""
    started = time.monotonic()
    deadline = started + max_wait_s
    while time.monotonic() < deadline:
        try:
            held = await redis.exists(hold_key(session_id))
        except Exception:  # noqa: BLE001 - without Redis, analyse at once
            break
        if not held:
            break
        await asyncio.sleep(poll_s)
    return time.monotonic() - started
