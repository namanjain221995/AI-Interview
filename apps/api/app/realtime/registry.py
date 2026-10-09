"""Process-wide registry of live session runtimes.

A runtime is created on the first connection for a session (after the Redis
session lock is acquired) and outlives WebSocket reconnects until the
disconnect grace period ends, the session ends, or the process shuts down.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.queue import publish_session_event
from app.core.redis import get_redis
from app.realtime import deps
from app.realtime.context import SessionContext
from app.realtime.lock import SessionLock
from app.realtime.runtime import SessionRuntime

log = get_logger(__name__)

TAKEOVER_WAIT_SECONDS = 2.5


class SessionActiveElsewhere(Exception):
    def __init__(self, holder: str | None) -> None:
        super().__init__("session is owned by another instance")
        self.holder = holder


class RuntimeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, SessionRuntime] = {}
        self._creating: dict[str, asyncio.Future[SessionRuntime]] = {}

    def get(self, session_id: str | uuid.UUID) -> SessionRuntime | None:
        runtime = self._runtimes.get(str(session_id))
        if runtime is not None and runtime.closed:
            self._runtimes.pop(str(session_id), None)
            return None
        return runtime

    def all(self) -> list[SessionRuntime]:
        return [r for r in self._runtimes.values() if not r.closed]

    def __len__(self) -> int:
        return len(self.all())

    async def get_or_create(
        self, ctx: SessionContext, *, settings: Settings | None = None, redis: Any | None = None
    ) -> SessionRuntime:
        existing = self.get(ctx.session_id)
        if existing is not None:
            return existing
        pending = self._creating.get(ctx.session_id)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[SessionRuntime] = asyncio.get_running_loop().create_future()
        self._creating[ctx.session_id] = future
        try:
            runtime = await self._create(ctx, settings or get_settings(), redis)
            future.set_result(runtime)
            return runtime
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark retrieved
            raise
        finally:
            self._creating.pop(ctx.session_id, None)

    async def _create(self, ctx: SessionContext, settings: Settings, redis: Any | None) -> SessionRuntime:
        engine = deps.get_turn_engine()  # 503 ServiceUnavailable when missing
        redis_client = redis if redis is not None else _safe_redis()
        lock = SessionLock(
            ctx.session_id,
            redis_client,
            instance_id=settings.instance_id,
            ttl_seconds=settings.session_lock_ttl_seconds,
        )
        if not await lock.acquire() and not await self._request_takeover(ctx.session_id, lock, settings):
            raise SessionActiveElsewhere(lock.holder)
        runtime = SessionRuntime(
            ctx,
            settings=settings,
            engine=engine,
            coding=deps.get_coding_service(),
            integrity=deps.get_integrity_service(),
            lock=lock,
            redis=redis_client,
            on_closed=self._on_closed,
        )
        try:
            await runtime.start()
        except BaseException:
            await lock.release()
            raise
        self._runtimes[ctx.session_id] = runtime
        return runtime

    async def _request_takeover(self, session_id: str, lock: SessionLock, settings: Settings) -> bool:
        """Another instance owns the session (e.g. its runtime is in the
        disconnect grace period after the candidate reconnected through a
        different instance). Ask it on the session bus to hand over; it does so
        only when it has no live connection."""
        if lock.holder and str(lock.holder).startswith(settings.instance_id + ":"):
            return False
        await publish_session_event(
            session_id,
            {"type": "runtime.takeover", "instance_id": settings.instance_id, "session_id": session_id},
        )
        deadline = asyncio.get_running_loop().time() + TAKEOVER_WAIT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.15)
            if await lock.acquire():
                log.info("realtime.lock.handed_over", session_id=session_id, previous=lock.holder)
                return True
        return False

    async def _on_closed(self, runtime: SessionRuntime) -> None:
        if self._runtimes.get(runtime.session_id) is runtime:
            del self._runtimes[runtime.session_id]
        log.info("realtime.runtime.closed", session_id=runtime.session_id, remaining=len(self._runtimes))

    async def shutdown_all(self) -> None:
        runtimes = self.all()
        if not runtimes:
            return
        log.info("realtime.shutdown", runtimes=len(runtimes))
        await asyncio.gather(*(r.shutdown() for r in runtimes), return_exceptions=True)
        self._runtimes.clear()


def _safe_redis() -> Any | None:
    try:
        return get_redis()
    except Exception as exc:  # noqa: BLE001
        log.error("realtime.redis_unavailable", error=type(exc).__name__)
        return None


registry = RuntimeRegistry()


def get_registry() -> RuntimeRegistry:
    return registry
