"""Provider functions for the services the realtime runtime and the portal
API depend on.

The implementations are built by other modules (engine, coding, integrity,
resume). Each provider resolves its implementation lazily by convention and
caches it; the integrator can wire explicitly with ``set_*`` and tests
inject fakes with :func:`override_providers`::

    with override_providers(turn_engine=FakeEngine(), session_service=FakeSessions()):
        ...

Resolution order per provider: explicit override -> module-level factory
(``get_<name>()``) -> module-level instance -> a well-known class
instantiated without arguments -> the module itself when it exposes the
protocol's functions (module-as-service).
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.errors import ServiceUnavailable
from app.core.logging import get_logger
from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity.contracts import Disclosure, IntegrityOutcome

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Spec:
    name: str
    module: str
    factories: tuple[str, ...]
    instances: tuple[str, ...]
    classes: tuple[str, ...]
    functions: tuple[str, ...]  # module-as-service when all present


SPECS: dict[str, _Spec] = {
    "turn_engine": _Spec(
        "turn_engine",
        "app.services.interview.engine",
        ("get_turn_engine", "get_engine", "get_interview_engine"),
        ("engine", "turn_engine", "interview_engine"),
        ("InterviewEngine",),
        (),
    ),
    "session_service": _Spec(
        "session_service",
        "app.services.interview.sessions",
        ("get_session_service",),
        ("session_service", "sessions"),
        ("SessionServiceImpl", "InterviewSessionService", "DefaultSessionService", "Sessions"),
        ("create_or_resume", "candidate_view"),
    ),
    "coding_service": _Spec(
        "coding_service",
        "app.services.coding",
        ("get_coding_service",),
        ("coding_service", "service"),
        ("CodingServiceImpl", "DefaultCodingService", "SandboxCodingService"),
        ("start", "run", "record_update", "state_payload"),
    ),
    "integrity_service": _Spec(
        "integrity_service",
        "app.services.integrity",
        ("get_integrity_service",),
        ("integrity_service",),
        ("DefaultIntegrityService", "IntegrityServiceImpl"),
        ("record_client_events", "record_server_event", "submit_frame"),
    ),
    "resume_service": _Spec(
        "resume_service",
        "app.services.resume.service",
        ("get_resume_service",),
        ("resume_service", "service"),
        ("ResumeServiceImpl", "DefaultResumeService"),
        ("accept_upload", "progress", "latest_profile", "candidate_safe_summary"),
    ),
}

_lock = threading.Lock()
_overrides: dict[str, Any] = {}
_getters: dict[str, Callable[[], Any]] = {}
_missing_logged: set[str] = set()


def _resolve(spec: _Spec) -> Callable[[], Any] | None:
    """Find how to obtain the implementation. Module factories are called on
    every access (they own caching and their ``set_*`` overrides); instances
    built here from a class are cached."""
    try:
        module = importlib.import_module(spec.module)
    except ImportError as exc:
        if spec.name not in _missing_logged:
            _missing_logged.add(spec.name)
            log.warning(
                "realtime.provider.unavailable", provider=spec.name, module=spec.module, error=str(exc)[:200]
            )
        return None
    for factory in spec.factories:
        fn = getattr(module, factory, None)
        if callable(fn):
            return fn
    for name in spec.instances:
        obj = getattr(module, name, None)
        if (
            obj is not None
            and not isinstance(obj, type)
            and not _is_function(obj)
            and not inspect.ismodule(obj)
        ):
            return _const(obj)
    for cls_name in spec.classes:
        cls = getattr(module, cls_name, None)
        if isinstance(cls, type):
            try:
                instance = cls()
            except TypeError as exc:
                log.warning(
                    "realtime.provider.construct_failed",
                    provider=spec.name,
                    cls=cls_name,
                    error=str(exc)[:200],
                )
                continue
            return _const(instance)
    if spec.functions and all(callable(getattr(module, f, None)) for f in spec.functions):
        return _const(module)
    log.warning("realtime.provider.no_implementation", provider=spec.name, module=spec.module)
    return None


def _const(value: Any) -> Callable[[], Any]:
    def getter() -> Any:
        return value

    return getter


def _is_function(obj: Any) -> bool:
    return inspect.isfunction(obj) or inspect.ismethod(obj) or inspect.isbuiltin(obj)


def _get(name: str) -> Any | None:
    if name in _overrides:
        return _overrides[name]
    getter = _getters.get(name)
    if getter is None:
        with _lock:
            getter = _getters.get(name)
            if getter is None:
                getter = _resolve(SPECS[name])
                if getter is None:
                    return None
                _getters[name] = getter
    return getter()


def _required(name: str, label: str) -> Any:
    value = _get(name)
    if value is None:
        raise ServiceUnavailable(
            f"The {label} is not available yet.", code=f"{name}_unavailable", retry_after=5
        )
    return value


# --------------------------------------------------------------- providers


def get_turn_engine() -> Any:
    """``TurnEngine`` (``app.services.interview.contracts``). Raises 503 when missing."""
    return _required("turn_engine", "interview engine")


def get_session_service() -> Any:
    """``SessionService`` (create_or_resume / candidate_view). Raises 503 when missing."""
    return _required("session_service", "interview session service")


def get_coding_service() -> Any | None:
    """``CodingService`` or ``None`` (coding messages are then rejected)."""
    return _get("coding_service")


def get_resume_service() -> Any:
    return _required("resume_service", "resume service")


def get_integrity_service() -> Any:
    """``IntegrityService``; falls back to :class:`NullIntegrityService` so a
    missing integrity module never blocks an interview."""
    return _get("integrity_service") or _NULL_INTEGRITY


def get_disclosure_builder() -> Callable[..., Disclosure]:
    """``build_disclosure(policy_snapshot, *, organization_name, job_title) -> Disclosure``."""
    if "disclosure_builder" in _overrides:
        return _overrides["disclosure_builder"]
    try:
        module = importlib.import_module("app.services.integrity")
    except ImportError as exc:
        raise ServiceUnavailable(
            "The consent disclosure is not available yet.", code="disclosure_unavailable", retry_after=5
        ) from exc
    fn = getattr(module, "build_disclosure", None)
    if callable(fn):
        return fn
    builder_cls = getattr(module, "DisclosureBuilder", None) or getattr(
        module, "DefaultDisclosureBuilder", None
    )
    if isinstance(builder_cls, type):
        return builder_cls().build
    raise ServiceUnavailable(
        "The consent disclosure is not available yet.", code="disclosure_unavailable", retry_after=5
    )


def set_provider(name: str, value: Any) -> None:
    if name not in SPECS and name != "disclosure_builder":
        raise KeyError(name)
    _overrides[name] = value


def set_turn_engine(engine: Any) -> None:
    set_provider("turn_engine", engine)


def set_session_service(service: Any) -> None:
    set_provider("session_service", service)


def set_coding_service(service: Any) -> None:
    set_provider("coding_service", service)


def set_integrity_service(service: Any) -> None:
    set_provider("integrity_service", service)


def set_resume_service(service: Any) -> None:
    set_provider("resume_service", service)


def set_disclosure_builder(builder: Callable[..., Disclosure]) -> None:
    set_provider("disclosure_builder", builder)


def reset_providers() -> None:
    _overrides.clear()
    _getters.clear()
    _missing_logged.clear()


@contextlib.contextmanager
def override_providers(**providers: Any) -> Iterator[None]:
    """Temporarily replace providers (tests)."""
    previous = {k: _overrides.get(k, _MISSING) for k in providers}
    for key, value in providers.items():
        set_provider(key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is _MISSING:
                _overrides.pop(key, None)
            else:
                _overrides[key] = value


_MISSING = object()


# ------------------------------------------------------------- null service


class NullIntegrityService:
    """Logs signals when the integrity module is not installed. Never warns,
    pauses or terminates (no policy evaluation without the real module)."""

    async def record_client_events(
        self, session_id: str, events: Sequence[ClientIntegrityEvent]
    ) -> list[IntegrityOutcome]:
        log.info("realtime.integrity.null_events", session_id=session_id, count=len(events))
        return [
            IntegrityOutcome(
                event_id=None,
                event_type=e.event_type,
                severity=IntegritySeverity.INFO,
                action=IntegrityAction.LOG_ONLY,
            )
            for e in events
        ]

    async def record_server_event(
        self, session_id: str, event_type: IntegrityEventType, *, details: dict[str, Any] | None = None
    ) -> IntegrityOutcome | None:
        log.info("realtime.integrity.null_server_event", session_id=session_id, event_type=event_type.value)
        return None

    async def submit_frame(self, session_id: str, **_: Any) -> str:
        raise ServiceUnavailable(
            "Frame analysis is not available.", code="integrity_unavailable", retry_after=30
        )

    async def analyze_frame(self, session_id: str, media_id: str) -> list[IntegrityOutcome]:
        return []


_NULL_INTEGRITY = NullIntegrityService()
