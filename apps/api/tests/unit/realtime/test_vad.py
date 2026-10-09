from __future__ import annotations

import time

import numpy as np
import pytest

from app.core.config import get_settings
from app.realtime.audio.segmenter import AudioSegmenter, SegmentClosed, SpeechStarted
from app.realtime.audio.vad import WINDOW, EnergyVad, SileroVad, create_vad

from .conftest import feed, load_clip, noise, silence, tone


def windows(samples: np.ndarray):
    for i in range(0, samples.size - WINDOW + 1, WINDOW):
        yield samples[i : i + WINDOW]


@pytest.fixture(scope="module")
def silero_path() -> str:
    return get_settings().vad_model_path


def test_silero_loads_and_rejects_wrong_window(silero_path: str) -> None:
    vad = SileroVad(silero_path)
    with pytest.raises(ValueError):
        vad.process(np.zeros(100, dtype=np.int16))


def test_silero_low_probability_on_silence_and_noise(silero_path: str) -> None:
    vad = SileroVad(silero_path)
    probs = [vad.process(w) for w in windows(np.concatenate((silence(1000), noise(1000))))]
    assert max(probs) < 0.3


def test_silero_high_probability_on_speech_and_state_matters(silero_path: str) -> None:
    speech = load_clip("cost_vague")[: 16000 * 2]
    vad = SileroVad(silero_path)
    probs = [vad.process(w) for w in windows(speech)]
    assert np.mean(np.array(probs) > 0.5) > 0.5
    # A fresh stream on a single mid-speech window differs from the stateful
    # estimate: the recurrent state and the 64-sample context are carried over.
    fresh = SileroVad(silero_path)
    mid = list(windows(speech))[30]
    assert fresh.process(mid) != pytest.approx(probs[30], abs=1e-6)
    vad.reset()
    assert vad.process(mid) == pytest.approx(fresh.__class__(silero_path).process(mid), abs=1e-6)


def test_silero_is_cheap_enough_to_run_inline(silero_path: str) -> None:
    vad = SileroVad(silero_path)
    speech = load_clip("intro")
    started = time.perf_counter()
    n = 0
    for w in windows(speech):
        vad.process(w)
        n += 1
    per_window_ms = (time.perf_counter() - started) * 1000 / n
    assert per_window_ms < 2.0  # measured ~0.08 ms on the GB10


def test_silero_segments_real_answer(silero_path: str) -> None:
    seg = AudioSegmenter(SileroVad(silero_path))
    events = feed(seg, np.concatenate((silence(500), load_clip("cost_vague"), silence(1500))))
    starts = [e for e in events if isinstance(e, SpeechStarted)]
    closed = [e for e in events if isinstance(e, SegmentClosed)]
    assert starts and closed
    assert sum(c.speech_ms for c in closed) > 3000


def test_energy_vad_follows_level_against_noise_floor() -> None:
    vad = EnergyVad()
    quiet = [vad.process(w) for w in windows(noise(1500, amplitude=0.001))]
    loud = [vad.process(w) for w in windows(tone(500, amplitude=0.3))]
    assert max(quiet[5:]) < 0.2
    assert min(loud) > 0.9
    vad.reset()
    assert vad.noise_floor == -60.0


def test_create_vad_falls_back_to_energy_for_missing_model(tmp_path) -> None:
    vad = create_vad("silero", str(tmp_path / "missing.onnx"))
    assert isinstance(vad, EnergyVad)
    assert isinstance(create_vad("energy", "ignored"), EnergyVad)
