"""Signal helpers: loudness frames, audible region, pauses, PCM/WAV, resampling."""

from __future__ import annotations

import math
import struct

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float32]

FRAME_SECONDS = 0.005
# Frames this far below the loudest frame count as silence when looking for the
# start and end of speech. Kokoro's leading/trailing padding sits at -100 dB or
# lower, soft onsets (h, f, th) at -25 to -40 dB.
AUDIBLE_THRESHOLD_DB = -45.0
# Internal pauses: quieter than this for at least PAUSE_MIN_SECONDS. Stop
# closures inside words stay below ~100 ms; clause and sentence pauses are longer.
PAUSE_THRESHOLD_DB = -40.0
PAUSE_MIN_SECONDS = 0.1
# Peak frame RMS below this (about -80 dBFS) means the clip holds no speech at all.
ABSOLUTE_SILENCE_RMS = 1e-4
_LEVEL_FLOOR_DB = -120.0

SUPPORTED_SAMPLE_RATES = (8000, 12000, 16000, 24000, 32000, 48000)


def frame_levels(
    audio: NDArray[np.floating], sample_rate: int, frame_seconds: float = FRAME_SECONDS
) -> NDArray[np.float64]:
    """RMS level of consecutive frames in dB relative to the loudest frame (0 dB).

    A clip whose loudest frame is below ``ABSOLUTE_SILENCE_RMS`` returns the
    floor level for every frame.
    """
    frame = max(1, round(sample_rate * frame_seconds))
    count = math.ceil(len(audio) / frame)
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    padded = np.zeros(count * frame, dtype=np.float64)
    padded[: len(audio)] = audio
    rms = np.sqrt(np.mean(padded.reshape(count, frame) ** 2, axis=1))
    peak = float(rms.max())
    if peak < ABSOLUTE_SILENCE_RMS:
        return np.full(count, _LEVEL_FLOOR_DB)
    floor = peak * 10 ** (_LEVEL_FLOOR_DB / 20)
    return 20.0 * np.log10(np.maximum(rms, floor) / peak)


def audible_bounds(
    levels: NDArray[np.float64],
    frame_seconds: float = FRAME_SECONDS,
    threshold_db: float = AUDIBLE_THRESHOLD_DB,
) -> tuple[float, float] | None:
    """(start, end) in seconds of the region between the first and last audible frame."""
    loud = np.flatnonzero(levels > threshold_db)
    if loud.size == 0:
        return None
    return float(loud[0] * frame_seconds), float((loud[-1] + 1) * frame_seconds)


def quiet_runs(
    levels: NDArray[np.float64],
    start: float,
    end: float,
    frame_seconds: float = FRAME_SECONDS,
    threshold_db: float = PAUSE_THRESHOLD_DB,
    min_seconds: float = PAUSE_MIN_SECONDS,
) -> list[tuple[float, float]]:
    """Pauses (start, end) in seconds inside [start, end]: runs of quiet frames at least ``min_seconds`` long."""
    lo = max(0, int(start / frame_seconds))
    hi = min(len(levels), math.ceil(end / frame_seconds))
    if hi <= lo:
        return []
    quiet = levels[lo:hi] < threshold_db
    edges = np.diff(np.concatenate([[0], quiet.astype(np.int8), [0]]))
    run_starts = np.flatnonzero(edges == 1)
    run_ends = np.flatnonzero(edges == -1)
    min_frames = round(min_seconds / frame_seconds)
    return [
        (float((lo + s) * frame_seconds), float((lo + e) * frame_seconds))
        for s, e in zip(run_starts, run_ends, strict=True)
        if e - s >= min_frames
    ]


def apply_fades(
    audio: FloatArray, sample_rate: int, fade_in: float = 0.003, fade_out: float = 0.005
) -> FloatArray:
    """Short linear fades so a cut never starts or ends on a non-zero sample (no clicks)."""
    out = audio.astype(np.float32, copy=True)
    n_in = min(len(out), round(fade_in * sample_rate))
    n_out = min(len(out), round(fade_out * sample_rate))
    if n_in > 1:
        out[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)
    if n_out > 1:
        out[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)
    return out


def to_pcm16(audio: NDArray[np.floating]) -> bytes:
    """Float samples in [-1, 1] to little-endian signed 16-bit PCM."""
    clipped = np.clip(np.asarray(audio, dtype=np.float64), -1.0, 1.0)
    return np.round(clipped * 32767.0).astype("<i2").tobytes()


def wav_bytes(pcm16: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap PCM16 samples in a canonical 44-byte RIFF/WAVE header."""
    block_align = channels * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm16),
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        16,
        b"data",
        len(pcm16),
    )
    return header + pcm16


def resample(audio: FloatArray, source_rate: int, target_rate: int) -> FloatArray:
    """Rational-ratio resampling with a Kaiser-windowed sinc low-pass filter.

    Meant for the small ratios between the supported rates (24 kHz to 16, 48,
    8 kHz ...): zero-stuff by ``up``, filter, keep every ``down``-th sample.
    """
    if source_rate == target_rate or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    gcd = math.gcd(source_rate, target_rate)
    up, down = target_rate // gcd, source_rate // gcd
    if up * down > 64:
        raise ValueError(f"unsupported resampling ratio {source_rate}->{target_rate}")
    factor = max(up, down)
    half_taps = 16 * factor
    taps = np.arange(-half_taps, half_taps + 1, dtype=np.float64)
    cutoff = 0.45 / factor  # cycles per (upsampled) sample, a little below the lower Nyquist rate
    kernel = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.kaiser(len(taps), 8.0)
    kernel *= up / kernel.sum()
    stuffed = np.zeros(len(audio) * up, dtype=np.float64)
    stuffed[::up] = audio
    filtered = np.convolve(stuffed, kernel, mode="full")[half_taps : half_taps + len(stuffed)]
    return filtered[::down].astype(np.float32)
