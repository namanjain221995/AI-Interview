from __future__ import annotations

import pytest

from app.ai.base import AIUnavailable
from app.ai.breaker import CircuitBreaker, CircuitOpenError, CircuitState
from tests.unit.ai.conftest import FakeClock


def make(threshold: int = 3, reset: float = 10.0) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    return CircuitBreaker("test", failure_threshold=threshold, reset_seconds=reset, clock=clock), clock


def test_opens_after_threshold_consecutive_failures() -> None:
    breaker, _ = make(threshold=3)
    for _ in range(2):
        breaker.before_call()
        breaker.on_failure("unavailable")
    assert breaker.state is CircuitState.CLOSED
    breaker.before_call()
    breaker.on_failure("unavailable")
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError) as info:
        breaker.before_call()
    assert isinstance(info.value, AIUnavailable)
    assert info.value.retry_after == pytest.approx(10.0)


def test_success_resets_failure_count() -> None:
    breaker, _ = make(threshold=3)
    breaker.on_failure()
    breaker.on_failure()
    breaker.on_success()
    breaker.on_failure()
    breaker.on_failure()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.consecutive_failures == 2


def test_half_open_allows_single_probe_then_closes_on_success() -> None:
    breaker, clock = make(threshold=1, reset=5.0)
    breaker.on_failure("timeout")
    assert breaker.would_reject()
    clock.advance(5.0)
    assert breaker.state is CircuitState.HALF_OPEN
    assert not breaker.would_reject()
    breaker.before_call()  # the probe
    assert breaker.would_reject()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # second caller while the probe is in flight
    breaker.on_success()
    assert breaker.state is CircuitState.CLOSED
    breaker.before_call()


def test_half_open_probe_failure_reopens_for_another_period() -> None:
    breaker, clock = make(threshold=1, reset=5.0)
    breaker.on_failure()
    clock.advance(6.0)
    breaker.before_call()
    breaker.on_failure("unavailable")
    assert breaker.state is CircuitState.OPEN
    assert breaker.retry_after() == pytest.approx(5.0)
    clock.advance(4.9)
    assert breaker.state is CircuitState.OPEN
    clock.advance(0.2)
    assert breaker.state is CircuitState.HALF_OPEN


def test_neutral_outcome_releases_probe_slot() -> None:
    breaker, clock = make(threshold=1, reset=1.0)
    breaker.on_failure()
    clock.advance(1.0)
    breaker.before_call()
    breaker.on_neutral()  # e.g. cancelled
    assert not breaker.would_reject()
    breaker.before_call()


def test_reset_closes() -> None:
    breaker, _ = make(threshold=1)
    breaker.on_failure()
    breaker.reset()
    assert breaker.state is CircuitState.CLOSED
    breaker.before_call()


def test_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker("x", failure_threshold=0)
