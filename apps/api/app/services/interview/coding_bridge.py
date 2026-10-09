"""Bridge between the engine and the coding module.

* :meth:`CodingBridge.select` - ``CodingService.select_question`` (pinned >
  retrieved > generated) for the round's coding competencies and languages;
* :meth:`start` - ``CodingService.start`` (the questions row must exist first:
  ``coding_sessions.question_id`` references it);
* :meth:`observe` - ``coding.observer.decide(context, trigger, state)`` decides
  whether to comment (silence is the default);
* :meth:`hint` / :meth:`finish` - hints and closing the coding session.

All calls are bounded and failures degrade gracefully (the coding segment is
skipped, a comment is not made) - the interview never stalls on coding.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.db.models import CodingQuestion
from app.db.session import session_scope
from app.domain.enums import CodeLanguage, Difficulty
from app.schemas.realtime import CodingStateMsg
from app.services.interview.adapters import CodingPort, get_coding, maybe_await
from app.services.interview.contracts import BehaviorSpec
from app.services.interview.policy import is_coding_competency, is_technical_competency
from app.services.interview.state import InterviewState

log = get_logger(__name__)

SERVICE_TIMEOUT_S = 20.0


def _observer_timeout() -> float:
    """The coding module bounds one observer decision (deterministic triggers + one model call of
    ``OBSERVATION_TIMEOUT_S``); the engine waits slightly longer so a decision that finishes in its
    own budget is never dropped."""
    try:
        from app.services.coding.observer import OBSERVATION_TIMEOUT_S
    except Exception:  # noqa: BLE001 - coding module unavailable: keep the documented default
        return 8.0
    return float(OBSERVATION_TIMEOUT_S) + 1.0


OBSERVER_TIMEOUT_S = _observer_timeout()

_LANGUAGE_ALIASES: dict[CodeLanguage, tuple[str, ...]] = {
    CodeLanguage.PYTHON: ("python",),
    CodeLanguage.JAVASCRIPT: ("javascript", "js", "node", "node.js", "nodejs"),
    CodeLanguage.TYPESCRIPT: ("typescript", "ts"),
    CodeLanguage.JAVA: ("java",),
    CodeLanguage.GO: ("go", "golang"),
    CodeLanguage.CPP: ("c++", "cpp"),
    CodeLanguage.SQL: ("sql", "postgresql", "postgres", "mysql"),
}

# CodingService.finish reasons (documented by the coding module).
FINISH_REASON = {
    "submitted": "submitted",
    "time_limit": "time_limit",
    "interviewer_moved_on": "interviewer_moved_on",
    "moved_on": "interviewer_moved_on",
    "session_ended": "session_ended",
}


@dataclass(slots=True)
class HintOutcome:
    level: int
    text: str
    speech: str = ""
    hints_used: int | None = None
    hints_remaining: int | None = None


def _accepts(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


@dataclass(slots=True)
class CodingObservation:
    should_speak: bool
    speech: str
    kind: str = "question"
    focus: str = "other"
    behavior: BehaviorSpec | None = None


def coding_competencies(state: InterviewState) -> list[str]:
    keys = [
        k for k in state.round_config.competency_keys() if is_coding_competency(k, state.competencies.get(k))
    ]
    if keys:
        return keys
    return [
        c.key
        for c in state.round_config.competencies
        if c.required and is_technical_competency(c.key, state.competencies.get(c.key))
    ][:3]


def preferred_language(state: InterviewState) -> CodeLanguage:
    languages = list(state.round_config.coding.languages) or [CodeLanguage.PYTHON]
    profile = state.candidate_profile or {}
    names: list[tuple[str, float]] = []
    for items in (profile.get("skills") or {}).values():
        for item in items or []:
            if isinstance(item, dict) and item.get("name"):
                names.append((str(item["name"]).lower(), float(item.get("years") or 0)))
    names.sort(key=lambda kv: -kv[1])
    for name, _years in names:
        for lang in languages:
            if name in _LANGUAGE_ALIASES.get(lang, ()):
                return lang
    return languages[0]


def transition_speech(state: InterviewState, *, title: str, minutes: int, ack: str | None) -> str:
    hints = state.round_config.coding.hints_enabled and state.round_config.coding.max_hints > 0
    lead = f"{ack} " if ack else ""
    first = (
        f"{lead}Let's move to a short coding exercise; the editor is opening on your screen now. "
        f"The problem is called {title}, and the full description is right there."
    )
    second = (
        f"Pick whichever language you're most comfortable with, think out loud as you go, and run the tests whenever "
        f"you're ready; you have about {minutes} minutes"
        + (", and there are hints if you get stuck." if hints else ".")
    )
    return f"{first} {second}"


def short_hint(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"[`*#]+", "", text).replace("\n", " ").strip()
    first = re.split(r"(?<=[.!?])\s+", cleaned)[0] if cleaned else ""
    if len(first) > limit:
        first = first[: limit - 1].rsplit(" ", 1)[0] + "…"
    return first


def run_summary(run: Any) -> dict[str, Any]:
    status = getattr(run, "status", None)
    return {
        "run_id": str(getattr(run, "run_id", "") or uuid.uuid4()),
        "kind": getattr(getattr(run, "kind", None), "value", str(getattr(run, "kind", "test"))),
        "status": getattr(status, "value", str(status)),
        "passed": int(getattr(run, "passed", 0) or 0),
        "total": int(getattr(run, "total", 0) or 0),
        "error_excerpt": (
            (getattr(run, "compile_output", "") or getattr(run, "stderr", "") or "")[:300] or None
        ),
    }


class CodingBridge:
    def __init__(self, port: CodingPort | None = None) -> None:
        self._port = port

    @property
    def port(self) -> CodingPort:
        return self._port or get_coding()

    @property
    def available(self) -> bool:
        return self.port.service is not None

    async def select(self, state: InterviewState) -> str | None:
        service = self.port.service
        if service is None:
            return None
        cs = state.round_config.coding
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT_S):
                return await service.select_question(
                    state.session_id,
                    competency_keys=coding_competencies(state),
                    difficulty=Difficulty(cs.difficulty),
                    languages=list(cs.languages),
                )
        except Exception as exc:  # noqa: BLE001 - coding must never stall the interview
            ERRORS.labels(component="engine_coding").inc()
            log.warning(
                "engine.coding_select_failed",
                session_id=state.session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return None

    async def question_info(self, coding_question_id: str) -> dict[str, Any] | None:
        async with session_scope() as db:
            row = (
                await db.execute(
                    select(CodingQuestion).where(CodingQuestion.id == uuid.UUID(str(coding_question_id)))
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "title": row.title,
                "time_limit_minutes": row.time_limit_minutes,
                "difficulty": row.difficulty,
                "competency_keys": list(row.competency_keys or []),
            }

    async def start(
        self, state: InterviewState, coding_question_id: str, *, question_id: str, language: CodeLanguage
    ) -> CodingStateMsg | None:
        service = self.port.service
        if service is None:
            return None
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT_S):
                return await service.start(
                    state.session_id, coding_question_id, question_id=question_id, language=language
                )
        except Exception as exc:  # noqa: BLE001
            ERRORS.labels(component="engine_coding").inc()
            log.warning(
                "engine.coding_start_failed",
                session_id=state.session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return None

    async def hint(self, coding_session_id: str, *, requested_via: str) -> HintOutcome | None:
        """Next hint level. Uses ``request_hint_detailed`` (spoken version,
        counts) when the service offers it, else the contract's
        ``request_hint`` -> (level, text)."""
        service = self.port.service
        if service is None:
            return None
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT_S):
                detailed = getattr(service, "request_hint_detailed", None)
                if callable(detailed):
                    result = await detailed(coding_session_id, requested_via=requested_via)
                    if result is None:
                        return None
                    return HintOutcome(
                        level=int(result.level),
                        text=str(result.text),
                        speech=str(getattr(result, "speech", "") or ""),
                        hints_used=getattr(result, "hints_used", None),
                        hints_remaining=getattr(result, "hints_remaining", None),
                    )
                plain = await service.request_hint(coding_session_id, requested_via=requested_via)
                if plain is None:
                    return None
                level, text = plain
                return HintOutcome(level=int(level), text=str(text))
        except Exception as exc:  # noqa: BLE001
            ERRORS.labels(component="engine_coding").inc()
            log.warning(
                "engine.coding_hint_failed",
                coding_session_id=coding_session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return None

    async def finish(self, coding_session_id: str, *, reason: str) -> bool:
        service = self.port.service
        if service is None:
            return False
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT_S):
                await service.finish(coding_session_id, reason=FINISH_REASON.get(reason, reason))
            return True
        except Exception as exc:  # noqa: BLE001
            ERRORS.labels(component="engine_coding").inc()
            log.warning(
                "engine.coding_finish_failed",
                coding_session_id=coding_session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return False

    async def context(self, coding_session_id: str) -> Any | None:
        service = self.port.service
        if service is None:
            return None
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT_S):
                return await service.context(coding_session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "engine.coding_context_failed",
                coding_session_id=coding_session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return None

    async def observe(
        self, state: InterviewState, *, trigger: str, candidate_utterance: str | None = None
    ) -> CodingObservation | None:
        """Ask the observer whether to comment. Prefers the service's
        ``observe(coding_session_id, trigger)`` (persists the observer state
        with the coding session); otherwise calls
        ``observer.decide(context, trigger, state)`` with the engine-held
        observer state dict."""
        service = self.port.service
        observer = self.port.observer
        cs_id = state.coding.coding_session_id
        if cs_id is None or not state.round_config.coding.proactive_comments:
            return None
        try:
            async with asyncio.timeout(OBSERVER_TIMEOUT_S):
                service_observe = getattr(service, "observe", None) if service is not None else None
                if callable(service_observe):
                    kwargs = (
                        {"candidate_utterance": candidate_utterance}
                        if candidate_utterance and _accepts(service_observe, "candidate_utterance")
                        else {}
                    )
                    decision = await maybe_await(service_observe(cs_id, trigger, **kwargs))
                elif observer is not None:
                    context = await self.context(cs_id)
                    if context is None:
                        return None
                    extra: dict[str, Any] = {}
                    for name, value in (
                        ("section", state.round_config.coding),
                        ("interviewer_name", state.interviewer_name),
                        ("session_id", state.session_id),
                        ("organization_id", state.organization_id),
                        ("candidate_utterance", candidate_utterance),
                    ):
                        if value is not None and _accepts(observer, name):
                            extra[name] = value
                    decision = await maybe_await(
                        observer(context, trigger, state.coding.observer_state, **extra)
                    )
                else:
                    return None
        except Exception as exc:  # noqa: BLE001
            ERRORS.labels(component="engine_coding").inc()
            log.warning(
                "engine.coding_observer_failed",
                session_id=state.session_id,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            return None
        if decision is None or not getattr(decision, "should_speak", False):
            return None
        speech = str(getattr(decision, "speech", "") or "").strip()
        if not speech:
            return None
        behavior = getattr(decision, "behavior", None)
        if behavior is not None and not isinstance(behavior, BehaviorSpec):
            try:
                behavior = BehaviorSpec(
                    state=behavior.state,
                    expression=behavior.expression,
                    gesture=behavior.gesture,
                    gesture_intensity=behavior.gesture_intensity,
                    tone=behavior.tone,
                )
            except AttributeError:
                behavior = None
        return CodingObservation(
            should_speak=True,
            speech=speech[:400],
            kind=str(getattr(decision, "kind", "question") or "question"),
            focus=str(getattr(decision, "focus", "other") or "other"),
            behavior=behavior,
        )


__all__ = [
    "CodingBridge",
    "CodingObservation",
    "HintOutcome",
    "coding_competencies",
    "preferred_language",
    "run_summary",
    "short_hint",
    "transition_speech",
]
