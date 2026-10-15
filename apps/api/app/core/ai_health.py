"""Shared view of the AI endpoints' health (plan package S16).

One short-lived cache around ``get_ai().health()`` serves ``/readyz?deep=1``, the staff
system-health page and the startup probe, so a page refresh or a smoke test never hits the
shared GPUs more than once every :data:`CACHE_TTL_SECONDS`. ``REQUIRED_ENDPOINTS`` are the
ones an interview cannot run without; the ``OPTIONAL_ENDPOINTS`` only degrade features
(camera-frame analysis, question-bank retrieval, scanned resumes).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.lifecycle import optional_import
from app.core.logging import get_logger
from app.core.metrics import AI_ENDPOINT_UP

log = get_logger(__name__)

REQUIRED_ENDPOINTS: tuple[str, ...] = ("main", "whisper", "tts")
OPTIONAL_ENDPOINTS: tuple[str, ...] = ("vision", "embedding", "reranker", "ocr")
ALL_ENDPOINTS: tuple[str, ...] = REQUIRED_ENDPOINTS + OPTIONAL_ENDPOINTS

CACHE_TTL_SECONDS = 15.0
PROBE_TIMEOUT_SECONDS = 8.0
STARTUP_PROBE_TIMEOUT_SECONDS = 20.0

_UP = frozenset({"ok", "up", "healthy", "degraded"})  # degraded = reachable, circuit half-open
_DOWN = frozenset({"down", "error"})

# (monotonic timestamp, id() of the AIServices object probed, report)
_cache: tuple[float, int, dict[str, dict[str, Any]]] | None = None
_lock = asyncio.Lock()


def invalidate() -> None:
    """Forget the cached report (tests swap the AI services with ``set_ai``)."""
    global _cache
    _cache = None


def _services() -> Any | None:
    registry = optional_import("app.ai.registry", strict=False)
    get_ai = getattr(registry, "get_ai", None) if registry is not None else None
    return get_ai() if get_ai is not None else None


def _normalise(report: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, raw in (report or {}).items():
        data = dict(raw) if isinstance(raw, dict) else {"status": "ok" if raw else "down"}
        data["status"] = str(data.get("status", "unknown")).lower()
        out[str(name)] = data
    return out


def _record_gauges(report: dict[str, dict[str, Any]]) -> None:
    for name, data in report.items():
        status = data["status"]
        value = 1 if status in _UP else 0 if status in _DOWN else -1
        AI_ENDPOINT_UP.labels(endpoint=name).set(value)


async def probe(*, force: bool = False, budget_s: float = PROBE_TIMEOUT_SECONDS) -> dict[str, dict[str, Any]]:
    """Health of every AI endpoint: ``{name: {status, latency_ms, detail, ...}}``.

    Cached for :data:`CACHE_TTL_SECONDS` per AI services object. A failing or missing
    registry reports every endpoint as down (never raises)."""
    global _cache
    services = _services()
    key = id(services)
    now = time.monotonic()
    if not force and _cache is not None and _cache[1] == key and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[2]
    async with _lock:
        now = time.monotonic()
        if not force and _cache is not None and _cache[1] == key and now - _cache[0] < CACHE_TTL_SECONDS:
            return _cache[2]
        if services is None:
            report = {
                name: {"status": "unknown", "latency_ms": None, "detail": "AI registry unavailable"}
                for name in ALL_ENDPOINTS
            }
        else:
            try:
                async with asyncio.timeout(budget_s):
                    report = _normalise(await services.health())
            except Exception as exc:  # noqa: BLE001 - a probe failure is reported, never raised
                detail = f"{type(exc).__name__}: {exc}"[:200]
                report = {
                    name: {"status": "down", "latency_ms": None, "detail": detail} for name in ALL_ENDPOINTS
                }
        _record_gauges(report)
        _cache = (time.monotonic(), key, report)
        return report


def classify(report: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """``(required endpoints that are down, optional endpoints that are down or disabled)``.

    An endpoint missing from the report counts as down."""

    def status_of(name: str) -> str:
        data = report.get(name)
        return "down" if data is None else str(data.get("status", "unknown")).lower()

    required_down = [name for name in REQUIRED_ENDPOINTS if status_of(name) not in _UP]
    optional_down = [name for name in OPTIONAL_ENDPOINTS if status_of(name) not in _UP]
    return required_down, optional_down


async def log_startup_probe(process: str) -> None:
    """Right after the registry starts: one log line per unreachable endpoint.

    ``error`` for a required endpoint (interviews will not work), ``warning`` for an
    optional one (a feature degrades). Never raises, never delays startup (run as a task)."""
    try:
        report = await probe(force=True, budget_s=STARTUP_PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - defensive: probe() already swallows errors
        log.warning("ai.startup_probe_failed", process=process, error=type(exc).__name__)
        return
    required_down, optional_down = classify(report)
    for name in required_down:
        log.error(
            "ai.endpoint_unreachable", process=process, endpoint=name, required=True,
            detail=str(report.get(name, {}).get("detail", ""))[:200],
        )  # fmt: skip
    for name in optional_down:
        log.warning(
            "ai.endpoint_unreachable", process=process, endpoint=name, required=False,
            detail=str(report.get(name, {}).get("detail", ""))[:200],
        )  # fmt: skip
    if not required_down and not optional_down:
        log.info("ai.endpoints_ok", process=process, endpoints=sorted(report))
