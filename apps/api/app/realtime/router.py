"""WebSocket endpoint ``/ws/sessions/{session_id}?ticket=...``.

Connection sequence:

1. Origin check (before accepting; the edge does not check it) -> HTTP 403.
2. Accept, consume the single-use ticket (bound to session + candidate) ->
   close 4001 when missing/used/expired.
3. Load the session; ended sessions -> 4011; expired/cancelled invites -> 4011.
4. Wait for ``hello`` (10 s) -> 4400 otherwise. A system-check probe
   (``hello.client.system_check: true``) is answered here - heartbeat ->
   heartbeat.ack - without touching the session runtime: it never counts as a
   connection, reconnect or page reload and never replaces the live socket.
5. Get or create the session runtime (Redis lock; another instance owning a
   fresh lock -> 4009), attach (replay after ``hello.last_seq``, state sync,
   resume).
6. Receive loop: every text frame is size-checked, rate-limited per
   connection and validated with ``client_message_adapter``; binary frames are
   size-checked and rate-limited microphone audio (type 0x01).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.lifecycle import on_shutdown, on_startup
from app.core.logging import get_logger
from app.core.metrics import ERRORS, RATE_LIMITED, WS_CONNECTIONS
from app.core.security import consume_ws_ticket
from app.domain.enums import InterviewStatus
from app.realtime import close_codes
from app.realtime.context import SessionContext, load_session_context
from app.realtime.limits import MAX_BINARY_BYTES, MAX_TEXT_BYTES, ConnectionLimits
from app.realtime.origin import origin_allowed
from app.realtime.registry import SessionActiveElsewhere, get_registry
from app.realtime.runtime import RuntimeClosed, SessionRuntime
from app.realtime.transport import WebSocketTransport
from app.schemas.realtime import (
    ErrorMsg,
    HeartbeatAckMsg,
    HelloMsg,
    SessionEndedMsg,
    client_message_adapter,
)

log = get_logger(__name__)

router = APIRouter(tags=["realtime"])

# uvicorn logs WebSocket handshakes with the full query string ("...?ticket=...")
# on "uvicorn.error" (and HTTP paths on "uvicorn.access"): scrub single-use
# tickets and invite tokens before any handler formats the record.
_SECRET_PATTERNS = (
    (re.compile(r"(ticket=)[^&\s\"']+"), r"\1[redacted]"),
    (re.compile(r"(/candidate-access/)[A-Za-z0-9_-]{16,}"), r"\1[redacted]"),
    (re.compile(r"(/link-access/)[A-Za-z0-9_-]{16,}"), r"\1[redacted]"),
    (re.compile(r"(/link-return/)[A-Za-z0-9_-]{16,}"), r"\1[redacted]"),
)


def redact_secrets(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_secrets(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_log_redaction() -> None:
    for name in ("uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _RedactSecrets) for f in logger.filters):
            logger.addFilter(_RedactSecrets())


install_log_redaction()

HELLO_TIMEOUT_SECONDS = 10.0
ERROR_NOTICE_INTERVAL_S = 2.0
PROBE_MAX_SECONDS = 30.0
PROBE_MAX_MESSAGES = 50


async def _send_error(websocket: WebSocket, code: str, message: str, *, recoverable: bool = False) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_text(
            ErrorMsg(code=code, message=message, recoverable=recoverable).model_dump_json()
        )


async def _close(websocket: WebSocket, code: int) -> None:
    if (
        websocket.application_state == WebSocketState.CONNECTED
        and websocket.client_state != WebSocketState.DISCONNECTED
    ):
        with contextlib.suppress(Exception):
            await websocket.close(code=code, reason=close_codes.reason_for(code))


async def _receive_hello(websocket: WebSocket) -> tuple[HelloMsg | None, bool]:
    """The first message as ``(hello, is_system_check_probe)``."""
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=HELLO_TIMEOUT_SECONDS)
    except TimeoutError:
        return None, False
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if text is None or len(text) > MAX_TEXT_BYTES:
        return None, False
    try:
        parsed = client_message_adapter.validate_json(text)
    except ValidationError:
        return None, False
    if not isinstance(parsed, HelloMsg):
        return None, False
    return parsed, is_system_check_hello(text)


def is_system_check_hello(text: str) -> bool:
    """The web system-check page marks its connectivity probe with
    ``client.system_check: true`` (not part of ``ClientInfo``, which ignores it)."""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    client = data.get("client") if isinstance(data, dict) else None
    return isinstance(client, dict) and client.get("system_check") is True


async def _serve_probe(websocket: WebSocket, ctx: SessionContext) -> str:
    """Answer a system-check probe: heartbeat -> heartbeat.ack (round-trip time)
    until the client closes, ``PROBE_MAX_SECONDS`` pass or it sends too much.
    Nothing reaches the session runtime, the reconnect counters or integrity."""
    settings = get_settings()
    if settings.acknowledgment_enabled and not settings.is_test:
        with contextlib.suppress(Exception):  # warm the session voice for the interview to come
            from app.ai.registry import get_ai
            from app.realtime.acknowledgements import get_acknowledgement_cache, opener_phrases

            voice = ctx.policy.voice or settings.tts_voice
            cache = get_acknowledgement_cache()
            cache.warm_in_background(get_ai().tts, voice)
            cache.warm_openers(get_ai().tts, voice, opener_phrases(ctx.candidate_first_name))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_MAX_SECONDS
    for _ in range(PROBE_MAX_MESSAGES):
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
        except TimeoutError:
            break
        if message.get("type") == "websocket.disconnect":
            return "client_closed"
        text = message.get("text")
        if text is None or len(text) > MAX_TEXT_BYTES:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "heartbeat":
            t = data.get("t")
            ack = HeartbeatAckMsg(
                t=float(t) if isinstance(t, int | float) else 0.0, server_time=time.time() * 1000
            )
            await websocket.send_text(ack.model_dump_json())
    await _close(websocket, close_codes.NORMAL)
    return "probe_done"


def _validation_summary(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"loc": [str(p) for p in err.get("loc", ())][:6], "msg": str(err.get("msg", ""))[:200]}
        for err in exc.errors()[:5]
    ]


@router.websocket("/ws/sessions/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str) -> None:
    settings = get_settings()
    origin = websocket.headers.get("origin")
    if not origin_allowed(
        origin,
        host=websocket.headers.get("host"),
        public_base_url=settings.public_base_url,
        cors_allowed_origins=settings.cors_allowed_origins,
        allow_missing=settings.app_env not in ("production", "staging"),
    ):
        log.warning("realtime.origin_rejected", origin=(origin or "")[:200], session_id=session_id[:64])
        await websocket.close(code=close_codes.FORBIDDEN_ORIGIN)
        return

    ticket_value = websocket.query_params.get("ticket")
    await websocket.accept()
    WS_CONNECTIONS.inc()
    transport = WebSocketTransport(websocket)
    runtime: SessionRuntime | None = None
    reason = "client_closed"
    try:
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            await _send_error(websocket, "session_not_found", "Unknown interview session.")
            await _close(websocket, close_codes.SESSION_NOT_FOUND)
            return
        ticket = await consume_ws_ticket(ticket_value)
        if ticket is None or ticket.session_id != str(sid):
            await _send_error(
                websocket, "unauthorized", "Your connection ticket is invalid or expired. Please reconnect."
            )
            await _close(websocket, close_codes.UNAUTHORIZED)
            return
        ctx = await load_session_context(sid)
        if ctx is None or ctx.candidate_id != ticket.candidate_id or ctx.interview_id != ticket.interview_id:
            await _send_error(websocket, "session_not_found", "Unknown interview session.")
            await _close(websocket, close_codes.SESSION_NOT_FOUND)
            return
        if ctx.ended:
            with contextlib.suppress(Exception):
                await websocket.send_text(
                    SessionEndedMsg(
                        reason=ctx.end_reason or ctx.status.value, report_status="pending"
                    ).model_dump_json()
                )
            await _close(websocket, close_codes.SESSION_ENDED)
            return
        if (
            ctx.interview_status in (InterviewStatus.CANCELLED.value, InterviewStatus.EXPIRED.value)
            or ctx.invite_expired
        ):
            await _send_error(websocket, "interview_unavailable", "This interview is no longer available.")
            await _close(websocket, close_codes.SESSION_ENDED)
            return

        hello, probe = await _receive_hello(websocket)
        if hello is None:
            await _send_error(websocket, "hello_required", "The first message must be a valid hello.")
            await _close(websocket, close_codes.BAD_REQUEST)
            return
        if probe:
            reason = await _serve_probe(websocket, ctx)
            log.info("realtime.system_check_probe", session_id=str(sid), reason=reason)
            return

        registry = get_registry()
        try:
            runtime = await registry.get_or_create(ctx, settings=settings)
        except SessionActiveElsewhere:
            await _send_error(
                websocket, "session_active_elsewhere", "This interview is open in another window or device."
            )
            await _close(websocket, close_codes.ACTIVE_ELSEWHERE)
            return
        except AppError as exc:
            await _send_error(websocket, exc.code, exc.message, recoverable=True)
            await _close(websocket, close_codes.INTERNAL_ERROR)
            return
        try:
            attached = await runtime.attach(transport, hello)
        except RuntimeClosed:
            await _send_error(websocket, "session_restarting", "Please reconnect.", recoverable=True)
            await _close(websocket, close_codes.SERVICE_RESTART)
            return
        if not attached:
            runtime = None
            return
        reason = await _receive_loop(websocket, transport, runtime)
    except WebSocketDisconnect:
        reason = "client_closed"
    except Exception as exc:
        ERRORS.labels(component="realtime_ws").inc()
        log.error(
            "realtime.ws_error",
            session_id=session_id[:64],
            error=f"{type(exc).__name__}: {exc}",
            exc_info=exc,
        )
        reason = "server_error"
        await _send_error(
            websocket, "internal_error", "Something went wrong. Reconnecting...", recoverable=True
        )
        await _close(websocket, close_codes.INTERNAL_ERROR)
    finally:
        WS_CONNECTIONS.dec()
        if runtime is not None:
            with contextlib.suppress(Exception):
                await runtime.detach(transport, reason=reason)


def make_limits() -> ConnectionLimits:
    """Per-connection limits (module-level so tests can relax them)."""
    return ConnectionLimits()


async def _receive_loop(websocket: WebSocket, transport: WebSocketTransport, runtime: SessionRuntime) -> str:
    limits = make_limits()
    last_error_notice = 0.0
    last_drop_log = 0.0
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return "client_closed"
        if transport.closed or runtime.closed:
            return "server_closed"
        text = message.get("text")
        if text is not None:
            if len(text) > MAX_TEXT_BYTES:
                await _send_error(websocket, "message_too_big", "Message too large.")
                await transport.close(close_codes.MESSAGE_TOO_BIG)
                return "message_too_big"
            try:
                data = json.loads(text)
            except ValueError:
                runtime.send_error("invalid_json", "Messages must be JSON.", recoverable=True)
                continue
            msg_type = data.get("type") if isinstance(data, dict) else None
            if not isinstance(msg_type, str):
                runtime.send_error("invalid_message", "Messages need a type.", recoverable=True)
                continue
            if not limits.allow_message(msg_type):
                RATE_LIMITED.labels(scope="ws_message").inc()
                if limits.abusive:
                    await _send_error(websocket, "rate_limited", "Too many messages.")
                    await transport.close(close_codes.RATE_LIMITED)
                    return "rate_limited"
                now = time.monotonic()
                if now - last_error_notice > ERROR_NOTICE_INTERVAL_S:
                    last_error_notice = now
                    runtime.send_error(
                        "rate_limited",
                        f"Too many '{msg_type[:40]}' messages; some were dropped.",
                        recoverable=True,
                    )
                continue
            try:
                parsed = client_message_adapter.validate_python(data)
            except ValidationError as exc:
                runtime.post(
                    ErrorMsg(
                        code="invalid_message",
                        message=f"Invalid '{msg_type[:40]}' message: {_validation_summary(exc)}"[:500],
                    )
                )
                continue
            await runtime.handle_message(parsed)
            continue
        data_bytes = message.get("bytes")
        if data_bytes is not None:
            if len(data_bytes) > MAX_BINARY_BYTES:
                await _send_error(websocket, "message_too_big", "Audio frame too large.")
                await transport.close(close_codes.MESSAGE_TOO_BIG)
                return "message_too_big"
            if not limits.allow_frame(len(data_bytes)):
                RATE_LIMITED.labels(scope="ws_audio").inc()
                if limits.abusive:
                    await _send_error(websocket, "rate_limited", "Too much audio data.")
                    await transport.close(close_codes.RATE_LIMITED)
                    return "rate_limited"
                now = time.monotonic()
                if now - last_drop_log > 5.0:
                    last_drop_log = now
                    log.warning(
                        "realtime.audio_rate_limited",
                        session_id=runtime.session_id,
                        dropped=limits.dropped_frames,
                    )
                continue
            await runtime.handle_audio(data_bytes)


# ------------------------------------------------------------------ lifecycle


@on_startup("realtime.warm")
async def _warm() -> None:
    """Load the VAD model and pre-synthesize acknowledgements in the background."""
    settings = get_settings()

    async def warm() -> None:
        from app.ai.registry import get_ai
        from app.realtime.acknowledgements import get_acknowledgement_cache
        from app.realtime.audio.vad import create_vad

        try:
            await asyncio.to_thread(create_vad, settings.vad_engine, settings.vad_model_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("realtime.vad_warm_failed", error=type(exc).__name__)
        if settings.acknowledgment_enabled and not settings.is_test:
            with contextlib.suppress(Exception):
                get_acknowledgement_cache().warm_in_background(get_ai().tts, settings.tts_voice)

    task = asyncio.create_task(warm(), name="realtime.warm")
    _background.add(task)
    task.add_done_callback(_background.discard)


@on_shutdown("realtime.shutdown")
async def _shutdown() -> None:
    for task in list(_background):
        task.cancel()
    _background.clear()
    await get_registry().shutdown_all()


_background: set[asyncio.Task[Any]] = set()
