"""Per-endpoint circuit breaker (closed -> open -> half-open -> closed).

* CLOSED: calls flow; consecutive counted failures are tracked. Reaching
  ``failure_threshold`` opens the circuit.
* OPEN: calls fail fast with :class:`CircuitOpenError` until
  ``reset_seconds`` have elapsed.
* HALF_OPEN: exactly one probe call is let through. Success closes the
  circuit, failure re-opens it for another ``reset_seconds``.

The breaker is single-threaded asyncio code: state changes happen between
awaits, so no lock is needed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum

from app.ai.base import AIUnavailable
from app.core import metrics
from app.core.logging import get_logger

log = get_logger(__name__)

# Optional gauge (0 closed, 1 half-open, 2 open). Used when the metrics module
# defines it; see the AI layer report for the requested definition.
_STATE_GAUGE = getattr(metrics, "CIRCUIT_BREAKER_STATE", None)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_GAUGE_VALUE = {CircuitState.CLOSED: 0, CircuitState.HALF_OPEN: 1, CircuitState.OPEN: 2}


class CircuitOpenError(AIUnavailable):
    """Raised without contacting the endpoint while its circuit is open."""

    def __init__(self, endpoint: str, retry_after: float) -> None:
        super().__init__(
            f"circuit open for endpoint '{endpoint}' (retry in {retry_after:.1f}s)", endpoint=endpoint
        )
        self.retry_after = retry_after


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._last_failure_reason: str | None = None
        self._set_gauge()

    # ------------------------------------------------------------ inspection
    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._clock() - self._opened_at >= self.reset_seconds:
            return CircuitState.HALF_OPEN
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    @property
    def last_failure_reason(self) -> str | None:
        return self._last_failure_reason

    def would_reject(self) -> bool:
        """True when a call made now would fail fast (used by the router to
        pick a fallback endpoint instead of waiting for the error)."""
        state = self.state
        if state is CircuitState.OPEN:
            return True
        if state is CircuitState.HALF_OPEN:
            return self._probe_in_flight
        return False

    def retry_after(self) -> float:
        if self._state is not CircuitState.OPEN:
            return 0.0
        return max(0.0, self.reset_seconds - (self._clock() - self._opened_at))

    # ------------------------------------------------------------ call flow
    def before_call(self) -> None:
        """Admit a call or raise :class:`CircuitOpenError`."""
        state = self.state
        if state is CircuitState.CLOSED:
            return
        if state is CircuitState.OPEN:
            raise CircuitOpenError(self.name, self.retry_after())
        # HALF_OPEN: admit a single probe.
        if self._probe_in_flight:
            raise CircuitOpenError(self.name, 0.0)
        if self._state is CircuitState.OPEN:
            self._transition(CircuitState.HALF_OPEN)
        self._probe_in_flight = True

    def on_success(self) -> None:
        self._probe_in_flight = False
        self._failures = 0
        if self._state is not CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)

    def on_failure(self, reason: str = "error") -> None:
        self._last_failure_reason = reason
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._probe_in_flight:
            self._probe_in_flight = False
            self._open(reason)
            return
        if self._state is CircuitState.CLOSED and self._failures >= self.failure_threshold:
            self._open(reason)

    def on_neutral(self) -> None:
        """The call ended without telling us anything about endpoint health
        (cancelled, or a 4xx caused by the request itself). Releases a
        half-open probe slot so another call can probe."""
        self._probe_in_flight = False

    def reset(self) -> None:
        self._failures = 0
        self._probe_in_flight = False
        if self._state is not CircuitState.CLOSED:
            self._transition(CircuitState.CLOSED)

    # -------------------------------------------------------------- internal
    def _open(self, reason: str) -> None:
        self._opened_at = self._clock()
        self._transition(CircuitState.OPEN, reason=reason)

    def _transition(self, new_state: CircuitState, *, reason: str | None = None) -> None:
        old = self._state
        self._state = new_state
        if old is new_state:
            return
        if new_state is CircuitState.OPEN:
            log.warning(
                "ai.circuit.opened",
                endpoint=self.name,
                previous=old.value,
                failures=self._failures,
                reason=reason,
                reset_seconds=self.reset_seconds,
            )
        elif new_state is CircuitState.CLOSED:
            log.info("ai.circuit.closed", endpoint=self.name, previous=old.value)
        else:
            log.info("ai.circuit.half_open", endpoint=self.name)
        self._set_gauge()

    def _set_gauge(self) -> None:
        if _STATE_GAUGE is not None:
            _STATE_GAUGE.labels(endpoint=self.name).set(_GAUGE_VALUE[self._state])
