"""Runner job protocol (version 1) and Docker attach-stream handling.

The job is written to the container's stdin as ``SANDBOX-JOB 1 <len>\\n<json>``;
the runner answers with one frame on stdout delimited by markers that carry a
fresh 128-bit nonce. Candidate code can write to the container's stdout (via
/proc/1/fd/1) but never learns the nonce, so it cannot forge a result frame.
"""

from __future__ import annotations

import json
import secrets
import struct
from collections import deque
from typing import Any

PROTOCOL_VERSION = 1
_BEGIN = "<<SANDBOX-RESULT {nonce}>>\n"
_END = "\n<<SANDBOX-END {nonce}>>"


def new_nonce() -> str:
    return secrets.token_hex(16)


def encode_job(job: dict[str, Any]) -> bytes:
    body = json.dumps(job, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"SANDBOX-JOB {PROTOCOL_VERSION} {len(body)}\n".encode("ascii") + body


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name}")


def extract_result(stdout: bytes, nonce: str) -> dict[str, Any] | None:
    """Return the runner's result frame, or None when it is missing or ambiguous."""
    begin = _BEGIN.format(nonce=nonce).encode("ascii")
    end = _END.format(nonce=nonce).encode("ascii")
    start = stdout.find(begin)
    if start < 0 or stdout.find(begin, start + 1) >= 0:
        return None
    body_start = start + len(begin)
    stop = stdout.find(end, body_start)
    if stop < 0:
        return None
    try:
        result = json.loads(stdout[body_start:stop].decode("ascii"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(result, dict) or result.get("protocol") != PROTOCOL_VERSION:
        return None
    try:
        clean: dict[str, Any] = sanitize(result)
    except (ValueError, RecursionError):
        return None  # a forged, absurdly nested record: an invalid program result, never an HTTP 500 (S19)
    return clean


# The runner refuses a returned value nested deeper than its MAX_ACTUAL_DEPTH (64) per test; the result frame wraps
# that value a few levels deep (result -> steps -> step -> tests -> test -> actual). The frame cap must leave room
# for both, or a value the runner accepted would turn the whole run into an unreadable "crash".
MAX_DEPTH = 96


def _clean_str(value: str) -> str:
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return value.encode("utf-8", "replace").decode("utf-8")


def sanitize(value: Any, *, max_depth: int = MAX_DEPTH) -> Any:
    """Drop lone surrogates (valid in JSON escapes, not encodable as UTF-8) from container data.

    Iterative, so a candidate-controlled record cannot exhaust the stack; nesting deeper than ``max_depth``
    raises ``ValueError`` (S19)."""
    if isinstance(value, str):
        return _clean_str(value)
    if not isinstance(value, (list, dict)):
        return value
    root: Any = [] if isinstance(value, list) else {}
    stack: list[tuple[Any, Any, int]] = [(value, root, 1)]
    while stack:
        source, target, depth = stack.pop()
        if depth > max_depth:
            raise ValueError("record nests too deeply")
        items = enumerate(source) if isinstance(source, list) else source.items()
        for key, item in items:
            if isinstance(item, (list, dict)):
                child: Any = [] if isinstance(item, list) else {}
                stack.append((item, child, depth + 1))
            else:
                child = _clean_str(item) if isinstance(item, str) else item
            if isinstance(target, list):
                target.append(child)
            else:
                target[_clean_str(key) if isinstance(key, str) else key] = child
    return root


class StreamDemux:
    """Incremental decoder for Docker's multiplexed attach stream (tty=False).

    Each frame is an 8-byte header (stream id, 3 zero bytes, big-endian length)
    followed by the payload. stdout keeps its *tail* within ``stdout_cap`` (the
    result frame is always the last thing the runner writes); stderr keeps its
    head within ``stderr_cap`` for diagnostics. Linear time: payload chunks are
    queued and dropped whole from the left, never shifted byte by byte.
    """

    def __init__(self, stdout_cap: int, stderr_cap: int = 64 * 1024) -> None:
        self._pending = b""
        self._chunks: deque[bytes] = deque()
        self._kept = 0
        self._stderr = bytearray()
        self._stdout_cap = stdout_cap
        self._stderr_cap = stderr_cap
        self.stdout_total = 0
        self.stderr_total = 0

    @property
    def total(self) -> int:
        return self.stdout_total + self.stderr_total

    def feed(self, data: bytes) -> None:
        buf = self._pending + data if self._pending else data
        pos, end = 0, len(buf)
        while end - pos >= 8:
            stream = buf[pos]
            (length,) = struct.unpack_from(">I", buf, pos + 4)
            if end - pos - 8 < length:
                break
            payload = buf[pos + 8 : pos + 8 + length]
            pos += 8 + length
            if stream == 2:
                self.stderr_total += length
                room = self._stderr_cap - len(self._stderr)
                if room > 0:
                    self._stderr += payload[:room]
            elif length:
                self.stdout_total += length
                self._chunks.append(payload)
                self._kept += length
                while self._chunks and self._kept - len(self._chunks[0]) >= self._stdout_cap:
                    self._kept -= len(self._chunks.popleft())
        self._pending = buf[pos:]

    @property
    def stdout(self) -> bytes:
        data = b"".join(self._chunks)
        return data[-self._stdout_cap :] if len(data) > self._stdout_cap else data

    @property
    def stderr(self) -> bytes:
        return bytes(self._stderr)
