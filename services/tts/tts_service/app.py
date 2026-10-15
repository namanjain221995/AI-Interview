"""FastAPI application: /v1/tts, /v1/audio/speech, /v1/voices, /health, /metrics."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import MutableHeaders

from tts_service import __version__, metrics
from tts_service.audio import wav_bytes
from tts_service.config import Settings, get_settings
from tts_service.engine import Engine, KokoroEngine
from tts_service.logging_setup import configure_logging, get_logger
from tts_service.scheduler import JobTimeout, QueueFull
from tts_service.schemas import (
    HealthResponse,
    SpeechRequest,
    TtsRequest,
    TtsResponse,
    Viseme,
    VoiceOut,
    VoicesResponse,
)
from tts_service.service import NotReady, SpeakResult, SynthesisFailed, TtsService, UnknownVoice

log = get_logger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_ENDPOINTS = {
    "/v1/tts": "tts",
    "/v1/audio/speech": "speech",
    "/v1/voices": "voices",
    "/health": "health",
    "/metrics": "metrics",
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class Unauthorized(Exception):
    pass


def _request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value is not None else None


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": _request_id()}
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=headers)


async def _send_json(send: Send, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` with 413 before they reach the app."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        too_large = {
            "error": {
                "code": "payload_too_large",
                "message": f"request body exceeds {self.max_bytes} bytes",
                "request_id": _request_id(),
            }
        }
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                declared = self.max_bytes + 1
            if declared > self.max_bytes:
                await _send_json(send, 413, too_large)
                return
            await self.app(scope, receive, send)
            return
        # No Content-Length (chunked): buffer up to the limit, then replay.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await _send_json(send, 413, too_large)
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class RequestContextMiddleware:
    """Request id, structured access log and request metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        path = scope.get("path", "")
        endpoint = _ENDPOINTS.get(path, "other")
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append("x-request-id", request_id)
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            elapsed = time.perf_counter() - started
            metrics.REQUESTS.labels(endpoint, str(status)).inc()
            metrics.REQUEST_SECONDS.labels(endpoint).observe(elapsed)
            if endpoint not in ("health", "metrics"):
                log.info(
                    "http.request",
                    method=scope.get("method"),
                    path=path,
                    status=status,
                    duration_ms=round(elapsed * 1000, 1),
                )
            structlog.contextvars.unbind_contextvars("request_id")


def _synthesis_headers(result: SpeakResult) -> dict[str, str]:
    synthesis = result.synthesis
    headers = {
        "X-TTS-Cache": "hit" if result.cached else "miss",
        "X-TTS-Duration-Ms": str(synthesis.duration_ms),
        "X-TTS-Synthesis-Ms": f"{synthesis.synthesis_seconds * 1000:.1f}",
        "X-TTS-Voice": synthesis.voice,
        "X-Sample-Rate": str(synthesis.sample_rate),
    }
    if synthesis.timing:
        headers["X-TTS-Viseme-Timing"] = synthesis.timing
    if synthesis.audible_ms:
        headers["X-TTS-Audible-Ms"] = f"{synthesis.audible_ms[0]}-{synthesis.audible_ms[1]}"
    return headers


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    service = TtsService(settings, engine if engine is not None else KokoroEngine(settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        loader = asyncio.create_task(service.start(), name="tts-startup")
        warmer = asyncio.create_task(service.keep_warm(), name="tts-keepwarm")
        try:
            yield
        finally:
            for task in (warmer, loader):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            service.shutdown()

    app = FastAPI(
        title="Interview TTS",
        version=__version__,
        description="Kokoro-82M text-to-speech with Oculus viseme timelines.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.state.service = service
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware)

    def require_api_key(request: Request) -> None:
        if settings.api_key is None:
            return
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        expected = settings.api_key.get_secret_value().encode()
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), expected):
            raise Unauthorized()

    # ---------------------------------------------------------------- errors

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        metrics.ERRORS.labels("invalid_input").inc()
        errors = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg", ""), "type": error.get("type", "")}
            for error in exc.errors()
        ]  # the submitted text is never echoed back
        message = "; ".join(f"{'.'.join(map(str, e['loc'][1:])) or 'body'}: {e['msg']}" for e in errors)
        return error_response(
            422, "validation_error", message or "invalid request", details={"errors": errors}
        )

    @app.exception_handler(UnknownVoice)
    async def _unknown_voice(_: Request, exc: UnknownVoice) -> JSONResponse:
        return error_response(
            422,
            "unknown_voice",
            f"voice {exc.voice!r} is not available",
            details={"available": exc.available},
        )

    @app.exception_handler(NotReady)
    async def _not_ready(_: Request, exc: NotReady) -> JSONResponse:
        message = "model is loading" if exc.state == "loading" else f"service unavailable: {exc.detail}"
        return error_response(503, "not_ready", message, headers={"Retry-After": "2"})

    @app.exception_handler(QueueFull)
    async def _queue_full(_: Request, __: QueueFull) -> JSONResponse:
        return error_response(
            503, "busy", "synthesis queue is full, retry shortly", headers={"Retry-After": "1"}
        )

    @app.exception_handler(JobTimeout)
    async def _timeout(_: Request, exc: JobTimeout) -> JSONResponse:
        return error_response(504, "timeout", str(exc))

    @app.exception_handler(SynthesisFailed)
    async def _failed(_: Request, __: SynthesisFailed) -> JSONResponse:
        return error_response(500, "synthesis_failed", "speech synthesis failed")

    @app.exception_handler(Unauthorized)
    async def _unauthorized(_: Request, __: Unauthorized) -> JSONResponse:
        metrics.ERRORS.labels("unauthorized").inc()
        return error_response(
            401, "unauthorized", "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"}
        )

    # ---------------------------------------------------------------- routes

    @app.post("/v1/tts", response_model=TtsResponse, dependencies=[Depends(require_api_key)])
    async def synthesize(body: TtsRequest) -> Response:
        result = await service.speak(body.text, body.voice, body.speed, body.sample_rate)
        synthesis = result.synthesis
        payload = TtsResponse(
            audio_b64=base64.b64encode(synthesis.pcm16).decode("ascii"),
            sample_rate=synthesis.sample_rate,
            duration_ms=synthesis.duration_ms,
            visemes=[Viseme(t=cue.t_ms, v=cue.viseme, w=cue.weight) for cue in synthesis.visemes],  # type: ignore[arg-type]
            phonemes=synthesis.phonemes,
            voice=synthesis.voice,
            cached=result.cached,
        )
        return Response(
            payload.model_dump_json(), media_type="application/json", headers=_synthesis_headers(result)
        )

    @app.post(
        "/v1/audio/speech",
        dependencies=[Depends(require_api_key)],
        response_class=Response,
        responses={200: {"content": {"audio/wav": {}, "audio/pcm": {}}}},
    )
    async def openai_speech(body: SpeechRequest) -> Response:
        """OpenAI-compatible speech: WAV or raw PCM16 (mono, 24 kHz, little-endian)."""
        result = await service.speak(body.input, body.voice, body.speed, 24000)
        pcm = result.synthesis.pcm16
        if body.response_format == "wav":
            return Response(
                wav_bytes(pcm, result.synthesis.sample_rate),
                media_type="audio/wav",
                headers=_synthesis_headers(result),
            )
        return Response(pcm, media_type="audio/pcm", headers=_synthesis_headers(result))

    @app.get("/v1/voices", response_model=VoicesResponse, dependencies=[Depends(require_api_key)])
    async def list_voices() -> VoicesResponse:
        default = settings.default_voice
        return VoicesResponse(
            default=default,
            voices=[
                VoiceOut(
                    id=v.id,
                    language=v.language,
                    language_name=v.language_name,
                    gender=v.gender,
                    default=v.id == default,
                )
                for v in service.voices()
            ],
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> JSONResponse:
        report = HealthResponse(
            status=service.state,
            model=settings.model_name,
            warm=service.warm,
            last_latency_ms=service.last_latency_ms,
            viseme_timing=service.engine.timing_source,
            workers=settings.workers,
            threads=settings.threads,
            queue_depth=service.scheduler.queued,
            inflight=service.scheduler.running,
            cache_entries=len(service.cache),
            uptime_s=round(time.monotonic() - service.started_at, 1),
            detail=service.detail,
        )
        return JSONResponse(report.model_dump(), status_code=200 if service.state == "ok" else 503)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        body, content_type = metrics.render_latest()
        return Response(body, media_type=content_type)

    return app
