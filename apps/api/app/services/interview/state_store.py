"""InterviewState persistence.

* **Redis hot copy** ``session:{id}:state`` (JSON string, every change),
  written with a version-guarded compare-and-set Lua script: the write only
  succeeds when the stored ``version`` equals the version the writer loaded.
* **Postgres snapshot** ``interview_sessions.state`` / ``state_version`` at
  every committed turn (``save(..., durable=True, db=...)`` inside the turn's
  transaction) and debounced every ``postgres_flush_interval_s`` (15 s)
  otherwise. The snapshot update is guarded by ``state_version < new``.
* **Rebuild**: when Redis has no copy (restart, eviction, Redis loss), the
  state is loaded from the Postgres snapshot and its derived competency
  state is recomputed from the evidence records (``rebuild_competencies``),
  so scoring never depends on Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import case, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.db.models import InterviewSession
from app.db.session import session_scope
from app.domain.enums import SessionStatus
from app.services.interview.config import EngineConfig, get_engine_config
from app.services.interview.state import InterviewState

log = get_logger(__name__)

TERMINAL_STATUSES = frozenset({SessionStatus.COMPLETED, SessionStatus.ABORTED, SessionStatus.FAILED})
ENDED_STATUSES = TERMINAL_STATUSES | {SessionStatus.COMPLETING}

# KEYS[1] state key; ARGV: expected version, new json, new version, ttl seconds, force flag
_CAS_LUA = """
local current = redis.call('GET', KEYS[1])
local expected = tonumber(ARGV[1])
local stored = -1
if current then
  local ok, obj = pcall(cjson.decode, current)
  if ok and type(obj) == 'table' and obj['version'] ~= nil then
    stored = tonumber(obj['version'])
  else
    stored = 0
  end
end
if ARGV[5] ~= '1' and current and stored ~= expected then
  return {0, stored}
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[4]))
return {1, tonumber(ARGV[3])}
"""


def state_key(session_id: str) -> str:
    return f"session:{session_id}:state"


class StateConflict(Exception):
    """The stored version differs from the version this writer loaded."""

    def __init__(self, session_id: str, expected: int, stored: int) -> None:
        super().__init__(f"state version conflict for {session_id}: expected {expected}, stored {stored}")
        self.session_id = session_id
        self.expected = expected
        self.stored = stored


RebuildHook = Callable[[AsyncSession, InterviewState], Awaitable[None]]


class StateStore:
    def __init__(
        self,
        *,
        redis: Redis | Callable[[], Redis] | None = None,
        config: EngineConfig | None = None,
        rebuild_hook: RebuildHook | None = None,
    ) -> None:
        self._redis = redis
        self._config = config or get_engine_config()
        self._rebuild_hook = rebuild_hook
        self._last_flush: dict[str, float] = {}
        self._flush_tasks: dict[str, asyncio.Task[None]] = {}
        self._dirty: dict[str, InterviewState] = {}
        self._script: Any = None

    # ----------------------------------------------------------- plumbing
    @property
    def redis(self) -> Redis:
        if self._redis is None:
            from app.core.redis import get_redis

            return get_redis()
        if callable(self._redis) and not isinstance(self._redis, Redis):
            return self._redis()
        return self._redis  # type: ignore[return-value]

    def set_rebuild_hook(self, hook: RebuildHook | None) -> None:
        self._rebuild_hook = hook

    def _ttl(self, state: InterviewState) -> int:
        if state.status in ENDED_STATUSES:
            return self._config.state_ttl_ended_s
        return self._config.state_ttl_active_s

    # --------------------------------------------------------------- read
    async def load_cached(self, session_id: str) -> InterviewState | None:
        try:
            raw = await self.redis.get(state_key(session_id))
        except (RedisError, OSError) as exc:
            ERRORS.labels(component="engine_state").inc()
            log.warning("engine.state_redis_read_failed", session_id=session_id, error=type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            return InterviewState.model_validate_json(raw)
        except ValueError as exc:
            ERRORS.labels(component="engine_state").inc()
            log.error("engine.state_corrupt", session_id=session_id, error=str(exc)[:300])
            return None

    async def load(self, session_id: str, *, rebuild: bool = True) -> InterviewState | None:
        state = await self.load_cached(session_id)
        if state is not None or not rebuild:
            return state
        return await self.rebuild(session_id)

    async def load_from_db(self, db: AsyncSession, session_id: str) -> InterviewState | None:
        row = await db.get(InterviewSession, uuid.UUID(str(session_id)))
        if row is None or not row.state:
            return None
        state = InterviewState.model_validate(row.state)
        # The column is authoritative for the version and lifecycle status.
        state.version = max(state.version, int(row.state_version or 0))
        try:
            state.status = SessionStatus(row.status)
        except ValueError:
            pass
        return state

    async def rebuild(self, session_id: str) -> InterviewState | None:
        """Postgres snapshot + competency recompute from evidence -> Redis."""
        async with session_scope() as db:
            state = await self.load_from_db(db, session_id)
            if state is None:
                return None
            if self._rebuild_hook is not None:
                try:
                    await self._rebuild_hook(db, state)
                except Exception as exc:  # noqa: BLE001 - rebuild must not block the interview
                    ERRORS.labels(component="engine_state").inc()
                    log.error(
                        "engine.state_rebuild_hook_failed",
                        session_id=session_id,
                        error=f"{type(exc).__name__}: {exc}"[:300],
                    )
        await self._write_redis(state, expected=state.version, force=True, bump=False)
        log.info(
            "engine.state_rebuilt", session_id=session_id, version=state.version, status=state.status.value
        )
        return state

    # -------------------------------------------------------------- write
    async def _write_redis(self, state: InterviewState, *, expected: int, force: bool, bump: bool) -> int:
        new_version = expected + 1 if bump else state.version
        state.version = new_version
        payload = state.model_dump_json()
        try:
            result = await self.redis.eval(
                _CAS_LUA,
                1,
                state_key(state.session_id),
                str(expected),
                payload,
                str(new_version),
                str(self._ttl(state)),
                "1" if force else "0",
            )
        except (RedisError, OSError) as exc:
            ERRORS.labels(component="engine_state").inc()
            log.warning(
                "engine.state_redis_write_failed", session_id=state.session_id, error=type(exc).__name__
            )
            return new_version
        ok, stored = int(result[0]), int(result[1])
        if not ok:
            state.version = expected
            raise StateConflict(state.session_id, expected, stored)
        return new_version

    async def save(
        self,
        state: InterviewState,
        *,
        durable: bool = False,
        db: AsyncSession | None = None,
        force: bool = False,
        extra_columns: dict[str, Any] | None = None,
    ) -> InterviewState:
        """Persist ``state`` (version incremented). Raises :class:`StateConflict`
        unless ``force``. ``durable`` writes the Postgres snapshot now (inside
        ``db``'s transaction when given)."""
        expected = state.version
        await self._write_redis(state, expected=expected, force=force, bump=True)
        if durable:
            if db is not None:
                await self.write_snapshot(db, state, extra_columns=extra_columns)
            else:
                async with session_scope() as session:
                    await self.write_snapshot(session, state, extra_columns=extra_columns)
            self._last_flush[state.session_id] = time.monotonic()
            self._dirty.pop(state.session_id, None)
        else:
            self._schedule_flush(state)
        return state

    async def save_with_retry(
        self,
        session_id: str,
        mutate: Callable[[InterviewState], bool | None],
        *,
        attempts: int = 5,
        durable: bool = False,
    ) -> InterviewState | None:
        """Load -> mutate -> CAS save, retrying on conflict (for writers that
        do not hold the engine lock, e.g. background jobs). ``mutate`` returns
        False to skip saving."""
        for _ in range(max(1, attempts)):
            state = await self.load(session_id)
            if state is None:
                return None
            if mutate(state) is False:
                return state
            try:
                return await self.save(state, durable=durable)
            except StateConflict:
                await asyncio.sleep(0.02)
                continue
        log.warning("engine.state_save_retry_exhausted", session_id=session_id)
        return None

    async def write_snapshot(
        self, db: AsyncSession, state: InterviewState, *, extra_columns: dict[str, Any] | None = None
    ) -> bool:
        terminal = [st.value for st in TERMINAL_STATUSES]
        values: dict[str, Any] = {
            "state": state.model_dump(mode="json"),
            "state_version": state.version,
            # A late snapshot (e.g. a delivery ack after closing) must never move a
            # session out of a terminal status set by the report pipeline.
            "status": case(
                (InterviewSession.status.in_(terminal), InterviewSession.status),
                else_=literal(state.status.value),
            ),
            "elapsed_seconds": round(state.elapsed_seconds(), 3),
        }
        if state.plan is not None:
            values["plan"] = state.plan.model_dump(mode="json")
        if state.started_at is not None:
            values["started_at"] = state.started_at
        if extra_columns:
            values.update(extra_columns)
        result = await db.execute(
            update(InterviewSession)
            .where(
                InterviewSession.id == uuid.UUID(state.session_id),
                InterviewSession.state_version < state.version,
            )
            .values(**values)
        )
        written = bool(getattr(result, "rowcount", 0))
        if not written:
            # A newer snapshot already exists; still keep lifecycle columns in sync when explicitly given.
            if extra_columns:
                await db.execute(
                    update(InterviewSession)
                    .where(InterviewSession.id == uuid.UUID(state.session_id))
                    .values(**extra_columns)
                )
            log.debug("engine.snapshot_skipped_older", session_id=state.session_id, version=state.version)
        return written

    # ---------------------------------------------------------- debounce
    def _schedule_flush(self, state: InterviewState) -> None:
        sid = state.session_id
        self._dirty[sid] = state.model_copy(deep=True)
        last = self._last_flush.get(sid, 0.0)
        due_in = max(0.0, self._config.postgres_flush_interval_s - (time.monotonic() - last))
        task = self._flush_tasks.get(sid)
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._flush_tasks[sid] = loop.create_task(self._flush_later(sid, due_in))

    async def _flush_later(self, session_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.flush(session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            ERRORS.labels(component="engine_state").inc()
            log.warning(
                "engine.state_flush_failed", session_id=session_id, error=f"{type(exc).__name__}: {exc}"[:200]
            )

    async def flush(self, session_id: str) -> bool:
        state = self._dirty.pop(session_id, None)
        if state is None:
            return False
        async with session_scope() as db:
            written = await self.write_snapshot(db, state)
        self._last_flush[session_id] = time.monotonic()
        return written

    async def drain(self) -> None:
        """Flush every dirty session now (shutdown, tests)."""
        tasks = [t for t in self._flush_tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._flush_tasks.clear()
        for sid in list(self._dirty):
            with contextlib.suppress(Exception):
                await self.flush(sid)

    async def expire(self, session_id: str, ttl_s: int | None = None) -> None:
        with contextlib.suppress(RedisError, OSError):
            await self.redis.expire(state_key(session_id), ttl_s or self._config.state_ttl_ended_s)

    async def delete_cached(self, session_id: str) -> None:
        with contextlib.suppress(RedisError, OSError):
            await self.redis.delete(state_key(session_id))


async def load_session_row(
    db: AsyncSession, session_id: str, *, for_update: bool = False
) -> InterviewSession | None:
    stmt = select(InterviewSession).where(InterviewSession.id == uuid.UUID(str(session_id)))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


def dumps(data: Any) -> str:
    return json.dumps(data, default=str, separators=(",", ":"))


__all__ = [
    "ENDED_STATUSES",
    "TERMINAL_STATUSES",
    "StateConflict",
    "StateStore",
    "load_session_row",
    "state_key",
]
