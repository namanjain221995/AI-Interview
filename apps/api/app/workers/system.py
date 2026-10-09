"""Platform jobs.

``system.ping`` is a no-op used by tests and smoke checks. Optional payload:
``{"echo": any, "sleep_seconds": float <= 30, "fail_until_attempt": int}`` -
the last one raises until that attempt number to exercise retries.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.queue import JobContext, job_handler


class PingFailure(RuntimeError):
    """Deliberate failure requested through ``fail_until_attempt``."""


@job_handler("system.ping", concurrency=4, timeout_seconds=60)
async def ping(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    sleep_seconds = min(max(float(payload.get("sleep_seconds", 0) or 0), 0.0), 30.0)
    if sleep_seconds:
        await asyncio.sleep(sleep_seconds)
    fail_until = int(payload.get("fail_until_attempt", 0) or 0)
    if ctx.attempt < fail_until:
        raise PingFailure(f"requested failure on attempt {ctx.attempt}")
    return {"pong": True, "echo": payload.get("echo"), "attempt": ctx.attempt}
