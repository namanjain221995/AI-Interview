"""The "trouble hearing you" notice (stt_degraded) needs real evidence of failed speech.

Owner report (2026-10-09): the banner appeared at the very start of an interview,
before the candidate had answered. The trigger was Whisper replicas timing out on
segments opened by room noise; only failures on clearly voiced audio may raise it.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.realtime.runtime import (
    STT_DEGRADED_SPEECH_MS,
    SessionRuntime,
)
from app.realtime.stt import SegmentJob


def _fake_runtime() -> SimpleNamespace:
    notices: list[str] = []
    return SimpleNamespace(
        _stt_degraded_notified=False,
        _stt_failed_speech_ms=0,
        notices=notices,
        notice=lambda code, message, level="info": notices.append(code),
    )


def _job(index: int, speech_ms: int | None, vad_prob: float | None) -> SegmentJob:
    return SegmentJob(index=index, start_ms=0, end_ms=2000, wav=b"", speech_ms=speech_ms, vad_prob=vad_prob)


def _fail(rt: SimpleNamespace, job: SegmentJob) -> None:
    SessionRuntime._on_segment_failure(rt, job, RuntimeError("whisper: waited 30.0s for an STT replica"))  # type: ignore[arg-type]


def test_noise_and_silence_failures_never_raise_the_notice() -> None:
    rt = _fake_runtime()
    for i in range(30):  # many short / low-probability segments, as from room noise
        _fail(rt, _job(i, speech_ms=300, vad_prob=0.9))
        _fail(rt, _job(i, speech_ms=1500, vad_prob=0.3))
        _fail(rt, _job(i, speech_ms=None, vad_prob=None))
    assert rt.notices == []
    assert rt._stt_failed_speech_ms == 0


def test_one_short_voiced_failure_is_not_enough() -> None:
    rt = _fake_runtime()
    _fail(rt, _job(0, speech_ms=900, vad_prob=0.85))
    assert rt.notices == []


def test_failed_candidate_speech_raises_it_once() -> None:
    rt = _fake_runtime()
    _fail(rt, _job(0, speech_ms=900, vad_prob=0.85))
    _fail(rt, _job(1, speech_ms=STT_DEGRADED_SPEECH_MS - 900, vad_prob=0.8))
    _fail(rt, _job(2, speech_ms=2000, vad_prob=0.9))
    assert rt.notices == ["stt_degraded"]


def test_a_good_final_resets_the_evidence_and_recovers() -> None:
    rt = _fake_runtime()
    _fail(rt, _job(0, speech_ms=1000, vad_prob=0.9))
    SessionRuntime._on_segment_final(rt, _job(1, speech_ms=1000, vad_prob=0.9))  # type: ignore[arg-type]
    _fail(rt, _job(2, speech_ms=1000, vad_prob=0.9))
    assert rt.notices == []  # 1000 + (reset) 1000 never reaches the threshold
    _fail(rt, _job(3, speech_ms=1000, vad_prob=0.9))
    assert rt.notices == ["stt_degraded"]
    SessionRuntime._on_segment_final(rt, _job(4, speech_ms=1000, vad_prob=0.9))  # type: ignore[arg-type]
    assert rt.notices == ["stt_degraded", "stt_recovered"]
