from __future__ import annotations

import json
import struct

import pytest

from sandbox_service.protocol import StreamDemux, encode_job, extract_result, new_nonce, sanitize


def frame(stream: int, payload: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + struct.pack(">I", len(payload)) + payload


def result_frame(nonce: str, result: dict) -> bytes:
    return f"\n<<SANDBOX-RESULT {nonce}>>\n{json.dumps(result)}\n<<SANDBOX-END {nonce}>>\n".encode()


def test_encode_job_header():
    data = encode_job({"protocol": 1, "code": "é"})
    header, _, body = data.partition(b"\n")
    assert header.startswith(b"SANDBOX-JOB 1 ")
    assert int(header.split()[2]) == len(body)
    assert json.loads(body)["code"] == "é"


def test_nonce_is_128_bit_hex():
    nonce = new_nonce()
    assert len(nonce) == 32 and int(nonce, 16) >= 0
    assert new_nonce() != nonce


def test_extract_result_requires_matching_nonce():
    nonce = new_nonce()
    good = {"protocol": 1, "ok": True, "steps": []}
    noise = b"candidate garbage\n" + result_frame("0" * 32, {"protocol": 1, "ok": True, "steps": ["forged"]})
    assert extract_result(noise + result_frame(nonce, good), nonce) == good
    assert extract_result(noise, nonce) is None


def test_extract_result_rejects_duplicates_and_bad_payloads():
    nonce = new_nonce()
    ok = {"protocol": 1, "ok": True, "steps": []}
    assert extract_result(result_frame(nonce, ok) * 2, nonce) is None
    assert extract_result(result_frame(nonce, {"protocol": 2}), nonce) is None
    broken = f"\n<<SANDBOX-RESULT {nonce}>>\n{{not json\n<<SANDBOX-END {nonce}>>\n".encode()
    assert extract_result(broken, nonce) is None
    nan = f'\n<<SANDBOX-RESULT {nonce}>>\n{{"protocol": 1, "x": NaN}}\n<<SANDBOX-END {nonce}>>\n'.encode()
    assert extract_result(nan, nonce) is None
    truncated = f"\n<<SANDBOX-RESULT {nonce}>>\n{json.dumps(ok)}".encode()
    assert extract_result(truncated, nonce) is None


def test_sanitize_drops_lone_surrogates():
    value = {"a": ["ok", "bad\ud800"], "b": 1}
    clean = sanitize(value)
    json.dumps(clean, ensure_ascii=False).encode("utf-8")
    assert clean["a"][0] == "ok" and clean["b"] == 1


def test_demux_handles_split_frames_and_streams():
    demux = StreamDemux(stdout_cap=1024, stderr_cap=8)
    data = frame(1, b"hello ") + frame(2, b"diagnostics!") + frame(1, b"world")
    for i in range(0, len(data), 3):
        demux.feed(data[i : i + 3])
    assert demux.stdout == b"hello world"
    assert demux.stderr == b"diagnost"
    assert demux.stderr_total == len(b"diagnostics!")
    assert demux.total == 11 + 12


def test_demux_keeps_stdout_tail():
    demux = StreamDemux(stdout_cap=10)
    demux.feed(frame(1, b"x" * 100) + frame(1, b"0123456789"))
    assert demux.stdout == b"0123456789"
    assert demux.stdout_total == 110


def test_demux_is_linear_on_floods():
    import time

    demux = StreamDemux(stdout_cap=32 * 1024 * 1024)
    chunk = frame(1, b"Z" * 32768) * 32  # 1 MiB of 32 KiB frames per feed
    started = time.perf_counter()
    for _ in range(96):
        demux.feed(chunk)
    assert time.perf_counter() - started < 2.0
    assert demux.stdout_total == 96 * 1024 * 1024
    assert len(demux.stdout) == 32 * 1024 * 1024


def test_absurdly_nested_records_are_invalid_results_not_errors():
    """S19: a candidate-forged deeply nested record never raises (no HTTP 500 tripping the shared breaker)."""
    nonce = new_nonce()
    deep = {"protocol": 1, "ok": True, "steps": [{"actual": None}]}
    raw = json.dumps(deep).replace("null", "[" * 995 + "]" * 995)
    frame = f"\n<<SANDBOX-RESULT {nonce}>>\n{raw}\n<<SANDBOX-END {nonce}>>\n".encode()
    assert extract_result(frame, nonce) is None


def test_sanitize_is_iterative_with_a_depth_cap():
    value = {"a": ["x", {"b": "\ud800"}]}
    assert sanitize(value) == {"a": ["x", {"b": "?"}]}
    nested: list = []
    current = nested
    for _ in range(110):
        child: list = []
        current.append(child)
        current = child
    with pytest.raises(ValueError):
        sanitize(nested)
    assert sanitize([[1]], max_depth=2) == [[1]]


def _nested(depth: int) -> list:
    root: list = []
    current = root
    for _ in range(depth - 1):
        child: list = []
        current.append(child)
        current = child
    return root


def test_a_value_the_runner_accepts_is_never_rejected_by_the_service():
    """Review of S19: the frame cap must exceed the runner's per-value cap plus the frame's own wrapping."""
    from tests.unit.test_runner_unit import load_runner

    depth = load_runner().MAX_ACTUAL_DEPTH
    nonce = new_nonce()
    record = {"protocol": 1, "ok": True, "steps": [{"tests": [{"id": "t1", "actual": _nested(depth)}]}]}
    raw = json.dumps(record)
    framed = f"\n<<SANDBOX-RESULT {nonce}>>\n{raw}\n<<SANDBOX-END {nonce}>>\n".encode()
    assert extract_result(framed, nonce) == record
