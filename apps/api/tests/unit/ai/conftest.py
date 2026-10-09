"""Helpers for AI-layer unit tests (no network: respx or in-process fakes)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from app.ai.breaker import CircuitBreaker
from app.ai.http import EndpointClient
from app.ai.ledger import MemoryLedger


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_endpoint(
    name: str = "main",
    base_url: str = "http://main.test/v1",
    *,
    timeout: float = 5.0,
    concurrency: int = 4,
    retries: int = 2,
    threshold: int = 5,
    reset: float = 30.0,
    clock: Callable[[], float] | None = None,
) -> EndpointClient:
    breaker = CircuitBreaker(
        name, failure_threshold=threshold, reset_seconds=reset, **({"clock": clock} if clock else {})
    )
    return EndpointClient(
        name,
        base_url,
        timeout=timeout,
        max_concurrency=concurrency,
        max_retries=retries,
        breaker=breaker,
        backoff_base=0.001,
        backoff_max=0.004,
    )


@pytest.fixture
def ledger() -> MemoryLedger:
    return MemoryLedger(log_payloads=True)


@pytest.fixture
async def endpoint_factory() -> AsyncIterator[Callable[..., EndpointClient]]:
    created: list[EndpointClient] = []

    def factory(*args: object, **kwargs: object) -> EndpointClient:
        client = make_endpoint(*args, **kwargs)  # type: ignore[arg-type]
        created.append(client)
        return client

    yield factory
    for client in created:
        await client.aclose()
