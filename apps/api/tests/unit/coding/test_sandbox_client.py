"""Sandbox client: signing, timeouts, retry policy, circuit breaker, error
mapping and metrics (HTTP mocked with respx)."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.ai.breaker import CircuitBreaker
from app.core.metrics import REGISTRY
from app.domain.enums import CodeLanguage, RunStatus
from app.services.coding.contracts import ExecuteRequest, SandboxTestCase
from app.services.coding.sandbox_client import (
    SANDBOX_BUSY_MESSAGE,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SandboxClient,
    SandboxFault,
    compute_timeout,
    sign,
)

BASE = "http://sandbox.test"
SECRET = "unit-test-shared-secret-0123456789"


def ok_body(request_id: str = "r1", status: str = "passed", passed: int = 2, total: int = 2) -> dict:
    return {
        "request_id": request_id,
        "status": status,
        "stdout": "",
        "stderr": "",
        "compile_output": "",
        "exit_code": 0,
        "runtime_ms": 12,
        "memory_kb": 1000,
        "tests": [
            {
                "id": "t1",
                "passed": True,
                "actual": [0, 1],
                "expected": [0, 1],
                "runtime_ms": 1.0,
                "hidden": False,
            },
            {
                "id": "t2",
                "passed": True,
                "actual": [1, 2],
                "expected": [1, 2],
                "runtime_ms": 1.0,
                "hidden": True,
            },
        ][:total],
        "passed": passed,
        "total": total,
        "truncated": False,
        "meta": {"image": "interview-sandbox-python:local"},
    }


def make_request(language: CodeLanguage = CodeLanguage.PYTHON, request_id: str = "r1") -> ExecuteRequest:
    return ExecuteRequest(
        request_id=request_id,
        language=language,
        code="def two_sum(nums, target):\n    return [0, 1]\n",
        mode="tests",
        signature={
            "function_name": "two_sum",
            "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
            "return_type": "int[]",
        },
        tests=[
            SandboxTestCase(id="t1", args=[[2, 7], 9], expected=[0, 1]),
            SandboxTestCase(id="t2", args=[[3, 3], 6], expected=[0, 1], hidden=True),
        ],
    )


class Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class Sleeper:
    def __init__(self, clock: Clock | None = None) -> None:
        self.calls: list[float] = []
        self.clock = clock

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.clock is not None:
            self.clock.now += seconds


def client(**kwargs) -> SandboxClient:
    kwargs.setdefault("sleep", Sleeper())
    return SandboxClient(BASE, SECRET, **kwargs)


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_compute_timeout_covers_queue_compile_and_two_runs() -> None:
    req = make_request()
    # 15 s queue + 15 s compile + 2 x 5 s + 10 s overhead
    assert compute_timeout(req) == pytest.approx(50.0)
    assert compute_timeout(req, floor=90) == 90
    slow = req.model_copy(update={"time_limit_ms": 20000, "compile_time_limit_ms": 60000})
    assert compute_timeout(slow) == pytest.approx(15 + 60 + 40 + 10)


async def test_signs_every_request_with_hmac_of_timestamp_and_body() -> None:
    clock = Clock()
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").respond(200, json=ok_body())
        sandbox = client(clock=clock)
        response = await sandbox.execute(make_request())
        await sandbox.aclose()
    assert response.status == RunStatus.PASSED
    sent = route.calls.last.request
    timestamp = sent.headers[TIMESTAMP_HEADER]
    assert timestamp == str(int(clock.now))
    expected = hmac.new(SECRET.encode(), timestamp.encode() + b"." + sent.content, hashlib.sha256).hexdigest()
    assert sent.headers[SIGNATURE_HEADER] == expected == sign(SECRET.encode(), timestamp, sent.content)
    body = json.loads(sent.content)
    assert body["request_id"] == "r1" and body["mode"] == "tests" and len(body["tests"]) == 2


async def test_retries_503_with_retry_after_using_a_fresh_signature_and_request_id() -> None:
    clock = Clock()
    sleeper = Sleeper(clock)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").mock(
            side_effect=[
                httpx.Response(
                    503,
                    headers={"Retry-After": "1"},
                    json={"error": {"code": "queue_full", "message": "busy"}},
                ),
                httpx.Response(200, json=ok_body("r1.r2")),
            ]
        )
        sandbox = client(clock=clock, sleep=sleeper)
        response = await sandbox.execute(make_request())
        await sandbox.aclose()
    assert response.status == RunStatus.PASSED
    assert route.call_count == 2
    first, second = (call.request for call in route.calls)
    assert first.headers[SIGNATURE_HEADER] != second.headers[SIGNATURE_HEADER]
    assert int(second.headers[TIMESTAMP_HEADER]) > int(first.headers[TIMESTAMP_HEADER])
    assert json.loads(second.content)["request_id"] == "r1.r2"
    assert sleeper.calls and sleeper.calls[0] >= 1


@pytest.mark.parametrize(
    ("status", "headers", "reason"),
    [
        (503, {}, "queue_timeout"),  # 503 without Retry-After: no retry
        (500, {}, "internal"),
        (502, {}, "http_502"),
    ],
)
async def test_server_errors_without_retry_after_are_not_retried(
    status: int, headers: dict, reason: str
) -> None:
    body = {"error": {"code": reason, "message": "x"}} if reason != "http_502" else None
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").mock(
            return_value=httpx.Response(status, headers=headers, json=body)
            if body
            else httpx.Response(status, text="bad gateway")
        )
        sandbox = client()
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert route.call_count == 1
    assert info.value.reason == reason
    assert info.value.status_code == status


async def test_retry_after_longer_than_the_cap_is_not_waited_for() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").respond(
            503, headers={"Retry-After": "30"}, json={"error": {"code": "queue_full"}}
        )
        sandbox = client(max_retry_after=5)
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert route.call_count == 1
    assert info.value.retry_after == 30


async def test_connect_errors_are_retried_then_succeed() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").mock(
            side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=ok_body("r1.r2"))]
        )
        sandbox = client()
        response = await sandbox.execute(make_request())
        await sandbox.aclose()
    assert response.passed == 2
    assert route.call_count == 2


async def test_connect_errors_exhaust_attempts() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").mock(side_effect=httpx.ConnectError("refused"))
        sandbox = client(max_attempts=3)
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert route.call_count == 3
    assert info.value.reason == "transport"
    assert info.value.attempts == 3


async def test_read_timeout_is_never_retried_because_the_code_may_have_run() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").mock(side_effect=httpx.ReadTimeout("slow"))
        sandbox = client()
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert route.call_count == 1
    assert info.value.reason == "timeout"


async def test_dropped_response_is_not_retried() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").mock(side_effect=httpx.RemoteProtocolError("server disconnected"))
        sandbox = client()
        with pytest.raises(SandboxFault):
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "reason"), [(401, "unauthorized"), (422, "validation_failed"), (413, "payload_too_large")]
)
async def test_client_errors_map_to_platform_faults(status: int, reason: str) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/execute").respond(
            status, json={"error": {"code": reason if status != 401 else "unauthorized", "message": "no"}}
        )
        sandbox = client()
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert info.value.reason == reason
    assert info.value.status_code == status


async def test_malformed_success_body_is_a_platform_fault() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/execute").respond(200, text="not json")
        sandbox = client()
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert info.value.reason == "bad_response"


async def test_circuit_opens_after_repeated_faults_and_fails_fast() -> None:
    breaker = CircuitBreaker("sandbox-test", failure_threshold=2, reset_seconds=60)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/v1/execute").respond(500, json={"error": {"code": "internal"}})
        sandbox = client(breaker=breaker)
        for _ in range(2):
            with pytest.raises(SandboxFault):
                await sandbox.execute(make_request())
        with pytest.raises(SandboxFault) as info:
            await sandbox.execute(make_request())
        await sandbox.aclose()
    assert route.call_count == 2  # third call never reached the network
    assert info.value.reason == "circuit_open"


async def test_execute_safe_returns_candidate_safe_error_and_counts_sandbox_error_metric() -> None:
    labels_fault = {"language": "go", "status": "sandbox_error"}
    labels_ok = {"language": "go", "status": "failed"}
    before_fault = sample("interview_coding_executions_total", labels_fault)
    before_ok = sample("interview_coding_executions_total", labels_ok)
    before_seconds = sample("interview_coding_execution_seconds_count", {"language": "go"})
    with respx.mock(base_url=BASE) as mock:
        mock.post("/v1/execute").mock(
            side_effect=[
                httpx.Response(
                    500,
                    json={
                        "error": {
                            "code": "docker_error",
                            "message": "docker socket gone /var/run/docker.sock",
                        }
                    },
                ),
                httpx.Response(200, json=ok_body(status="failed", passed=1)),
            ]
        )
        sandbox = client()
        response, fault = await sandbox.execute_safe(make_request(CodeLanguage.GO))
        ok, no_fault = await sandbox.execute_safe(make_request(CodeLanguage.GO))
        await sandbox.aclose()
    assert fault is not None and no_fault is None
    assert response.status == RunStatus.ERROR
    assert response.stderr == SANDBOX_BUSY_MESSAGE
    assert "docker" not in response.stderr.lower()
    assert response.meta["platform_fault"] is True and response.meta["reason"] == "docker_error"
    assert response.total == 2 and response.passed == 0
    assert ok.status == RunStatus.FAILED
    assert sample("interview_coding_executions_total", labels_fault) == before_fault + 1
    assert sample("interview_coding_executions_total", labels_ok) == before_ok + 1
    assert sample("interview_coding_execution_seconds_count", {"language": "go"}) == before_seconds + 2


async def test_health() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/health").respond(200, json={"status": "ok", "runtimes": {}})
        sandbox = client()
        assert (await sandbox.health())["status"] == "ok"
        mock.get("/health").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(SandboxFault):
            await sandbox.health()
        await sandbox.aclose()


def test_rejects_short_secrets() -> None:
    with pytest.raises(ValueError):
        SandboxClient(BASE, "short")
