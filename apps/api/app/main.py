"""ASGI application factory (``uvicorn app.main:app``).

* Routers are auto-discovered: every module of ``app.api.routers`` exposing a
  ``router`` is included under ``/api/v1`` (test-hook modules only when
  ``ENABLE_TEST_HOOKS=true``); ``app.realtime.router.router`` (WebSocket
  ``/ws/...``) is included at the root when that module exists.
* ``/healthz``, ``/readyz`` and ``/metrics`` live at the root.
* OpenAPI: ``/api/docs`` and ``/api/openapi.json`` when ``ENABLE_API_DOCS``.
* Lifespan: logging, storage root, Redis, AI registry, lifecycle hooks
  registered by other modules (``app.core.lifecycle``), and orderly shutdown.

Production and staging are *strict*: a router or hook that fails to import or
start aborts startup. Development and test log the error and continue.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from app.api.routers.health import app_version, root_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.lifecycle import (
    LifecycleRunner,
    drain_after_commit,
    import_submodules,
    init_ai_registry,
    optional_import,
    shutdown_ai_registry,
)
from app.core.logging import configure_logging, get_logger
from app.core.middleware import install_middleware
from app.core.redis import close_redis, ping_redis
from app.core.tracing import setup_tracing, shutdown_tracing
from app.db.session import dispose_engine
from app.services.storage import get_storage
from app.services.storage.media import wait_for_cleanup

log = get_logger(__name__)

API_PREFIX = "/api/v1"
ROUTERS_PACKAGE = "app.api.routers"
REALTIME_ROUTER_MODULE = "app.realtime.router"


def is_test_hooks_module(name: str) -> bool:
    return name == "test_hooks" or name.startswith("test_hooks_") or name.endswith("_test_hooks")


def _strict(settings: Settings) -> bool:
    return settings.app_env in ("production", "staging")


def include_discovered_routers(app: FastAPI, settings: Settings) -> list[str]:
    """Include every router module; returns the included module names."""
    strict = _strict(settings)
    hooks_enabled = settings.enable_test_hooks and not settings.is_production
    included: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    modules = import_submodules(
        ROUTERS_PACKAGE, strict=strict, skip=lambda name: is_test_hooks_module(name) and not hooks_enabled
    )
    for module in modules:
        name = module.__name__.rsplit(".", 1)[-1]
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            if name != "health":
                log.warning("router.missing_router", module=name)
            continue
        if getattr(module, "TEST_HOOKS", False) and not hooks_enabled:
            continue
        for route in router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods or ():
                    key = (method, API_PREFIX + route.path)
                    if key in seen:
                        log.warning("router.duplicate_route", method=method, path=key[1], first=seen[key], second=name)
                    seen.setdefault(key, name)
        app.include_router(router, prefix=API_PREFIX)
        included.append(name)
    realtime = optional_import(REALTIME_ROUTER_MODULE, strict=strict)
    realtime_router = getattr(realtime, "router", None) if realtime is not None else None
    if isinstance(realtime_router, APIRouter):
        app.include_router(realtime_router)
        included.append("realtime")
    log.info("routers.included", modules=included)
    return included


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    strict = _strict(settings)
    configure_logging(settings.log_level, settings.log_json)
    log.info("api.starting", environment=settings.app_env, instance_id=settings.instance_id, version=app_version())
    await get_storage().ensure_root()
    if not await ping_redis():
        log.warning("api.redis_unavailable_at_startup")
    ai_started = await init_ai_registry(strict=strict, is_test=settings.is_test)
    runner = LifecycleRunner("api", strict=strict)
    app.state.lifecycle = runner
    await runner.start()
    log.info("api.started")
    try:
        yield
    finally:
        log.info("api.stopping")
        await runner.stop()
        if ai_started:
            await shutdown_ai_registry()
        await drain_after_commit()
        await wait_for_cleanup()
        await close_redis()
        await dispose_engine()
        shutdown_tracing()
        log.info("api.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    docs = settings.enable_api_docs
    app = FastAPI(
        title=settings.app_name,
        version=app_version(),
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
        swagger_ui_oauth2_redirect_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    register_exception_handlers(app)
    install_middleware(app, settings)
    app.include_router(root_router)
    include_discovered_routers(app, settings)
    setup_tracing(app)
    return app


app = create_app()
