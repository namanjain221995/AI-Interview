"""Pure-function tests of the in-container runner (nothing is executed on the host)."""

from __future__ import annotations

import filecmp
import importlib.util
import json
from pathlib import Path

import pytest

RUNTIMES = Path(__file__).resolve().parents[2] / "runtimes"


def load_runner():
    spec = importlib.util.spec_from_file_location("sandbox_runner", RUNTIMES / "runner.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = load_runner()


@pytest.mark.parametrize("lang", ["python", "node", "java", "go", "cpp"])
def test_runner_copies_are_in_sync(lang):
    assert filecmp.cmp(RUNTIMES / "runner.py", RUNTIMES / lang / "runner.py", shallow=False), (
        f"runtimes/{lang}/runner.py differs: run python services/sandbox/scripts/sync_runner.py"
    )


def test_kill_all_is_disabled_outside_a_container_pid_namespace():
    # On a host, pid 1 is systemd/init: the runner must never call kill(-1) there.
    assert runner._PRIVATE_PIDNS is False


def test_capture_keeps_head_and_tail():
    cap = runner.Capture(100)
    cap.feed(b"H" * 30)
    cap.feed(b"x" * 1000)
    cap.feed(b"T" * 10)
    text = cap.text()
    assert cap.truncated and cap.total == 1040
    assert text.startswith("H" * 25) and text.endswith("T" * 10)
    assert "bytes truncated" in text
    small = runner.Capture(100)
    small.feed(b"hello")
    assert small.text() == "hello" and not small.truncated


def test_capture_discard_mode_counts_only():
    cap = runner.Capture(100, keep=False)
    cap.feed(b"secret hidden-test output")
    assert cap.text() == "" and cap.total == 25 and not cap.truncated


def test_capture_decodes_split_utf8():
    cap = runner.Capture(8)
    cap.feed("ééééééé".encode())
    assert "�" in cap.text() or cap.text()


def test_record_reader_validates_untrusted_records():
    reader = runner.RecordReader(record_cap=200, total_cap=10_000, pending={0, 1})
    lines = [
        {"t": "ready"},
        {"t": "begin", "i": 0},
        {"t": "result", "i": 0, "ok": True, "actual": [1, 2], "ms": 0.5},
        {"t": "result", "i": 0, "ok": True, "actual": "second result ignored"},
        {"t": "result", "i": 7, "ok": True, "actual": "unknown test ignored"},
        {"t": "result", "i": True, "ok": True},
        {"t": "evil", "i": 1},
        {"t": "result", "i": 1, "ok": False, "error": "x" * 10, "error_type": "runtime", "ms": -3},
        {"t": "done"},
    ]
    data = "".join(json.dumps(line) + "\n" for line in lines).encode()
    data += b"not json\n" + b'{"t":"result","i":1,"ok":true,"actual":NaN}\n'
    for i in range(0, len(data), 7):
        reader.feed(data[i : i + 7])
    kinds = [r["t"] for r in reader.records]
    assert kinds == ["ready", "begin", "result", "result", "done"]
    first, second = reader.records[2], reader.records[3]
    assert first == {"t": "result", "i": 0, "ok": True, "ms": 0.5, "actual": [1, 2]}
    assert second["ok"] is False and second["error"] == "x" * 10 and "ms" not in second
    assert reader.pending == set()


def test_record_reader_bounds():
    reader = runner.RecordReader(record_cap=50, total_cap=10_000, pending={0})
    reader.feed(b'{"t":"begin","i":0}\n')
    reader.feed(b'{"t":"result","i":0,"ok":true,"actual":"' + b"y" * 100)
    reader.feed(b'"}\n')
    assert reader.oversized == [0]
    assert [r["t"] for r in reader.records] == ["begin"]
    flood = runner.RecordReader(record_cap=50, total_cap=100, pending=None)
    flood.feed(b"z" * 200)
    assert flood.overflow


def test_safe_path():
    assert runner._safe_path("/tmp/work", "a/b.txt") == "/tmp/work/a/b.txt"
    for bad in ("/etc/passwd", "../x", "a/../../x", "", "a//b", "./x", "a\x00b"):
        with pytest.raises(ValueError):
            runner._safe_path("/tmp/work", bad)


def test_crash_reason_priorities():
    base = {
        "spawn_error": None,
        "timed_out": False,
        "memory_exceeded": False,
        "output_limit_exceeded": False,
        "results_overflow": False,
        "signal": None,
        "exit_code": 1,
    }
    assert runner._crash_reason({**base, "timed_out": True, "signal": "SIGKILL"})[0] == "timeout"
    assert runner._crash_reason({**base, "memory_exceeded": True, "signal": "SIGKILL"})[0] == "memory"
    assert runner._crash_reason({**base, "signal": "SIGSEGV"}) == ("crash", "terminated by SIGSEGV")
    assert runner._crash_reason({**base, "spawn_error": "nope"})[0] == "startup"
    assert runner._crash_reason({**base, "exit_code": 0})[1] == "exited with code 0 before finishing"
