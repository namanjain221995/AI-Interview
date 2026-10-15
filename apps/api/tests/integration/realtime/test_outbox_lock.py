"""Outbox persistence/replay across runtimes and the Redis session lock."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.realtime.lock import SessionLock, lock_key
from app.realtime.outbox import Outbox, outbox_key, seq_key
from app.schemas.realtime import NoticeMsg, TtsChunkMsg

pytestmark = pytest.mark.integration


def put(outbox: Outbox, message) -> int:
    seq = outbox.next_seq()
    message.seq = seq
    outbox.record(seq, message.model_dump_json(), msg_type=message.type)
    return seq


async def test_outbox_persists_and_a_new_runtime_continues_the_sequence(redis) -> None:
    first = Outbox("sess-1", redis, buffer_size=20)
    await first.init()
    for i in range(30):
        put(first, NoticeMsg(code=f"n{i}", message="m"))
    put(first, TtsChunkMsg(utterance_id="u", chunk_id=1, index=0, text="x", sample_rate=24000, duration_ms=5))
    assert await first.flush()
    assert int(await redis.get(seq_key("sess-1"))) == 30  # last replayable seq persisted
    assert await redis.llen(outbox_key("sess-1")) == 20
    assert 0 < await redis.ttl(outbox_key("sess-1")) <= 6 * 3600
    await first.aclose()

    # A runtime rebuilt elsewhere: replay comes from Redis, the sequence continues.
    second = Outbox("sess-1", redis, buffer_size=20)
    assert await second.init() == 30
    replay = [json.loads(r)["seq"] for r in await second.replay_after(25)]
    assert replay == [26, 27, 28, 29, 30]
    old = [json.loads(r)["seq"] for r in await second.replay_after(0)]
    assert old == list(range(11, 31))  # trimmed to the buffer size
    assert put(second, NoticeMsg(code="after", message="m")) == 31
    await second.aclose()


async def test_outbox_survives_redis_errors() -> None:
    class Broken:
        async def get(self, *a, **k):
            raise OSError("down")

        async def lindex(self, *a, **k):
            raise OSError("down")

        def pipeline(self, **kwargs):
            raise OSError("down")

        async def lrange(self, *a, **k):
            raise OSError("down")

    outbox = Outbox("sess-2", Broken())  # type: ignore[arg-type]
    assert await outbox.init() == 0
    put(outbox, NoticeMsg(code="a", message="m"))
    await asyncio.sleep(0.01)
    assert [json.loads(r)["seq"] for r in await outbox.replay_after(0)] == [1]
    assert outbox.redis_errors >= 1
    await outbox.aclose()


async def test_session_lock_acquire_refresh_release_and_takeover(redis) -> None:
    a = SessionLock("sess-3", redis, instance_id="inst-a", ttl_seconds=30, refresh_seconds=0.05)
    assert await a.acquire()
    assert (await redis.get(lock_key("sess-3"))).startswith("inst-a:")
    b = SessionLock("sess-3", redis, instance_id="inst-b", ttl_seconds=30)
    assert not await b.acquire() and b.holder.startswith("inst-a:")
    # Same instance (runtime rebuilt in-process) may take over.
    a2 = SessionLock("sess-3", redis, instance_id="inst-a", ttl_seconds=30)
    assert await a2.acquire()
    lost: list[bool] = []

    async def on_lost() -> None:
        lost.append(True)

    a.start_refresh(on_lost)
    await asyncio.sleep(0.2)
    assert lost == [True]  # a's token was replaced by a2
    await a2.release()
    assert await redis.get(lock_key("sess-3")) is None
    assert await b.acquire()
    await b.release()


async def test_stale_lock_expires_and_no_ttl_lock_is_taken_over(redis) -> None:
    await redis.set(lock_key("sess-4"), "inst-x:old", px=150)
    lock = SessionLock("sess-4", redis, instance_id="inst-y", ttl_seconds=30)
    assert not await lock.acquire()
    await asyncio.sleep(0.25)
    assert await lock.acquire()
    await lock.release()
    await redis.set(lock_key("sess-5"), "inst-x:forever")  # no TTL: left by a buggy writer
    lock = SessionLock("sess-5", redis, instance_id="inst-y", ttl_seconds=30)
    assert await lock.acquire()
    assert 0 < await redis.pttl(lock_key("sess-5")) <= 30_000
    await lock.release()


async def test_refresh_reacquires_after_redis_flush(redis) -> None:
    lock = SessionLock("sess-6", redis, instance_id="inst-a", ttl_seconds=30)
    assert await lock.acquire()
    await redis.delete(lock_key("sess-6"))
    assert await lock.refresh()
    assert (await redis.get(lock_key("sess-6"))) == lock.value
    await lock.release()


class _BrokenRedis:
    """Every command fails: Redis is unreachable."""

    def __getattr__(self, name: str):
        async def fail(*args, **kwargs):
            from redis.exceptions import RedisError

            raise RedisError("connection refused")

        return fail


async def test_lock_fails_open_for_the_runtime_and_closed_for_background_writers() -> None:
    runtime_lock = SessionLock("sess-g1", _BrokenRedis(), instance_id="api-1")  # type: ignore[arg-type]
    assert await runtime_lock.acquire() is True and runtime_lock.held
    sweeper_lock = SessionLock("sess-g1", _BrokenRedis(), instance_id="worker-1", fail_open=False)  # type: ignore[arg-type]
    assert await sweeper_lock.acquire() is False and not sweeper_lock.held
