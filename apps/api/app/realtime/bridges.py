"""Coding and integrity bridges of the session runtime.

CodingBridge
    ``code.update/run/submit/hint_request/language`` -> ``CodingService`` +
    ``engine.handle_coding_activity`` (through the runtime's floor queue so
    engine reactions never talk over the candidate); sandbox results ->
    ``coding.run_result`` (visible tests only); idle detection and coding time
    warnings from the runtime tick.

IntegrityBridge
    ``integrity.events`` -> ``IntegrityService.record_client_events`` ->
    ``integrity.warning`` when an outcome carries a candidate message; server
    events (disconnect / reconnect / page reload) -> ``record_server_event``;
    pause / terminate policy actions are applied only for objective,
    recruiter-configured conditions - never for vision or screen-analysis
    observations.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Literal

from app.core import metrics
from app.core.errors import AppError
from app.core.logging import get_logger
from app.domain.enums import (
    CodeLanguage,
    IntegrityAction,
    IntegrityEventType,
    IntegritySource,
    RunKind,
    RunStatus,
)
from app.schemas.realtime import (
    ClientIntegrityEvent,
    CodeRunMsg,
    CodeSubmitMsg,
    CodeUpdateMsg,
    CodingRunResultMsg,
    CodingStateMsg,
    HintRequestMsg,
    IntegrityWarningMsg,
    LanguageChangeMsg,
)
from app.services.coding.sandbox_client import SANDBOX_BUSY_MESSAGE
from app.services.integrity.contracts import NON_TERMINATING_SOURCES, IntegrityOutcome
from app.services.integrity.rules import SCREEN_ANALYSIS_TYPES, VISION_TYPES, default_source
from app.services.interview.contracts import CodingActivity

if TYPE_CHECKING:
    from app.realtime.runtime import SessionRuntime

log = get_logger(__name__)

CODING_IDLE_SECONDS = 90.0
CODING_TIME_WARNING_SECONDS = 120.0
RUN_TIMEOUT_SECONDS = 90.0

# The integrity rule engine owns the event -> source mapping; the bridge uses
# the same sets so both agree (e.g. screen_share_not_full_screen is reported by
# the browser's display-surface check: a browser source, not screen analysis).
VISION_EVENT_TYPES: frozenset[IntegrityEventType] = VISION_TYPES
SCREEN_ANALYSIS_EVENT_TYPES: frozenset[IntegrityEventType] = SCREEN_ANALYSIS_TYPES


def event_source(event_type: IntegrityEventType, declared: str | None = None) -> IntegritySource:
    if declared:
        try:
            return IntegritySource(declared)
        except ValueError:
            pass
    return default_source(event_type)


def objective_action_allowed(event_type: IntegrityEventType, source: IntegritySource | None = None) -> bool:
    """Pause/terminate may only follow objective signals (never model guesses)."""
    src = source or event_source(event_type)
    return src not in NON_TERMINATING_SOURCES and event_type not in VISION_EVENT_TYPES


HIDDEN_TEST_KEYS = frozenset({"id", "passed", "hidden"})


def sanitize_run_result(result: CodingRunResultMsg) -> CodingRunResultMsg:
    """Forward the coding service's result as-is, except - defence in depth -
    any hidden-test entry is reduced to ``{id, passed, hidden}`` (the sandbox
    reports actual/expected/error for hidden tests; candidates must never see them)."""
    changed = False
    tests: list[dict[str, Any]] = []
    for test in result.tests:
        if isinstance(test, dict) and test.get("hidden") and set(test) - HIDDEN_TEST_KEYS:
            tests.append({k: v for k, v in test.items() if k in HIDDEN_TEST_KEYS})
            changed = True
        else:
            tests.append(test)
    return result.model_copy(update={"tests": tests}) if changed else result


def is_execution_result(result: CodingRunResultMsg) -> bool:
    """True when the sandbox actually ran the candidate's code: not a rejected
    run and not a platform fault (the coding service turns those into an
    ``error`` result carrying the sandbox client's busy message)."""
    if result.status is RunStatus.REJECTED:
        return False
    return not (result.status is RunStatus.ERROR and result.stderr.strip() == SANDBOX_BUSY_MESSAGE)


class CodingBridge:
    def __init__(self, runtime: SessionRuntime) -> None:
        self.rt = runtime
        self.state: CodingStateMsg | None = None
        self.coding_session_id: str | None = None
        self.started_at: float | None = None
        self.time_limit_s: float = 0.0
        self.last_edit_at: float = 0.0
        self.last_idle_fired_at: float = 0.0
        self.latest_code: str | None = None
        self.latest_version: int = 0
        self.language: CodeLanguage | None = None
        self.warned = False
        self.timed_out = False
        self._run_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ lifecycle
    @property
    def active(self) -> bool:
        return self.coding_session_id is not None

    def started(self, coding: CodingStateMsg) -> None:
        now = time.monotonic()
        self.state = coding
        self.coding_session_id = coding.coding_session_id
        self.started_at = now - float(coding.elapsed_s or 0.0)
        self.time_limit_s = float(coding.time_limit_s)
        self.last_edit_at = now
        self.last_idle_fired_at = 0.0
        self.latest_code = coding.code
        self.language = coding.language
        self.warned = False
        self.timed_out = False

    def ended(self, coding_session_id: str | None = None) -> None:
        if coding_session_id is None or coding_session_id == self.coding_session_id:
            self.coding_session_id = None
            self.state = None
            self.started_at = None

    def remaining_s(self, now: float | None = None) -> float | None:
        if self.started_at is None:
            return None
        return self.time_limit_s - ((now or time.monotonic()) - self.started_at)

    # ------------------------------------------------------------- messages
    def _check(self, coding_session_id: str) -> bool:
        if self.rt.coding is None:
            self.rt.send_error(
                "coding_unavailable", "The coding environment is not available.", recoverable=True
            )
            return False
        if not self.active or coding_session_id != self.coding_session_id:
            self.rt.send_error(
                "coding_not_active", "There is no active coding exercise with that id.", recoverable=True
            )
            return False
        return True

    async def on_update(self, msg: CodeUpdateMsg) -> None:
        if not self._check(msg.coding_session_id):
            return
        if msg.version < self.latest_version:
            return  # stale editor version
        self.latest_code = msg.code
        self.latest_version = msg.version
        self.language = msg.language
        self.last_edit_at = time.monotonic()
        coding = self.rt.coding
        assert coding is not None  # _check() guarantees it
        try:
            await coding.record_update(
                msg.coding_session_id, code=msg.code, language=msg.language, version=msg.version
            )
        except Exception as exc:  # noqa: BLE001
            metrics.ERRORS.labels(component="realtime_coding").inc()
            log.warning("realtime.coding.update_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        self.rt.enqueue_coding_activity(
            CodingActivity(
                kind="edit",
                coding_session_id=msg.coding_session_id,
                code=msg.code,
                language=msg.language.value,
                version=msg.version,
            ),
            coalesce="edit",
        )

    async def on_run(self, msg: CodeRunMsg | CodeSubmitMsg) -> None:
        if not self._check(msg.coding_session_id):
            return
        # Concurrent / duplicate runs are answered by the coding service with a
        # "rejected" result, which is forwarded like any other result.
        kind = RunKind.SUBMIT if isinstance(msg, CodeSubmitMsg) else msg.kind
        stdin = msg.stdin if isinstance(msg, CodeRunMsg) else None
        self.latest_code = msg.code
        self.language = msg.language
        self.last_edit_at = time.monotonic()
        self.rt.track(
            asyncio.create_task(self._run(msg.coding_session_id, kind, msg.code, msg.language, stdin))
        )

    async def _run(
        self, coding_session_id: str, kind: RunKind, code: str, language: CodeLanguage, stdin: str | None
    ) -> None:
        started = time.perf_counter()
        coding = self.rt.coding
        assert coding is not None
        try:
            result = await asyncio.wait_for(
                coding.run(coding_session_id, kind=kind, code=code, language=language, stdin=stdin),
                timeout=RUN_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - sandbox down, timeout, validation
            metrics.ERRORS.labels(component="realtime_coding").inc()
            log.warning(
                "realtime.coding.run_failed", kind=kind.value, error=f"{type(exc).__name__}: {exc}"[:200]
            )
            result = CodingRunResultMsg(
                coding_session_id=coding_session_id,
                run_id="",
                kind=kind,
                status=RunStatus.ERROR,
                stderr="The code runner is temporarily unavailable. Please try again in a moment.",
            )
            self.rt.post(result)
            return
        result = sanitize_run_result(result)
        log.info(
            "realtime.coding.run_done",
            kind=kind.value,
            status=result.status.value,
            passed=result.passed,
            total=result.total,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        self.rt.post(result)
        if not is_execution_result(result):
            # A platform fault (runner busy/unavailable) or a rejected run (another run in
            # progress, double click, exercise over): nothing was evaluated, so the
            # exercise must not end on it - the candidate simply tries again.
            log.info("realtime.coding.run_not_counted", kind=kind.value, status=result.status.value)
            return
        activity_kind: Literal["submitted", "run_completed"] = (
            "submitted" if kind is RunKind.SUBMIT else "run_completed"
        )
        self.rt.enqueue_coding_activity(
            CodingActivity(
                kind=activity_kind,
                coding_session_id=coding_session_id,
                code=code,
                language=language.value,
                run=result,
            )
        )

    async def on_hint(self, msg: HintRequestMsg) -> None:
        if not self._check(msg.coding_session_id):
            return
        self.rt.enqueue_coding_activity(
            CodingActivity(
                kind="hint_requested", coding_session_id=msg.coding_session_id, requested_via="button"
            ),
            priority_boost=True,
        )

    async def on_language(self, msg: LanguageChangeMsg) -> None:
        if not self._check(msg.coding_session_id):
            return
        change = getattr(self.rt.coding, "change_language", None)
        if change is not None:
            try:
                payload = await change(msg.coding_session_id, msg.language)
            except AppError as exc:
                self.rt.send_error(exc.code, exc.message, recoverable=True)
                return
            except Exception as exc:  # noqa: BLE001
                metrics.ERRORS.labels(component="realtime_coding").inc()
                log.warning("realtime.coding.language_failed", error=f"{type(exc).__name__}: {exc}"[:200])
                self.rt.send_error(
                    "language_change_failed", "Could not switch the language. Please try again."
                )
                return
            if isinstance(payload, CodingStateMsg):
                self.state = payload
                self.latest_code = payload.code
                self.language = payload.language
                await self.rt.push_state("coding_language_changed", coding=payload)
        else:
            self.language = msg.language
        self.last_edit_at = time.monotonic()
        self.rt.enqueue_coding_activity(
            CodingActivity(
                kind="language_changed", coding_session_id=msg.coding_session_id, language=msg.language.value
            )
        )

    # ----------------------------------------------------------------- tick
    def tick(self, now: float, *, floor_idle: bool) -> None:
        if not self.active or self.coding_session_id is None:
            return
        remaining = self.remaining_s(now)
        if remaining is not None:
            if not self.timed_out and remaining <= 0:
                self.timed_out = True
                self.rt.enqueue_coding_activity(
                    CodingActivity(kind="time_up", coding_session_id=self.coding_session_id),
                    priority_boost=True,
                )
                return
            if (
                not self.warned
                and remaining <= CODING_TIME_WARNING_SECONDS
                and self.time_limit_s > 2 * CODING_TIME_WARNING_SECONDS
            ):
                self.warned = True
                self.rt.enqueue_coding_activity(
                    CodingActivity(kind="time_warning", coding_session_id=self.coding_session_id)
                )
        if not floor_idle:
            return
        idle = now - max(self.last_edit_at, self.last_idle_fired_at)
        if idle >= CODING_IDLE_SECONDS:
            self.last_idle_fired_at = now
            self.rt.enqueue_coding_activity(
                CodingActivity(
                    kind="idle",
                    coding_session_id=self.coding_session_id,
                    idle_seconds=round(now - self.last_edit_at, 1),
                    code=self.latest_code,
                    language=self.language.value if self.language else None,
                ),
                coalesce="idle",
            )


class IntegrityBridge:
    """Delivers integrity outcomes to the candidate and applies policy actions.

    Outcomes of ``record_client_events`` / ``record_server_event`` are
    *returned* to the runtime, which delivers warnings and actions itself. On
    the session bus the integrity service publishes ``integrity.event``
    (skipped when ``delivered_to_caller``) and ``integrity.warning`` (frame
    analysis in the worker, or REST callers that asked for it); the portal REST
    fallback publishes ``integrity.action`` for objective pause/terminate
    outcomes. Metrics are counted by the integrity service, not here."""

    def __init__(self, runtime: SessionRuntime) -> None:
        self.rt = runtime
        self._warned_event_ids: set[str] = set()

    async def on_client_events(self, events: list[ClientIntegrityEvent]) -> None:
        if not events:
            return
        try:
            outcomes = await self.rt.integrity.record_client_events(self.rt.session_id, events)
        except Exception as exc:  # noqa: BLE001 - never break the interview over monitoring
            metrics.ERRORS.labels(component="realtime_integrity").inc()
            log.warning("realtime.integrity.record_failed", error=f"{type(exc).__name__}: {exc}"[:200])
            return
        await self.apply(outcomes or [])

    async def on_server_event(
        self, event_type: IntegrityEventType, details: dict[str, Any] | None = None
    ) -> None:
        try:
            outcome = await self.rt.integrity.record_server_event(
                self.rt.session_id, event_type, details=details or {}
            )
        except Exception as exc:  # noqa: BLE001
            metrics.ERRORS.labels(component="realtime_integrity").inc()
            log.warning(
                "realtime.integrity.server_event_failed",
                event_type=event_type.value,
                error=type(exc).__name__,
            )
            return
        if outcome is not None:
            await self.apply([outcome])

    def _warn(self, event_type: IntegrityEventType, message: str, event_id: str | None) -> None:
        if not self.rt.ctx.policy.snapshot.integrity.warn_candidate or not message:
            return
        if event_id:
            if event_id in self._warned_event_ids:
                return
            self._warned_event_ids.add(event_id)
            if len(self._warned_event_ids) > 500:
                self._warned_event_ids.clear()
        self.rt.post(IntegrityWarningMsg(event_type=event_type, message=message[:500]))

    async def apply(self, outcomes: list[IntegrityOutcome]) -> None:
        for outcome in outcomes:
            if outcome.candidate_message:
                self._warn(outcome.event_type, outcome.candidate_message, outcome.event_id)
            await self.apply_action(outcome.action, outcome.event_type, message=outcome.candidate_message)

    async def apply_action(
        self,
        action: IntegrityAction | str,
        event_type: IntegrityEventType,
        *,
        message: str | None = None,
        source: IntegritySource | None = None,
    ) -> None:
        try:
            action = IntegrityAction(action)
        except ValueError:
            return
        if action not in (IntegrityAction.PAUSE_INTERVIEW, IntegrityAction.TERMINATE_INTERVIEW):
            return
        if not objective_action_allowed(event_type, source):
            log.warning(
                "realtime.integrity.action_refused",
                action=action.value,
                event_type=event_type.value,
                reason="non_objective_source",
            )
            return
        if action is IntegrityAction.PAUSE_INTERVIEW:
            await self.rt.policy_pause(event_type, message)
        else:
            await self.rt.policy_terminate(event_type, message)

    @staticmethod
    def _event_type(event: dict[str, Any]) -> IntegrityEventType | None:
        raw = event.get("event_type")
        if not isinstance(raw, str):
            return None
        try:
            return IntegrityEventType(raw)
        except ValueError:
            return None

    @staticmethod
    def _source(event: dict[str, Any]) -> IntegritySource | None:
        raw = event.get("source")
        if isinstance(raw, str):
            try:
                return IntegritySource(raw)
            except ValueError:
                return None
        return None

    async def on_bus_event(self, event: dict[str, Any]) -> None:
        """``integrity.event`` (integrity service) / ``integrity.action`` (portal REST fallback)."""
        event_type = self._event_type(event)
        if event_type is None:
            return
        if event.get("type") == "integrity.event" and event.get("delivered_to_caller"):
            return  # the caller already delivered the outcome
        action = event.get("action")
        if action:
            message = event.get("candidate_message")
            await self.apply_action(
                action,
                event_type,
                message=message if isinstance(message, str) else None,
                source=self._source(event),
            )

    async def on_bus_warning(self, event: dict[str, Any]) -> None:
        """``integrity.warning`` {event_id, event_type, message} -> ``integrity.warning`` to the client."""
        event_type = self._event_type(event)
        message = event.get("message")
        if event_type is None or not isinstance(message, str):
            return
        event_id = event.get("event_id")
        self._warn(event_type, message, str(event_id) if event_id else None)
