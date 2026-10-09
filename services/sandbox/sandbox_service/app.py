"""HTTP API of the sandbox service.

POST /v1/execute   ExecuteRequest -> ExecuteResponse        (HMAC-signed)
GET  /v1/runtimes  languages, toolchains, conventions, limits (HMAC-signed)
GET  /health       docker + per-runtime image availability   (unauthenticated, for probes)
GET  /metrics      Prometheus                                 (unauthenticated, loopback/backend only)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from . import __version__, metrics
from .auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, Authenticator, AuthError
from .config import Settings, get_settings
from .drivers import ENTRY_CONVENTIONS, build_drivers
from .executor import RUNTIME_LANGUAGES, DockerSandbox, Executor, SandboxUnavailable
from .logging import configure_logging, get_logger
from .models import CodeLanguage, ExecuteRequest
from .signature import COMPARISONS, SUPPORTED_TYPES

log = get_logger(__name__)


class BodyTooLarge(Exception):
    pass


def _error(
    status: int, code: str, message: str, request_id: str | None = None, headers: dict[str, str] | None = None, **details: Any
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


async def read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise BodyTooLarge
        except ValueError as exc:
            raise BodyTooLarge from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(
    settings: Settings | None = None,
    executor: Executor | None = None,
    run_janitor: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    authenticator = Authenticator(settings.shared_secret.get_secret_value().encode(), settings.signature_max_skew_seconds)
    state: dict[str, Any] = {}

    async def janitor_loop(ex: Executor) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(None, ex.sandbox.sweep)
            except Exception as exc:
                log.warning("sandbox.janitor_error", error=str(exc)[:200])
            await asyncio.sleep(settings.janitor_interval_seconds)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ex = executor or Executor(settings, DockerSandbox(settings), build_drivers(settings))
        state["executor"] = ex
        task = asyncio.create_task(janitor_loop(ex)) if run_janitor else None
        loop = asyncio.get_running_loop()
        try:
            status = await loop.run_in_executor(None, lambda: ex.sandbox.runtime_status(force=True))
            log.info(
                "sandbox.started",
                version=__version__,
                docker=status.get("docker"),
                runtimes={k: v.get("available") for k, v in status.get("runtimes", {}).items()},
                max_concurrency=settings.max_concurrency,
            )
        except Exception as exc:
            log.warning("sandbox.startup_check_failed", error=str(exc)[:200])
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await loop.run_in_executor(None, ex.close)

    app = FastAPI(
        title="Interview code sandbox",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def authenticate(request: Request, body: bytes) -> None:
        try:
            authenticator.verify(request.headers.get(TIMESTAMP_HEADER), request.headers.get(SIGNATURE_HEADER), body)
        except AuthError as exc:
            metrics.REJECTIONS.labels(f"auth_{exc.reason}").inc()
            log.warning("sandbox.auth_failed", reason=exc.reason, client=request.client.host if request.client else None)
            raise

    @app.post("/v1/execute")
    async def execute(request: Request) -> Response:
        try:
            body = await read_body(request, settings.max_request_bytes)
        except BodyTooLarge:
            metrics.REJECTIONS.labels("too_large").inc()
            return _error(413, "payload_too_large", f"request body exceeds {settings.max_request_bytes} bytes")
        try:
            authenticate(request, body)
        except AuthError as exc:
            return _error(401, "unauthorized", exc.message)
        try:
            req = ExecuteRequest.model_validate_json(body)
        except ValidationError as exc:
            metrics.REJECTIONS.labels("validation").inc()
            errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()[:20]]
            return _error(422, "validation_failed", "invalid execute request", errors=errors)
        try:
            response = await state["executor"].execute(req)
        except SandboxUnavailable as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            return _error(503, exc.code, exc.message, request_id=req.request_id, headers=headers)
        return Response(response.model_dump_json(), media_type="application/json")

    @app.get("/v1/runtimes")
    async def runtimes(request: Request) -> Response:
        try:
            authenticate(request, b"")
        except AuthError as exc:
            return _error(401, "unauthorized", exc.message)
        ex: Executor = state["executor"]
        status = await asyncio.get_running_loop().run_in_executor(None, ex.sandbox.runtime_status)
        languages = {}
        for runtime, langs in RUNTIME_LANGUAGES.items():
            info = status["runtimes"].get(runtime, {})
            for lang in langs:
                driver = ex.drivers[CodeLanguage(lang)]
                languages[lang] = {
                    "runtime": runtime,
                    "image": info.get("image"),
                    "available": bool(info.get("available")),
                    "toolchain": info.get("toolchain"),
                    "modes": ["run", "tests"],
                    "entry_point": ENTRY_CONVENTIONS[CodeLanguage(lang)],
                    "source_file": driver.source_name
                    if lang != "java"
                    else "<PublicClass>.java (Main.java / Solution.java by default)",
                    "memory_overhead_mb": driver.baseline_mb,
                }
        return JSONResponse(
            {
                "languages": languages,
                "signature": {"types": list(SUPPORTED_TYPES), "comparisons": list(COMPARISONS)},
                "limits": {
                    "time_limit_ms": {"default": 5000, "min": 500, "max": 20000},
                    "compile_time_limit_ms": {"default": 15000, "min": 1000, "max": 60000},
                    "memory_limit_mb": {"default": 256, "min": 64, "max": 1024},
                    "max_tests": 100,
                    "max_code_chars": 200_000,
                    "max_stdin_chars": 100_000,
                    "max_request_bytes": settings.max_request_bytes,
                    "output_capture_bytes": settings.output_capture_bytes,
                    "cpus": settings.cpus,
                    "pids": settings.pids_limit,
                },
                "max_concurrency": settings.max_concurrency,
            }
        )

    @app.get("/health")
    async def health() -> Response:
        ex: Executor | None = state.get("executor")
        if ex is None:
            return _error(503, "starting", "sandbox is starting")
        try:
            status = await asyncio.get_running_loop().run_in_executor(None, ex.sandbox.runtime_status)
        except Exception as exc:
            return JSONResponse({"status": "error", "docker": False, "error": str(exc)[:200]}, status_code=503)
        runtimes = status.get("runtimes", {})
        all_ok = all(r.get("available") for r in runtimes.values())
        overall = "ok" if status.get("docker") and all_ok else ("degraded" if status.get("docker") else "error")
        languages = {
            lang: {"runtime": runtime, "available": bool(runtimes.get(runtime, {}).get("available"))}
            for runtime, langs in RUNTIME_LANGUAGES.items()
            for lang in langs
        }
        body = {
            "status": overall,
            "version": __version__,
            "docker": bool(status.get("docker")),
            "runtimes": runtimes,
            "languages": languages,
            "max_concurrency": settings.max_concurrency,
            "inflight": ex.inflight,
            "queued": ex.waiting,
            "time": int(time.time()),
        }
        return JSONResponse(body, status_code=200 if status.get("docker") else 503)

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
