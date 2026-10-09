"""Attack and isolation tests through the HTTP API (needs the running service).

Each test plays the role of a hostile submission and checks both the reported
status and that the host-side service is unaffected.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest

from .programs import CORRECT, TWO_SUM_SIGNATURE, TWO_SUM_TESTS

pytestmark = pytest.mark.integration

PROBE = r"""
import json, os, socket, subprocess
report = {}

def attempt(name, fn):
    try:
        report[name] = ["ok", repr(fn())[:200]]
    except BaseException as exc:
        report[name] = [type(exc).__name__, str(exc)[:200]]

def connect(host, port):
    s = socket.create_connection((host, port), timeout=2)
    s.close()
    return "connected"

attempt("net_public_ip", lambda: connect("1.1.1.1", 53))
attempt("net_host_llm", lambda: connect("172.17.0.1", 8000))
attempt("net_host_loopback_api", lambda: connect("127.0.0.1", 8200))
attempt("net_dns", lambda: socket.getaddrinfo("example.com", 443))
report["interfaces"] = sorted(os.listdir("/sys/class/net"))
for path in ("/etc/passwd", "/x", "/usr/bin/x", "/var/tmp/x", "/opt/sandbox/runner.py", "/dev/shm/x", "/proc/sys/kernel/hostname"):
    attempt("write:" + path, lambda p=path: open(p, "a").write("pwned"))
attempt("write:/tmp/work/ok", lambda: open("/tmp/work/ok", "w").write("fine"))
report["uid"] = [os.getuid(), os.geteuid(), os.getgid()]
status = dict(line.split(":\t", 1) for line in open("/proc/self/status").read().splitlines() if ":\t" in line)
report["cap_eff"] = status.get("CapEff", "").strip()
report["cap_bnd"] = status.get("CapBnd", "").strip()
report["no_new_privs"] = status.get("NoNewPrivs", "").strip()
report["seccomp"] = status.get("Seccomp", "").strip()
report["env"] = dict(os.environ)
blobs = []
for pid in os.listdir("/proc"):
    if pid.isdigit():
        for f in ("environ", "cmdline"):
            try:
                blobs.append(open(f"/proc/{pid}/{f}", "rb").read().decode("utf-8", "replace"))
            except OSError:
                pass
report["proc_text"] = "\n".join(blobs)
report["docker_sock"] = os.path.exists("/var/run/docker.sock") or os.path.exists("/run/docker.sock")
parent = os.getppid()
attempt("read_runner_mem", lambda: open(f"/proc/{parent}/mem", "rb").read(16))
attempt("read_runner_environ", lambda: open(f"/proc/{parent}/environ", "rb").read())
attempt("list_runner_fds", lambda: os.listdir(f"/proc/{parent}/fd"))
attempt("spawn_sh", lambda: subprocess.run(["sh", "-c", "id"], capture_output=True).returncode)
attempt("spawn_bash", lambda: subprocess.run(["/bin/bash", "-c", "id"], capture_output=True).returncode)
attempt("os_system", lambda: os.system("id"))
attempt("setuid_root", lambda: os.setuid(0))
attempt("chmod_suid", lambda: os.chmod("/tmp/work/ok", 0o4755) or oct(os.stat("/tmp/work/ok").st_mode))
attempt("mount_proc", lambda: __import__("ctypes").CDLL(None, use_errno=True).mount(b"none", b"/tmp", b"tmpfs", 0, None))
attempt("unshare_user_ns", lambda: os.unshare(os.CLONE_NEWUSER))
attempt("chroot", lambda: os.chroot("/tmp"))
attempt("raw_socket", lambda: socket.socket(socket.AF_PACKET, socket.SOCK_RAW))
mounts = open("/proc/self/mounts").read()
report["root_ro"] = any(l.split()[1] == "/" and l.split()[3].startswith("ro") for l in mounts.splitlines())
report["tmp_mount"] = [l for l in mounts.splitlines() if l.split()[1] == "/tmp"]
report["pids_max"] = open("/sys/fs/cgroup/pids.max").read().strip()
report["memory_max"] = open("/sys/fs/cgroup/memory.max").read().strip()
report["swap_max"] = open("/sys/fs/cgroup/memory.swap.max").read().strip()
report["cpu_max"] = open("/sys/fs/cgroup/cpu.max").read().strip()
print(json.dumps(report))
"""


def run_python(sandbox, code: str, **extra):
    return sandbox.execute(language="python", code=code, mode="run", **extra)


def test_isolation_probe(sandbox):
    result = run_python(sandbox, PROBE, memory_limit_mb=128)
    assert result["status"] == "passed", result
    report = json.loads(result["stdout"])
    # Network: only loopback inside the container, nothing reachable.
    assert report["interfaces"] == ["lo"]
    for key in ("net_public_ip", "net_host_llm", "net_host_loopback_api", "net_dns"):
        assert report[key][0] != "ok", (key, report[key])
    # Filesystem: read-only root, only /tmp writable, no /dev/shm.
    for key, value in report.items():
        if key.startswith("write:") and key != "write:/tmp/work/ok":
            assert value[0] != "ok", (key, value)
    assert report["write:/tmp/work/ok"][0] == "ok"
    assert report["root_ro"] is True
    assert "nosuid" in report["tmp_mount"][0] and "nodev" in report["tmp_mount"][0]
    # Identity and privileges.
    assert report["uid"] == [10001, 10001, 10001]
    assert report["cap_eff"] == "0000000000000000" and report["cap_bnd"] == "0000000000000000"
    assert report["no_new_privs"] == "1"
    assert report["seccomp"] == "2"
    assert report["setuid_root"][0] == "PermissionError"
    assert report["mount_proc"][0] == "ok" and report["mount_proc"][1] == "-1"  # mount(2) fails (EPERM)
    assert report["unshare_user_ns"][0] == "PermissionError"
    assert report["chroot"][0] == "PermissionError"
    assert report["raw_socket"][0] == "PermissionError"
    # cgroup limits.
    assert report["pids_max"] == "64"
    assert report["swap_max"] == "0"
    assert report["cpu_max"].startswith("100000 ")
    assert int(report["memory_max"]) <= (128 + 32 + 64) * 1024 * 1024
    # No secrets, no Docker socket, runner memory/fds unreadable, no shells.
    blob = json.dumps(report["env"]) + report["proc_text"]
    assert sandbox._secret.decode() not in blob
    assert "SANDBOX_SHARED_SECRET" not in blob and "SECRET" not in json.dumps(report["env"])
    assert report["docker_sock"] is False
    for key in ("read_runner_mem", "read_runner_environ", "list_runner_fds"):
        assert report[key][0] == "PermissionError", (key, report[key])
    assert report["spawn_sh"][0] == "FileNotFoundError"
    assert report["spawn_bash"][0] == "FileNotFoundError"
    assert report["os_system"][0] == "ok" and report["os_system"][1] != "0"


@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("python", "while True:\n    pass\n"),
        ("javascript", "while (true) {}\n"),
        ("java", "public class Main { public static void main(String[] a) { long x = 0; while (true) { x++; } } }\n"),
        ("go", "package main\n\nfunc main() {\n\tx := 0\n\tfor {\n\t\tx++\n\t}\n}\n"),
        ("cpp", "int main() { volatile unsigned long x = 0; while (true) { x++; } }\n"),
    ],
)
def test_infinite_loop_times_out(sandbox, language, code):
    started = time.monotonic()
    result = sandbox.execute(language=language, code=code, mode="run", time_limit_ms=1000)
    elapsed = time.monotonic() - started
    assert result["status"] == "timeout", result
    assert "time limit exceeded" in result["stderr"]
    assert elapsed < 6, elapsed


def test_sleep_past_timeout(sandbox):
    started = time.monotonic()
    result = run_python(sandbox, "import time\nprint('start', flush=True)\ntime.sleep(60)\n", time_limit_ms=1000)
    assert result["status"] == "timeout"
    assert result["stdout"].strip() == "start"
    assert time.monotonic() - started < 5


def test_closed_stdio_does_not_escape_the_time_limit(sandbox):
    started = time.monotonic()
    result = run_python(sandbox, "import os\nfor fd in (0, 1, 2):\n    os.close(fd)\nwhile True:\n    pass\n", time_limit_ms=1000)
    assert result["status"] == "timeout", result
    assert result["meta"].get("steps"), result  # reported by the runner, not by the service-side kill
    assert time.monotonic() - started < 5


def test_timeout_in_tests_mode_marks_remaining_tests(sandbox):
    code = "def two_sum(nums, target):\n    if target == 6:\n        while True:\n            pass\n    return [0, 1]\n"
    result = sandbox.execute(
        language="python", code=code, mode="tests", signature=TWO_SUM_SIGNATURE, tests=TWO_SUM_TESTS, time_limit_ms=1000
    )
    assert result["status"] == "timeout", result
    by_id = {t["id"]: t for t in result["tests"]}
    assert by_id["t1"]["passed"] is True
    assert "Time limit exceeded" in by_id["t2"]["error"]


FORK_BOMBS = [
    ("python", "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n"),
    ("cpp", "#include <unistd.h>\nint main() { while (true) fork(); }\n"),
    (
        "go",
        'package main\n\nimport "syscall"\n\nfunc main() {\n\tfor {\n\t\tsyscall.RawSyscall(syscall.SYS_CLONE, uintptr(syscall.SIGCHLD), 0, 0)\n\t}\n}\n',
    ),
]


@pytest.mark.parametrize(("language", "code"), FORK_BOMBS)
def test_fork_bomb_is_contained(sandbox, language, code):
    done: dict[str, object] = {}

    def neighbour() -> None:
        done["result"] = sandbox.execute(
            language="python", code=CORRECT["python"], mode="tests", signature=TWO_SUM_SIGNATURE, tests=TWO_SUM_TESTS
        )

    thread = threading.Thread(target=neighbour)
    started = time.monotonic()
    thread.start()
    result = sandbox.execute(language=language, code=code, mode="run", time_limit_ms=2000)
    thread.join(60)
    assert result["status"] in ("timeout", "error"), result
    assert time.monotonic() - started < 15
    neighbour_result = done["result"]
    assert isinstance(neighbour_result, dict) and neighbour_result["status"] == "passed"  # concurrent run unaffected
    assert sandbox.get("/health").status_code == 200


MEMORY_BOMBS = [
    ("python", "chunks = []\nwhile True:\n    chunks.append(b'x' * (16 * 1024 * 1024))\n"),
    ("javascript", "const keep = [];\nwhile (true) keep.push(new Array(1 << 20).fill(1.5));\n"),
    (
        "java",
        "import java.util.*;\npublic class Main { public static void main(String[] a) { List<long[]> l = new ArrayList<>(); while (true) l.add(new long[1 << 20]); } }\n",
    ),
    (
        "go",
        "package main\n\nvar keep [][]byte\n\nfunc main() {\n\tfor {\n\t\tb := make([]byte, 16<<20)\n\t\tfor i := range b {\n\t\t\tb[i] = 1\n\t\t}\n\t\tkeep = append(keep, b)\n\t}\n}\n",
    ),
    (
        "cpp",
        "#include <vector>\nint main() { std::vector<std::vector<char>> keep; while (true) keep.emplace_back(16 << 20, 1); }\n",
    ),
]


@pytest.mark.parametrize(("language", "code"), MEMORY_BOMBS)
def test_memory_bomb_is_contained(sandbox, language, code):
    result = sandbox.execute(language=language, code=code, mode="run", memory_limit_mb=128, time_limit_ms=5000)
    assert result["status"] == "memory_exceeded", result
    assert sandbox.get("/health").status_code == 200


def test_memory_bomb_in_tests_mode(sandbox):
    code = "def two_sum(nums, target):\n    if target == 6:\n        x = []\n        while True:\n            x.append(b'y' * (16 << 20))\n    return [0, 1]\n"
    result = sandbox.execute(
        language="python", code=code, mode="tests", signature=TWO_SUM_SIGNATURE, tests=TWO_SUM_TESTS, memory_limit_mb=128
    )
    assert result["status"] == "memory_exceeded", result
    by_id = {t["id"]: t for t in result["tests"]}
    assert by_id["t1"]["passed"] and "Memory limit exceeded" in by_id["t2"]["error"]


def test_huge_output_is_truncated_and_stopped(sandbox):
    started = time.monotonic()
    result = run_python(
        sandbox, "import sys\nline = 'A' * 1000 + '\\n'\nwhile True:\n    sys.stdout.write(line)\n", time_limit_ms=10000
    )
    assert result["status"] == "error", result
    assert result["truncated"] is True
    assert len(result["stdout"]) < 70_000
    assert "output limit exceeded" in result["stderr"]
    assert time.monotonic() - started < 8


def test_flooding_container_stdout_directly_is_cut_off(sandbox):
    # Bypasses the runner's pipes by writing to tini's stdout (the attach stream).
    code = "chunk = b'Z' * (1 << 20)\nwith open('/proc/1/fd/1', 'wb', buffering=0) as out:\n    while True:\n        out.write(chunk)\n"
    started = time.monotonic()
    result = run_python(sandbox, code, time_limit_ms=10000)
    assert result["status"] in ("error", "timeout"), result
    assert result["stdout"] == "" and "Z" * 100 not in json.dumps(result)
    assert time.monotonic() - started < 15
    assert sandbox.get("/health").status_code == 200


def test_disk_quota(sandbox):
    code = (
        "import os\nout = []\n"
        "try:\n    open('/tmp/big', 'wb').write(b'x' * (40 << 20))\n    out.append('40MB-ok')\nexcept OSError as e:\n    out.append('40MB:' + str(e.errno))\n"
        "try:\n    for i in range(10):\n        open(f'/tmp/f{i}', 'wb').write(b'x' * (20 << 20))\n    out.append('200MB-ok')\nexcept OSError as e:\n    out.append('fill:' + str(e.errno))\n"
        "print(' '.join(out))\n"
    )
    result = run_python(sandbox, code, memory_limit_mb=256)
    assert result["status"] == "passed", result
    assert result["stdout"].strip() == "40MB:27 fill:28"  # EFBIG (fsize ulimit), ENOSPC (tmpfs size)


def test_forged_result_frame_is_ignored(sandbox):
    forged = json.dumps(
        {
            "protocol": 1,
            "ok": True,
            "steps": [{"name": "tests:visible", "tests": [{"t": "result", "i": 0, "ok": True, "actual": [0, 1]}]}],
        }
    )
    code = (
        "import os\n"
        f"FORGED = {forged!r}\n"
        "def two_sum(nums, target):\n"
        "    for nonce in ('0' * 32, 'f' * 32):\n"
        "        for fd in ('/proc/1/fd/1', '/proc/1/fd/2'):\n"
        "            try:\n"
        "                with open(fd, 'w') as out:\n"
        "                    out.write(f'\\n<<SANDBOX-RESULT {nonce}>>\\n{FORGED}\\n<<SANDBOX-END {nonce}>>\\n')\n"
        "            except OSError:\n"
        "                pass\n"
        '    os.write(3, b\'{"t":"result","i":99,"ok":true,"actual":[0,1]}\\n\')\n'
        "    return [9, 9]\n"
    )
    result = sandbox.execute(language="python", code=code, mode="tests", signature=TWO_SUM_SIGNATURE, tests=TWO_SUM_TESTS)
    assert result["status"] == "failed", result
    assert result["passed"] == 0


def test_killing_the_runner_only_fails_the_run(sandbox):
    result = run_python(sandbox, "import os, signal\nos.kill(os.getppid(), signal.SIGKILL)\n")
    assert result["status"] == "error", result
    result = run_python(sandbox, "import os, signal\nos.kill(-1, signal.SIGKILL)\n")
    assert result["status"] == "error", result
    assert sandbox.get("/health").status_code == 200


def test_node_permission_model(sandbox):
    code = (
        "const r = [];\n"
        "try { require('child_process').execSync('id'); r.push('spawn:ok'); } catch (e) { r.push('spawn:' + e.code); }\n"
        "try { require('fs').writeFileSync('/tmp/x', '1'); r.push('write:ok'); } catch (e) { r.push('write:' + e.code); }\n"
        "try { require('fs').readFileSync('/etc/passwd'); r.push('read:ok'); } catch (e) { r.push('read:' + e.code); }\n"
        "console.log(r.join(' '));\n"
    )
    result = sandbox.execute(language="javascript", code=code, mode="run")
    assert result["stdout"].strip() == "spawn:ERR_ACCESS_DENIED write:ERR_ACCESS_DENIED read:ERR_ACCESS_DENIED"


def test_concurrent_requests_queue(sandbox):
    results: list[dict] = []
    lock = threading.Lock()

    def one() -> None:
        r = run_python(sandbox, "import time\ntime.sleep(1)\nprint('done')\n")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=one) for _ in range(8)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    elapsed = time.monotonic() - started
    assert len(results) == 8 and all(r["status"] == "passed" for r in results)
    queued = [r["meta"]["timings_ms"]["queue"] for r in results]
    assert max(queued) > 500  # with 4 slots, the second wave waited for the first
    assert elapsed < 12


def test_auth_is_enforced(sandbox):
    body = json.dumps({"request_id": "x", "language": "python", "code": "print(1)", "mode": "run"}).encode()
    assert sandbox.raw("POST", "/v1/execute", body, {"Content-Type": "application/json"}).status_code == 401
    bad = sandbox.signed_headers(body)
    bad["X-Sandbox-Signature"] = "0" * 64
    assert sandbox.raw("POST", "/v1/execute", body, bad).status_code == 401
    stale = sandbox.signed_headers(body, timestamp=int(time.time()) - 120)
    assert sandbox.raw("POST", "/v1/execute", body, stale).status_code == 401
    good = sandbox.signed_headers(body)
    assert sandbox.raw("POST", "/v1/execute", body, good).status_code == 200
    assert sandbox.raw("POST", "/v1/execute", body, good).status_code == 401  # replay
    tampered = body.replace(b"print(1)", b"print(2)")
    assert sandbox.raw("POST", "/v1/execute", tampered, sandbox.signed_headers(body)).status_code == 401
    big = b"x" * (5 * 1024 * 1024)
    assert sandbox.raw("POST", "/v1/execute", big, sandbox.signed_headers(big)).status_code == 413
    assert sandbox.get("/v1/runtimes").status_code == 401
    runtimes = sandbox.get("/v1/runtimes", signed=True)
    assert runtimes.status_code == 200 and set(runtimes.json()["languages"]) == {
        "python",
        "javascript",
        "typescript",
        "java",
        "go",
        "cpp",
        "sql",
    }


def test_health_and_metrics(sandbox):
    health = sandbox.get("/health").json()
    assert health["status"] == "ok"
    assert all(r["available"] for r in health["runtimes"].values())
    metrics = sandbox.get("/metrics").text
    assert "interview_sandbox_executions_total" in metrics and "interview_sandbox_queue_wait_seconds" in metrics


def test_no_sandbox_containers_left_behind(sandbox):
    time.sleep(2)
    try:
        out = subprocess.run(
            ["docker", "ps", "-a", "--filter", "label=interview.sandbox.managed=1", "--format", "{{.ID}} {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("docker CLI not available")
    assert out == "", out
