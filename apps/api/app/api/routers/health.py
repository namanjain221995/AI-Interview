"""Liveness, readiness and Prometheus metrics.

``root_router`` (mounted at the application root by ``app.main``):

* ``GET /healthz`` - liveness, touches no dependency.
* ``GET /readyz``  - database, Redis and storage; 503 when any is down.
* ``GET /metrics`` - Prometheus exposition (blocked from outside by the edge).

``router`` (auto-included under ``/api/v1``): ``GET /api/v1/health`` - the same
liveness payload for the SPA through the edge proxy.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core import ai_health
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import render_latest
from app.core.redis import get_redis
from app.db.session import get_engine
from app.services.storage import get_storage

log = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])
root_router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 3.0


def app_version() -> str:
    try:
        from importlib.metadata import version

        return version("interview-api")
    except Exception:
        return "0.1.0"


def _liveness() -> dict[str, Any]:
    settings = get_settings()
    return {"status": "ok", "version": app_version(), "environment": settings.app_env, "instance_id": settings.instance_id}


async def check_database() -> float:
    started = time.perf_counter()
    async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    return (time.perf_counter() - started) * 1000


async def check_redis() -> float:
    started = time.perf_counter()
    async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
        if not await get_redis().ping():
            raise RuntimeError("redis ping failed")
    return (time.perf_counter() - started) * 1000


async def check_storage() -> float:
    async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
        return await get_storage().check()


async def run_check(name: str, check: Any) -> dict[str, Any]:
    try:
        latency = await check()
        return {"name": name, "status": "ok", "latency_ms": round(latency, 2)}
    except Exception as exc:
        log.warning("readiness.check_failed", check=name, error=type(exc).__name__)
        return {"name": name, "status": "down", "error": type(exc).__name__}


@root_router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, Any]:
    return _liveness()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(request: Request) -> bool:
    """True only for clients on this host.

    Direct connections: the peer address must be loopback. Through the edge the peer is the
    edge itself (loopback), so the first ``X-Forwarded-For`` hop it adds must be loopback as
    well; a direct loopback client never sends that header, and anyone who can forge it from
    loopback is already on the host."""
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK_HOSTS:
        return False
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return True
    first_hop = forwarded.split(",")[0].strip()
    return first_hop in _LOOPBACK_HOSTS


@root_router.get(
    "/readyz",
    summary="Readiness probe (database, Redis, storage; ?deep=1 adds the AI endpoints, loopback clients only)",
)
async def readyz(
    request: Request,
    deep: bool = Query(False, description="Also probe the AI endpoints (cached 15 s). Loopback clients only."),
) -> JSONResponse:
    results = await asyncio.gather(
        run_check("database", check_database), run_check("redis", check_redis), run_check("storage", check_storage)
    )
    body: dict[str, Any] = {"checks": {r["name"]: r for r in results}}
    ready = all(r["status"] == "ok" for r in results)
    if deep:
        if not _is_loopback(request):
            return JSONResponse(
                {"status": "forbidden", "detail": "deep readiness is available to loopback clients only"},
                status_code=403,
            )
        # main, whisper and tts are required for an interview; the others only degrade features.
        report = await ai_health.probe()
        required_down, optional_down = ai_health.classify(report)
        body["ai"] = {
            name: {"status": data.get("status"), "latency_ms": data.get("latency_ms")} for name, data in report.items()
        }
        body["required_down"] = required_down
        body["degraded"] = optional_down
        ready = ready and not required_down
    body["status"] = "ready" if ready else "not_ready"
    return JSONResponse(body, status_code=200 if ready else 503)


@root_router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    if not get_settings().metrics_enabled:
        return Response(status_code=404)
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)


@router.get("", summary="API liveness (through the edge proxy)")
async def api_health() -> dict[str, Any]:
    return _liveness()
