#!/usr/bin/env python3
"""In-container job runner for the interview code sandbox (protocol 1).

The sandbox service starts one single-use container per execution and talks
to this runner only through the container's stdio:

stdin   ``b"SANDBOX-JOB 1 <length>\\n"`` followed by ``<length>`` bytes of UTF-8
        JSON (the job). The job is read completely before any untrusted
        process exists, so nothing the candidate runs can ever read it.
stdout  exactly one frame::

            \\n<<SANDBOX-RESULT {nonce}>>\\n{result json}\\n<<SANDBOX-END {nonce}>>\\n

Untrusted processes never inherit the runner's stdio: their stdin/stdout/
stderr (and the optional harness result channel on fd 3) are pipes owned by
the runner, which bounds and accounts everything it reads. Candidate code can
still reach the container's stdout through ``/proc/1/fd/1`` (tini runs as the
same user), which is why results are framed with a per-execution nonce that
only the service and this (non-dumpable) process know, and why every
untrusted process is killed before the frame is written.

Hidden test expectations never enter the container: harnesses only report
the values the candidate's function returned and the service compares them.

This file is copied verbatim into every runtime image (services/sandbox/
runtimes/<lang>/runner.py); edit services/sandbox/runtimes/runner.py and run
``python services/sandbox/scripts/sync_runner.py``.
"""

import ctypes
import errno
import fcntl
import json
import os
import resource
import selectors
import signal
import sys
import time

PROTOCOL = 1
MAGIC = b"SANDBOX-JOB"
MAX_JOB_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 5000
MAX_ERROR_CHARS = 4000
POLL_INTERVAL = 0.02
POST_EXIT_DRAIN = 0.1
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024
CGROUP_EVENTS = "/sys/fs/cgroup/memory.events"
CGROUP_PEAK = "/sys/fs/cgroup/memory.peak"
CGROUP_STAT = "/sys/fs/cgroup/memory.stat"
SIGNAL_NAMES = {
    int(getattr(signal, name)): name
    for name in dir(signal)
    if name.startswith("SIG") and not name.startswith("SIG_") and isinstance(getattr(signal, name), int)
}
RECORD_TYPES = {"ready", "begin", "result", "fatal", "compile_error", "done"}

_SELF_PID = os.getpid()


def _private_pid_namespace() -> bool:
    """kill(-1) is only safe inside a container's own PID namespace."""
    if _SELF_PID == 1:
        return True
    try:
        with open("/proc/1/comm", encoding="ascii") as fh:
            init = fh.read().strip()
    except OSError:
        return False
    return init in {"docker-init", "tini", "tini-static", "catatonit", "dumb-init"}


_PRIVATE_PIDNS = _private_pid_namespace()


# --------------------------------------------------------------------------- utils


def _now() -> float:
    return time.monotonic()


def _set_nondumpable() -> None:
    """Make /proc/<runner>/{mem,fd,environ} and ptrace unavailable to same-uid processes."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except Exception:  # pragma: no cover - best effort, isolation does not depend on it
        pass


def _read_exact(fd: int, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = os.read(fd, min(remaining, 1 << 20))
        if not chunk:
            raise EOFError("job truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_job() -> dict:
    header = b""
    while not header.endswith(b"\n"):
        ch = os.read(0, 1)
        if not ch:
            raise EOFError("missing job header")
        header += ch
        if len(header) > 64:
            raise ValueError("job header too long")
    parts = header.decode("ascii", "replace").split()
    if len(parts) != 3 or parts[0].encode() != MAGIC or parts[1] != str(PROTOCOL):
        raise ValueError("unsupported job header")
    length = int(parts[2])
    if length <= 0 or length > MAX_JOB_BYTES:
        raise ValueError("job length out of range")
    job = json.loads(_read_exact(0, length).decode("utf-8"))
    if not isinstance(job, dict) or job.get("protocol") != PROTOCOL:
        raise ValueError("unsupported job protocol")
    return job


def _detach_stdin() -> None:
    """Replace fd 0 with /dev/null so new pipes never land on fd 0."""
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)


def _oom_kills() -> int:
    try:
        with open(CGROUP_EVENTS, encoding="ascii") as fh:
            for line in fh:
                key, _, value = line.partition(" ")
                if key == "oom_kill":
                    return int(value)
    except (OSError, ValueError):
        pass
    return 0


def _cgroup_peak_kb() -> int | None:
    try:
        with open(CGROUP_PEAK, encoding="ascii") as fh:
            return int(fh.read().strip()) // 1024
    except (OSError, ValueError):
        return None


def _other_pids() -> list[int]:
    pids = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            pid = int(entry)
            if pid not in (1, _SELF_PID):
                pids.append(pid)
    return pids


def _cgroup_anon_kb() -> int | None:
    """Anonymous memory charged to the container cgroup (copy-on-write pages count once)."""
    try:
        with open(CGROUP_STAT, encoding="ascii") as fh:
            for line in fh:
                key, _, value = line.partition(" ")
                if key == "anon":
                    return int(value) // 1024
    except (OSError, ValueError):
        pass
    return None


def _tree_rss_kb() -> int:
    total = 0
    for pid in _other_pids():
        try:
            with open(f"/proc/{pid}/statm", encoding="ascii") as fh:
                fields = fh.read().split()
            total += int(fields[1]) * PAGE_KB
        except (OSError, ValueError, IndexError):
            continue
    return total


def _kill_all() -> None:
    """SIGKILL every process in the container except tini (pid 1) and the runner."""
    if not _PRIVATE_PIDNS:
        return
    for _ in range(3):
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            pass
        if not _other_pids():
            return
        time.sleep(0.005)


def _set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "replace")


class Capture:
    """Keeps the head and tail of a byte stream within a fixed budget."""

    def __init__(self, budget: int, keep: bool = True) -> None:
        self.keep = keep and budget > 0
        self.head_cap = budget // 4 if self.keep else 0
        self.tail_cap = budget - self.head_cap if self.keep else 0
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def feed(self, data: bytes) -> None:
        self.total += len(data)
        if not self.keep:
            return
        room = self.head_cap - len(self.head)
        if room > 0:
            self.head += data[:room]
            data = data[room:]
        if data:
            self.tail += data
            if len(self.tail) > self.tail_cap:
                del self.tail[: len(self.tail) - self.tail_cap]

    @property
    def truncated(self) -> bool:
        return self.keep and self.total > len(self.head) + len(self.tail)

    def text(self) -> str:
        if not self.keep:
            return ""
        if not self.truncated:
            return _decode(bytes(self.head + self.tail))
        omitted = self.total - len(self.head) - len(self.tail)
        return _decode(bytes(self.head)) + f"\n... [{omitted} bytes truncated] ...\n" + _decode(bytes(self.tail))


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name} is not valid JSON")


class RecordReader:
    """Parses newline-delimited JSON records from a harness result channel.

    Everything on this channel is written by the untrusted process: records are
    size-bounded, type-checked and the first result per test index wins.
    """

    def __init__(self, record_cap: int, total_cap: int, pending: set[int] | None) -> None:
        self.record_cap = record_cap
        self.total_cap = total_cap
        self.pending = pending
        self.buffer = bytearray()
        self.discarding = False
        self.total = 0
        self.records: list[dict] = []
        self.overflow = False
        self.oversized: list[int] = []
        self.current: int | None = None

    def feed(self, data: bytes) -> None:
        self.total += len(data)
        if self.total > self.total_cap:
            self.overflow = True
            return
        start = 0
        while True:
            nl = data.find(b"\n", start)
            if nl < 0:
                chunk = data[start:]
                if not self.discarding:
                    self.buffer += chunk
                    if len(self.buffer) > self.record_cap:
                        self.discarding = True
                        self.buffer.clear()
                        if self.current is not None:
                            self.oversized.append(self.current)
                return
            chunk = data[start:nl]
            if self.discarding:
                self.discarding = False
            else:
                self.buffer += chunk
                if len(self.buffer) > self.record_cap:
                    if self.current is not None:
                        self.oversized.append(self.current)
                else:
                    self._accept(bytes(self.buffer))
            self.buffer.clear()
            start = nl + 1

    def _accept(self, line: bytes) -> None:
        if len(self.records) >= MAX_RECORDS or not line.strip():
            return
        try:
            record = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return
        if not isinstance(record, dict) or record.get("t") not in RECORD_TYPES:
            return
        kind = record["t"]
        if kind in ("begin", "result"):
            index = record.get("i")
            if not isinstance(index, int) or isinstance(index, bool):
                return
            if self.pending is not None and index not in self.pending:
                return
            if kind == "begin":
                self.current = index
                self.records.append({"t": "begin", "i": index})
                return
            if self.pending is not None:
                self.pending.discard(index)
            if self.current == index:
                self.current = None
            clean = {"t": "result", "i": index, "ok": record.get("ok") is True}
            ms = record.get("ms")
            if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms >= 0:
                clean["ms"] = float(min(ms, 1e9))
            if clean["ok"]:
                clean["actual"] = record.get("actual")
            else:
                clean["error"] = str(record.get("error") or "error")[:MAX_ERROR_CHARS]
                clean["error_type"] = str(record.get("error_type") or "runtime")[:32]
            self.records.append(clean)
            return
        if kind in ("fatal", "compile_error"):
            self.records.append(
                {
                    "t": kind,
                    "kind": str(record.get("kind") or kind)[:32],
                    "message": str(record.get("message") or "")[: MAX_ERROR_CHARS * 4],
                }
            )
            return
        self.records.append({"t": kind})


# --------------------------------------------------------------------------- process


def _spawn(argv, env, cwd, cpu_seconds, has_results):
    """fork/exec ``argv`` in a new session with stdio (and fd 3) on fresh pipes."""
    stdin_r, stdin_w = os.pipe()
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    res_r, res_w = os.pipe() if has_results else (None, None)
    exec_r, exec_w = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:  # child
        try:
            exec_w = fcntl.fcntl(exec_w, fcntl.F_DUPFD_CLOEXEC, 32)
            targets = {0: stdin_r, 1: out_w, 2: err_w}
            if res_w is not None:
                targets[3] = res_w
            staged = {target: fcntl.fcntl(src, fcntl.F_DUPFD, 32) for target, src in targets.items()}
            for target, src in staged.items():
                os.dup2(src, target, inheritable=True)
            top = max(targets) + 1
            os.closerange(top, exec_w)
            os.closerange(exec_w + 1, 4096)
            os.setsid()
            for sig in (signal.SIGPIPE, signal.SIGXFSZ, signal.SIGINT, signal.SIGTERM, signal.SIGCHLD):
                signal.signal(sig, signal.SIG_DFL)
            if cpu_seconds:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        except BaseException as exc:  # noqa: BLE001
            try:
                os.write(exec_w, f"{type(exc).__name__}: {exc}".encode("utf-8", "replace")[:2000])
            finally:
                os._exit(127)
    for fd in (stdin_r, out_w, err_w, exec_w) + ((res_w,) if res_w is not None else ()):
        os.close(fd)
    failure = b""
    while True:
        try:
            chunk = os.read(exec_r, 4096)
        except InterruptedError:
            continue
        if not chunk:
            break
        failure += chunk
    os.close(exec_r)
    return pid, stdin_w, out_r, err_r, res_r, failure.decode("utf-8", "replace")


def run_process(
    argv,
    env,
    cwd,
    stdin_data,
    timeout_s,
    memory_limit_kb,
    out_cap,
    err_cap,
    output_hard_cap,
    reader,
):
    """Run one untrusted process to completion under the runner's watchdogs."""
    oom_before = _oom_kills()
    # Memory watchdog: growth of the cgroup's anonymous memory since the step
    # started (exact for forked children sharing pages); per-process RSS sum
    # only when memory.stat is unavailable.
    anon_base = _cgroup_anon_kb() if memory_limit_kb else None
    cpu_seconds = max(1, int(timeout_s + 0.999) + 1)
    start = _now()
    pid, stdin_w, out_r, err_r, res_r, failure = _spawn(argv, env, cwd, cpu_seconds, reader is not None)
    outcome = {
        "exit_code": None,
        "signal": None,
        "timed_out": False,
        "memory_exceeded": False,
        "oom_killed": False,
        "output_limit_exceeded": False,
        "results_overflow": False,
        "spawn_error": None,
        "wall_ms": 0.0,
        "cpu_ms": 0.0,
        "max_rss_kb": 0,
    }
    if failure:
        os.waitpid(pid, 0)
        for fd in (stdin_w, out_r, err_r) + ((res_r,) if res_r is not None else ()):
            os.close(fd)
        outcome["spawn_error"] = failure
        outcome["exit_code"] = 127
        return outcome

    sel = selectors.DefaultSelector()
    streams = {out_r: out_cap, err_r: err_cap}
    if res_r is not None:
        streams[res_r] = reader
    for fd in streams:
        _set_nonblocking(fd)
        sel.register(fd, selectors.EVENT_READ)
    pending_in = memoryview(stdin_data or b"")
    if pending_in:
        _set_nonblocking(stdin_w)
        sel.register(stdin_w, selectors.EVENT_WRITE)
    else:
        os.close(stdin_w)
        stdin_w = None

    deadline = start + timeout_s
    status = None
    rusage = None
    exited_at = None
    next_mem_check = start
    killed = False

    def kill():
        nonlocal killed
        if not killed:
            killed = True
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            _kill_all()

    while True:
        now = _now()
        if status is None:
            if now >= deadline:
                outcome["timed_out"] = True
                kill()
            elif memory_limit_kb and now >= next_mem_check:
                next_mem_check = now + POLL_INTERVAL
                anon = _cgroup_anon_kb() if anon_base is not None else None
                used = anon - anon_base if anon is not None else _tree_rss_kb()
                if used > memory_limit_kb:
                    outcome["memory_exceeded"] = True
                    kill()
        if not sel.get_map():
            if status is not None:
                break
            # Every pipe is closed but the process lives on: keep enforcing the limits.
            reaped, st, ru = os.wait4(pid, os.WNOHANG)
            if reaped:
                status, rusage = st, ru
                outcome["wall_ms"] = (_now() - start) * 1000.0
                break
            time.sleep(POLL_INTERVAL)
            continue
        if exited_at is not None and now - exited_at > POST_EXIT_DRAIN:
            break
        wait = POLL_INTERVAL if status is None else max(0.0, POST_EXIT_DRAIN - (now - exited_at))
        if status is None:
            wait = min(wait, max(0.0, deadline - now))
        for key, _ in sel.select(wait):
            fd = key.fd
            if fd == stdin_w:
                try:
                    written = os.write(fd, pending_in[:65536])
                    pending_in = pending_in[written:]
                except BlockingIOError:
                    written = 0
                except OSError:
                    pending_in = memoryview(b"")
                if not pending_in:
                    sel.unregister(fd)
                    os.close(fd)
                    stdin_w = None
                continue
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                sel.unregister(fd)
                os.close(fd)
                continue
            streams[fd].feed(data)
            if fd != res_r and out_cap.total + err_cap.total > output_hard_cap and not outcome["output_limit_exceeded"]:
                outcome["output_limit_exceeded"] = True
                kill()
            if fd == res_r and reader.overflow and not outcome["results_overflow"]:
                outcome["results_overflow"] = True
                kill()
        if status is None:
            reaped, st, ru = os.wait4(pid, os.WNOHANG)
            if reaped:
                status, rusage = st, ru
                exited_at = _now()
                outcome["wall_ms"] = (exited_at - start) * 1000.0

    if status is None:
        kill()
        _, status, rusage = os.wait4(pid, 0)
    if not outcome["wall_ms"]:
        outcome["wall_ms"] = (_now() - start) * 1000.0
    # Nothing the candidate started may outlive the step.
    _kill_all()
    for key in list(sel.get_map().values()):
        sel.unregister(key.fd)
        os.close(key.fd)
    if stdin_w is not None:
        try:
            os.close(stdin_w)
        except OSError:
            pass
    sel.close()

    if os.WIFEXITED(status):
        outcome["exit_code"] = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        outcome["signal"] = SIGNAL_NAMES.get(sig, f"SIG{sig}")
        outcome["exit_code"] = 128 + sig
    if rusage is not None:
        outcome["cpu_ms"] = (rusage.ru_utime + rusage.ru_stime) * 1000.0
        outcome["max_rss_kb"] = int(rusage.ru_maxrss)
    if _oom_kills() > oom_before:
        outcome["oom_killed"] = True
        if outcome["signal"] == "SIGKILL" and not outcome["timed_out"]:
            outcome["memory_exceeded"] = True
    if outcome["signal"] == "SIGXCPU":
        outcome["timed_out"] = True
    return outcome


# --------------------------------------------------------------------------- steps


def _step_env(base_env, step_env):
    env = dict(base_env)
    for key, value in (step_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[str(key)] = str(value)
    return env


def run_plain_step(step, ctx):
    out_cap = Capture(ctx["capture_bytes"], keep=step.get("capture", True))
    err_cap = Capture(ctx["capture_bytes"], keep=step.get("capture", True))
    reader = None
    if step.get("results_fd"):
        reader = RecordReader(ctx["record_cap_bytes"], ctx["results_cap_bytes"], None)
    stdin = step.get("stdin")
    outcome = run_process(
        step["argv"],
        _step_env(ctx["env"], step.get("env")),
        step.get("cwd") or ctx["workdir"],
        stdin.encode("utf-8") if isinstance(stdin, str) else b"",
        step["timeout_s"],
        step.get("memory_limit_kb"),
        out_cap,
        err_cap,
        ctx["output_hard_cap_bytes"],
        reader,
    )
    result = dict(outcome)
    result.update(
        {
            "name": step["name"],
            "stdout": out_cap.text(),
            "stderr": err_cap.text(),
            "stdout_bytes": out_cap.total,
            "stderr_bytes": err_cap.total,
            "truncated": out_cap.truncated or err_cap.truncated,
            "attempts": 1,
        }
    )
    if reader is not None:
        result["records"] = reader.records[:50]
    return result


def _crash_reason(outcome):
    if outcome.get("spawn_error"):
        return "startup", outcome["spawn_error"]
    if outcome["timed_out"]:
        return "timeout", "time limit exceeded"
    if outcome["memory_exceeded"]:
        return "memory", "memory limit exceeded"
    if outcome["output_limit_exceeded"]:
        return "output_limit", "output limit exceeded"
    if outcome["results_overflow"]:
        return "output_limit", "result channel limit exceeded"
    if outcome["signal"]:
        return "crash", f"terminated by {outcome['signal']}"
    return "crash", f"exited with code {outcome['exit_code']} before finishing"


def run_harness_step(step, ctx):
    """Run a test harness; restart it after a crash so later tests still run."""
    harness = step["harness"]
    tests = harness["tests"]
    order = [t["i"] for t in tests]
    by_index = {t["i"]: t for t in tests}
    outcomes: dict[int, dict] = {}
    out_cap = Capture(ctx["capture_bytes"], keep=step.get("capture", True))
    err_cap = Capture(ctx["capture_bytes"], keep=step.get("capture", True))
    max_attempts = 1 + max(0, int(harness.get("max_restarts", 3)))
    budget = step["timeout_s"]
    started = _now()
    attempts = []
    fatal = None
    ready_seen = False
    remaining = list(order)
    stop_reason = None
    total_wall = 0.0
    max_rss = 0
    cpu = 0.0
    exit_code = None
    sig = None
    flags = {"timed_out": False, "memory_exceeded": False, "oom_killed": False, "output_limit_exceeded": False}

    while remaining and len(attempts) < max_attempts:
        time_left = budget - (_now() - started)
        if time_left <= 0.05:
            flags["timed_out"] = True
            break
        payload = dict(harness.get("input") or {})
        payload["tests"] = [by_index[i] for i in remaining]
        stdin = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        reader = RecordReader(ctx["record_cap_bytes"], ctx["results_cap_bytes"], set(remaining))
        tail_probe = Capture(8192)
        err_probe = _Tee(err_cap, tail_probe)
        outcome = run_process(
            step["argv"],
            _step_env(ctx["env"], step.get("env")),
            step.get("cwd") or ctx["workdir"],
            stdin,
            time_left,
            step.get("memory_limit_kb"),
            out_cap,
            err_probe,
            ctx["output_hard_cap_bytes"],
            reader,
        )
        attempts.append({k: outcome[k] for k in ("exit_code", "signal", "timed_out", "memory_exceeded", "oom_killed", "wall_ms")})
        total_wall += outcome["wall_ms"]
        cpu += outcome["cpu_ms"]
        max_rss = max(max_rss, outcome["max_rss_kb"])
        exit_code, sig = outcome["exit_code"], outcome["signal"]
        for key in flags:
            flags[key] = flags[key] or bool(outcome.get(key))

        finished = set()
        current = None
        done = False
        for record in reader.records:
            kind = record["t"]
            if kind == "ready":
                ready_seen = True
            elif kind == "begin":
                current = record["i"]
            elif kind == "result":
                outcomes[record["i"]] = record
                finished.add(record["i"])
                if current == record["i"]:
                    current = None
            elif kind in ("fatal", "compile_error") and fatal is None:
                fatal = record
            elif kind == "done":
                done = True
        for index in reader.oversized:
            if index not in outcomes:
                outcomes[index] = {
                    "t": "result",
                    "i": index,
                    "ok": False,
                    "error": "return value is too large to report",
                    "error_type": "output_limit",
                }
                finished.add(index)
        remaining = [i for i in remaining if i not in finished]
        if fatal is not None or done or not remaining:
            break
        kind, reason = _crash_reason(outcome)
        tail = tail_probe.text().strip()
        if current is None:
            # Died before (or between) tests: nothing to attribute it to.
            if not ready_seen or kind not in ("crash", "memory"):
                fatal = {"t": "fatal", "kind": "runtime" if kind == "crash" else kind, "message": tail or reason}
                break
            current = remaining[0]
        outcomes[current] = {
            "t": "result",
            "i": current,
            "ok": False,
            "error": reason if not tail else f"{reason}\n{tail[-2000:]}",
            "error_type": kind,
        }
        remaining = [i for i in remaining if i != current]
        if kind not in ("crash", "memory"):
            stop_reason = reason
            break  # timeouts and output limits end the step; crashes and OOM kills only end that test

    if remaining and stop_reason is None:
        out_of_time = flags["timed_out"] or budget - (_now() - started) <= 0.05
        stop_reason = "time limit exceeded" if out_of_time else "too many crashes"
    for index in remaining:
        if index not in outcomes:
            outcomes[index] = {
                "t": "result",
                "i": index,
                "ok": False,
                "error": f"not executed: {stop_reason}",
                "error_type": "not_run",
            }
    return {
        "name": step["name"],
        "stdout": out_cap.text(),
        "stderr": err_cap.text(),
        "stdout_bytes": out_cap.total,
        "stderr_bytes": err_cap.total,
        "truncated": out_cap.truncated or err_cap.truncated,
        "exit_code": exit_code,
        "signal": sig,
        "wall_ms": total_wall,
        "cpu_ms": cpu,
        "max_rss_kb": max_rss,
        "attempts": len(attempts),
        "attempt_details": attempts,
        "ready": ready_seen,
        "fatal": fatal,
        "tests": [outcomes[i] for i in order if i in outcomes],
        **flags,
    }


class _Tee:
    def __init__(self, *sinks) -> None:
        self.sinks = sinks

    @property
    def total(self) -> int:
        return self.sinks[0].total

    def feed(self, data: bytes) -> None:
        for sink in self.sinks:
            sink.feed(data)


def _safe_path(workdir: str, rel: str) -> str:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\x00" in rel:
        raise ValueError(f"invalid path {rel!r}")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid path {rel!r}")
    return os.path.join(workdir, *parts)


def prepare_workdir(job) -> str:
    workdir = job.get("workdir") or "/tmp/work"
    os.makedirs(workdir, mode=0o755, exist_ok=True)
    for spec in job.get("files", []):
        path = _safe_path(workdir, spec["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = 0o755 if spec.get("executable") else 0o644
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(spec.get("content", "").encode("utf-8"))
    for spec in job.get("copies", []):
        src = spec["src"]
        if not os.path.isabs(src):
            raise ValueError(f"copy source must be absolute: {src!r}")
        dst = _safe_path(workdir, spec["dst"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb") as fin:
            data = fin.read()
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    return workdir


def execute(job) -> dict:
    started = _now()
    deadline = started + float(job.get("deadline_ms", 60000)) / 1000.0
    limits = job.get("limits") or {}
    workdir = prepare_workdir(job)
    env = {k: v for k, v in os.environ.items()}
    env.update({str(k): str(v) for k, v in (job.get("env") or {}).items()})
    ctx = {
        "workdir": workdir,
        "env": env,
        "capture_bytes": int(limits.get("capture_bytes", 65536)),
        "output_hard_cap_bytes": int(limits.get("output_hard_cap_bytes", 4 << 20)),
        "results_cap_bytes": int(limits.get("results_cap_bytes", 8 << 20)),
        "record_cap_bytes": int(limits.get("record_cap_bytes", 1 << 20)),
    }
    steps_out = []
    for step in job.get("steps", []):
        left = deadline - _now()
        if left <= 0.05:
            steps_out.append({"name": step.get("name"), "skipped": "deadline"})
            break
        step = dict(step)
        step["timeout_s"] = max(0.05, min(float(step.get("timeout_ms", 5000)) / 1000.0, left))
        if "harness" in step:
            result = run_harness_step(step, ctx)
        else:
            result = run_plain_step(step, ctx)
        steps_out.append(result)
        failed = (
            result.get("exit_code") != 0 or result.get("timed_out") or result.get("memory_exceeded") or result.get("spawn_error")
        )
        if failed and step.get("stop_on_failure"):
            break
        fatal = result.get("fatal") or {}
        if fatal.get("t") == "compile_error" or fatal.get("kind") in ("entry", "compile_error"):
            break  # later harness steps would only report the same problem
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "steps": steps_out,
        "runner_ms": (_now() - started) * 1000.0,
        "cgroup_peak_kb": _cgroup_peak_kb(),
    }


def _emit(nonce: str, result: dict) -> None:
    try:
        body = json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False, default=str)
    except (TypeError, ValueError, RecursionError) as exc:
        failure = {"protocol": PROTOCOL, "ok": False, "error": f"unserializable result: {exc}"[:500], "steps": []}
        body = json.dumps(failure, separators=(",", ":"), ensure_ascii=True)
    frame = f"\n<<SANDBOX-RESULT {nonce}>>\n{body}\n<<SANDBOX-END {nonce}>>\n".encode("ascii")
    view = memoryview(frame)
    while view:
        try:
            written = os.write(1, view)
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                time.sleep(0.001)
                continue
            raise
        view = view[written:]


def main() -> int:
    _set_nondumpable()
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    except (ValueError, OSError):
        soft, hard = (0, 0)
    nonce = ""
    try:
        job = _read_job()
        nonce = str(job.get("nonce") or "")
        if len(nonce) < 32 or not all(c in "0123456789abcdef" for c in nonce):
            raise ValueError("invalid nonce")
        _detach_stdin()
        stack = int((job.get("limits") or {}).get("stack_bytes", 64 << 20))
        if stack > 0 and soft != resource.RLIM_INFINITY:
            target = stack if hard == resource.RLIM_INFINITY else min(stack, hard)
            try:
                resource.setrlimit(resource.RLIMIT_STACK, (target, hard))
            except (ValueError, OSError):
                pass
        os.umask(0o022)
        result = execute(job)
    except Exception as exc:  # noqa: BLE001 - always report, never hang
        result = {"protocol": PROTOCOL, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000], "steps": []}
    _kill_all()
    if nonce:
        _emit(nonce, result)
        return 0
    sys.stderr.write("sandbox runner: invalid job\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
