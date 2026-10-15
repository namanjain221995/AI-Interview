"""Atomic sliding-window rate limiting on Redis.

Each limit is a sorted set ``rl:{scope}:{key}`` holding one member per
accepted request (scored by Redis server time in ms). A Lua script trims the
window, counts, and admits or rejects in one atomic step, so concurrent API
replicas share the same budget. If Redis is unreachable the limiter fails
open (logged and counted) so an outage of the cache never locks users out;
account lockout in the database still protects credentials.

Usage::

    @router.post("/login", dependencies=[Depends(rate_limit("login", 30, 60))])

    await enforce_rate_limit("ws_msg", str(session_id), limit=50, window_s=1)
"""

from __future__ import annotations

import hashlib
import inspect
import ipaddress
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request
from redis.exceptions import RedisError

from app.core.errors import RateLimited
from app.core.logging import get_logger
from app.core.metrics import ERRORS, RATE_LIMITED

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger(__name__)

KeyFunc = Callable[[Request], str | Awaitable[str]]

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, member .. ':' .. now)
  redis.call('PEXPIRE', key, window)
  return {1, limit - count - 1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry = window
if oldest[2] then
  retry = tonumber(oldest[2]) + window - now
end
if retry < 1 then retry = 1 end
return {0, 0, retry}
"""

_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.:@-]")


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: float  # seconds; 0 when allowed


def rate_limit_key(scope: str, key: str) -> str:
    cleaned = _KEY_SAFE.sub("_", key)
    if len(cleaned) > 128:
        cleaned = hashlib.sha256(cleaned.encode()).hexdigest()
    return f"rl:{scope}:{cleaned}"


def hashed_key(value: str) -> str:
    """Stable, non-reversible key for personal data (e.g. an email address)."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


def client_ip(request: Request) -> str:
    """Client address as seen by uvicorn (``--proxy-headers`` trusts the edge)."""
    return request.client.host if request.client else "unknown"


def ip_key(ip: str) -> str:
    """Rate-limit key of an address: IPv4 as is, IPv6 by its /64 (one subscriber's allocation), so rotating
    addresses inside it does not reset the limits (S15)."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)


def client_ip_key(request: Request) -> str:
    """``ip_key`` of the client (``client_ip`` stays the full address for logging)."""
    return ip_key(client_ip(request))


async def hit(
    scope: str,
    key: str,
    limit: int,
    window_s: float,
    *,
    redis: Redis | None = None,
) -> RateLimitResult:
    """Record one request against ``scope``/``key``; fail open on Redis errors."""
    if limit <= 0:
        return RateLimitResult(allowed=False, remaining=0, retry_after=window_s)
    if redis is None:
        from app.core.redis import get_redis

        redis = get_redis()
    window_ms = max(1, int(window_s * 1000))
    try:
        allowed, remaining, retry_ms = await redis.eval(  # type: ignore[misc]
            _SLIDING_WINDOW_LUA, 1, rate_limit_key(scope, key), window_ms, limit, secrets.token_hex(6)
        )
    except (RedisError, OSError) as exc:
        ERRORS.labels(component="ratelimit").inc()
        log.warning("ratelimit.redis_unavailable", scope=scope, error=type(exc).__name__)
        return RateLimitResult(allowed=True, remaining=limit, retry_after=0.0)
    return RateLimitResult(
        allowed=bool(int(allowed)), remaining=int(remaining), retry_after=int(retry_ms) / 1000.0
    )


async def reset(scope: str, key: str, *, redis: Redis | None = None) -> None:
    if redis is None:
        from app.core.redis import get_redis

        redis = get_redis()
    try:
        await redis.delete(rate_limit_key(scope, key))
    except (RedisError, OSError) as exc:
        log.warning("ratelimit.reset_failed", scope=scope, error=type(exc).__name__)


async def enforce_rate_limit(
    scope: str,
    key: str,
    *,
    limit: int,
    window_s: float,
    redis: Redis | None = None,
    message: str | None = None,
) -> RateLimitResult:
    """Like :func:`hit` but raises :class:`RateLimited` when over the limit."""
    result = await hit(scope, key, limit, window_s, redis=redis)
    if not result.allowed:
        RATE_LIMITED.labels(scope=scope).inc()
        log.info("ratelimit.rejected", scope=scope, retry_after=result.retry_after)
        raise RateLimited(message, retry_after=result.retry_after)
    return result


def rate_limit(
    scope: str,
    limit: int,
    window_s: float,
    key_func: KeyFunc | None = None,
) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory; rejects with 429 + ``Retry-After``."""
    resolve = key_func or client_ip

    async def _dependency(request: Request) -> None:
        key = resolve(request)
        if inspect.isawaitable(key):
            key = await key
        await enforce_rate_limit(scope, str(key), limit=limit, window_s=window_s)

    _dependency.__name__ = f"rate_limit_{scope}"
    return _dependency
