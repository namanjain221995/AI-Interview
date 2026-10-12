"""Text-to-speech providers (``TtsProvider``), selected by ``TTS_PROVIDER``.

* ``kokoro``: our TTS microservice. Wire contract::

      POST {TTS_BASE_URL}/v1/tts
        {"text": str, "voice": str|null, "speed": float, "sample_rate": 24000}
      -> {"audio_b64": base64 PCM16 LE mono, "sample_rate": int, "duration_ms": int,
          "visemes": [{"t": ms, "v": OculusId, "w": weight}], "phonemes": str,
          "voice": str, "cached": bool}
      GET {TTS_BASE_URL}/health -> {"status": "ok", ...}

* ``openai_compatible``: any ``POST /v1/audio/speech`` server (Qwen3-TTS,
  CosyVoice, ...) returning ``wav`` or raw ``pcm``; visemes are derived
  locally from the text.
* ``browser``: no server synthesis; ``synthesize()`` raises
  :class:`~app.ai.base.TtsUnavailable` so the realtime runtime sends
  ``tts.fallback`` and the browser speaks.
* ``mock``: deterministic tone/silence, ~60 ms per character, text-derived
  visemes (tests and GPU-less development).

Every provider failure surfaces as ``TtsUnavailable`` (an ``AIUnavailable``):
the caller's only decision is "fall back to browser speech for this
utterance".
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import time
import wave
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from app.ai.base import AIError, TtsResult, TtsUnavailable, VisemeCue
from app.ai.http import EndpointClient, call_status, failure_reason
from app.ai.ledger import LedgerSink, ModelCallRecord, NullLedger
from app.ai.visemes import OCULUS_VISEMES, phonemes_to_visemes, text_to_visemes
from app.core import metrics
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus, ModelTask

if TYPE_CHECKING:
    from app.ai.base import TtsProvider
    from app.core.config import Settings

log = get_logger(__name__)

MIN_SPEED = 0.5
MAX_SPEED = 2.0
MAX_TEXT_CHARS = 2000
# The Kokoro service contract: speed 0.7-1.3, at most 1000 characters per request.
KOKORO_MIN_SPEED = 0.7
KOKORO_MAX_SPEED = 1.3
KOKORO_MAX_TEXT_CHARS = 1000
_VALID_VISEMES = frozenset(OCULUS_VISEMES)


def _clamp_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return 1.0
    return min(MAX_SPEED, max(MIN_SPEED, value)) if value == value else 1.0


def split_for_tts(text: str, limit: int) -> list[str]:
    """Split text into pieces of at most ``limit`` characters, preferring
    sentence boundaries, then clause boundaries, then spaces."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for marks in ((". ", "? ", "! "), ("; ", ": ", ", "), (" ",)):
            cut = max(window.rfind(m) for m in marks)
            if cut >= limit // 3:
                cut += 1
                break
        if cut < limit // 3:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


def _concat_results(results: list[TtsResult], provider: str, sample_rate: int) -> TtsResult:
    pcm = bytearray()
    visemes: list[VisemeCue] = []
    offset = 0
    latency = 0.0
    for result in results:
        pcm.extend(result.pcm16)
        visemes.extend(VisemeCue(t_ms=v.t_ms + offset, viseme=v.viseme, weight=v.weight) for v in result.visemes)
        offset += result.duration_ms
        latency += result.latency_ms
    return TtsResult(
        pcm16=bytes(pcm),
        sample_rate=results[0].sample_rate if results else sample_rate,
        duration_ms=offset,
        visemes=visemes,
        provider=provider,
        latency_ms=latency,
    )


def _empty(provider: str, sample_rate: int) -> TtsResult:
    return TtsResult(pcm16=b"", sample_rate=sample_rate, duration_ms=0, visemes=[], provider=provider)


def pcm_duration_ms(pcm16: bytes, sample_rate: int) -> int:
    return round(len(pcm16) / 2 * 1000 / sample_rate) if sample_rate > 0 else 0


def decode_wav(data: bytes) -> tuple[bytes, int]:
    """WAV -> (mono PCM16 LE, sample_rate). Supports 8/16/24/32-bit integer
    PCM with any channel count (downmixed)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise TtsUnavailable(f"tts: invalid WAV audio ({exc})", endpoint="tts") from exc
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) * 256.0
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        ints = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        samples = ints.astype(np.float32) / 256.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 65536.0
    else:
        raise TtsUnavailable(f"tts: unsupported WAV sample width {width}", endpoint="tts")
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes(), rate


class _LedgerMixin:
    name: str
    model: str
    _ledger: LedgerSink

    def _record(
        self, status: ModelCallStatus, started: float, attempts: int, error: BaseException | None
    ) -> None:
        try:
            self._ledger.record(
                ModelCallRecord(
                    task=ModelTask.TTS.value,
                    endpoint="tts",
                    model=self.model,
                    status=status.value,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=None if error is None else f"{type(error).__name__}: {error}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai.ledger.record_failed", error=str(exc))

    def _fail(self, exc: AIError, started: float) -> TtsUnavailable:
        metrics.MODEL_CALL_FAILURES.labels(
            endpoint="tts", task=ModelTask.TTS.value, reason=failure_reason(exc)
        ).inc()
        self._record(call_status(exc), started, 1, exc)
        if isinstance(exc, TtsUnavailable):
            return exc
        return TtsUnavailable(f"{self.name}: {exc}", endpoint="tts")


class KokoroTtsProvider(_LedgerMixin):
    name = "kokoro"

    def __init__(
        self,
        http: EndpointClient,
        *,
        voice: str | None = None,
        sample_rate: int = 24000,
        model: str = "kokoro-v1.0",
        ledger: LedgerSink | None = None,
    ) -> None:
        self._http = http
        self.voice = voice
        self.sample_rate = sample_rate
        self.model = model
        self._ledger = ledger or NullLedger()

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> TtsResult:
        pieces = split_for_tts(text, KOKORO_MAX_TEXT_CHARS)
        if not pieces:
            return _empty(self.name, self.sample_rate)
        if len(pieces) == 1:
            return await self._synthesize_one(pieces[0], voice=voice, speed=speed)
        results = [await self._synthesize_one(piece, voice=voice, speed=speed) for piece in pieces]
        return _concat_results(results, self.name, self.sample_rate)

    async def _synthesize_one(self, text: str, *, voice: str | None, speed: float) -> TtsResult:
        try:
            value = float(speed)
        except (TypeError, ValueError):
            value = 1.0
        clamped = min(KOKORO_MAX_SPEED, max(KOKORO_MIN_SPEED, value)) if value == value else 1.0
        body = {
            "text": text,
            "voice": voice or self.voice,
            "speed": clamped,
            "sample_rate": self.sample_rate,
        }
        started = time.perf_counter()
        try:
            result = await self._http.request("POST", "/v1/tts", json=body, task=ModelTask.TTS.value)
            payload = result.json()
            tts = self._parse(payload, text)
        except AIError as exc:
            raise self._fail(exc, started) from exc
        tts.latency_ms = (time.perf_counter() - started) * 1000
        metrics.TTS_LATENCY.labels(provider=self.name).observe(tts.latency_ms / 1000)
        self._record(ModelCallStatus.OK, started, result.attempts, None)
        return tts

    def _parse(self, payload: Any, text: str) -> TtsResult:
        if not isinstance(payload, dict) or not isinstance(payload.get("audio_b64"), str):
            raise TtsUnavailable("kokoro: response has no audio_b64", endpoint="tts")
        try:
            pcm = base64.b64decode(payload["audio_b64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TtsUnavailable("kokoro: audio_b64 is not valid base64", endpoint="tts") from exc
        if len(pcm) % 2:
            pcm = pcm[:-1]
        sample_rate = payload.get("sample_rate")
        if not isinstance(sample_rate, int) or sample_rate <= 0:
            sample_rate = self.sample_rate
        duration = payload.get("duration_ms")
        duration_ms = (
            duration if isinstance(duration, int) and duration >= 0 else pcm_duration_ms(pcm, sample_rate)
        )
        visemes: list[VisemeCue] = []
        for item in payload.get("visemes") or []:
            if not isinstance(item, dict) or item.get("v") not in _VALID_VISEMES:
                continue
            try:
                t_ms = int(item.get("t", 0))
                weight = float(item.get("w", 1.0))
            except (TypeError, ValueError):
                continue
            if visemes and t_ms <= visemes[-1].t_ms:
                continue
            visemes.append(
                VisemeCue(t_ms=max(0, t_ms), viseme=str(item["v"]), weight=min(1.0, max(0.0, weight)))
            )
        if not visemes and duration_ms > 0:
            phonemes = payload.get("phonemes")
            visemes = (
                phonemes_to_visemes(phonemes, duration_ms)
                if isinstance(phonemes, str) and phonemes.strip()
                else text_to_visemes(text, duration_ms)
            )
        word_timings = payload.get("word_timings")
        return TtsResult(
            pcm16=pcm,
            sample_rate=sample_rate,
            duration_ms=duration_ms,
            visemes=visemes,
            word_timings=word_timings if isinstance(word_timings, list) else [],
            provider=self.name,
        )

    async def health(self) -> bool:
        try:
            result = await self._http.request(
                "GET",
                "/health",
                timeout=2.0,
                max_retries=0,
                use_breaker=False,
                acquire_slot=False,
                task="health",
            )
            body = result.json()
        except AIError:
            return False
        return isinstance(body, dict) and body.get("status") == "ok"


class OpenAICompatibleTtsProvider(_LedgerMixin):
    name = "openai_compatible"

    def __init__(
        self,
        http: EndpointClient,
        *,
        model: str,
        voice: str | None = None,
        sample_rate: int = 24000,
        response_format: Literal["wav", "pcm"] = "wav",
        ledger: LedgerSink | None = None,
    ) -> None:
        self._http = http
        self.model = model
        self.voice = voice
        self.sample_rate = sample_rate
        self.response_format = response_format
        self._ledger = ledger or NullLedger()

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> TtsResult:
        text = " ".join(text.split())[:MAX_TEXT_CHARS]
        if not text:
            return _empty(self.name, self.sample_rate)
        body: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "voice": voice or self.voice,
            "response_format": self.response_format,
            "speed": _clamp_speed(speed),
        }
        started = time.perf_counter()
        try:
            result = await self._http.request(
                "POST", "/v1/audio/speech", json=body, headers={"Accept": "audio/*"}, task=ModelTask.TTS.value
            )
            audio = result.response.content
            if audio[:4] == b"RIFF":
                pcm, rate = decode_wav(audio)
            elif self.response_format == "wav":
                raise TtsUnavailable("openai_compatible: expected WAV audio", endpoint="tts")
            else:
                pcm, rate = audio[: len(audio) // 2 * 2], self.sample_rate
        except AIError as exc:
            raise self._fail(exc, started) from exc
        duration_ms = pcm_duration_ms(pcm, rate)
        latency_ms = (time.perf_counter() - started) * 1000
        metrics.TTS_LATENCY.labels(provider=self.name).observe(latency_ms / 1000)
        self._record(ModelCallStatus.OK, started, result.attempts, None)
        return TtsResult(
            pcm16=pcm,
            sample_rate=rate,
            duration_ms=duration_ms,
            visemes=text_to_visemes(text, duration_ms) if duration_ms > 0 else [],
            provider=self.name,
            latency_ms=latency_ms,
        )

    async def health(self) -> bool:
        for path in ("/health", "/v1/models"):
            try:
                await self._http.request(
                    "GET",
                    path,
                    timeout=2.0,
                    max_retries=0,
                    use_breaker=False,
                    acquire_slot=False,
                    task="health",
                )
            except AIError:
                continue
            return True
        return False


class BrowserTtsProvider:
    """Server-side synthesis disabled: the browser speaks (``tts.fallback``)."""

    name = "browser"

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate = sample_rate

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> TtsResult:
        raise TtsUnavailable("server-side TTS is disabled (TTS_PROVIDER=browser)", endpoint="tts")

    async def health(self) -> bool:
        return True


class MockTtsProvider:
    """Deterministic audio: a soft tone while the mouth is open, silence on
    pauses; ~60 ms per character; text-derived visemes."""

    name = "mock"

    def __init__(
        self,
        sample_rate: int = 24000,
        *,
        ms_per_char: float = 60.0,
        tone_hz: float = 220.0,
        amplitude: float = 0.08,
    ) -> None:
        self.sample_rate = sample_rate
        self.ms_per_char = ms_per_char
        self.tone_hz = tone_hz
        self.amplitude = amplitude
        self.calls: list[str] = []
        self.fail_with: BaseException | None = None
        self.healthy = True
        self.delay_s = 0.0  # tests: a slow synthesis

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> TtsResult:
        self.calls.append(text)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail_with is not None:
            raise self.fail_with
        text = " ".join(text.split())
        if not text:
            return _empty(self.name, self.sample_rate)
        duration_ms = max(120, round(len(text) * self.ms_per_char / _clamp_speed(speed)))
        visemes = text_to_visemes(text, duration_ms)
        total = self.sample_rate * duration_ms // 1000
        t = np.arange(total, dtype=np.float32) / self.sample_rate
        wave_ = self.amplitude * np.sin(2 * np.pi * self.tone_hz * t)
        gate = np.ones(total, dtype=np.float32)
        bounds = [*(cue.t_ms for cue in visemes), duration_ms]
        for cue, end_ms in zip(visemes, bounds[1:], strict=False):
            if cue.viseme == "sil":
                gate[cue.t_ms * self.sample_rate // 1000 : end_ms * self.sample_rate // 1000] = 0.0
        pcm = (wave_ * gate * 32767).astype("<i2").tobytes()
        return TtsResult(
            pcm16=pcm,
            sample_rate=self.sample_rate,
            duration_ms=duration_ms,
            visemes=visemes,
            provider=self.name,
            latency_ms=0.0,
        )

    async def health(self) -> bool:
        return self.healthy


def create_tts_provider(
    settings: Settings, *, http: EndpointClient | None = None, ledger: LedgerSink | None = None
) -> TtsProvider:
    provider = settings.tts_provider
    if provider == "mock":
        return MockTtsProvider(settings.tts_sample_rate)
    if provider == "browser":
        return BrowserTtsProvider(settings.tts_sample_rate)
    if http is None:
        raise ValueError(f"TTS provider {provider!r} needs an endpoint client")
    if provider == "openai_compatible":
        return OpenAICompatibleTtsProvider(
            http,
            model=settings.tts_model,
            voice=settings.tts_voice,
            sample_rate=settings.tts_sample_rate,
            ledger=ledger,
        )
    return KokoroTtsProvider(
        http,
        voice=settings.tts_voice,
        sample_rate=settings.tts_sample_rate,
        model=settings.tts_model,
        ledger=ledger,
    )
