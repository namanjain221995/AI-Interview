"""Background worker process: ``python -m app.worker``.

Startup: logging, tracing, storage root, Redis and database reachability, AI
registry, auto-import of every ``app.workers`` module (handlers self-register),
worker lifecycle hooks. Then it runs, in one event loop:

* the queue :class:`~app.core.queue.Worker` (streams, retries, dead letters,
  reclaim, recovery, queue-depth gauges every 15 s);
* a tiny ASGI server on ``API_HOST:WORKER_METRICS_PORT`` with ``/metrics`` and
  ``/healthz`` (200 while the consume loop is alive);
* the periodic scheduler (``retention.sweep`` hourly, plus anything registered
  with :func:`app.core.queue.register_periodic_job`);
* a heartbeat key ``worker:heartbeat:{instance_id}`` read by
  ``/api/v1/system/health``.

SIGTERM/SIGINT stop intake, let in-flight jobs finish (grace period), then
close every resource.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import uvicorn
from sqlalchemy import text

from app.core.ai_health import log_startup_probe
from app.core.config import Settings, get_settings
from app.core.lifecycle import LifecycleRunner, import_submodules, init_ai_registry, shutdown_ai_registry
from app.core.logging import configure_logging, get_logger
from app.core.metrics import render_latest
from app.core.queue import Worker, enqueue_due_periodic_jobs, register_periodic_job, registered_job_types
from app.core.redis import close_redis, get_redis
from app.core.tracing import setup_tracing, shutdown_tracing
from app.db.session import dispose_engine, get_engine
from app.services.storage import get_storage

log = get_logger("app.worker")

WORKERS_PACKAGE = "app.workers"
HEARTBEAT_PREFIX = "worker:heartbeat:"
WORKER_IDS_SET = "worker:ids"
HEARTBEAT_INTERVAL_SECONDS = 10
SCHEDULER_INTERVAL_SECONDS = 30
STARTUP_WAIT_SECONDS = 60

register_periodic_job("retention.sweep", 3600)

ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


def build_metrics_app(worker: Worker) -> ASGIApp:
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        if path == "/metrics":
            body, content_type = render_latest()
            status = 200
        elif path == "/healthz":
            healthy = worker.healthy()
            body = json.dumps(
                {
                    "status": "ok" if healthy else "unhealthy",
                    "inflight": worker.inflight,
                    "processed": worker.processed,
                    "job_types": worker.job_types,
                }
            ).encode()
            content_type, status = "application/json", 200 if healthy else 503
        else:
            body, content_type, status = b'{"error":"not found"}', "application/json", 404
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type.encode()),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


class _MetricsServer(uvicorn.Server):
    """uvicorn server that leaves signal handling to the worker runtime."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def _wait_for_dependencies(deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    delay = 0.5
    while True:
        problems: list[str] = []
        try:
            await get_redis().ping()
        except Exception as exc:
            problems.append(f"redis: {type(exc).__name__}")
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            problems.append(f"database: {type(exc).__name__}")
        if not problems:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("dependencies unavailable: " + ", ".join(problems))
        log.warning("worker.waiting_for_dependencies", problems=problems, retry_in=delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 5.0)


async def _heartbeat_loop(worker: Worker, settings: Settings, started_at: str, stop: asyncio.Event) -> None:
    key = f"{HEARTBEAT_PREFIX}{settings.instance_id}"
    redis = get_redis()
    while not stop.is_set():
        try:
            await redis.sadd(WORKER_IDS_SET, settings.instance_id)
            await redis.set(
                key,
                json.dumps(
                    {
                        "id": settings.instance_id,
                        "started_at": started_at,
                        "seen_at": datetime.now(UTC).isoformat(),
                        "inflight": worker.inflight,
                        "processed": worker.processed,
                        "job_types": worker.job_types,
                        "healthy": worker.healthy(),
                    }
                ),
                ex=HEARTBEAT_INTERVAL_SECONDS * 3,
            )
        except Exception as exc:
            log.warning("worker.heartbeat_write_failed", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
    with contextlib.suppress(Exception):
        await redis.delete(key)
        await redis.srem(WORKER_IDS_SET, settings.instance_id)


async def _scheduler_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            enqueued = await enqueue_due_periodic_jobs()
            if enqueued:
                log.info("worker.periodic_enqueued", jobs=enqueued)
        except Exception as exc:
            log.warning("worker.scheduler_failed", error=f"{type(exc).__name__}: {exc}")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=SCHEDULER_INTERVAL_SECONDS)


async def run_worker(settings: Settings | None = None, *, stop: asyncio.Event | None = None) -> None:
    settings = settings or get_settings()
    strict = settings.app_env in ("production", "staging")
    configure_logging(settings.log_level, settings.log_json)
    setup_tracing(service_name=f"{settings.otel_service_name}-worker")
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop.set)

    started_at = datetime.now(UTC).isoformat()
    log.info("worker.starting", instance_id=settings.instance_id, environment=settings.app_env)
    await get_storage().ensure_root()
    await _wait_for_dependencies(STARTUP_WAIT_SECONDS)
    ai_started = await init_ai_registry(strict=strict, is_test=settings.is_test)
    if ai_started:
        # One log line per unreachable AI endpoint (S16); runs in the background, never delays startup.
        _startup_probe = asyncio.create_task(log_startup_probe("worker"), name="ai.startup_probe")
    import_submodules(WORKERS_PACKAGE, strict=strict)
    log.info("worker.handlers_registered", job_types=registered_job_types())
    runner = LifecycleRunner("worker", strict=strict)
    await runner.start()

    worker = Worker(consumer_name=settings.instance_id, depth_interval=15.0)
    server = _MetricsServer(
        uvicorn.Config(
            build_metrics_app(worker),
            host=settings.api_host,
            port=settings.worker_metrics_port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
    )
    tasks = {
        asyncio.create_task(worker.run(stop), name="worker"),
        asyncio.create_task(server.serve(), name="metrics-server"),
        asyncio.create_task(_heartbeat_loop(worker, settings, started_at, stop), name="heartbeat"),
        asyncio.create_task(_scheduler_loop(stop), name="scheduler"),
    }
    stop_waiter = asyncio.create_task(stop.wait(), name="stop")
    try:
        done, _ = await asyncio.wait({*tasks, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stop_waiter and task.exception() is not None:
                log.error("worker.component_crashed", component=task.get_name(), exc_info=task.exception())
    finally:
        log.info("worker.stopping")
        stop.set()
        worker.stop()
        server.should_exit = True
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results, strict=False):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                log.warning("worker.component_error_on_shutdown", component=task.get_name(), error=type(result).__name__)
        stop_waiter.cancel()
        await runner.stop()
        if ai_started:
            await shutdown_ai_registry()
        await close_redis()
        await dispose_engine()
        shutdown_tracing()
        log.info("worker.stopped")


def main() -> None:
    try:
        import uvloop

        loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = uvloop.new_event_loop
    except ImportError:  # pragma: no cover - uvicorn[standard] ships uvloop
        loop_factory = None
    asyncio.run(run_worker(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
