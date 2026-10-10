"""Operational status for staff dashboards (``GET /api/v1/system/health``).

Probes run concurrently with short timeouts: database, Redis, storage (with
free disk space), job queues, live workers, every AI endpoint (through
``app.ai.registry.get_ai().health()`` when the registry is available), the TTS
service and the code sandbox. Details never include URLs or credentials.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends

from app.api.routers.health import app_version, check_database, check_redis, check_storage
from app.core import ai_health
from app.core.config import get_settings
from app.core.deps import require_permission
from app.core.logging import get_logger
from app.core.queue import DEAD_STREAM, DELAYED_ZSET, JOB_TYPES_SET, stream_key
from app.core.rbac import Permission, Principal
from app.core.redis import get_redis
from app.schemas.api.common import ERROR_RESPONSES, HealthComponent, SystemHealthResponse

log = get_logger(__name__)

router = APIRouter(prefix="/system", tags=["system"], responses=ERROR_RESPONSES)

# Operational detail (component latencies, circuit states) is for administrators (settings:manage),
# not every staff role.
SettingsManager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]

PROBE_TIMEOUT_SECONDS = 5.0
WORKER_HEARTBEAT_PREFIX = "worker:heartbeat:"
WORKER_IDS_SET = "worker:ids"
_CRITICAL = frozenset({"database", "redis"})
_STATUS_MAP = {"ok": "ok", "up": "ok", "healthy": "ok", "degraded": "degraded", "down": "down", "error": "down",
               "disabled": "disabled", "unknown": "unknown"}


async def _timed(name: str, probe: Any) -> HealthComponent:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            result = await probe()
    except Exception as exc:
        return HealthComponent(
            name=name, status="down", latency_ms=round((time.perf_counter() - started) * 1000, 1), detail=type(exc).__name__
        )
    if isinstance(result, HealthComponent):
        return result
    return HealthComponent(name=name, status="ok", latency_ms=round(float(result), 1))


async def _storage_component() -> HealthComponent:
    latency = await check_storage()
    usage = shutil.disk_usage(get_settings().storage_local_root)
    free_ratio = usage.free / usage.total if usage.total else 0.0
    return HealthComponent(
        name="storage",
        status="ok" if free_ratio >= 0.05 else "degraded",
        latency_ms=round(latency, 1),
        detail=None if free_ratio >= 0.05 else "less than 5% disk space free",
        info={"backend": "local", "free_bytes": usage.free, "total_bytes": usage.total},
    )


async def _queue_component() -> HealthComponent:
    redis = get_redis()
    started = time.perf_counter()
    depths: dict[str, int] = {}
    job_types: set[str] = await redis.smembers(JOB_TYPES_SET)  # type: ignore[assignment]  # str client
    for job_type in sorted(job_types):
        depths[job_type] = int(await redis.xlen(stream_key(job_type)))
    delayed = int(await redis.zcard(DELAYED_ZSET))
    dead = int(await redis.xlen(DEAD_STREAM))
    return HealthComponent(
        name="queue",
        status="ok",
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        info={"pending": depths, "delayed": delayed, "dead_letters": dead},
    )


async def _workers_component() -> HealthComponent:
    redis = get_redis()
    workers: list[dict[str, Any]] = []
    members: set[str] = await redis.smembers(WORKER_IDS_SET)  # type: ignore[assignment]  # str client
    ids = sorted(members)
    beats = await redis.mget([f"{WORKER_HEARTBEAT_PREFIX}{worker_id}" for worker_id in ids]) if ids else []
    for worker_id, raw in zip(ids, beats, strict=True):
        if raw is None:  # heartbeat expired: the worker is gone
            await redis.srem(WORKER_IDS_SET, worker_id)
            continue
        try:
            workers.append(json.loads(raw))
        except ValueError:
            continue
    return HealthComponent(
        name="workers",
        status="ok" if workers else "down",
        detail=None if workers else "no live worker heartbeat",
        info={"count": len(workers), "workers": workers[:20]},
    )


async def _http_component(name: str, base_url: str) -> HealthComponent:
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.get(f"{base_url}/health")
    latency = round((time.perf_counter() - started) * 1000, 1)
    if response.status_code == 200:
        return HealthComponent(name=name, status="ok", latency_ms=latency)
    return HealthComponent(name=name, status="down", latency_ms=latency, detail=f"HTTP {response.status_code}")


async def _ai_components() -> list[HealthComponent]:
    """AI endpoints from the shared, 15 s cached probe (a page refresh never re-probes the GPUs)."""
    try:
        report = await ai_health.probe()
    except Exception as exc:  # noqa: BLE001 - probe() reports failures itself; this is belt and braces
        return [HealthComponent(name="ai", status="down", detail=type(exc).__name__)]
    components: list[HealthComponent] = []
    for endpoint, data in report.items():
        status = _STATUS_MAP.get(str(data.get("status", "unknown")).lower(), "unknown")
        latency = data.get("latency_ms")
        detail = data.get("detail")
        components.append(
            HealthComponent(
                name=f"ai.{endpoint}",
                status=status,  # type: ignore[arg-type]
                latency_ms=float(latency) if isinstance(latency, int | float) else None,
                detail=str(detail)[:200] if detail else None,
                info={k: v for k, v in data.items() if k in ("circuit", "model")},
            )
        )
    return components


@router.get("/health", response_model=SystemHealthResponse, summary="Health of every platform dependency")
async def system_health(principal: SettingsManager) -> SystemHealthResponse:
    settings = get_settings()
    results: list[Any] = await asyncio.gather(
        _timed("database", check_database),
        _timed("redis", check_redis),
        _timed("storage", _storage_component),
        _timed("queue", _queue_component),
        _timed("workers", _workers_component),
        _timed("tts", lambda: _http_component("tts", settings.tts_base_url)),
        _timed("sandbox", lambda: _http_component("sandbox", settings.sandbox_base_url)),
        _ai_components(),
    )
    components: list[HealthComponent] = []
    for item in results:
        components.extend(item if isinstance(item, list) else [item])
    if any(c.status == "down" for c in components if c.name in _CRITICAL):
        overall = "down"
    elif any(c.status in ("down", "degraded") for c in components):
        overall = "degraded"
    else:
        overall = "ok"
    return SystemHealthResponse(
        status=overall,  # type: ignore[arg-type]
        version=app_version(),
        environment=settings.app_env,
        instance_id=settings.instance_id,
        checked_at=datetime.now(UTC),
        components=components,
    )
