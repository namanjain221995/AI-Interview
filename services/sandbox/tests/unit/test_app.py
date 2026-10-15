"""HTTP layer with a fake executor (no Docker)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sandbox_service.app import create_app
from sandbox_service.auth import sign
from sandbox_service.config import Settings
from sandbox_service.drivers import build_drivers
from sandbox_service.executor import Executor, SandboxUnavailable
from sandbox_service.models import ExecuteRequest, ExecuteResponse, RunStatus

SECRET = "unit-test-secret-0123456789abcdef"


class FakeSandbox:
    def __init__(self) -> None:
        self.closed = False

    def runtime_status(self, force: bool = False) -> dict[str, Any]:
        runtimes = {
            r: {"image": f"interview-sandbox-{r}:local", "available": True} for r in ("python", "node", "java", "go", "cpp")
        }
        return {"docker": True, "runtimes": runtimes}

    def sweep(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class FakeExecutor(Executor):
    def __init__(self, settings: Settings, behaviour: str = "ok") -> None:
        super().__init__(settings, FakeSandbox(), build_drivers(settings))  # type: ignore[arg-type]
        self.behaviour = behaviour
        self.seen: list[ExecuteRequest] = []

    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        self.seen.append(req)
        if self.behaviour == "busy":
            raise SandboxUnavailable("queue_timeout", "no execution slot became available in time", retry_after=3)
        await asyncio.sleep(0)
        return ExecuteResponse(request_id=req.request_id, status=RunStatus.PASSED, stdout="hi\n")


@pytest.fixture()
def settings() -> Settings:
    return Settings(shared_secret=SECRET, max_request_bytes=64 * 1024)


def signed(body: bytes, ts: int | None = None) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    return {
        "X-Sandbox-Timestamp": stamp,
        "X-Sandbox-Signature": sign(SECRET.encode(), stamp, body),
        "Content-Type": "application/json",
    }


def body_for(**extra: Any) -> bytes:
    payload = {"request_id": "req-1", "language": "python", "code": "print('hi')", "mode": "run", **extra}
    return json.dumps(payload).encode()


def make_client(settings: Settings, behaviour: str = "ok") -> tuple[TestClient, FakeExecutor]:
    executor = FakeExecutor(settings, behaviour)
    app = create_app(settings, executor=executor, run_janitor=False)
    return TestClient(app), executor


def test_execute_requires_valid_signature(settings):
    client, executor = make_client(settings)
    with client:
        body = body_for()
        assert client.post("/v1/execute", content=body, headers={"Content-Type": "application/json"}).status_code == 401
        bad = signed(body)
        bad["X-Sandbox-Signature"] = "f" * 64
        response = client.post("/v1/execute", content=body, headers=bad)
        assert response.status_code == 401 and response.json()["error"]["code"] == "unauthorized"
        assert client.post("/v1/execute", content=body, headers=signed(body, ts=int(time.time()) - 61)).status_code == 401
        ok = client.post("/v1/execute", content=body, headers=signed(body))
        assert ok.status_code == 200 and ok.json()["status"] == "passed" and ok.json()["stdout"] == "hi\n"
        assert executor.seen[0].request_id == "req-1"


def test_execute_rejects_replays_and_large_bodies(settings):
    client, _ = make_client(settings)
    with client:
        body = body_for()
        headers = signed(body)
        assert client.post("/v1/execute", content=body, headers=headers).status_code == 200
        assert client.post("/v1/execute", content=body, headers=headers).status_code == 401
        big = body_for(code="x" * (70 * 1024))
        response = client.post("/v1/execute", content=big, headers=signed(big))
        assert response.status_code == 413 and response.json()["error"]["code"] == "payload_too_large"


def test_execute_validation_errors(settings):
    client, _ = make_client(settings)
    with client:
        for payload in (b"not json", body_for(language="cobol"), body_for(time_limit_ms=1), body_for(unknown_field=1)):
            response = client.post("/v1/execute", content=payload, headers=signed(payload))
            assert response.status_code == 422, payload
            assert response.json()["error"]["code"] == "validation_failed"


def test_execute_busy_returns_503_with_retry_after(settings):
    client, _ = make_client(settings, behaviour="busy")
    with client:
        body = body_for()
        response = client.post("/v1/execute", content=body, headers=signed(body))
        assert response.status_code == 503
        assert response.headers["retry-after"] == "3"
        assert response.json()["error"]["code"] == "queue_timeout"
        assert response.json()["error"]["request_id"] == "req-1"


def test_health_runtimes_metrics(settings):
    client, _ = make_client(settings)
    with client:
        health = client.get("/health")
        assert health.status_code == 200
        data = health.json()
        assert data["status"] == "ok" and data["languages"]["sql"] == {"runtime": "python", "available": True}
        assert client.get("/v1/runtimes").status_code == 401
        runtimes = client.get("/v1/runtimes", headers=signed(b""))
        assert runtimes.status_code == 200
        assert runtimes.json()["languages"]["typescript"]["runtime"] == "node"
        assert "int[][]" in runtimes.json()["signature"]["types"]
        assert "interview_sandbox_executions_total" in client.get("/metrics").text
        assert client.get("/docs").status_code == 404


def test_rejected_requests_never_reach_docker(settings):
    executor = Executor(settings, FakeSandbox(), build_drivers(settings))  # type: ignore[arg-type]
    req = ExecuteRequest.model_validate(
        {
            "request_id": "r",
            "language": "python",
            "code": "x",
            "mode": "tests",
            "signature": {"function_name": "f"},
            "tests": [{"id": "a"}],
        }
    )
    response = asyncio.run(executor.execute(req))
    assert response.status == RunStatus.REJECTED and "return_type" in response.stderr


def test_ready_names_the_languages_whose_runtime_is_missing(settings):
    """S19: /ready is 503 until every runtime image is present (/health stays 200 while Docker answers)."""

    class MissingJava(FakeSandbox):
        def runtime_status(self, force: bool = False) -> dict[str, Any]:
            status = super().runtime_status(force)
            status["runtimes"]["java"]["available"] = False
            return status

    class Partial(FakeExecutor):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.sandbox = MissingJava()  # type: ignore[assignment]

    client = TestClient(create_app(settings, executor=Partial(settings), run_janitor=False))
    with client:
        ready = client.get("/ready")
        assert ready.status_code == 503 and ready.json() == {"ready": False, "docker": True, "missing_languages": ["java"]}
        assert client.get("/health").status_code == 200
    healthy = TestClient(create_app(settings, executor=FakeExecutor(settings), run_janitor=False))
    with healthy:
        assert healthy.get("/ready").json() == {"ready": True, "docker": True, "missing_languages": []}
