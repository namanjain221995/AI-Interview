"""HTTP middleware: request context, access log, metrics, security headers, CORS.

``RequestContextMiddleware`` is a pure ASGI middleware (no BaseHTTPMiddleware,
so streaming responses and background tasks are unaffected). For every HTTP
request it:

* accepts a well-formed inbound ``X-Request-ID`` or generates one, exposes it
  as ``request.state.request_id``, binds it to the log context and echoes it;
* emits one JSON access log line (method, route template, status,
  duration_ms) - never the raw path, query string, cookies or tokens, because
  paths such as ``/media/signed/{token}`` carry secrets;
* records ``interview_http_requests_total`` / ``interview_http_request_seconds``
  labelled by route template (unmatched paths share one label);
* adds security headers (nosniff, no-store, Referrer-Policy, frame and CSP
  restrictions) to every response;
* converts unhandled exceptions into the standard 500 envelope, logs them with
  the request id and counts ``interview_errors_total{component="api"}``.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import structlog
from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import internal_error_response
from app.core.logging import get_logger
from app.core.metrics import ERRORS, HTTP_REQUEST_SECONDS, HTTP_REQUESTS

log = get_logger("app.access")
error_log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
_DOCS_PREFIXES = ("/api/docs", "/api/redoc", "/api/openapi.json")
_UNMATCHED_ROUTE = "<unmatched>"

_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-site"),
)
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def _route_template(scope: Scope) -> str:
    """Full route template (``/api/v1/users/{user_id}``) of the matched route.

    FastAPI >= 0.14x keeps included routers un-flattened: ``scope["route"]`` is
    the inner route without the include prefix, while the effective context
    carries the full template. Fall back to the inner template otherwise.
    """
    fastapi_scope = scope.get("fastapi")
    if isinstance(fastapi_scope, dict):
        effective = fastapi_scope.get("effective_route_context")
        template = getattr(effective, "path_format", None)
        if template:
            return str(template)
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return str(template) if template else _UNMATCHED_ROUTE


def _restore_log_context(saved: dict[str, Any]) -> None:
    """Drop everything bound during the request (request id, user/candidate,
    organization) and restore the caller's context."""
    structlog.contextvars.clear_contextvars()
    if saved:
        structlog.contextvars.bind_contextvars(**saved)


def _inbound_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == b"x-request-id":
            candidate = value.decode("latin-1").strip()
            return candidate if _REQUEST_ID_PATTERN.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            request_id = _inbound_request_id(scope) or uuid.uuid4().hex
            scope.setdefault("state", {})["request_id"] = request_id
            saved = structlog.contextvars.get_contextvars()
            structlog.contextvars.bind_contextvars(request_id=request_id)
            try:
                await self.app(scope, receive, send)
            finally:
                _restore_log_context(saved)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        saved = structlog.contextvars.get_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        path: str = scope.get("path", "")
        is_docs = path.startswith(_DOCS_PREFIXES)
        method = scope.get("method", "GET").upper()
        method_label = method if method in _KNOWN_METHODS else "OTHER"
        state: dict[str, Any] = {"status": 500, "started": False}
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["started"] = True
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in _SECURITY_HEADERS:
                    headers.setdefault(name, value)
                headers.setdefault("Cache-Control", "no-store")
                if not is_docs:
                    headers.setdefault("Content-Security-Policy", _API_CSP)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            ERRORS.labels(component="api").inc()
            error_log.error(
                "http.unhandled_exception",
                method=method_label,
                route=_route_template(scope),
                error=type(exc).__name__,
                exc_info=exc,
            )
            if state["started"]:
                raise
            await internal_error_response(request_id)(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            route = _route_template(scope)
            status = int(state["status"])
            HTTP_REQUESTS.labels(method=method_label, route=route, status=str(status)).inc()
            HTTP_REQUEST_SECONDS.labels(method=method_label, route=route).observe(duration)
            fields = {
                "method": method_label,
                "route": route,
                "status": status,
                "duration_ms": round(duration * 1000, 2),
            }
            if path in _QUIET_PATHS and status < 400:
                log.debug("http.request", **fields)
            elif status >= 500:
                log.error("http.request", **fields)
            elif status >= 400:
                log.warning("http.request", **fields)
            else:
                log.info("http.request", **fields)
            _restore_log_context(saved)


def install_middleware(app: FastAPI, settings: Settings) -> None:
    """Install in order: request context (inner), then CORS (outermost) so that
    even error envelopes carry CORS headers for allowed origins."""
    app.add_middleware(RequestContextMiddleware)
    origins = [o.rstrip("/") for o in settings.cors_allowed_origins if o and o.strip() != "*"]
    if len(origins) != len(settings.cors_allowed_origins):
        error_log.warning("cors.wildcard_ignored", reason="wildcard origins are unsafe with credentials")
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Authorization", "Range"],
            expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition", "Content-Range", "Accept-Ranges"],
            max_age=600,
        )
