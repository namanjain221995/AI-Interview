"""Small adapters around modules built concurrently (scoring, coding).

The engine depends only on the contracts:

* scoring - ``ScoringApi.observations_from_evaluation`` /
  ``compute_competency_states`` (pure) and
  ``recompute_session_scores(db, session_id, *, final)`` (persistence);
* coding - ``CodingService`` (contracts.py) and
  ``coding.observer.decide(context, trigger, state) -> ObservationDecision | None``.

Implementations are discovered lazily (so import order never matters) and can
be injected explicitly (``set_scoring`` / ``set_coding``) by the integrator or
by tests. When the scoring package exposes no implementation the engine uses
:class:`~app.services.interview.scoring_fallback.FallbackScoring`; when no
coding service exists the coding segment is skipped gracefully.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.services.interview.scoring_fallback import FallbackScoring

log = get_logger(__name__)

RecomputeFn = Callable[..., Awaitable[Any]]
ObserverFn = Callable[..., Any]


@dataclass(slots=True)
class ScoringPort:
    api: Any  # ScoringApi-compatible object (pure math)
    recompute: RecomputeFn | None  # recompute_session_scores(db, session_id, *, final)
    source: str = "fallback"


@dataclass(slots=True)
class CodingPort:
    service: Any | None  # CodingService-compatible
    observer: ObserverFn | None  # decide(context, trigger, state)
    source: str = "none"

    @property
    def available(self) -> bool:
        return self.service is not None


_scoring: ScoringPort | None = None
_coding: CodingPort | None = None


def _try_import(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 - a broken sibling module must not break the engine
        log.warning("engine.adapter_import_failed", module=name, error=f"{type(exc).__name__}: {exc}"[:200])
        return None


def _has_api(obj: Any) -> bool:
    return callable(getattr(obj, "observations_from_evaluation", None)) and callable(
        getattr(obj, "compute_competency_states", None)
    )


def discover_scoring() -> ScoringPort:
    api: Any = None
    recompute: RecomputeFn | None = None
    source = "fallback"
    for name in (
        "app.services.scoring",
        "app.services.scoring.engine",
        "app.services.scoring.service",
        "app.services.scoring.api",
        "app.services.scoring.math",
        "app.services.scoring.core",
        "app.services.scoring.persistence",
        "app.services.scoring.recompute",
    ):
        module = _try_import(name)
        if module is None:
            continue
        if api is None:
            for factory in ("get_scoring_api", "get_scoring", "scoring_api"):
                candidate = getattr(module, factory, None)
                if callable(candidate) and not inspect.isclass(candidate):
                    try:
                        value = candidate()
                    except TypeError:
                        continue
                    if _has_api(value):
                        api, source = value, f"{name}.{factory}"
                        break
                elif candidate is not None and _has_api(candidate):
                    api, source = candidate, f"{name}.{factory}"
                    break
            if api is None and _has_api(module):
                api, source = module, name
        if recompute is None:
            fn = getattr(module, "recompute_session_scores", None)
            if callable(fn) and inspect.iscoroutinefunction(fn):
                recompute = fn
    if api is None:
        api = FallbackScoring()
    return ScoringPort(api=api, recompute=recompute, source=source)


def get_scoring() -> ScoringPort:
    global _scoring
    if _scoring is None:
        _scoring = discover_scoring()
        log.info("engine.scoring_adapter", source=_scoring.source, recompute=_scoring.recompute is not None)
    return _scoring


def set_scoring(port: ScoringPort | None) -> None:
    global _scoring
    _scoring = port


def discover_coding() -> CodingPort:
    service: Any | None = None
    observer: ObserverFn | None = None
    source = "none"
    for name in ("app.services.coding", "app.services.coding.service"):
        module = _try_import(name)
        if module is None:
            continue
        for factory in ("get_coding_service", "coding_service"):
            fn = getattr(module, factory, None)
            if callable(fn) and not inspect.isclass(fn):
                try:
                    service = fn()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "engine.coding_service_factory_failed",
                        factory=f"{name}.{factory}",
                        error=str(exc)[:200],
                    )
                    service = None
                if service is not None:
                    source = f"{name}.{factory}"
                    break
        if service is not None:
            break
        for cls_name in ("CodingServiceImpl", "DefaultCodingService", "CodingServiceImplementation"):
            cls = getattr(module, cls_name, None)
            if inspect.isclass(cls):
                try:
                    service = cls()
                    source = f"{name}.{cls_name}"
                    break
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "engine.coding_service_init_failed", cls=f"{name}.{cls_name}", error=str(exc)[:200]
                    )
        if service is not None:
            break
    observer_module = _try_import("app.services.coding.observer")
    if observer_module is not None:
        fn = getattr(observer_module, "decide", None)
        if callable(fn):
            observer = fn
    return CodingPort(service=service, observer=observer, source=source)


def get_coding() -> CodingPort:
    global _coding
    if _coding is None or (_coding.service is None and _coding.source == "none"):
        _coding = discover_coding()
        if _coding.service is not None:
            log.info("engine.coding_adapter", source=_coding.source, observer=_coding.observer is not None)
    return _coding


def set_coding(port: CodingPort | None) -> None:
    global _coding
    _coding = port


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "CodingPort",
    "ScoringPort",
    "discover_coding",
    "discover_scoring",
    "get_coding",
    "get_scoring",
    "maybe_await",
    "set_coding",
    "set_scoring",
]
