"""Helpers for realtime unit tests (synthetic audio, scripted VAD, clips)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from app.realtime.audio.pcm import SAMPLE_RATE, bytes_to_samples, wav_to_pcm16
from app.realtime.audio.vad import WINDOW

AUDIO_DIR = Path(__file__).resolve().parents[5] / "tests" / "e2e" / "fixtures" / "audio"


def load_clip(name: str) -> np.ndarray:
    pcm, rate = wav_to_pcm16((AUDIO_DIR / f"{name}.wav").read_bytes())
    assert rate == SAMPLE_RATE
    return bytes_to_samples(pcm)


def tone(ms: int, *, freq: float = 220.0, amplitude: float = 0.3) -> np.ndarray:
    n = SAMPLE_RATE * ms // 1000
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.int16)


def noise(ms: int, *, amplitude: float = 0.002, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(SAMPLE_RATE * ms // 1000) * amplitude * 32767).astype(np.int16)


class ScriptedVad:
    """Returns probabilities from ``fn(window_start_ms)``."""

    name = "scripted"

    def __init__(self, fn: Callable[[float], float]) -> None:
        self.fn = fn
        self.t = 0
        self.resets = 0

    def process(self, window: np.ndarray) -> float:
        assert window.size == WINDOW
        start_ms = self.t * 1000 / SAMPLE_RATE
        self.t += WINDOW
        return float(self.fn(start_ms))

    def reset(self) -> None:
        self.resets += 1


def speech_between(
    *intervals: tuple[float, float], high: float = 0.95, low: float = 0.02
) -> Callable[[float], float]:
    def fn(t_ms: float) -> float:
        return high if any(a <= t_ms < b for a, b in intervals) else low

    return fn


def feed(segmenter, samples: np.ndarray, frame_ms: int = 100) -> list:
    events = []
    step = SAMPLE_RATE * frame_ms // 1000
    for i in range(0, samples.size, step):
        events.extend(segmenter.process(samples[i : i + step]))
    return events


@pytest.fixture
def clip() -> Callable[[str], np.ndarray]:
    return load_clip
