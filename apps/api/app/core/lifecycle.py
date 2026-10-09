"""Process lifecycle hooks shared by the API and the worker.

Modules register hooks at import time; the API lifespan (``app.main``) and the
worker runtime (``app.worker``) run the hooks registered for their process
kind. Hooks registered from a router module are picked up because routers are
auto-imported; hooks in ``app.workers.*`` modules because the worker
auto-imports that package.

    from app.core.lifecycle import background_task, on_shutdown, on_startup

    @on_startup("realtime.warm_vad")                       # api only (default)
    async def warm_vad() -> None: ...

    @background_task("realtime.bus_listener", processes=("api",))
    async def bus_listener(stop: asyncio.Event) -> None:   # runs until stop is set
        while not stop.is_set(): ...

    @on_shutdown("realtime.flush", processes=("api", "worker"))
    async def flush() -> None: ...

Startup hooks run in registration order, shutdown hooks in reverse order.
Background tasks are supervised: a crash is logged and the task restarted with
exponential backoff until shutdown. Re-registering a name replaces the hook.

Transaction hooks: :func:`run_after_commit` defers side effects (Redis stream
dispatch, cache invalidation) until a SQLAlchemy session's transaction has
committed, and drops them on rollback. ``app.core.deps.get_db`` awaits them
before the HTTP response is sent; elsewhere :func:`drain_after_commit` awaits
them explicitly.

Process helpers used by ``app.main`` / ``app.worker`` / ``app.seed``:
:func:`import_submodules` (package auto-discovery), :func:`optional_import`
(modules that may not exist yet) and :func:`init_ai_registry` /
:func:`shutdown_ai_registry` (``app.ai.registry``; skipped in the test env).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import pkgutil
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import event

from app.core.logging import get_logger
from app.core.metrics import ERRORS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

log = get_logger(__name__)

# ------------------------------------------------------------ transactions

_PENDING = "lifecycle.after_commit.pending"
_TASKS = "lifecycle.after_commit.tasks"
_HOOKED = "lifecycle.after_commit.hooked"
_orphan_tasks: set[asyncio.Task[Any]] = set()


async def _run_callback(callback: Callable[[], Awaitable[None]]) -> None:
    try:
        await callback()
    except Exception as exc:
        ERRORS.labels(component="after_commit").inc()
        log.error("lifecycle.after_commit_failed", error=str(exc), exc_info=exc)


def _fire_after_commit(session: Session) -> None:
    callbacks: list[Callable[[], Awaitable[None]]] = session.info.pop(_PENDING, [])
    if not callbacks:
        return
    loop = asyncio.get_running_loop()
    tasks: list[asyncio.Task[Any]] = session.info.setdefault(_TASKS, [])
    for callback in callbacks:
        task = loop.create_task(_run_callback(callback))
        tasks.append(task)
        _orphan_tasks.add(task)
        task.add_done_callback(_orphan_tasks.discard)


def _drop_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


def run_after_commit(db: AsyncSession, callback: Callable[[], Awaitable[None]]) -> None:
    """Run ``callback()`` once the current transaction of ``db`` commits."""
    sync_session = db.sync_session
    if not sync_session.info.get(_HOOKED):
        event.listen(sync_session, "after_commit", _fire_after_commit)
        event.listen(sync_session, "after_rollback", _drop_after_rollback)
        sync_session.info[_HOOKED] = True
    sync_session.info.setdefault(_PENDING, []).append(callback)


async def drain_after_commit(db: AsyncSession | None = None) -> None:
    """Await callbacks fired by commits (of ``db``, or of every session)."""
    if db is not None:
        tasks = db.sync_session.info.pop(_TASKS, [])
    else:
        tasks = list(_orphan_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

ProcessKind = Literal["api", "worker"]
StartupFn = Callable[[], Awaitable[None]]
BackgroundFn = Callable[[asyncio.Event], Awaitable[None]]


@dataclass(slots=True)
class _Hook:
    name: str
    kind: Literal["startup", "shutdown", "background"]
    processes: frozenset[str]
    fn: Callable[..., Awaitable[None]]


@dataclass(slots=True)
class _Registry:
    hooks: dict[str, _Hook] = field(default_factory=dict)


_registry = _Registry()


def _validate_processes(name: str, processes: Iterable[str]) -> frozenset[str]:
    procs = frozenset(processes)
    unknown = procs - {"api", "worker"}
    if unknown or not procs:
        raise ValueError(f"Invalid process kind(s) {sorted(unknown) or '[]'} for hook {name!r}")
    return procs


def _register(name: str, kind: Literal["startup", "shutdown", "background"], processes: Iterable[str], fn: Callable[..., Awaitable[None]]) -> None:
    procs = _validate_processes(name, processes)
    key = f"{kind}:{name}"
    _registry.hooks.pop(key, None)  # keep insertion order = latest registration
    _registry.hooks[key] = _Hook(name=name, kind=kind, processes=procs, fn=fn)


def on_startup(name: str, *, processes: Iterable[ProcessKind] = ("api",)) -> Callable[[StartupFn], StartupFn]:
    processes = _validate_processes(name, processes)  # type: ignore[assignment]

    def decorator(fn: StartupFn) -> StartupFn:
        _register(name, "startup", processes, fn)
        return fn

    return decorator


def on_shutdown(name: str, *, processes: Iterable[ProcessKind] = ("api",)) -> Callable[[StartupFn], StartupFn]:
    processes = _validate_processes(name, processes)  # type: ignore[assignment]

    def decorator(fn: StartupFn) -> StartupFn:
        _register(name, "shutdown", processes, fn)
        return fn

    return decorator


def background_task(name: str, *, processes: Iterable[ProcessKind] = ("api",)) -> Callable[[BackgroundFn], BackgroundFn]:
    processes = _validate_processes(name, processes)  # type: ignore[assignment]

    def decorator(fn: BackgroundFn) -> BackgroundFn:
        _register(name, "background", processes, fn)
        return fn

    return decorator


def unregister(name: str) -> None:
    for kind in ("startup", "shutdown", "background"):
        _registry.hooks.pop(f"{kind}:{name}", None)


def registered_hooks(process: ProcessKind | None = None) -> list[tuple[str, str]]:
    return [(h.kind, h.name) for h in _registry.hooks.values() if process is None or process in h.processes]


# ------------------------------------------------------- module discovery


def import_submodules(
    package_name: str,
    *,
    strict: bool,
    skip: Callable[[str], bool] | None = None,
) -> list[ModuleType]:
    """Import every direct submodule of ``package_name`` (sorted by name).

    In strict mode (production/staging) an import error aborts startup; in
    development/test it is logged and the module skipped, so a module that is
    still being written cannot take the whole process down.
    """
    package = importlib.import_module(package_name)
    modules: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(getattr(package, "__path__", [])), key=lambda m: m.name):
        if info.name.startswith("_") or (skip is not None and skip(info.name)):
            continue
        qualified = f"{package_name}.{info.name}"
        try:
            modules.append(importlib.import_module(qualified))
        except Exception as exc:
            ERRORS.labels(component="startup").inc()
            log.error("lifecycle.module_import_failed", module=qualified, error=f"{type(exc).__name__}: {exc}", exc_info=exc)
            if strict:
                raise
    return modules


def optional_import(module_name: str, *, strict: bool) -> ModuleType | None:
    """Import ``module_name`` if it exists (``None`` when the module itself is absent)."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name or (exc.name and module_name.startswith(exc.name + ".")):
            log.info("lifecycle.optional_module_absent", module=module_name)
            return None
        log.error("lifecycle.module_import_failed", module=module_name, error=str(exc), exc_info=exc)
        if strict:
            raise
        return None
    except Exception as exc:
        ERRORS.labels(component="startup").inc()
        log.error("lifecycle.module_import_failed", module=module_name, error=f"{type(exc).__name__}: {exc}", exc_info=exc)
        if strict:
            raise
        return None


# ------------------------------------------------------------- AI registry


async def init_ai_registry(*, strict: bool, is_test: bool) -> bool:
    """Start ``app.ai.registry`` when it exists.

    Skipped in the ``test`` environment: there ``get_ai()`` falls back to the
    mock services (or whatever a fixture installed with ``set_ai``) instead of
    building real clients for the shared GPU endpoints.
    """
    if is_test:
        return False
    registry = optional_import("app.ai.registry", strict=strict)
    init = getattr(registry, "init_ai", None) if registry is not None else None
    if init is None:
        if registry is None:
            log.warning("lifecycle.ai_registry_unavailable")
        return False
    try:
        result = init()
        if inspect.isawaitable(result):
            await result
        return True
    except Exception as exc:
        ERRORS.labels(component="startup").inc()
        log.error("lifecycle.ai_init_failed", error=f"{type(exc).__name__}: {exc}", exc_info=exc)
        if strict:
            raise
        return False


async def shutdown_ai_registry() -> None:
    registry = optional_import("app.ai.registry", strict=False)
    shutdown = getattr(registry, "shutdown_ai", None) if registry is not None else None
    if shutdown is None:
        return
    try:
        result = shutdown()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        log.warning("lifecycle.ai_shutdown_failed", error=f"{type(exc).__name__}: {exc}")


class LifecycleRunner:
    """Runs the hooks of one process kind. ``strict`` re-raises startup failures."""

    def __init__(self, process: ProcessKind, *, strict: bool = False, shutdown_timeout: float = 10.0) -> None:
        self.process = process
        self.strict = strict
        self.shutdown_timeout = shutdown_timeout
        self._stop = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._started: list[_Hook] = []

    def _hooks(self, kind: str) -> list[_Hook]:
        return [h for h in _registry.hooks.values() if h.kind == kind and self.process in h.processes]

    async def start(self) -> None:
        for hook in self._hooks("startup"):
            try:
                await hook.fn()
                self._started.append(hook)
                log.info("lifecycle.startup_hook", hook=hook.name, process=self.process)
            except Exception as exc:
                ERRORS.labels(component=f"{self.process}_lifecycle").inc()
                log.error("lifecycle.startup_hook_failed", hook=hook.name, error=str(exc), exc_info=exc)
                if self.strict:
                    raise
        for hook in self._hooks("background"):
            self._tasks[hook.name] = asyncio.create_task(self._supervise(hook), name=f"bg:{hook.name}")

    async def _supervise(self, hook: _Hook) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await hook.fn(self._stop)
                if self._stop.is_set():
                    return
                log.warning("lifecycle.background_task_returned", hook=hook.name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ERRORS.labels(component=f"{self.process}_lifecycle").inc()
                log.error("lifecycle.background_task_crashed", hook=hook.name, error=str(exc), exc_info=exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            delay = min(delay * 2, 60.0)

    async def stop(self) -> None:
        self._stop.set()
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks.values(), timeout=self.shutdown_timeout)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._tasks.clear()
        for hook in reversed(self._hooks("shutdown")):
            try:
                await hook.fn()
            except Exception as exc:
                log.error("lifecycle.shutdown_hook_failed", hook=hook.name, error=str(exc), exc_info=exc)
