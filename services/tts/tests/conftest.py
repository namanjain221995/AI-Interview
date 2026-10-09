from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from tts_service.audio import to_pcm16
from tts_service.engine import Synthesis, VoiceInfo
from tts_service.visemes import VisemeCue

SERVICE_ROOT = Path(__file__).resolve().parents[1]

MODELS_DIR = Path(os.environ.get("TTS_TEST_MODELS_DIR", SERVICE_ROOT / "models"))
BASE_MODEL = MODELS_DIR / "kokoro-v1.0.onnx"
TIMED_MODEL = MODELS_DIR / "kokoro-v1.0-durations.onnx"
VOICES = MODELS_DIR / "voices-v1.0.bin"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if BASE_MODEL.is_file() and VOICES.is_file():
        return
    skip = pytest.mark.skip(reason=f"Kokoro model files not found in {MODELS_DIR}")
    for item in items:
        if "model" in item.keywords:
            item.add_marker(skip)


class FakeEngine:
    """Engine double: deterministic 0.5 s tone, optional delay/failure/blocking load."""

    model_name = "kokoro-test"

    def __init__(
        self,
        *,
        delay: float = 0.0,
        fail: bool = False,
        load_error: Exception | None = None,
        load_gate: threading.Event | None = None,
    ) -> None:
        self.delay = delay
        self.fail = fail
        self.load_error = load_error
        self.load_gate = load_gate
        self.calls: list[tuple[str, str, float, int]] = []
        self._lock = threading.Lock()

    @property
    def timing_source(self) -> str:
        return "model"

    def load(self) -> None:
        if self.load_gate is not None:
            self.load_gate.wait(timeout=10)
        if self.load_error is not None:
            raise self.load_error

    def warm_up(self, text: str, voice: str) -> float:
        return 0.012

    def voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo("af_alloy", "en-us", "American English", "female"),
            VoiceInfo("af_heart", "en-us", "American English", "female"),
            VoiceInfo("am_michael", "en-us", "American English", "male"),
            VoiceInfo("bf_emma", "en-gb", "British English", "female"),
            VoiceInfo("ef_dora", "es", "Spanish", "female"),
        ]

    def synthesize(self, text: str, voice: str, speed: float, sample_rate: int) -> Synthesis:
        with self._lock:
            self.calls.append((text, voice, speed, sample_rate))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("onnxruntime exploded")
        samples = sample_rate // 2
        t = np.arange(samples) / sample_rate
        pcm = to_pcm16(0.2 * np.sin(2 * np.pi * 220 * t))
        cues = [
            VisemeCue(0, "sil", 1.0),
            VisemeCue(20, "aa", 0.9),
            VisemeCue(200, "PP", 1.0),
            VisemeCue(480, "sil", 1.0),
        ]
        return Synthesis(pcm, sample_rate, 500, cues, "fˈeɪk", voice, 0.05, "model", (20, 480))


@pytest.fixture
def fake_engine() -> FakeEngine:
    return FakeEngine()
