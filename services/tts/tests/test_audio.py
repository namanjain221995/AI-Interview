from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from tts_service.audio import (
    SUPPORTED_SAMPLE_RATES,
    apply_fades,
    audible_bounds,
    frame_levels,
    quiet_runs,
    resample,
    to_pcm16,
    wav_bytes,
)

SR = 24000


def tone(seconds: float, freq: float = 220.0, amp: float = 0.3, sr: int = SR) -> np.ndarray:
    t = np.arange(round(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(round(seconds * sr), dtype=np.float32)


def test_frame_levels_relative_to_peak() -> None:
    audio = np.concatenate([silence(0.1), tone(0.2, amp=0.5), tone(0.2, amp=0.05)])
    levels = frame_levels(audio, SR)
    assert len(levels) == 100  # 0.5 s / 5 ms
    assert levels.max() == pytest.approx(0.0)
    assert levels[:20].max() < -100  # digital silence sits at the floor
    loud, quiet = levels[30:55].mean(), levels[70:95].mean()
    assert quiet - loud == pytest.approx(-20.0, abs=0.5)  # 10x amplitude = 20 dB


def test_frame_levels_all_silent_and_empty() -> None:
    assert frame_levels(np.zeros(0, dtype=np.float32), SR).size == 0
    levels = frame_levels(silence(0.1) + 1e-6, SR)
    assert (levels <= -100).all()
    assert audible_bounds(levels) is None


def test_audible_bounds_finds_speech_region() -> None:
    audio = np.concatenate([silence(0.3), tone(1.0), silence(0.45)])
    bounds = audible_bounds(frame_levels(audio, SR))
    assert bounds is not None
    assert bounds[0] == pytest.approx(0.3, abs=0.006)
    assert bounds[1] == pytest.approx(1.3, abs=0.006)


def test_quiet_runs_detects_pauses_not_short_dips() -> None:
    audio = np.concatenate([tone(0.4), silence(0.05), tone(0.3), silence(0.25), tone(0.4)])
    levels = frame_levels(audio, SR)
    runs = quiet_runs(levels, 0.0, len(audio) / SR)
    assert len(runs) == 1  # the 50 ms dip is shorter than PAUSE_MIN_SECONDS
    start, end = runs[0]
    assert start == pytest.approx(0.75, abs=0.006)
    assert end == pytest.approx(1.0, abs=0.006)
    assert quiet_runs(levels, 0.8, 0.85) == []  # a 50 ms window inside the pause is too short to qualify
    assert all(isinstance(value, float) for run in runs for value in run)


def test_pcm16_round_trip_and_clipping() -> None:
    audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0, 1.7, -3.0], dtype=np.float32)
    pcm = to_pcm16(audio)
    samples = np.frombuffer(pcm, dtype="<i2")
    assert samples.tolist() == [0, 16384, -16384, 32767, -32767, 32767, -32767]
    assert len(pcm) == 2 * len(audio)


def test_wav_header_is_valid() -> None:
    pcm = to_pcm16(tone(0.25))
    data = wav_bytes(pcm, SR)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE" and len(data) == 44 + len(pcm)
    with wave.open(io.BytesIO(data)) as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == SR
        assert reader.getnframes() == len(pcm) // 2
        assert reader.readframes(reader.getnframes()) == pcm


def test_fades_zero_the_edges() -> None:
    audio = np.ones(SR // 10, dtype=np.float32)
    faded = apply_fades(audio, SR)
    assert faded[0] == 0.0 and faded[-1] == 0.0
    assert faded[len(faded) // 2] == 1.0
    assert audio[0] == 1.0  # input untouched


def dominant_frequency(audio: np.ndarray, sr: int) -> float:
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    return float(np.fft.rfftfreq(len(audio), 1 / sr)[spectrum.argmax()])


@pytest.mark.parametrize("target", [rate for rate in SUPPORTED_SAMPLE_RATES if rate != SR])
def test_resample_preserves_pitch_duration_and_level(target: int) -> None:
    source = tone(0.5, freq=440.0, amp=0.5)
    out = resample(source, SR, target)
    assert abs(len(out) - len(source) * target / SR) <= 1
    assert dominant_frequency(out, target) == pytest.approx(440.0, abs=3.0)
    middle = out[len(out) // 4 : 3 * len(out) // 4]
    assert np.sqrt(np.mean(middle**2)) == pytest.approx(0.5 / np.sqrt(2), rel=0.03)


def test_resample_removes_content_above_new_nyquist() -> None:
    out = resample(tone(0.5, freq=7000.0, amp=0.5), SR, 8000)  # 7 kHz cannot exist at 8 kHz
    assert np.sqrt(np.mean(out[400:-400] ** 2)) < 0.01


def test_resample_identity_and_bad_ratio() -> None:
    audio = tone(0.1)
    assert resample(audio, SR, SR) is audio or np.array_equal(resample(audio, SR, SR), audio)
    with pytest.raises(ValueError):
        resample(audio, SR, 44100)
