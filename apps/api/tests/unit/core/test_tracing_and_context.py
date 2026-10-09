from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.middleware import install_middleware
from app.core.tracing import setup_tracing, tracing_enabled
from app.main import is_test_hooks_module

API_ROOT = Path(__file__).resolve().parents[3]

_TRACING_PROBE = """
import asyncio, httpx
from app.core import tracing
from app.main import create_app

app = create_app()
assert tracing._provider is not None, "tracer provider not configured"
from opentelemetry import trace
assert trace.get_tracer_provider() is tracing._provider

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
        assert r.status_code == 200
asyncio.run(main())
tracing.shutdown_tracing()
print("TRACING_OK")
"""


def test_tracing_disabled_is_a_noop():
    assert not tracing_enabled()
    assert setup_tracing(FastAPI()) is False


def test_tracing_enabled_instruments_without_crashing():
    env = dict(os.environ, OTEL_ENABLED="true", OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:9", LOG_JSON="true")
    result = subprocess.run(
        [sys.executable, "-c", _TRACING_PROBE], cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "TRACING_OK" in result.stdout


def test_test_hook_module_names():
    assert is_test_hooks_module("test_hooks")
    assert is_test_hooks_module("test_hooks_interview")
    assert is_test_hooks_module("coding_test_hooks")
    assert not is_test_hooks_module("testimonials")
    assert not is_test_hooks_module("auth")


async def test_request_log_context_does_not_leak():
    app = FastAPI()
    register_exception_handlers(app)
    install_middleware(app, get_settings())

    @app.get("/bind")
    async def bind() -> dict[str, str]:
        structlog.contextvars.bind_contextvars(user_id="leaky-user")
        return {"ok": "1"}

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(outer="kept")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/bind")).status_code == 200
    context = structlog.contextvars.get_contextvars()
    structlog.contextvars.clear_contextvars()
    assert context == {"outer": "kept"}
