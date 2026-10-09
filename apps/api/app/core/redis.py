"""Shared Redis clients.

Two process-wide clients built from ``REDIS_URL``: :func:`get_redis` decodes
responses to ``str`` (keys, JSON, streams, pub/sub) and :func:`get_redis_bytes`
returns raw ``bytes`` (audio buffers, binary caches). Both retry transient
connection errors with jittered exponential backoff and health-check idle
connections. Clients are created lazily inside the running event loop and must
be closed with :func:`close_redis` on shutdown.
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Long enough for XREADGROUP BLOCK (<= 5 s) and pub/sub polling; short enough
# that a dead connection is noticed quickly.
SOCKET_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 5.0
HEALTH_CHECK_INTERVAL_SECONDS = 30

_clients: dict[bool, Redis] = {}


def create_redis_client(url: str | None = None, *, decode_responses: bool = True, client_name: str | None = None) -> Redis:
    """Build a new client (callers own it and must ``aclose()`` it)."""
    settings = get_settings()
    return Redis.from_url(
        url or settings.redis_url,
        decode_responses=decode_responses,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_keepalive=True,
        health_check_interval=HEALTH_CHECK_INTERVAL_SECONDS,
        retry=Retry(ExponentialWithJitterBackoff(cap=2.0, base=0.05), retries=5),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
        client_name=client_name or f"interview:{settings.instance_id}"[:64],
    )


def get_redis() -> Redis:
    """Process-wide client returning ``str`` values."""
    client = _clients.get(True)
    if client is None:
        client = _clients[True] = create_redis_client(decode_responses=True)
    return client


def get_redis_bytes() -> Redis:
    """Process-wide client returning ``bytes`` values."""
    client = _clients.get(False)
    if client is None:
        client = _clients[False] = create_redis_client(decode_responses=False)
    return client


async def ping_redis(timeout: float = 2.0) -> bool:  # noqa: ASYNC109
    try:
        async with asyncio.timeout(timeout):
            return bool(await get_redis().ping())
    except (RedisError, OSError, TimeoutError) as exc:
        log.warning("redis.ping_failed", error=type(exc).__name__)
        return False


async def close_redis() -> None:
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except (RedisError, OSError, RuntimeError) as exc:  # loop already closing
            log.debug("redis.close_failed", error=type(exc).__name__)
