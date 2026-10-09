"""Voice activity detection on 512-sample (32 ms @ 16 kHz) windows.

* :class:`SileroVad` runs the Silero v5 ONNX model through onnxruntime. One
  ``InferenceSession`` is shared by the whole process (``run`` is thread safe
  and every stream passes its own recurrent state), so a live session costs
  only its 2x1x128 state plus a 64-sample context. The model expects the
  previous window's last 64 samples prepended to each 512-sample window.
* :class:`EnergyVad` is the fallback when the model is missing or fails: a
  logistic over the window level relative to an adaptive noise floor.

``process(window)`` takes exactly ``WINDOW`` int16 samples and returns a
speech probability in [0, 1].
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.core.logging import get_logger
from app.realtime.audio.pcm import SAMPLE_RATE

log = get_logger(__name__)

WINDOW = 512  # samples per VAD window at 16 kHz (32 ms)
WINDOW_MS = WINDOW * 1000 / SAMPLE_RATE
CONTEXT = 64  # Silero v5 context samples at 16 kHz


class VadStream(Protocol):
    name: str

    def process(self, window: np.ndarray) -> float: ...

    def reset(self) -> None: ...


# --------------------------------------------------------------------- silero

_session_lock = threading.Lock()
_sessions: dict[str, Any] = {}
_failed_paths: set[str] = set()


def load_silero_session(model_path: str) -> Any:
    """Shared onnxruntime session for ``model_path`` (raises on failure)."""
    path = str(Path(model_path).resolve())
    session = _sessions.get(path)
    if session is not None:
        return session
    with _session_lock:
        session = _sessions.get(path)
        if session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])
            _sessions[path] = session
            log.info("realtime.vad.silero_loaded", path=path)
    return session


class SileroVad:
    name = "silero"

    def __init__(self, model_path: str) -> None:
        self._session = load_silero_session(model_path)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def process(self, window: np.ndarray) -> float:
        if window.size != WINDOW:
            raise ValueError(f"VAD window must be {WINDOW} samples (got {window.size})")
        x = window.astype(np.float32) / 32768.0
        frame = np.concatenate((self._context, x))[np.newaxis, :]
        out, state = self._session.run(None, {"input": frame, "state": self._state, "sr": self._sr})
        self._state = state
        self._context = x[-CONTEXT:]
        return float(np.clip(out.reshape(-1)[0], 0.0, 1.0))


# --------------------------------------------------------------------- energy


class EnergyVad:
    """Level-based fallback: probability rises ~12 dB above the noise floor."""

    name = "energy"

    def __init__(
        self, *, margin_db: float = 12.0, slope_db: float = 3.0, min_speech_dbfs: float = -50.0
    ) -> None:
        self.margin_db = margin_db
        self.slope_db = slope_db
        self.min_speech_dbfs = min_speech_dbfs
        self.reset()

    def reset(self) -> None:
        self.noise_floor = -60.0

    def process(self, window: np.ndarray) -> float:
        if window.size == 0:
            return 0.0
        x = window.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x)))
        dbfs = -120.0 if rms <= 1e-7 else 20.0 * math.log10(rms)
        if dbfs < self.noise_floor:
            self.noise_floor = 0.8 * self.noise_floor + 0.2 * dbfs
        else:
            self.noise_floor += 0.002 * (dbfs - self.noise_floor)
        self.noise_floor = min(-25.0, max(-90.0, self.noise_floor))
        threshold = max(self.noise_floor + self.margin_db, self.min_speech_dbfs)
        z = (dbfs - threshold) / self.slope_db
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))


# -------------------------------------------------------------------- factory


def create_vad(engine: str, model_path: str) -> VadStream:
    """Silero when configured and loadable, otherwise the energy fallback."""
    if engine == "silero" and model_path not in _failed_paths:
        try:
            return SileroVad(model_path)
        except Exception as exc:  # noqa: BLE001 - missing file, bad model, ORT error
            _failed_paths.add(model_path)
            log.error(
                "realtime.vad.silero_unavailable", path=model_path, error=f"{type(exc).__name__}: {exc}"[:300]
            )
    return EnergyVad()
