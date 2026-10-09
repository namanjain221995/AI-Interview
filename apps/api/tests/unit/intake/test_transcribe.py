"""Windowed final pass of activity captures (INTAKE_INTERVIEW.md 7.7): windows <= 25 s cut at VAD pauses,
spare-replica transcription with a bounded wait, statuses."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.base import AIUnavailable, CallContext, PartialSkipped, TranscriptionResult
from app.domain.enums import ModelTask
from app.services.intake.transcribe import (
    BYTES_PER_MS,
    Window,
    build_windows,
    keeps_words,
    window_audio,
    windowed_final_pass,
)


def seg(start: int, end: int, text: str = "words") -> dict[str, Any]:
    return {"start_ms": start, "end_ms": end, "speech_ms": end - start, "text": text}


def pause(start: int, end: int) -> dict[str, Any]:
    return {"start_ms": start, "end_ms": end, "duration_ms": end - start}


def test_short_capture_is_one_window() -> None:
    windows = build_windows([seg(1000, 6000), seg(6400, 12000)], [pause(6000, 6400)])
    assert [(w.start_ms, w.end_ms) for w in windows] == [(1000, 12000)]


def test_long_capture_is_cut_at_pause_midpoints_within_25_seconds() -> None:
    segments = [seg(t, t + 5600) for t in range(0, 70_000, 6000)]  # 400 ms gaps
    pauses = [pause(t + 5600, t + 6000) for t in range(0, 64_000, 6000)]
    windows = build_windows(segments, pauses)
    assert all(w.duration_ms <= 25_000 for w in windows)
    assert (
        windows[0].start_ms == 0
        and windows[-1].end_ms == 71_600 - 6000 + 5600
        or windows[-1].end_ms == max(s["end_ms"] for s in segments)
    )
    midpoints = {(p["start_ms"] + p["end_ms"]) // 2 for p in pauses}
    for left, right in zip(windows, windows[1:], strict=False):
        assert left.end_ms == right.start_ms and left.end_ms in midpoints
    assert len(windows) == 3


def test_short_pauses_are_not_cut_points_but_segment_gaps_are_the_fallback() -> None:
    segments = [seg(t, t + 5900) for t in range(0, 40_000, 6000)]  # 100 ms gaps, no VAD pause >= 300 ms
    windows = build_windows(segments, [pause(5900, 6000)])
    gaps = {(t + 5900 + t + 6000) // 2 for t in range(0, 40_000, 6000)}
    assert len(windows) == 2 and windows[0].end_ms in gaps and windows[0].duration_ms <= 25_000


def test_continuous_speech_longer_than_the_limit_is_cut_hard() -> None:
    windows = build_windows([seg(0, 31_000)], [])
    assert [(w.start_ms, w.end_ms) for w in windows] == [(0, 25_000), (25_000, 31_000)]


def test_windows_without_speech_are_dropped() -> None:
    windows = build_windows([seg(0, 3000), seg(60_000, 63_000)], [pause(3000, 60_000)])
    assert all(
        any(s <= w.start_ms < e or w.start_ms <= s < w.end_ms for s, e in [(0, 3000), (60_000, 63_000)])
        for w in windows
    )
    assert build_windows([], []) == []


def test_window_audio_offsets_follow_the_session_origin() -> None:
    pcm = bytes(range(256)) * 400  # 102400 bytes = 3200 ms
    audio = window_audio(pcm, Window(11_000, 11_500), origin_ms=10_000)
    assert len(audio) == 500 * BYTES_PER_MS
    assert audio == pcm[1000 * BYTES_PER_MS : 1500 * BYTES_PER_MS]


def test_keeps_words() -> None:
    assert keeps_words("one two three four five", "one two three four")
    assert not keeps_words("one two three four five", "one two")
    assert not keeps_words("one", "")


class FakeStt:
    """Records calls; ``script`` items: str | Exception | 'skip' (PartialSkipped). Accepts ``background``."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav", language=None, context=None,
                         background: bool = False, prompt=None, partial: bool = False) -> TranscriptionResult:  # fmt: skip
        return await self._next(audio, background=background)

    async def _next(self, audio: bytes, *, background: Any) -> TranscriptionResult:
        self.calls.append({"bytes": len(audio), "background": background})
        item = self.script.pop(0) if self.script else "text"
        if item == "skip":
            raise PartialSkipped("busy", endpoint="whisper")
        if isinstance(item, Exception):
            raise item
        return TranscriptionResult(text=item, language="en", duration_s=1.0)


class PlainStt(FakeStt):
    """An STT client without the ``background`` option (the protocol / mocks)."""

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav", language=None, context=None,
                         prompt=None, partial: bool = False) -> TranscriptionResult:  # type: ignore[override]  # fmt: skip
        return await self._next(audio, background=None)


CTX = CallContext(task=ModelTask.STT, session_id="s1")
PCM = b"\x01\x00" * 16000 * 60  # 60 s of PCM16


async def test_final_pass_transcribes_each_window_on_a_spare_replica() -> None:
    stt = FakeStt("first part.", "second part.")
    segments = [seg(0, 20_000), seg(20_500, 40_000)]
    result = await windowed_final_pass(
        PCM, segments, [pause(20_000, 20_500)], origin_ms=0, stt=stt, context=CTX, correct=lambda t: t.upper()
    )
    assert (
        result.status == "final" and result.text == "FIRST PART, SECOND PART."
    )  # join_texts: lower-case continuation
    assert [c["background"] for c in stt.calls] == [True, True]
    assert [w.record() for w in result.windows] == [
        {"start_ms": 0, "end_ms": 20_250, "text": "first part."},
        {"start_ms": 20_250, "end_ms": 40_000, "text": "second part."},
    ]


async def test_final_pass_without_background_support_still_runs() -> None:
    stt = PlainStt("hello there.")
    result = await windowed_final_pass(PCM, [seg(0, 4000)], [], origin_ms=0, stt=stt, context=CTX)
    assert result.status == "final" and stt.calls[0]["background"] is None


async def test_busy_replicas_until_the_wait_runs_out_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.intake.transcribe as mod

    monkeypatch.setattr(mod, "RETRY_SLEEP_S", 0.01)
    stt = FakeStt(*(["skip"] * 50))
    result = await windowed_final_pass(
        PCM, [seg(0, 4000)], [], origin_ms=0, stt=stt, context=CTX, wait_s=0.05
    )
    assert result.status == "busy" and result.error


async def test_a_busy_replica_that_frees_up_in_time_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.intake.transcribe as mod

    monkeypatch.setattr(mod, "RETRY_SLEEP_S", 0.01)
    stt = FakeStt("skip", "skip", "finally.")
    result = await windowed_final_pass(PCM, [seg(0, 4000)], [], origin_ms=0, stt=stt, context=CTX, wait_s=5)
    assert result.status == "final" and result.text == "finally." and len(stt.calls) == 3


async def test_stt_error_is_failed_and_no_segments_is_empty() -> None:
    failed = await windowed_final_pass(
        PCM,
        [seg(0, 4000)],
        [],
        origin_ms=0,
        stt=FakeStt(AIUnavailable("down", endpoint="whisper")),
        context=CTX,
    )
    assert failed.status == "failed" and "AIUnavailable" in (failed.error or "")
    empty = await windowed_final_pass(PCM, [], [], origin_ms=0, stt=FakeStt(), context=CTX)
    assert empty.status == "empty"
