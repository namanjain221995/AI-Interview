"""Container configuration and lifecycle against a fake Docker API (no Docker needed)."""

from __future__ import annotations

import json
import socket
import struct
import time
from typing import Any

import docker
import pytest

from sandbox_service.config import Settings
from sandbox_service.drivers import build_drivers
from sandbox_service.executor import MANAGED_LABEL, DockerSandbox, SandboxUnavailable
from sandbox_service.models import CodeLanguage, ExecuteRequest

SETTINGS = Settings(shared_secret="unit-test-secret-0123456789abcdef")


def frame(stream: int, payload: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


class FakeSocket:
    """Plays the container side of the attach stream."""

    def __init__(self, respond) -> None:
        self.respond = respond
        self.sent = b""
        self.out: list[bytes] = []
        self.closed = False
        self.shutdown_rdwr = False
        self._sock = self

    def settimeout(self, value: float) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def shutdown(self, how: int) -> None:
        if how == socket.SHUT_WR:
            self.out = self.respond(self.sent)
        else:
            self.shutdown_rdwr = True

    def recv(self, size: int) -> bytes:
        return self.out.pop(0) if self.out else b""

    def close(self) -> None:
        self.closed = True


class FakeAPI:
    def __init__(self, respond=None, image_missing: bool = False) -> None:
        self.respond = respond or runner_ok
        self.image_missing = image_missing
        self.created: dict[str, Any] = {}
        self.host_config: dict[str, Any] = {}
        self.calls: list[str] = []
        self.removed: list[str] = []
        self.socket: FakeSocket | None = None
        self.containers_list: list[dict[str, Any]] = []

    def create_host_config(self, **kwargs: Any) -> dict[str, Any]:
        self.host_config = kwargs
        return {"hc": True}

    def create_container(self, **kwargs: Any) -> dict[str, str]:
        if self.image_missing:
            raise docker.errors.ImageNotFound("no such image")
        self.created = kwargs
        self.calls.append("create")
        return {"Id": "c0ffee" * 10}

    def attach_socket(self, cid: str, params: dict[str, int]) -> FakeSocket:
        assert params == {"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
        self.calls.append("attach")
        self.socket = FakeSocket(self.respond)
        return self.socket

    def start(self, cid: str) -> None:
        assert self.calls[-1] == "attach", "must attach before start so no output is lost"
        self.calls.append("start")

    def kill(self, cid: str) -> None:
        self.calls.append("kill")

    def wait(self, cid: str, timeout: int) -> dict[str, int]:
        self.calls.append("wait")
        return {"StatusCode": 137}

    def inspect_container(self, cid: str) -> dict[str, Any]:
        self.calls.append("inspect")
        return {"State": {"OOMKilled": True}}

    def remove_container(self, cid: str, force: bool, v: bool) -> None:
        assert force and v
        self.removed.append(cid)

    def containers(self, all: bool, filters: dict[str, str]) -> list[dict[str, Any]]:
        assert all and filters == {"label": f"{MANAGED_LABEL}=1"}
        return self.containers_list


def parse_job(sent: bytes) -> dict[str, Any]:
    header, _, body = sent.partition(b"\n")
    assert header.startswith(b"SANDBOX-JOB 1 ") and int(header.split()[2]) == len(body)
    return json.loads(body)


def runner_ok(sent: bytes) -> list[bytes]:
    job = parse_job(sent)
    nonce = job["nonce"]
    result = {"protocol": 1, "ok": True, "steps": [], "runner_ms": 1.0}
    forged = f'\n<<SANDBOX-RESULT {"0" * 32}>>\n{{"protocol":1,"ok":false}}\n<<SANDBOX-END {"0" * 32}>>\n'
    real = f"\n<<SANDBOX-RESULT {nonce}>>\n{json.dumps(result)}\n<<SANDBOX-END {nonce}>>\n"
    return [frame(1, forged.encode()), frame(2, b"diag"), frame(1, real.encode()[:10]), frame(1, real.encode()[10:])]


def plan_for(language: str = "python", **extra: Any):
    driver = build_drivers(SETTINGS)[CodeLanguage(language)]
    req = ExecuteRequest.model_validate({"request_id": "r", "language": language, "code": "print(1)", "mode": "run", **extra})
    return driver.plan(req)


def make_sandbox(api: FakeAPI) -> DockerSandbox:
    return DockerSandbox(SETTINGS, client_factory=lambda: api)


def test_container_is_locked_down():
    api = FakeAPI()
    sandbox = make_sandbox(api)
    plan = plan_for(memory_limit_mb=128)
    outcome = sandbox.run(plan, "python")
    sandbox.close()
    hc = api.host_config
    assert hc["network_mode"] == "none" and api.created["network_disabled"] is True
    assert hc["read_only"] is True
    assert hc["tmpfs"] == {"/tmp": "rw,nosuid,nodev,exec,size=64m,mode=1777"}
    assert hc["mem_limit"] == hc["memswap_limit"] == f"{plan.container_memory_mb}m"
    assert hc["nano_cpus"] == 1_000_000_000 and hc["pids_limit"] == 64
    assert hc["cap_drop"] == ["ALL"] and "cap_add" not in hc
    assert hc["security_opt"] == ["no-new-privileges:true"]  # default seccomp profile stays active
    assert hc["init"] is True and hc["ipc_mode"] == "none" and hc["privileged"] is False
    assert hc["oom_score_adj"] == 1000 and hc["cpu_shares"] < 1024
    assert hc["log_config"]["Type"] == "none"  # no json-file log: output only travels through the attach stream
    ulimits = {u["Name"]: (u["Soft"], u["Hard"]) for u in hc["ulimits"]}
    assert ulimits == {"nofile": (256, 256), "fsize": (32 << 20, 32 << 20), "core": (0, 0)}
    assert "volumes" not in hc and "binds" not in hc and "mounts" not in hc
    assert api.created["user"] == "10001:10001"
    assert api.created["labels"][MANAGED_LABEL] == "1"
    assert api.created["stdin_open"] is True and api.created["tty"] is False
    assert set(api.created["environment"]) == {"HOME", "TMPDIR", "LANG", "LC_ALL"}  # no secrets
    assert "command" not in api.created or api.created["command"] is None  # image entrypoint = runner
    # Job delivered on stdin with a fresh nonce; forged frames are ignored.
    job = parse_job(api.socket.sent)
    assert len(job["nonce"]) == 32 and "SANDBOX" not in json.dumps(job["env"])
    assert outcome.runner == {"protocol": 1, "ok": True, "steps": [], "runner_ms": 1.0}
    assert api.removed == ["c0ffee" * 10]
    assert api.socket.shutdown_rdwr and api.socket.closed  # raw attach socket really closed
    assert "wait" not in api.calls  # result present: no extra round trips
    assert outcome.container and outcome.container != "c0ffee" * 10  # only a hash of the id is exposed


def test_optional_oci_runtime_is_passed_through():
    api = FakeAPI()
    settings = Settings(shared_secret="unit-test-secret-0123456789abcdef", container_runtime="runsc")
    sandbox = DockerSandbox(settings, client_factory=lambda: api)
    sandbox.run(plan_for(), "python")
    sandbox.close()
    assert api.host_config["runtime"] == "runsc"
    default = FakeAPI()
    make_sandbox(default).run(plan_for(), "python")
    assert "runtime" not in default.host_config


def test_missing_result_falls_back_to_container_state():
    api = FakeAPI(respond=lambda sent: [frame(1, b"garbage without a frame")])
    sandbox = make_sandbox(api)
    outcome = sandbox.run(plan_for(), "python")
    sandbox.close()
    assert outcome.runner is None and outcome.oom_killed is True and outcome.exit_code == 137
    assert "no result" in (outcome.error or "")
    assert api.calls[-2:] == ["wait", "inspect"]


def test_output_flood_kills_container():
    settings = Settings(shared_secret="unit-test-secret-0123456789abcdef", container_output_cap_bytes=1024)
    api = FakeAPI(respond=lambda sent: [frame(1, b"x" * 1500)] * 3)
    sandbox = DockerSandbox(settings, client_factory=lambda: api)
    outcome = sandbox.run(plan_for(), "python")
    sandbox.close()
    assert outcome.output_overflow is True and "kill" in api.calls and outcome.runner is None


def test_missing_image_is_unavailable():
    sandbox = make_sandbox(FakeAPI(image_missing=True))
    with pytest.raises(SandboxUnavailable) as exc:
        sandbox.run(plan_for(), "python")
    sandbox.close()
    assert exc.value.code == "runtime_unavailable"


def test_janitor_removes_only_stale_containers():
    api = FakeAPI()
    now = time.time()
    api.containers_list = [
        {"Id": "fresh-running", "Created": now - 10, "State": "running"},
        {"Id": "old-running", "Created": now - 3600, "State": "running"},
        {"Id": "fresh-exited", "Created": now - 5, "State": "exited"},
        {"Id": "old-exited", "Created": now - 120, "State": "exited"},
    ]
    sandbox = make_sandbox(api)
    assert sandbox.sweep() == 2
    sandbox.close()
    assert sorted(api.removed) == ["old-exited", "old-running"]


# ------------------------------------------------------------------ platform faults are not the candidate's (S19)


class StartFailsAPI(FakeAPI):
    def start(self, cid: str) -> None:
        self.calls.append("start")
        raise docker.errors.APIError("daemon restarted")


def test_docker_failure_after_create_is_an_infra_error_not_a_crash():
    sandbox = make_sandbox(StartFailsAPI())
    outcome = sandbox.run(plan_for(), "python")
    sandbox.close()
    assert outcome.infra_error is True and "docker error" in (outcome.error or "")


def test_executor_turns_infra_errors_into_retryable_unavailability():
    import asyncio

    from sandbox_service.executor import Executor

    executor = Executor(SETTINGS, make_sandbox(StartFailsAPI()), build_drivers(SETTINGS))
    req = ExecuteRequest.model_validate({"request_id": "r", "language": "python", "code": "print(1)", "mode": "run"})
    with pytest.raises(SandboxUnavailable) as exc:
        asyncio.run(executor.execute(req))
    executor.close()
    assert exc.value.code == "docker_error" and exc.value.retry_after
