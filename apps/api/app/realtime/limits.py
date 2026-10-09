"""Per-connection limits for the realtime socket (in memory, no Redis round
trip per frame).

* frame sizes: JSON text <= ``MAX_TEXT_BYTES`` (code updates may carry up to
  200k characters), binary <= ``MAX_BINARY_BYTES`` (1 s of 16 kHz PCM16);
* token buckets per connection: JSON messages, audio frames and audio bytes,
  plus per-type buckets for expensive actions (code runs, hints, integrity
  batches). Sustained abuse (``strikes``) closes the socket.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

MAX_TEXT_BYTES = 512 * 1024
MAX_BINARY_BYTES = 8 + 32_000  # header + 1 s of 16 kHz mono PCM16
STRIKES_BEFORE_CLOSE = 50


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float, *, clock: object = None) -> None:
        self.rate = float(rate_per_s)
        self.capacity = float(burst)
        self.tokens = float(burst)
        self._clock = clock or time.monotonic
        self._last = self._clock()  # type: ignore[operator]

    def allow(self, cost: float = 1.0) -> bool:
        now = self._clock()  # type: ignore[operator]
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# message type -> (rate per second, burst)
TYPE_LIMITS: dict[str, tuple[float, float]] = {
    "code.run": (0.5, 3),
    "code.submit": (0.2, 2),
    "code.hint_request": (0.2, 2),
    "code.update": (4, 10),
    "code.language": (0.5, 3),
    "integrity.events": (5, 20),
    "control.repeat": (0.5, 3),
    "control.start": (1, 3),
    "control.end_turn": (2, 5),
    "answer.text": (1, 3),
    "client.error": (2, 10),
    "playback.progress": (20, 40),
    "activity.finish": (2, 4),  # intake activity finish button (2/s per connection)
}


@dataclass
class ConnectionLimits:
    messages: TokenBucket = field(default_factory=lambda: TokenBucket(30, 60))
    frames: TokenBucket = field(default_factory=lambda: TokenBucket(60, 120))
    audio_bytes: TokenBucket = field(default_factory=lambda: TokenBucket(64_000, 192_000))
    per_type: dict[str, TokenBucket] = field(default_factory=dict)
    # Strike budget: refills at 2/s, so only sustained abuse exhausts it.
    abuse: TokenBucket = field(default_factory=lambda: TokenBucket(2, STRIKES_BEFORE_CLOSE))
    exhausted: bool = False
    dropped_frames: int = 0
    dropped_messages: int = 0

    def allow_message(self, msg_type: str) -> bool:
        if not self.messages.allow():
            self._strike()
            self.dropped_messages += 1
            return False
        spec = TYPE_LIMITS.get(msg_type)
        if spec is not None:
            bucket = self.per_type.get(msg_type)
            if bucket is None:
                bucket = self.per_type[msg_type] = TokenBucket(*spec)
            if not bucket.allow():
                self._strike()
                self.dropped_messages += 1
                return False
        return True

    def allow_frame(self, size: int) -> bool:
        if not self.frames.allow() or not self.audio_bytes.allow(size):
            self.dropped_frames += 1
            self._strike(0.2)
            return False
        return True

    def _strike(self, weight: float = 1.0) -> None:
        if not self.abuse.allow(weight):
            self.exhausted = True

    @property
    def abusive(self) -> bool:
        return self.exhausted
