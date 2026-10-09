"""Final transcript of an intake activity capture (design contract 7.7).

The live transcript of a 2-3 minute reading or answer is the join of short per-segment transcriptions (made for
endpointing, not accuracy). After the capture the runtime re-transcribes the whole audio in windows of at most
25 s - Whisper's client timeout is 30 s, so a whole 180 s reading can never go in one request - built by merging
consecutive live segments and cutting at VAD pauses (>= 300 ms). Each window runs with ``background=True``
(spare Whisper replica only, ``PartialSkipped`` when none is idle) and the pass waits at most
``INTAKE_ACTIVITY_FINAL_PASS_WAIT_SECONDS`` in total for replicas; when it cannot run, the worker job
``intake.activity_transcribe`` repeats it from the stored WAV.

Timestamps are on the session clock (ms); ``origin_ms`` is the session time of the first PCM sample.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.ai.base import AIError, CallContext, PartialSkipped
from app.core.logging import get_logger
from app.realtime.audio.pcm import pcm16_to_wav
from app.realtime.stt import accepts_stt_option, join_texts

log = get_logger(__name__)

WINDOW_MAX_MS = 25_000
PAUSE_MIN_MS = 300
MIN_WINDOW_SPEECH_MS = 200
BYTES_PER_MS = 32  # 16 kHz mono PCM16
RETRY_SLEEP_S = 0.5


@dataclass(slots=True)
class Window:
    start_ms: int
    end_ms: int
    text: str = ""

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def record(self) -> dict[str, Any]:
        return {"start_ms": int(self.start_ms), "end_ms": int(self.end_ms), "text": self.text}


@dataclass(slots=True)
class FinalPassResult:
    status: Literal["final", "busy", "failed", "empty"]
    text: str = ""
    windows: list[Window] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None

    def window_records(self) -> list[dict[str, Any]]:
        return [w.record() for w in self.windows]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _spans(items: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    spans = []
    for item in items:
        start, end = _int(item.get("start_ms")), _int(item.get("end_ms"))
        if end > start:
            spans.append((start, end))
    return sorted(spans)


def build_windows(
    segments: Sequence[Mapping[str, Any]],
    pauses: Sequence[Mapping[str, Any]],
    *,
    max_ms: int = WINDOW_MAX_MS,
    min_pause_ms: int = PAUSE_MIN_MS,
) -> list[Window]:
    """Windows (session ms) covering the speech: each at most ``max_ms``, cut at the midpoint of a VAD pause
    >= ``min_pause_ms`` when one exists inside the limit, else at the gap between two live segments, else hard at
    the limit (continuous speech longer than ``max_ms`` - rare). Windows without speech are dropped."""
    seg_spans = _spans(segments)
    if not seg_spans:
        return []
    span_start = seg_spans[0][0]
    span_end = max(end for _, end in seg_spans)
    pause_cuts = sorted(
        (start + end) // 2
        for start, end in _spans(pauses)
        if end - start >= min_pause_ms and span_start < (start + end) // 2 < span_end
    )
    gap_cuts = sorted(
        (prev_end + next_start) // 2
        for (_, prev_end), (next_start, _) in zip(seg_spans, seg_spans[1:], strict=False)
        if next_start >= prev_end
    )
    windows: list[Window] = []
    cursor = span_start
    while cursor < span_end:
        limit = cursor + max_ms
        if span_end <= limit:
            windows.append(Window(cursor, span_end))
            break
        cut = next((c for c in reversed(pause_cuts) if cursor < c <= limit), None)
        if cut is None:
            cut = next((c for c in reversed(gap_cuts) if cursor < c <= limit), None)
        if cut is None:
            cut = limit
        windows.append(Window(cursor, cut))
        cursor = cut
    return [w for w in windows if _speech_in(w, seg_spans) >= MIN_WINDOW_SPEECH_MS]


def _speech_in(window: Window, spans: Sequence[tuple[int, int]]) -> int:
    return sum(max(0, min(end, window.end_ms) - max(start, window.start_ms)) for start, end in spans)


def window_audio(pcm: bytes, window: Window, *, origin_ms: int) -> bytes:
    """The PCM16 bytes of ``window`` (session ms) inside a capture whose first sample is at ``origin_ms``."""
    start = max(0, (window.start_ms - origin_ms) * BYTES_PER_MS)
    end = max(start, (window.end_ms - origin_ms) * BYTES_PER_MS)
    start -= start % 2
    end -= end % 2
    return pcm[start:end]


async def windowed_final_pass(
    pcm: bytes,
    segments: Sequence[Mapping[str, Any]],
    pauses: Sequence[Mapping[str, Any]],
    *,
    origin_ms: int,
    stt: Any,
    context: CallContext,
    language: str | None = None,
    wait_s: float = 60.0,
    correct: Callable[[str], str] | None = None,
    background: bool = True,
    max_window_ms: int = WINDOW_MAX_MS,
) -> FinalPassResult:
    """Transcribe ``pcm`` window by window (sequentially: one spare replica at a time).

    ``busy``: no spare replica within ``wait_s`` (the worker job retries from the stored WAV); ``failed``: an
    STT error; ``empty``: no speech segment to transcribe."""
    started = time.monotonic()
    windows = build_windows(segments, pauses, max_ms=max_window_ms)
    if not windows or not pcm:
        return FinalPassResult("empty", windows=windows)
    options: dict[str, Any] = {}
    if background and accepts_stt_option(stt, "background"):
        options["background"] = True
    for window in windows:
        audio = window_audio(pcm, window, origin_ms=origin_ms)
        if not audio:
            continue
        wav = pcm16_to_wav(audio)
        while True:
            try:
                result = await stt.transcribe(
                    wav, content_type="audio/wav", language=language, context=context, **options
                )
                window.text = (result.text or "").strip()
                break
            except PartialSkipped:
                if time.monotonic() - started >= wait_s:
                    return FinalPassResult(
                        "busy",
                        windows=windows,
                        latency_ms=(time.monotonic() - started) * 1000.0,
                        error="no spare STT replica",
                    )
                await asyncio.sleep(RETRY_SLEEP_S)
            except AIError as exc:
                return FinalPassResult(
                    "failed",
                    windows=windows,
                    latency_ms=(time.monotonic() - started) * 1000.0,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                )
    text = join_texts([w.text for w in windows])
    if correct is not None and text:
        text = correct(text)
    return FinalPassResult(
        "final", text=text, windows=windows, latency_ms=(time.monotonic() - started) * 1000.0
    )


def keeps_words(live: str, final: str, *, ratio: float = 0.8) -> bool:
    """The final pass kept at least ``ratio`` of the live transcript's words (answer revision rule)."""
    live_words = len((live or "").split())
    final_words = len((final or "").split())
    return final_words > 0 and final_words >= ratio * live_words


__all__ = [
    "PAUSE_MIN_MS",
    "WINDOW_MAX_MS",
    "FinalPassResult",
    "Window",
    "build_windows",
    "keeps_words",
    "window_audio",
    "windowed_final_pass",
]
