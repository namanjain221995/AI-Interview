from __future__ import annotations

import numpy as np

from app.realtime.audio.pcm import SAMPLE_RATE, PcmRingBuffer, pcm16_to_wav, wav_to_pcm16
from app.realtime.audio.segmenter import AudioSegmenter, HighSpeech, SegmentClosed, SpeechEnded, SpeechStarted
from app.realtime.audio.vad import EnergyVad

from .conftest import ScriptedVad, feed, silence, speech_between, tone


def kinds(events) -> list[str]:
    return [type(e).__name__ for e in events]


def test_speech_start_needs_three_windows_and_keeps_preroll() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((1000, 2000))), preroll_ms=300)
    events = feed(seg, tone(3000))
    starts = [e for e in events if isinstance(e, SpeechStarted)]
    assert len(starts) == 1
    # first window at/after 1000 ms starts at 1024 ms (window grid of 32 ms)
    assert 990 <= starts[0].t_ms <= 1040
    assert starts[0].segment_start_ms == starts[0].t_ms - 300


def test_isolated_spikes_do_not_start_speech() -> None:
    def spiky(t: float) -> float:
        return 0.9 if int(t // 32) % 3 == 0 else 0.1  # never 3 consecutive

    seg = AudioSegmenter(ScriptedVad(spiky))
    assert not [e for e in feed(seg, tone(2000)) if isinstance(e, SpeechStarted)]


def test_pause_closes_segment_and_reports_speech_end() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((500, 1500))), segment_pause_ms=350, tail_ms=150)
    events = feed(seg, tone(2500))
    assert kinds(events) == ["SpeechStarted", "HighSpeech", "SegmentClosed", "SpeechEnded"]
    closed = next(e for e in events if isinstance(e, SegmentClosed))
    ended = next(e for e in events if isinstance(e, SpeechEnded))
    assert closed.reason == "pause"
    assert abs(ended.t_ms - 1500) <= 40
    assert closed.end_ms == ended.t_ms + 150  # tail kept for Whisper
    assert closed.samples.size == (closed.end_ms - closed.start_ms) * SAMPLE_RATE // 1000
    assert 900 <= closed.speech_ms <= 1100


def test_short_dip_does_not_close_segment() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((500, 1500), (1700, 2500))), segment_pause_ms=350)
    events = feed(seg, tone(3500))
    closed = [e for e in events if isinstance(e, SegmentClosed)]
    assert len(closed) == 1
    assert closed[0].start_ms < 500 and closed[0].end_ms >= 2500


def test_long_pause_produces_two_ordered_segments_without_overlap() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((300, 1300), (2000, 3000))))
    events = feed(seg, tone(4000))
    closed = [e for e in events if isinstance(e, SegmentClosed)]
    assert [c.index for c in closed] == [0, 1]
    assert closed[1].start_ms >= closed[0].end_ms


def test_max_segment_split_at_lowest_energy_point() -> None:
    # 12 s of speech with a quiet dip at 7.0-7.2 s; max segment 9 s.
    audio = np.concatenate(
        (tone(7000, amplitude=0.4), tone(200, amplitude=0.01), tone(4800, amplitude=0.4), silence(1000))
    )
    seg = AudioSegmenter(ScriptedVad(speech_between((0, 12000))), max_segment_ms=9000, preroll_ms=0)
    events = feed(seg, audio)
    closed = [e for e in events if isinstance(e, SegmentClosed)]
    assert [c.reason for c in closed] == ["max_length", "pause"]
    assert 7000 <= closed[0].end_ms <= 7200
    assert closed[1].start_ms == closed[0].end_ms
    assert all(c.duration_ms <= 9000 for c in closed)


def test_flush_closes_open_segment() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((0, 10_000))))
    feed(seg, tone(1500))
    assert seg.segment_open
    closed = seg.flush()
    assert closed is not None and closed.reason == "flush"
    assert not seg.segment_open and seg.flush() is None


def test_high_speech_fires_once_per_sustained_episode() -> None:
    def probs(t: float) -> float:
        if 200 <= t < 600:
            return 0.95
        if 600 <= t < 700:
            return 0.6  # dip that stays above speech threshold: episode continues
        if 700 <= t < 800:
            return 0.95
        if 1500 <= t < 1700:
            return 0.95  # 200 ms: below the 280 ms barge-in minimum
        return 0.05

    seg = AudioSegmenter(ScriptedVad(probs), barge_in_threshold=0.8, barge_in_min_speech_ms=280)
    highs = [e for e in feed(seg, tone(2500)) if isinstance(e, HighSpeech)]
    assert len(highs) == 1
    assert highs[0].sustained_ms >= 280
    assert 470 <= highs[0].t_ms <= 520


def test_silence_ms_tracks_time_since_last_voiced_window() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((0, 1000))), segment_pause_ms=5000)
    feed(seg, tone(1600))
    assert 550 <= seg.silence_ms <= 650
    assert seg.in_speech  # dip shorter than the (large) pause keeps speech open


def test_energy_vad_segments_synthetic_bursts() -> None:
    audio = np.concatenate((silence(800), tone(1200), silence(900), tone(800), silence(900)))
    seg = AudioSegmenter(EnergyVad())
    closed = [e for e in feed(seg, audio) if isinstance(e, SegmentClosed)]
    assert len(closed) == 2
    assert 400 <= closed[0].start_ms <= 800 and 1950 <= closed[0].end_ms <= 2250
    assert closed[1].start_ms >= closed[0].end_ms


def test_ring_buffer_wraps_and_clips() -> None:
    ring = PcmRingBuffer(1000)
    data = np.arange(2500, dtype=np.int16)
    for i in range(0, 2500, 300):
        ring.write(data[i : i + 300])
    assert ring.total == 2500 and ring.oldest == 1500
    assert np.array_equal(ring.read(1500, 2500), data[1500:2500])
    assert np.array_equal(ring.read(0, 1600), data[1500:1600])  # clipped to retained range
    big = np.arange(5000, 8000, dtype=np.int16)
    ring.write(big)
    assert np.array_equal(ring.read(ring.total - 10), big[-10:])


def test_wav_roundtrip() -> None:
    samples = tone(250)
    pcm, rate = wav_to_pcm16(pcm16_to_wav(samples))
    assert rate == SAMPLE_RATE
    assert np.array_equal(np.frombuffer(pcm, dtype="<i2"), samples)


def test_eager_pause_closes_a_long_segment_on_a_short_pause() -> None:
    """Once the segment holds eager_after_ms of audio, a 200 ms pause already
    closes it (the 350 ms pause still applies to short segments)."""
    script = speech_between((300, 1300), (1520, 3400), (3620, 4400))  # pauses of ~220 ms
    plain = AudioSegmenter(ScriptedVad(script), segment_pause_ms=350)
    eager = AudioSegmenter(ScriptedVad(script), segment_pause_ms=350, eager_pause_ms=200, eager_after_ms=2500)
    plain_closed = [e for e in feed(plain, tone(5200)) if isinstance(e, SegmentClosed)]
    eager_closed = [e for e in feed(eager, tone(5200)) if isinstance(e, SegmentClosed)]
    assert len(plain_closed) == 1  # 220 ms pauses never reach 350 ms
    # first pause: the segment is only ~1.3 s old -> no eager close; second pause: ~3.4 s -> closed
    assert len(eager_closed) == 2
    assert abs(eager_closed[0].end_ms - (3400 + 150)) <= 40
    # the next segment may reuse the previous one's silent 150 ms tail as pre-roll
    assert eager_closed[0].end_ms - 150 <= eager_closed[1].start_ms < 3620
    assert all(c.reason == "pause" for c in eager_closed)


def test_segment_reports_mean_vad_probability() -> None:
    seg = AudioSegmenter(ScriptedVad(speech_between((300, 1500), high=0.9)), segment_pause_ms=350)
    closed = next(e for e in feed(seg, tone(2500)) if isinstance(e, SegmentClosed))
    assert 0.85 <= closed.mean_prob <= 0.95


def test_forced_split_waits_for_a_pause_between_words() -> None:
    """Max-length splits never cut a word: with continuous speech (no quiet window)
    the segment may grow to 1.5x the limit; a pause in the second half is used."""
    continuous = AudioSegmenter(ScriptedVad(speech_between((0, 8000))), max_segment_ms=4000, preroll_ms=0)
    early = [e for e in feed(continuous, tone(5900)) if isinstance(e, SegmentClosed)]
    assert early == []  # no pause yet: not split at 4 s
    late = [
        e
        for e in feed(continuous, np.concatenate((tone(2100), silence(800))))
        if isinstance(e, SegmentClosed)
    ]
    assert late[0].reason == "max_length"  # split once the 1.5x hard limit (6 s) was reached

    def with_gap(t: float) -> float:
        return 0.1 if 4500 <= t < 4600 else (0.95 if t < 8000 else 0.02)

    paused = AudioSegmenter(ScriptedVad(with_gap), max_segment_ms=4000, preroll_ms=0)
    closed = [
        e for e in feed(paused, np.concatenate((tone(8000), silence(800)))) if isinstance(e, SegmentClosed)
    ]
    assert closed[0].reason == "max_length" and 4500 <= closed[0].end_ms <= 4630  # at the pause
