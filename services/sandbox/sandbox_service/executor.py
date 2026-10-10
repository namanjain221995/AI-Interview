"""Execution engine: one locked-down, single-use container per request.

Every container gets: no network, read-only root filesystem, a size-limited
nosuid/nodev tmpfs on /tmp (exec allowed for compiled binaries), memory limit
with swap disabled, a CPU quota, a pids limit, all capabilities dropped,
no-new-privileges, Docker's default seccomp profile, a non-root user,
nofile/fsize/core ulimits, tini as init, no IPC namespace sharing and no
/dev/shm, no mounts, no logging driver (output is only read through the
attach stream), oom_score_adj=1000 and a reduced CPU weight so the host's
other workloads win under pressure. The job (code, tests, nonce) is delivered
on stdin; the container is killed at the service-side hard deadline and
always removed (a janitor sweeps leftovers, e.g. after a crash).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.types import LogConfig, Ulimit
from requests.exceptions import RequestException

from . import metrics
from .config import Settings
from .drivers import ContainerOutcome, Driver, Plan, Rejected
from .logging import get_logger
from .models import CodeLanguage, ExecuteRequest, ExecuteResponse, RunStatus
from .protocol import PROTOCOL_VERSION, StreamDemux, encode_job, extract_result, new_nonce

log = get_logger(__name__)

MANAGED_LABEL = "interview.sandbox.managed"
RUNTIMES = ("python", "node", "java", "go", "cpp")
RUNTIME_LANGUAGES = {
    "python": ("python", "sql"),
    "node": ("javascript", "typescript"),
    "java": ("java",),
    "go": ("go",),
    "cpp": ("cpp",),
}
_TOOLCHAIN_ENV = {"python": "PYTHON_VERSION", "node": "NODE_VERSION", "java": "JAVA_VERSION"}


class SandboxUnavailable(Exception):
    """The sandbox cannot run anything right now (HTTP 503)."""

    def __init__(self, code: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


class DockerSandbox:
    """Synchronous Docker Engine API operations (run from worker threads)."""

    def __init__(self, settings: Settings, client_factory: Callable[[], Any] | None = None) -> None:
        self.settings = settings
        self._local = threading.local()
        self._factory = client_factory or self._default_client
        self._cleanup = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sandbox-cleanup")
        self._image_cache: tuple[float, dict[str, Any]] | None = None
        self._image_lock = threading.Lock()

    def _default_client(self) -> docker.APIClient:
        return docker.APIClient(
            base_url=self.settings.docker_url,
            version=self.settings.docker_api_version,
            timeout=self.settings.docker_timeout_seconds,
        )

    def client(self) -> Any:
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._factory()
            self._local.client = client
        return client

    def close(self) -> None:
        self._cleanup.shutdown(wait=True)

    # ------------------------------------------------------------ container
    def host_config(self, api: Any, plan: Plan) -> Any:
        s = self.settings
        memory = f"{plan.container_memory_mb}m"
        extra: dict[str, Any] = {"runtime": s.container_runtime} if s.container_runtime else {}
        return api.create_host_config(
            **extra,
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": f"rw,nosuid,nodev,exec,size={s.tmpfs_size_mb}m,mode=1777"},
            mem_limit=memory,
            memswap_limit=memory,
            nano_cpus=int(s.cpus * 1_000_000_000),
            cpu_shares=s.cpu_shares,
            pids_limit=s.pids_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            init=True,
            ipc_mode="none",
            privileged=False,
            ulimits=[
                Ulimit(name="nofile", soft=s.nofile_limit, hard=s.nofile_limit),
                Ulimit(name="fsize", soft=s.fsize_limit_mb << 20, hard=s.fsize_limit_mb << 20),
                Ulimit(name="core", soft=0, hard=0),
            ],
            log_config=LogConfig(type="none"),
            oom_score_adj=1000,
        )

    def run(self, plan: Plan, language: str) -> ContainerOutcome:
        s = self.settings
        api = self.client()
        image = s.image_for(plan.runtime)
        nonce = new_nonce()
        payload = encode_job({**plan.job, "nonce": nonce})
        t0 = time.monotonic()
        try:
            created = api.create_container(
                image=image,
                user=s.container_user,
                environment={"HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                working_dir="/tmp",
                hostname="sandbox",
                stdin_open=True,
                detach=False,
                tty=False,
                network_disabled=True,
                labels={MANAGED_LABEL: "1", "interview.sandbox.language": language},
                host_config=self.host_config(api, plan),
            )
        except ImageNotFound as exc:
            metrics.CONTAINER_FAILURES.labels("image_missing").inc()
            raise SandboxUnavailable("runtime_unavailable", f"runtime image for {language} is not available") from exc
        except (APIError, DockerException, RequestException) as exc:
            metrics.CONTAINER_FAILURES.labels("create_failed").inc()
            log.error("sandbox.create_failed", language=language, error=str(exc)[:300])
            raise SandboxUnavailable("docker_error", "the sandbox could not create a container", retry_after=2) from exc
        cid = created["Id"]
        outcome = ContainerOutcome(runner=None, image=image, container=_short_hash(cid))
        timings = outcome.timings_ms
        timings["create"] = (time.monotonic() - t0) * 1000
        try:
            self._attach_and_run(api, cid, plan, payload, nonce, outcome)
        except (APIError, DockerException, RequestException, OSError) as exc:
            metrics.CONTAINER_FAILURES.labels("docker_error").inc()
            outcome.error = f"docker error: {str(exc)[:200]}"
            outcome.infra_error = True
            log.error("sandbox.container_error", language=language, container=outcome.container, error=str(exc)[:300])
        finally:
            self._cleanup.submit(self._remove, cid)
        timings["container_total"] = (time.monotonic() - t0) * 1000
        return outcome

    def _attach_and_run(self, api: Any, cid: str, plan: Plan, payload: bytes, nonce: str, outcome: ContainerOutcome) -> None:
        s = self.settings
        timings = outcome.timings_ms
        sock = api.attach_socket(cid, params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        raw: socket.socket = getattr(sock, "_sock", sock)
        try:
            t_start = time.monotonic()
            api.start(cid)
            t_started = time.monotonic()
            timings["start"] = (t_started - t_start) * 1000
            deadline = t_started + plan.hard_deadline_s
            try:
                raw.settimeout(max(1.0, min(10.0, plan.hard_deadline_s)))
                raw.sendall(payload)
                raw.shutdown(socket.SHUT_WR)
            except OSError as exc:
                outcome.error = f"job delivery failed: {exc}"
                outcome.infra_error = True
            demux = StreamDemux(stdout_cap=s.container_output_cap_bytes)
            hard_cap = 2 * s.container_output_cap_bytes
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome.deadline_exceeded = True
                    break
                raw.settimeout(min(remaining, 1.0))
                try:
                    data = raw.recv(256 * 1024)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                demux.feed(data)
                if demux.total > hard_cap:
                    outcome.output_overflow = True
                    break
            timings["run"] = (time.monotonic() - t_started) * 1000
        finally:
            # Close the raw socket itself: closing docker-py's SocketIO wrapper alone leaves it
            # open, and a container still writing would then block the daemon (and our kill,
            # wait and inspect calls) on a full stream buffer.
            with contextlib.suppress(OSError):
                raw.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                raw.close()
            with contextlib.suppress(Exception):
                sock.close()
        if outcome.deadline_exceeded or outcome.output_overflow:
            metrics.CONTAINER_FAILURES.labels("deadline_kill" if outcome.deadline_exceeded else "output_overflow").inc()
            with contextlib.suppress(APIError, DockerException, RequestException):
                api.kill(cid)
        else:
            outcome.runner = extract_result(demux.stdout, nonce)
        if outcome.runner is not None:
            return  # the runner reported everything; exit code / OOM flag are only needed as a fallback
        try:
            outcome.exit_code = api.wait(cid, timeout=10).get("StatusCode")
        except (APIError, DockerException, RequestException):
            outcome.exit_code = None
        try:
            state = api.inspect_container(cid).get("State", {})
            outcome.oom_killed = bool(state.get("OOMKilled"))
        except (APIError, DockerException, RequestException):
            pass
        if not outcome.deadline_exceeded and not outcome.output_overflow:
            metrics.CONTAINER_FAILURES.labels("no_result").inc()
            diag = demux.stderr.decode("utf-8", "replace").strip()[-300:]
            outcome.error = outcome.error or f"runner produced no result (exit {outcome.exit_code}){': ' + diag if diag else ''}"

    def _remove(self, cid: str) -> None:
        api = self.client()
        for attempt in range(3):
            try:
                api.remove_container(cid, force=True, v=True)
                return
            except NotFound:
                return
            except (APIError, DockerException, RequestException) as exc:
                if attempt == 2:
                    log.warning("sandbox.remove_failed", container=_short_hash(cid), error=str(exc)[:200])
                time.sleep(0.2 * (attempt + 1))

    # --------------------------------------------------------------- health
    def runtime_status(self, force: bool = False) -> dict[str, Any]:
        with self._image_lock:
            now = time.monotonic()
            if not force and self._image_cache and now - self._image_cache[0] < self.settings.image_check_ttl_seconds:
                return self._image_cache[1]
            api = self.client()
            status: dict[str, Any] = {"docker": False, "runtimes": {}}
            try:
                api.ping()
                status["docker"] = True
            except (APIError, DockerException, RequestException, OSError):
                status["docker"] = False
            for runtime in RUNTIMES:
                image = self.settings.image_for(runtime)
                entry: dict[str, Any] = {"image": image, "available": False, "languages": list(RUNTIME_LANGUAGES[runtime])}
                if status["docker"]:
                    try:
                        info = api.inspect_image(image)
                        config = info.get("Config") or {}
                        labels = config.get("Labels") or {}
                        env = dict(e.split("=", 1) for e in (config.get("Env") or []) if "=" in e)
                        protocol = labels.get("interview.sandbox.protocol")
                        entry["available"] = protocol == str(PROTOCOL_VERSION)
                        entry["protocol"] = protocol
                        entry["image_id"] = str(info.get("Id", ""))[7:19]
                        toolchain = labels.get("interview.sandbox.toolchain") or env.get(_TOOLCHAIN_ENV.get(runtime, ""), "")
                        if toolchain:
                            entry["toolchain"] = toolchain
                        if protocol != str(PROTOCOL_VERSION):
                            entry["error"] = "image protocol mismatch"
                    except ImageNotFound:
                        entry["error"] = "image not built (docker compose --profile runtimes build)"
                    except (APIError, DockerException, RequestException) as exc:
                        entry["error"] = str(exc)[:200]
                metrics.RUNTIME_AVAILABLE.labels(runtime).set(1 if entry["available"] else 0)
                status["runtimes"][runtime] = entry
            self._image_cache = (now, status)
            return status

    # -------------------------------------------------------------- janitor
    def sweep(self) -> int:
        api = self.client()
        removed = 0
        now = time.time()
        try:
            containers = api.containers(all=True, filters={"label": f"{MANAGED_LABEL}=1"})
        except (APIError, DockerException, RequestException) as exc:
            log.warning("sandbox.janitor_list_failed", error=str(exc)[:200])
            return 0
        for c in containers:
            age = now - float(c.get("Created") or now)
            running = c.get("State") == "running"
            if (running and age > self.settings.stale_container_seconds) or (not running and age > 60):
                try:
                    api.remove_container(c["Id"], force=True, v=True)
                    removed += 1
                except NotFound:
                    continue
                except (APIError, DockerException, RequestException) as exc:
                    log.warning("sandbox.janitor_remove_failed", container=_short_hash(c["Id"]), error=str(exc)[:200])
        if removed:
            metrics.JANITOR_REMOVED.inc(removed)
            log.info("sandbox.janitor_removed", count=removed)
        return removed


class Executor:
    """Async front: validation, bounded queue, concurrency slots, metrics, logs."""

    def __init__(self, settings: Settings, sandbox: DockerSandbox, drivers: dict[CodeLanguage, Driver]) -> None:
        self.settings = settings
        self.sandbox = sandbox
        self.drivers = drivers
        self._slots = asyncio.Semaphore(settings.max_concurrency)
        self._pool = ThreadPoolExecutor(max_workers=settings.max_concurrency, thread_name_prefix="sandbox-exec")
        self.waiting = 0
        self.inflight = 0

    def close(self) -> None:
        self._pool.shutdown(wait=True)
        self.sandbox.close()

    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        started = time.monotonic()
        driver = self.drivers[req.language]
        try:
            plan = driver.plan(req)
        except Rejected as exc:
            metrics.REJECTIONS.labels("invalid_request").inc()
            metrics.EXECUTIONS.labels(req.language.value, req.mode, RunStatus.REJECTED.value).inc()
            log.info("sandbox.rejected", request_id=req.request_id[:64], language=req.language.value, reason=str(exc)[:200])
            return ExecuteResponse(
                request_id=req.request_id,
                status=RunStatus.REJECTED,
                stderr=str(exc),
                total=len(req.tests) if req.mode == "tests" else 0,
                meta={"reason": str(exc)},
            )
        if self.waiting >= self.settings.max_queue and self._slots.locked():
            metrics.REJECTIONS.labels("queue_full").inc()
            raise SandboxUnavailable("queue_full", "the sandbox queue is full", retry_after=2)
        self.waiting += 1
        metrics.QUEUED.set(self.waiting)
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.settings.queue_timeout_seconds)
        except TimeoutError as exc:
            metrics.REJECTIONS.labels("queue_timeout").inc()
            raise SandboxUnavailable("queue_timeout", "no execution slot became available in time", retry_after=3) from exc
        finally:
            self.waiting -= 1
            metrics.QUEUED.set(self.waiting)
        queue_s = time.monotonic() - started
        metrics.QUEUE_WAIT_SECONDS.observe(queue_s)
        self.inflight += 1
        metrics.INFLIGHT.set(self.inflight)
        exec_started = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            outcome = await loop.run_in_executor(self._pool, self.sandbox.run, plan, req.language.value)
        finally:
            self._slots.release()
            self.inflight -= 1
            metrics.INFLIGHT.set(self.inflight)
        exec_s = time.monotonic() - exec_started
        if outcome.infra_error:
            # Never the candidate's crash (and never a scored final run): the caller retries or shows a neutral
            # "we couldn't run your code" (S19).
            metrics.EXECUTIONS.labels(req.language.value, req.mode, "infra_error").inc()
            raise SandboxUnavailable("docker_error", "the sandbox could not run the program", retry_after=2)
        response = driver.interpret(req, plan, outcome)
        timings = response.meta.setdefault("timings_ms", {})
        timings["queue"] = round(queue_s * 1000, 3)
        timings["execution"] = round(exec_s * 1000, 3)
        timings["total"] = round((time.monotonic() - started) * 1000, 3)
        for key, value in list(timings.items()):
            timings[key] = round(float(value), 3)
        self._observe(req, response, exec_s, outcome)
        return response

    def _observe(self, req: ExecuteRequest, response: ExecuteResponse, exec_s: float, outcome: ContainerOutcome) -> None:
        language = req.language.value
        metrics.EXECUTIONS.labels(language, req.mode, response.status.value).inc()
        metrics.EXECUTION_SECONDS.labels(language, req.mode).observe(exec_s)
        timings = response.meta.get("timings_ms", {})
        runner_ms = float(timings.get("runner", 0.0) or 0.0)
        if runner_ms:
            metrics.CONTAINER_OVERHEAD_SECONDS.labels(language).observe(max(0.0, exec_s - runner_ms / 1000))
        for phase in ("compile", "run", "tests", "hidden_tests"):
            if phase in timings:
                metrics.PHASE_SECONDS.labels(language, phase).observe(float(timings[phase]) / 1000)
        log.info(
            "sandbox.executed",
            request_id=req.request_id[:64],
            language=language,
            mode=req.mode,
            status=response.status.value,
            passed=response.passed,
            total=response.total,
            exit_code=response.exit_code,
            runtime_ms=response.runtime_ms,
            memory_kb=response.memory_kb,
            queue_ms=timings.get("queue"),
            execution_ms=round(exec_s * 1000, 1),
            compile_ms=timings.get("compile"),
            runner_ms=timings.get("runner"),
            container=outcome.container,
            error=outcome.error,
        )
