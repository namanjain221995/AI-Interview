"""Application error hierarchy and the FastAPI exception handlers.

Every error response uses one envelope::

    {"error": {"code": "not_found", "message": "...", "request_id": "...", "details": {...}}}

Domain code raises :class:`AppError` subclasses; framework errors
(``HTTPException``, request validation, unique-constraint violations) are
mapped onto the same envelope. Unhandled exceptions are converted to a 500 by
:class:`app.core.middleware.RequestContextMiddleware`, which calls
:func:`internal_error_response` so internals never leak to clients.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)

_HTTP_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    410: "gone",
    411: "length_required",
    413: "payload_too_large",
    415: "unsupported_media_type",
    416: "range_not_satisfiable",
    422: "validation_failed",
    423: "locked",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}

# Postgres SQLSTATEs that are client-caused conflicts rather than server bugs.
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"
_MAX_VALIDATION_ERRORS = 50


class AppError(Exception):
    """Base class for errors that are safe to show to API clients."""

    status_code: int = 400
    code: str = "bad_request"
    message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details: dict[str, Any] = dict(details or {})
        self.headers: dict[str, str] = dict(headers or {})
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"
    message = "The request is malformed."


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication is required."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        merged = {"WWW-Authenticate": "Bearer"}
        merged.update(headers or {})
        super().__init__(message, code=code, details=details, headers=merged)


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class UnsupportedRoundType(Conflict):
    """A stored round type this build does not know (a newer release wrote it, e.g. before a rollback).

    Readers raise it instead of crashing with a 500; background jobs do not retry it."""

    code = "unsupported_round_type"
    message = "This round type is not supported by this version of the platform."
    retryable = False


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The request payload is too large."


class UnsupportedMediaType(AppError):
    status_code = 415
    code = "unsupported_media_type"
    message = "The media type is not supported."


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
    message = "The request is invalid."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please retry later."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: float = 1.0,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.retry_after = max(1, math.ceil(retry_after))
        merged_details = {"retry_after_seconds": self.retry_after, **dict(details or {})}
        merged_headers = {"Retry-After": str(self.retry_after), **dict(headers or {})}
        super().__init__(message, code=code, details=merged_details, headers=merged_headers)


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "The service is temporarily unavailable."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: float | None = None,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        merged_headers = dict(headers or {})
        self.retry_after = None if retry_after is None else max(1, math.ceil(retry_after))
        if self.retry_after is not None:
            merged_headers.setdefault("Retry-After", str(self.retry_after))
        super().__init__(message, code=code, details=details, headers=merged_headers)


# ------------------------------------------------------------------ helpers


def current_request_id(request: Request | None = None) -> str | None:
    """Request id of the current request (middleware state or log context)."""
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return str(rid)
    rid = structlog.contextvars.get_contextvars().get("request_id")
    return str(rid) if rid else None


def error_payload(code: str, message: str, request_id: str | None, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id, "details": dict(details or {})}}


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str | None,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_payload(code, message, request_id, details),
        status_code=status_code,
        headers=dict(headers or {}),
    )


def internal_error_response(request_id: str | None) -> JSONResponse:
    return error_response(500, "internal_error", "An unexpected error occurred.", request_id=request_id)


def code_for_status(status_code: int) -> str:
    return _HTTP_STATUS_CODES.get(status_code, f"http_{status_code}")


def sanitize_validation_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Strip ``input``/``ctx`` (may echo passwords or tokens) from pydantic errors."""
    cleaned: list[dict[str, Any]] = []
    for err in list(errors)[:_MAX_VALIDATION_ERRORS]:
        cleaned.append(
            {
                "loc": [p if isinstance(p, int) else str(p) for p in err.get("loc", ())],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return cleaned


def _sqlstate(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        if candidate is None:
            continue
        state = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if state:
            return str(state)
    return None


# ----------------------------------------------------------------- handlers


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        log.warning("http.app_error", code=exc.code, status=exc.status_code, message=exc.message)
    return error_response(
        exc.status_code,
        exc.code,
        exc.message,
        request_id=current_request_id(request),
        details=exc.details,
        headers=exc.headers,
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Request failed."
    details: dict[str, Any] = {}
    if not isinstance(exc.detail, str) and exc.detail is not None:
        details["detail"] = exc.detail
    return error_response(
        exc.status_code,
        code_for_status(exc.status_code),
        message,
        request_id=current_request_id(request),
        details=details,
        headers=dict(exc.headers or {}),
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(
        422,
        "validation_failed",
        "The request is invalid.",
        request_id=current_request_id(request),
        details={"errors": sanitize_validation_errors(exc.errors())},
    )


async def _integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IntegrityError)
    state = _sqlstate(exc)
    if state == _UNIQUE_VIOLATION:
        log.info("http.unique_violation")
        return error_response(
            409, "conflict", "A resource with the same unique value already exists.", request_id=current_request_id(request)
        )
    if state == _FOREIGN_KEY_VIOLATION:
        log.info("http.foreign_key_violation")
        return error_response(
            409, "conflict", "The request references a resource that does not exist or is still in use.",
            request_id=current_request_id(request),
        )
    # Any other integrity failure is a server bug: let the middleware render a 500.
    raise exc


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
