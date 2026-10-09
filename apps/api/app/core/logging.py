"""Structured logging (structlog -> JSON lines on stdout).

Context variables (request_id, session_id, organization_id, user/candidate
id) are bound with :func:`bind_context` and appear on every log line emitted
in that async context.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_configured = False

# Defence in depth: values of keys that look like credentials are masked even
# if a caller accidentally logs them (e.g. ``log.info("x", password=...)``).
# Matches "password", "jwt_secret", "csrf_token", "access_code", ... but not
# counters such as "prompt_tokens" or "max_tokens".
_SENSITIVE_KEY = re.compile(
    r"^(?:.*_)?(?:password|passwd|pwd|secret|authorization|cookies?|api_?key|access_code|credentials?|token)$",
    re.IGNORECASE,
)


def redact_sensitive(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key != "event" and _SENSITIVE_KEY.match(key):
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    global _configured
    if _configured:
        return
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        redact_sensitive,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        renderer,
    ])
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_context(**values: Any) -> None:
    structlog.contextvars.bind_contextvars(**{k: v for k, v in values.items() if v is not None})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_request_id() -> str | None:
    """Request id bound by the HTTP middleware for the current async context."""
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None
