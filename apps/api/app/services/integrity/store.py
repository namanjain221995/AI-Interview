"""Redis state of the integrity module.

Keys (all under ``integrity:``)::

    integrity:{session_id}:occ:{name}      ZSET  occurrence timestamps (sliding windows)
    integrity:{session_id}:warned:{type}   STR   candidate-warning cooldown (SET NX EX)
    integrity:{session_id}:vision:{kind}   STR   JSON vision state (sustained-signal rules)
    integrity:{session_id}:reconnects      STR   reconnect counter
    integrity:{session_id}:faces:camera    STR   JSON face tracker (static faces) of the server-side detector
    integrity:{session_id}:facecheck:{ep}  STR   JSON server check of one face-check interval (snapshot result)
    integrity:frame:{media_id}             HASH  transient frame bytes + meta (short TTL)

Window/cooldown/state helpers fail open on Redis errors (the event is still
recorded in Postgres, just without repeat counting); transient frame storage
raises ``ServiceUnavailable`` because the frame cannot be kept otherwise.
"""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import ServiceUnavailable
from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.core.redis import get_redis, get_redis_bytes

log = get_logger(__name__)

PREFIX = "integrity"
FRAME_PREFIX = f"{PREFIX}:frame:"
OCCURRENCE_RETENTION_S = 3600.0
STATE_TTL_S = 12 * 3600
FACE_CHECK_TTL_S = 3600


def _occ_key(session_id: uuid.UUID | str, name: str) -> str:
    return f"{PREFIX}:{session_id}:occ:{name}"


class IntegrityStore:
    """Thin async wrapper; clients are resolved per call so a test that resets
    the process-wide Redis clients never leaves this object with a closed one."""

    def __init__(self, redis: Redis | None = None, redis_bytes: Redis | None = None) -> None:
        self._redis = redis
        self._redis_bytes = redis_bytes

    @property
    def redis(self) -> Redis:
        return self._redis or get_redis()

    @property
    def redis_bytes(self) -> Redis:
        return self._redis_bytes or get_redis_bytes()

    @staticmethod
    def _failed(op: str, exc: BaseException) -> None:
        ERRORS.labels(component="integrity").inc()
        log.warning("integrity.redis_failed", op=op, error=type(exc).__name__)

    # ------------------------------------------------------------- windows
    async def record_occurrence(
        self, session_id: uuid.UUID | str, name: str, t: float, window_s: float
    ) -> int:
        """Add an occurrence at ``t`` and return how many fall in ``[t - window_s, t]``."""
        key = _occ_key(session_id, name)
        keep = max(window_s, OCCURRENCE_RETENTION_S)
        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.zadd(key, {f"{t:.3f}:{secrets.token_hex(3)}": t})
            pipe.zremrangebyscore(key, "-inf", f"({t - keep}")
            pipe.zcount(key, t - window_s, t + 0.001)
            pipe.expire(key, int(keep * 2))
            result = await pipe.execute()
            return max(1, int(result[2]))
        except (RedisError, OSError) as exc:
            self._failed("record_occurrence", exc)
            return 1

    async def count_recent(self, session_id: uuid.UUID | str, name: str, t: float, window_s: float) -> int:
        try:
            return int(await self.redis.zcount(_occ_key(session_id, name), t - window_s, t + 0.001))
        except (RedisError, OSError) as exc:
            self._failed("count_recent", exc)
            return 0

    async def claim_warning(self, session_id: uuid.UUID | str, event_type: str, cooldown_s: float) -> bool:
        """True when no reminder for this type was shown within the cooldown."""
        try:
            return bool(
                await self.redis.set(
                    f"{PREFIX}:{session_id}:warned:{event_type}", "1", nx=True, ex=max(1, int(cooldown_s))
                )
            )
        except (RedisError, OSError) as exc:
            self._failed("claim_warning", exc)
            return True

    async def incr_reconnects(self, session_id: uuid.UUID | str) -> int:
        key = f"{PREFIX}:{session_id}:reconnects"
        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, STATE_TTL_S)
            result = await pipe.execute()
            return int(result[0])
        except (RedisError, OSError) as exc:
            self._failed("incr_reconnects", exc)
            return 0

    # --------------------------------------------------------- vision state
    async def get_vision_state(self, session_id: uuid.UUID | str, kind: str) -> dict[str, Any] | None:
        try:
            raw = await self.redis.get(f"{PREFIX}:{session_id}:vision:{kind}")
        except (RedisError, OSError) as exc:
            self._failed("get_vision_state", exc)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def set_vision_state(self, session_id: uuid.UUID | str, kind: str, state: dict[str, Any]) -> None:
        try:
            await self.redis.set(
                f"{PREFIX}:{session_id}:vision:{kind}",
                json.dumps(state, separators=(",", ":")),
                ex=STATE_TTL_S,
            )
        except (RedisError, OSError) as exc:
            self._failed("set_vision_state", exc)

    async def _get_json(self, key: str, op: str) -> dict[str, Any] | None:
        try:
            raw = await self.redis.get(key)
        except (RedisError, OSError) as exc:
            self._failed(op, exc)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def _set_json(self, key: str, value: dict[str, Any], ttl_s: int, op: str) -> None:
        try:
            await self.redis.set(key, json.dumps(value, separators=(",", ":")), ex=ttl_s)
        except (RedisError, OSError) as exc:
            self._failed(op, exc)

    # ------------------------------------------------------ face check
    async def get_face_tracks(self, session_id: uuid.UUID | str) -> dict[str, Any] | None:
        return await self._get_json(f"{PREFIX}:{session_id}:faces:camera", "get_face_tracks")

    async def set_face_tracks(self, session_id: uuid.UUID | str, state: dict[str, Any]) -> None:
        await self._set_json(f"{PREFIX}:{session_id}:faces:camera", state, STATE_TTL_S, "set_face_tracks")

    async def get_face_check(self, session_id: uuid.UUID | str, episode: str) -> dict[str, Any] | None:
        return await self._get_json(f"{PREFIX}:{session_id}:facecheck:{episode}", "get_face_check")

    async def put_face_check(self, session_id: uuid.UUID | str, episode: str, result: dict[str, Any]) -> None:
        await self._set_json(
            f"{PREFIX}:{session_id}:facecheck:{episode}", result, FACE_CHECK_TTL_S, "put_face_check"
        )

    # ------------------------------------------------------ transient frames
    async def put_frame(
        self, media_id: uuid.UUID | str, data: bytes, meta: dict[str, Any], ttl_s: int
    ) -> None:
        key = f"{FRAME_PREFIX}{media_id}"
        try:
            pipe = self.redis_bytes.pipeline(transaction=True)
            pipe.hset(
                key, mapping={"data": data, "meta": json.dumps(meta, separators=(",", ":")).encode("utf-8")}
            )
            pipe.expire(key, ttl_s)
            await pipe.execute()
        except (RedisError, OSError) as exc:
            self._failed("put_frame", exc)
            raise ServiceUnavailable("Frame analysis is temporarily unavailable.", retry_after=5) from exc

    async def get_frame(self, media_id: uuid.UUID | str) -> tuple[bytes, dict[str, Any]] | None:
        try:
            values = await self.redis_bytes.hmget(f"{FRAME_PREFIX}{media_id}", ["data", "meta"])
        except (RedisError, OSError) as exc:
            self._failed("get_frame", exc)
            raise
        data, meta = values[0], values[1]
        if not data or not meta:
            return None
        try:
            parsed = json.loads(meta)
        except ValueError:
            return None
        raw = data.encode("latin-1") if isinstance(data, str) else bytes(data)
        return raw, parsed if isinstance(parsed, dict) else {}

    async def delete_frame(self, media_id: uuid.UUID | str) -> None:
        try:
            await self.redis_bytes.delete(f"{FRAME_PREFIX}{media_id}")
        except (RedisError, OSError) as exc:
            self._failed("delete_frame", exc)

    async def purge_stale_frames(self, *, max_ttl_s: int) -> int:
        """Delete transient frames without an expiry (or with a TTL longer than
        allowed) - a safety net; normal frames expire on their own."""
        purged = 0
        try:
            async for key in self.redis_bytes.scan_iter(match=f"{FRAME_PREFIX}*", count=500):
                ttl = await self.redis_bytes.ttl(key)
                if ttl == -1 or ttl > max_ttl_s:
                    purged += int(await self.redis_bytes.delete(key))
        except (RedisError, OSError) as exc:
            self._failed("purge_stale_frames", exc)
        return purged
