"""OpenTelemetry tracing (opt-in with ``OTEL_ENABLED=true``).

When enabled, spans for FastAPI requests, outgoing httpx calls (model
endpoints, tts, sandbox), SQLAlchemy and Redis are exported over OTLP/HTTP to
``OTEL_EXPORTER_OTLP_ENDPOINT``. When disabled every function here is a no-op
and no OpenTelemetry module is imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)

_provider: Any = None
_instrumented_clients = False


def tracing_enabled() -> bool:
    return bool(get_settings().otel_enabled)


def _traces_endpoint(base: str | None) -> str | None:
    if not base:
        return None
    base = base.rstrip("/")
    return base if base.endswith("/v1/traces") else f"{base}/v1/traces"


def setup_tracing(app: FastAPI | None = None, *, service_name: str | None = None) -> bool:
    """Configure the tracer provider and instrument libraries; returns whether tracing is active.

    Must be called before the ASGI app starts serving (it adds middleware).
    """
    global _provider, _instrumented_clients
    settings = get_settings()
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover - deps are in pyproject
        log.warning("tracing.unavailable", error=str(exc))
        return False

    if _provider is None:
        resource = Resource.create(
            {
                "service.name": service_name or settings.otel_service_name,
                "service.instance.id": settings.instance_id,
                "deployment.environment": settings.app_env,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=_traces_endpoint(settings.otel_exporter_otlp_endpoint))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _provider = provider

    if not _instrumented_clients:
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            from opentelemetry.instrumentation.redis import RedisInstrumentor
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            from app.db.session import get_engine

            HTTPXClientInstrumentor().instrument(tracer_provider=_provider)
            RedisInstrumentor().instrument(tracer_provider=_provider)
            SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine, tracer_provider=_provider)
            _instrumented_clients = True
        except Exception as exc:  # instrumentation must never take the service down
            log.warning("tracing.instrumentation_failed", error=str(exc))

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(
                app, tracer_provider=_provider, excluded_urls="healthz,readyz,metrics"
            )
        except Exception as exc:
            log.warning("tracing.fastapi_instrumentation_failed", error=str(exc))
    log.info("tracing.enabled", endpoint=_traces_endpoint(settings.otel_exporter_otlp_endpoint))
    return True


def shutdown_tracing() -> None:
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception as exc:
        log.warning("tracing.shutdown_failed", error=str(exc))
    _provider = None
