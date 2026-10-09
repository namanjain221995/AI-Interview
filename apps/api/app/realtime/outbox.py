"""Sequenced server messages + replay on resume (CONTRACTS.md section 6).

Every server JSON message gets a monotonically increasing ``seq``. Replayable
messages are kept in memory (for reconnects to the same runtime) and appended
to the Redis list ``session:{id}:outbox`` (trimmed to
``SESSION_RESUME_BUFFER_MESSAGES``, TTL 6 h) with the counter in
``session:{id}:seq``, so a runtime rebuilt on another instance (or after a
restart) continues the sequence and can replay what the client missed.

Redis writes are batched by one writer task per outbox (ordered, never on
the send path); a Redis outage only degrades replay to the in-memory buffer.
Audio announcements (``tts.chunk``) and heartbeat acks are sequenced but not
replayed: audio is never replayed, interrupted utterances are re-spoken.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.core.metrics import ERRORS

log = get_logger(__name__)

NON_REPLAYABLE_TYPES: frozenset[str] = frozenset({"tts.chunk", "heartbeat.ack"})
OUTBOX_TTL_SECONDS = 6 * 3600


def outbox_key(session_id: str) -> str:
    return f"session:{session_id}:outbox"


def seq_key(session_id: str) -> str:
    return f"session:{session_id}:seq"


def _seq_of(raw: str) -> int:
    try:
        value = json.loads(raw).get("seq", 0)
        return int(value)
    except (ValueError, TypeError, AttributeError):
        return 0


class Outbox:
    def __init__(
        self,
        session_id: str,
        redis: Redis | None,
        *,
        buffer_size: int = 300,
        ttl_seconds: int = OUTBOX_TTL_SECONDS,
    ) -> None:
        self.session_id = session_id
        self._redis = redis
        self.buffer_size = max(10, buffer_size)
        self.ttl_seconds = ttl_seconds
        self.seq = 0
        self._memory: deque[tuple[int, str]] = deque(maxlen=self.buffer_size)
        self._pending: list[tuple[int, str]] = []
        self._wakeup = asyncio.Event()
        self._writer: asyncio.Task[None] | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._closed = False
        self.redis_errors = 0

    # ------------------------------------------------------------ lifecycle
    async def init(self) -> int:
        """Continue the sequence persisted in Redis (fresh sessions start at 0)."""
        if self._redis is not None:
            try:
                raw_seq, last = await asyncio.gather(
                    self._redis.get(seq_key(self.session_id)),
                    self._redis.lindex(outbox_key(self.session_id), -1),
                )
                stored = int(raw_seq) if raw_seq else 0
                if last:
                    stored = max(stored, _seq_of(last.decode() if isinstance(last, bytes) else str(last)))
                self.seq = max(self.seq, stored)
            except (RedisError, OSError, ValueError) as exc:
                self._redis_failed("init", exc)
        self._writer = asyncio.create_task(self._write_loop(), name=f"realtime.outbox.{self.session_id}")
        return self.seq

    async def aclose(self) -> None:
        await self.flush(timeout=2.0)
        self._closed = True
        self._wakeup.set()
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer
            self._writer = None

    # ---------------------------------------------------------------- write
    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def record(self, seq: int, payload: str, *, msg_type: str) -> None:
        if msg_type in NON_REPLAYABLE_TYPES:
            return
        self._memory.append((seq, payload))
        if self._redis is not None and not self._closed:
            self._pending.append((seq, payload))
            self._idle.clear()
            self._wakeup.set()

    async def _write_loop(self) -> None:
        while not self._closed:
            await self._wakeup.wait()
            self._wakeup.clear()
            while self._pending:
                batch, self._pending = self._pending, []
                await self._write(batch)
            self._idle.set()

    async def _write(self, batch: list[tuple[int, str]]) -> None:
        assert self._redis is not None
        key = outbox_key(self.session_id)
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.rpush(key, *[payload for _, payload in batch])
            pipe.ltrim(key, -self.buffer_size, -1)
            pipe.expire(key, self.ttl_seconds)
            pipe.set(seq_key(self.session_id), max(seq for seq, _ in batch), ex=self.ttl_seconds)
            await pipe.execute()
        except (RedisError, OSError) as exc:
            self._redis_failed("write", exc)

    async def flush(self, timeout: float = 2.0) -> bool:  # noqa: ASYNC109
        if self._writer is None or self._writer.done():
            return True
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
            return True
        except TimeoutError:
            return False

    def _redis_failed(self, op: str, exc: BaseException) -> None:
        self.redis_errors += 1
        ERRORS.labels(component="realtime_outbox").inc()
        log.warning(
            "realtime.outbox.redis_failed", op=op, session_id=self.session_id, error=type(exc).__name__
        )

    # --------------------------------------------------------------- replay
    async def replay_after(self, last_seq: int) -> list[str]:
        """Replayable messages with ``seq > last_seq`` in order."""
        if last_seq >= self.seq:
            return []
        if self._memory and self._memory[0][0] <= last_seq + 1:
            return [payload for seq, payload in self._memory if seq > last_seq]
        items: list[tuple[int, str]] = []
        if self._redis is not None:
            await self.flush(timeout=1.0)
            try:
                raw: list[Any] = await self._redis.lrange(outbox_key(self.session_id), 0, -1)
                items = [(s, r) for r in raw if (s := _seq_of(r)) > last_seq]
            except (RedisError, OSError) as exc:
                self._redis_failed("replay", exc)
        known = {s for s, _ in items}
        items.extend((s, p) for s, p in self._memory if s > last_seq and s not in known)
        items.sort(key=lambda item: item[0])
        return [payload for _, payload in items]
