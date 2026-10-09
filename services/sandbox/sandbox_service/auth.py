"""HMAC request authentication.

``X-Sandbox-Signature = hex(HMAC-SHA256(SANDBOX_SHARED_SECRET, f"{X-Sandbox-Timestamp}.{raw_body}"))``
with ``X-Sandbox-Timestamp`` in unix seconds; requests more than 60 s away
from the server clock are rejected, and a signature is accepted only once
inside that window (replays of captured requests are refused).
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import OrderedDict

TIMESTAMP_HEADER = "X-Sandbox-Timestamp"
SIGNATURE_HEADER = "X-Sandbox-Signature"


class AuthError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


class ReplayGuard:
    """Remembers accepted signatures for the validity window (bounded memory)."""

    def __init__(self, window_seconds: float, max_entries: int = 100_000) -> None:
        self._window = window_seconds
        self._max = max_entries
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def check_and_remember(self, signature: str, now: float) -> bool:
        with self._lock:
            cutoff = now - self._window
            while self._seen:
                oldest_sig, oldest_ts = next(iter(self._seen.items()))
                if oldest_ts >= cutoff and len(self._seen) < self._max:
                    break
                del self._seen[oldest_sig]
            if signature in self._seen:
                return False
            self._seen[signature] = now
            return True


class Authenticator:
    def __init__(self, secret: bytes, max_skew_seconds: int = 60) -> None:
        if len(secret) < 16:
            raise ValueError("shared secret too short")
        self._secret = secret
        self._max_skew = max_skew_seconds
        self._replay = ReplayGuard(window_seconds=2 * max_skew_seconds + 5)

    def verify(self, timestamp: str | None, signature: str | None, body: bytes, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not timestamp or not signature:
            raise AuthError("missing", "missing X-Sandbox-Timestamp or X-Sandbox-Signature header")
        if len(timestamp) > 20 or not timestamp.isdigit():
            raise AuthError("bad_timestamp", "X-Sandbox-Timestamp must be unix seconds")
        if abs(now - int(timestamp)) > self._max_skew:
            raise AuthError("expired", "request timestamp outside the allowed clock skew")
        provided = signature.strip().lower()
        if len(provided) != 64:
            raise AuthError("bad_signature", "invalid request signature")
        expected = sign(self._secret, timestamp, body)
        if not hmac.compare_digest(expected, provided):
            raise AuthError("bad_signature", "invalid request signature")
        if not self._replay.check_and_remember(provided, now):
            raise AuthError("replay", "request signature already used")
