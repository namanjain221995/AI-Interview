"""PCM16 helpers and a sample-indexed ring buffer (16 kHz mono)."""

from __future__ import annotations

import io
import wave

import numpy as np

from app.schemas.realtime import MIC_SAMPLE_RATE

SAMPLE_RATE = MIC_SAMPLE_RATE  # 16 kHz mono PCM16 from the browser


def ms_to_samples(ms: float, sample_rate: int = SAMPLE_RATE) -> int:
    return int(round(ms * sample_rate / 1000.0))


def samples_to_ms(samples: int, sample_rate: int = SAMPLE_RATE) -> int:
    return int(samples * 1000 // sample_rate)


def bytes_to_samples(data: bytes) -> np.ndarray:
    """Little-endian PCM16 bytes -> int16 array (a trailing odd byte is dropped)."""
    usable = len(data) - (len(data) % 2)
    return np.frombuffer(data[:usable], dtype="<i2").astype(np.int16, copy=False)


def samples_to_bytes(samples: np.ndarray) -> bytes:
    return np.asarray(samples, dtype="<i2").tobytes()


def rms_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    x = samples.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    return -120.0 if rms <= 1e-7 else 20.0 * float(np.log10(rms))


def pcm16_to_wav(pcm: bytes | np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap mono PCM16 in a RIFF/WAVE container."""
    data = pcm if isinstance(pcm, bytes | bytearray) else samples_to_bytes(pcm)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(data))
    return buffer.getvalue()


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    """Decode a mono PCM16 WAV -> (pcm bytes, sample_rate). Raises ``ValueError``."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                raise ValueError("expected mono PCM16 WAV")
            return wav.readframes(wav.getnframes()), wav.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc


class PcmRingBuffer:
    """Fixed-capacity int16 ring addressed by absolute sample index.

    ``total`` is the number of samples ever written; ``read(start, end)``
    returns the samples in ``[start, end)`` that are still retained (older
    samples are silently clipped)."""

    def __init__(self, capacity_samples: int) -> None:
        if capacity_samples <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = int(capacity_samples)
        self._buf = np.zeros(self.capacity, dtype=np.int16)
        self.total = 0

    @property
    def oldest(self) -> int:
        return max(0, self.total - self.capacity)

    def write(self, samples: np.ndarray) -> None:
        n = int(samples.size)
        if n == 0:
            return
        if n >= self.capacity:
            self._buf[:] = samples[-self.capacity :]
            self.total += n
            # Re-align so that index (total % capacity) is the next write slot.
            shift = self.total % self.capacity
            self._buf = np.roll(self._buf, shift)
            return
        start = self.total % self.capacity
        first = min(n, self.capacity - start)
        self._buf[start : start + first] = samples[:first]
        if first < n:
            self._buf[: n - first] = samples[first:]
        self.total += n

    def read(self, start: int, end: int | None = None) -> np.ndarray:
        end = self.total if end is None else min(end, self.total)
        start = max(start, self.oldest)
        if end <= start:
            return np.zeros(0, dtype=np.int16)
        n = end - start
        s = start % self.capacity
        if s + n <= self.capacity:
            return self._buf[s : s + n].copy()
        first = self.capacity - s
        return np.concatenate((self._buf[s:], self._buf[: n - first]))
