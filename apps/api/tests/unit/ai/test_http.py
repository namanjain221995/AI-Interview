from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.ai.base import AIRejected, AITimeout, AIUnavailable
from app.ai.breaker import CircuitOpenError, CircuitState
from app.ai.http import call_status, error_for_status, failure_reason, gather_or_cancel
from app.domain.enums import ModelCallStatus

BASE = "http://main.test/v1"


async def test_success_returns_result(endpoint_factory) -> None:
    client = endpoint_factory()
    with respx.mock(base_url=BASE) as mock:
        mock.get("/models").respond(200, json={"data": [{"id": "m"}]})
        result = await client.request("GET", "/models")
    assert result.attempts == 1
    assert result.json() == {"data": [{"id": "m"}]}
    assert client.inflight == 0


async def test_retries_5xx_then_succeeds(endpoint_factory) -> None:
    client = endpoint_factory(retries=2)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/embeddings").mock(
            side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})]
        )
        result = await client.request("POST", "/embeddings", json={})
    assert route.call_count == 3
    assert result.attempts == 3
    assert client.breaker.state is CircuitState.CLOSED
    assert client.breaker.consecutive_failures == 0


async def test_retries_exhausted_maps_to_unavailable(endpoint_factory) -> None:
    client = endpoint_factory(retries=1)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/x").respond(500, json={"error": {"message": "engine dead"}})
        with pytest.raises(AIUnavailable) as info:
            await client.request("POST", "/x", json={})
    assert route.call_count == 2
    assert "engine dead" in str(info.value)
    assert info.value.endpoint == "main"
    assert info.value.retryable


async def test_4xx_is_rejected_without_retry(endpoint_factory) -> None:
    client = endpoint_factory(retries=3)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/x").respond(400, json={"object": "error", "message": "bad schema"})
        with pytest.raises(AIRejected) as info:
            await client.request("POST", "/x", json={})
    assert route.call_count == 1
    assert "bad schema" in str(info.value)
    assert not info.value.retryable
    assert client.breaker.consecutive_failures == 0  # client errors say nothing about endpoint health


async def test_429_retried_and_honours_retry_after(endpoint_factory) -> None:
    client = endpoint_factory(retries=1)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/x").mock(
            side_effect=[httpx.Response(429, headers={"retry-after": "0.05"}), httpx.Response(200, json={})]
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await client.request("POST", "/x", json={})
        elapsed = loop.time() - started
    assert route.call_count == 2
    assert result.attempts == 2
    assert elapsed >= 0.008  # capped at 2 x backoff_max in tests, but a real wait happened


async def test_connect_error_retried_and_mapped(endpoint_factory) -> None:
    client = endpoint_factory(retries=2)
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/models").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(AIUnavailable):
            await client.request("GET", "/models")
    assert route.call_count == 3
    assert client.breaker.consecutive_failures == 3


async def test_read_timeout_retried_only_when_idempotent(endpoint_factory) -> None:
    client = endpoint_factory(retries=2)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(AITimeout):
            await client.request("POST", "/chat/completions", json={}, idempotent=False)
        assert route.call_count == 1
        route.reset()
        with pytest.raises(AITimeout):
            await client.request("POST", "/chat/completions", json={}, idempotent=True)
        assert route.call_count == 3


async def test_short_caller_deadline_does_not_trip_breaker(endpoint_factory) -> None:
    client = endpoint_factory(retries=0, threshold=1, timeout=30.0)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/x").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(AITimeout):
            await client.request("POST", "/x", json={}, timeout=1.0, idempotent=False)
    assert client.breaker.state is CircuitState.CLOSED


async def test_breaker_opens_and_fails_fast(endpoint_factory) -> None:
    client = endpoint_factory(retries=0, threshold=2)
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/models").respond(503)
        for _ in range(2):
            with pytest.raises(AIUnavailable):
                await client.request("GET", "/models")
        assert client.breaker.state is CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            await client.request("GET", "/models")
        assert route.call_count == 2  # the third call never reached the endpoint
        # health probes bypass the breaker
        mock.get("/health").respond(200, json={})
        await client.request("GET", "/health", use_breaker=False)


async def test_concurrency_limit_is_enforced(endpoint_factory) -> None:
    client = endpoint_factory(concurrency=2, retries=0)
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, json={})

    with respx.mock(base_url=BASE) as mock:
        mock.post("/x").mock(side_effect=handler)
        await asyncio.gather(*(client.request("POST", "/x", json={}) for _ in range(6)))
    assert peak == 2
    assert client.inflight == 0


async def test_queue_wait_counts_against_budget(endpoint_factory) -> None:
    client = endpoint_factory(concurrency=1, retries=0)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={})

    with respx.mock(base_url=BASE) as mock:
        mock.post("/x").mock(side_effect=slow)
        first = asyncio.create_task(client.request("POST", "/x", json={}))
        await asyncio.sleep(0.01)
        with pytest.raises(AITimeout, match="slots"):
            await client.request("POST", "/x", json={}, timeout=0.05)
        await first
    assert client.inflight == 0


async def test_cancellation_releases_slot_and_is_neutral(endpoint_factory) -> None:
    client = endpoint_factory(concurrency=1, retries=0, threshold=1)

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200)

    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/x").mock(side_effect=hang)
        task = asyncio.create_task(client.request("POST", "/x", json={}))
        await asyncio.sleep(0.02)
        assert client.inflight == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client.inflight == 0
    assert client.breaker.state is CircuitState.CLOSED


async def test_stream_retries_until_headers_then_closes(endpoint_factory) -> None:
    client = endpoint_factory(retries=2)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, content=b"data: [DONE]\n\n")]
        )
        async with client.stream("POST", "/chat/completions", json={}) as handle:
            assert handle.attempts == 2
            assert client.inflight == 1
            body = await handle.response.aread()
        assert body == b"data: [DONE]\n\n"
        assert route.call_count == 2
    assert client.inflight == 0


async def test_stream_rejects_4xx(endpoint_factory) -> None:
    client = endpoint_factory(retries=2)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(400, json={"error": {"message": "context too long"}})
        with pytest.raises(AIRejected, match="context too long"):
            async with client.stream("POST", "/chat/completions", json={}):
                pass
    assert client.inflight == 0


async def test_api_key_header_sent() -> None:
    from app.ai.http import EndpointClient

    client = EndpointClient("main", BASE, timeout=1, max_concurrency=1, api_key="sk-test")
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.get("/models").respond(200, json={})
            await client.request("GET", "/models")
        assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"
    finally:
        await client.aclose()


async def test_gather_or_cancel_cancels_siblings() -> None:
    cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def boom() -> None:
        await asyncio.sleep(0.01)
        raise AIUnavailable("down")

    with pytest.raises(AIUnavailable):
        await gather_or_cancel(slow(), boom())
    assert cancelled.is_set()


def test_status_and_reason_mapping() -> None:
    assert isinstance(error_for_status("e", 408, ""), AITimeout)
    assert isinstance(error_for_status("e", 429, ""), AIUnavailable)
    assert isinstance(error_for_status("e", 503, ""), AIUnavailable)
    assert isinstance(error_for_status("e", 422, ""), AIRejected)
    assert failure_reason(CircuitOpenError("e", 1.0)) == "circuit_open"
    assert failure_reason(AITimeout("t")) == "timeout"
    assert call_status(CircuitOpenError("e", 1.0)) is ModelCallStatus.CIRCUIT_OPEN
    assert call_status(AITimeout("t")) is ModelCallStatus.TIMEOUT
    assert call_status(asyncio.CancelledError()) is ModelCallStatus.CANCELLED
    assert call_status(AIRejected("r")) is ModelCallStatus.ERROR
