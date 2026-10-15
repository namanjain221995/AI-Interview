"""Tier 1: camera-frame analysis waits (bounded) while an interviewer reply is starting."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.integrity import reply_window

pytestmark = pytest.mark.integration


async def test_analysis_starts_at_once_without_a_reply_in_progress(redis) -> None:
    waited = await reply_window.wait_for_reply_start(redis, str(uuid.uuid4()), max_wait_s=1.0, poll_s=0.05)
    assert waited < 0.05


async def test_analysis_waits_until_the_reply_audio_starts(redis) -> None:
    sid = str(uuid.uuid4())
    await reply_window.hold(redis, sid)

    async def reply_starts() -> None:
        await asyncio.sleep(0.3)
        await reply_window.release(redis, sid)

    task = asyncio.create_task(reply_starts())
    waited = await reply_window.wait_for_reply_start(redis, sid, max_wait_s=3.0, poll_s=0.05)
    await task
    assert 0.25 <= waited < 1.0


async def test_the_wait_is_bounded_and_the_hold_expires(redis) -> None:
    sid = str(uuid.uuid4())
    await reply_window.hold(redis, sid)
    waited = await reply_window.wait_for_reply_start(redis, sid, max_wait_s=0.4, poll_s=0.05)
    assert 0.35 <= waited < 0.8
    ttl = await redis.pttl(reply_window.hold_key(sid))
    assert 0 < ttl <= reply_window.HOLD_TTL_MS  # never left behind forever
