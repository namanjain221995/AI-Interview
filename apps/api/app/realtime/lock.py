"""Redis session ownership lock ``session:{id}:lock`` (REALTIME_DESIGN.md section 2).

Value ``{instance_id}:{token}``, ``SET NX PX`` (30 s), refreshed every 10 s
with a compare-and-pexpire script. A lock held by another instance is
respected until it expires (stale locks expire on their own because only a
live owner refreshes them). Takeover is allowed when the holder is this same
instance (a runtime rebuilt in-process) or when the key has no TTL (left by
a buggy writer). Redis being unreachable fails *open* for acquisition (a
single-instance deployment must keep interviewing) but is logged loudly; writers that
must not act without the lock (background sweepers) pass ``fail_open=False``.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.core.metrics import ERRORS

log = get_logger(__name__)

_REFRESH_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_TAKEOVER_LUA = """
local current = redis.call('GET', KEYS[1])
if current == false or current == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
  return 1
end
return 0
"""


def lock_key(session_id: str) -> str:
    return f"session:{session_id}:lock"


class LockLost(Exception):
    pass


class SessionLock:
    def __init__(
        self,
        session_id: str,
        redis: Redis | None,
        *,
        instance_id: str,
        ttl_seconds: int = 30,
        refresh_seconds: float | None = None,
        fail_open: bool = True,
    ) -> None:
        self.session_id = session_id
        self._redis = redis
        self.instance_id = instance_id
        # fail_open=False (worker-side writers such as the session sweeper): a Redis error means
        # "not acquired" - they must never touch a session they could not lock (G1).
        self.fail_open = fail_open
        self.ttl_ms = max(1000, int(ttl_seconds * 1000))
        self.refresh_seconds = refresh_seconds if refresh_seconds is not None else max(1.0, ttl_seconds / 3)
        self.value = f"{instance_id}:{secrets.token_hex(8)}"
        self.held = False
        self._task: asyncio.Task[None] | None = None
        self.holder: str | None = None

    @property
    def key(self) -> str:
        return lock_key(self.session_id)

    async def acquire(self) -> bool:
        if self._redis is None:
            self.held = True
            return True
        try:
            if await self._redis.set(self.key, self.value, nx=True, px=self.ttl_ms):
                self.held = True
                return True
            raw = await self._redis.get(self.key)
            current = raw.decode() if isinstance(raw, bytes) else raw
            self.holder = current
            if current is None:
                ok = await self._redis.set(self.key, self.value, nx=True, px=self.ttl_ms)
                self.held = bool(ok)
                return self.held
            same_instance = str(current).startswith(self.instance_id + ":")
            ttl = await self._redis.pttl(self.key)
            if same_instance or ttl == -1:
                ok = await self._redis.eval(_TAKEOVER_LUA, 1, self.key, current, self.value, self.ttl_ms)  # type: ignore[misc]
                self.held = bool(int(ok))
                if self.held:
                    log.info(
                        "realtime.lock.takeover",
                        session_id=self.session_id,
                        reason="same_instance" if same_instance else "no_ttl",
                    )
                return self.held
            return False
        except (RedisError, OSError) as exc:
            ERRORS.labels(component="realtime_lock").inc()
            log.error(
                "realtime.lock.redis_unavailable",
                session_id=self.session_id,
                error=type(exc).__name__,
                fail_open=self.fail_open,
            )
            self.held = self.fail_open
            return self.fail_open

    async def refresh(self) -> bool:
        if self._redis is None:
            return True
        try:
            ok = await self._redis.eval(_REFRESH_LUA, 1, self.key, self.value, self.ttl_ms)  # type: ignore[misc]
        except (RedisError, OSError) as exc:
            ERRORS.labels(component="realtime_lock").inc()
            log.warning("realtime.lock.refresh_failed", session_id=self.session_id, error=type(exc).__name__)
            return True  # keep going; Redis restarts are handled by re-acquiring below
        if int(ok) == 1:
            return True
        # Key vanished (Redis restart without persistence): re-acquire if free.
        try:
            if await self._redis.set(self.key, self.value, nx=True, px=self.ttl_ms):
                log.info("realtime.lock.reacquired", session_id=self.session_id)
                return True
        except (RedisError, OSError):
            return True
        return False

    def start_refresh(self, on_lost: Callable[[], Awaitable[None]]) -> None:
        async def loop() -> None:
            while self.held:
                await asyncio.sleep(self.refresh_seconds)
                if not await self.refresh():
                    self.held = False
                    log.warning("realtime.lock.lost", session_id=self.session_id)
                    await on_lost()
                    return

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(loop(), name=f"realtime.lock.{self.session_id}")

    async def release(self) -> None:
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if not self.held:
            return
        self.held = False
        if self._redis is None:
            return
        try:
            await self._redis.eval(_RELEASE_LUA, 1, self.key, self.value)  # type: ignore[misc]
        except (RedisError, OSError) as exc:
            log.warning("realtime.lock.release_failed", session_id=self.session_id, error=type(exc).__name__)
