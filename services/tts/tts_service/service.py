"""Request orchestration: readiness, voices, normalisation, cache, scheduling."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from functools import partial
from typing import Literal

from tts_service import metrics
from tts_service.cache import LruCache
from tts_service.config import Settings
from tts_service.engine import Engine, Synthesis, VoiceInfo, empty_synthesis
from tts_service.logging_setup import get_logger
from tts_service.scheduler import JobTimeout, QueueFull, Scheduler
from tts_service.text import normalize_for_speech

log = get_logger(__name__)

State = Literal["loading", "ok", "error"]
CacheKey = tuple[str, float, int, str]


class NotReady(Exception):
    def __init__(self, state: State, detail: str | None) -> None:
        super().__init__(f"service is {state}")
        self.state = state
        self.detail = detail


class UnknownVoice(Exception):
    def __init__(self, voice: str, available: list[str]) -> None:
        super().__init__(f"unknown voice {voice!r}")
        self.voice = voice
        self.available = available


class SynthesisFailed(Exception):
    pass


@dataclass(slots=True)
class SpeakResult:
    synthesis: Synthesis
    cached: bool
    characters: int


class TtsService:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.settings = settings
        self.engine = engine
        self.state: State = "loading"
        self.detail: str | None = None
        self.warm = False
        self.last_latency_ms: float | None = None
        self.started_at = time.monotonic()
        self.last_used = time.monotonic()
        self._voices: dict[str, VoiceInfo] = {}
        self.cache: LruCache[Synthesis] = LruCache(
            max_entries=settings.cache_max_entries,
            max_bytes=settings.cache_max_bytes,
            max_item_bytes=settings.cache_max_item_bytes,
            size_of=lambda synthesis: synthesis.nbytes,
        )
        self.scheduler: Scheduler[Synthesis] = Scheduler(
            workers=settings.workers, max_queue=settings.max_queue, on_change=self._queue_changed
        )

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Load the model and run the warm-up synthesis; the service is ``ok`` only after both."""
        started = time.perf_counter()
        metrics.READY.set(0)
        try:
            await asyncio.to_thread(self.engine.load)
            self._voices = {voice.id: voice for voice in self.engine.voices()}
            latency = await asyncio.to_thread(
                self.engine.warm_up, self.settings.warmup_text, self.settings.default_voice
            )
        except Exception as exc:  # reported through /health; the process stays up for diagnosis
            self.state, self.detail = "error", f"{type(exc).__name__}: {exc}"
            log.exception("tts.startup_failed", detail=self.detail)
            return
        self.last_latency_ms = round(latency * 1000, 1)
        self.warm = True
        self.state = "ok"
        elapsed = time.perf_counter() - started
        metrics.READY.set(1)
        metrics.MODEL_LOAD_SECONDS.set(elapsed)
        log.info(
            "tts.ready",
            startup_s=round(elapsed, 2),
            warmup_ms=self.last_latency_ms,
            viseme_timing=self.engine.timing_source,
            voices=len(self._voices),
        )

    def shutdown(self) -> None:
        self.scheduler.shutdown()

    async def keep_warm(self) -> None:
        """While idle, synthesize a short phrase on every session every ``keepwarm_seconds`` so the model's pages and
        ONNX arenas stay resident; never while real work is running or queued."""
        interval = self.settings.keepwarm_seconds
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            if self.state != "ok" or time.monotonic() - self.last_used < interval:
                continue
            if self.scheduler.running or self.scheduler.queued:
                continue
            try:
                await asyncio.to_thread(self.engine.warm_up, self.settings.keepwarm_text, self.settings.default_voice)
                metrics.KEEPWARM.inc()
            except Exception as exc:  # noqa: BLE001 - a failed warm-up must never take the service down
                log.warning("tts.keepwarm_failed", error=f"{type(exc).__name__}: {exc}"[:200])

    # ------------------------------------------------------------ voices

    def voices(self) -> list[VoiceInfo]:
        return list(self._voices.values())

    def resolve_voice(self, requested: str | None) -> VoiceInfo:
        """Kokoro voice id, or a unique bare name (``alloy`` -> ``af_alloy``, as OpenAI clients send)."""
        if not requested:
            return self._voices[self.settings.default_voice]
        if requested in self._voices:
            return self._voices[requested]
        name = requested.strip().lower()
        if name in self._voices:
            return self._voices[name]
        matches = [voice for voice_id, voice in self._voices.items() if voice_id.split("_", 1)[1] == name]
        if len(matches) == 1:
            return matches[0]
        raise UnknownVoice(requested, sorted(self._voices))

    # ------------------------------------------------------------ synthesis

    async def speak(self, text: str, voice: str | None, speed: float, sample_rate: int) -> SpeakResult:
        self.last_used = time.monotonic()
        if self.state != "ok":
            metrics.ERRORS.labels("not_ready").inc()
            raise NotReady(self.state, self.detail)
        try:
            info = self.resolve_voice(voice)
        except UnknownVoice:
            metrics.ERRORS.labels("unknown_voice").inc()
            raise
        normalized = normalize_for_speech(text, english=info.english)
        if not normalized:
            return SpeakResult(empty_synthesis(info.id, sample_rate), cached=False, characters=0)

        speed = round(float(speed), 3)
        key: CacheKey = (info.id, speed, sample_rate, normalized)
        cached = self.cache.get(key)
        if cached is not None:
            metrics.CACHE_HITS.inc()
            metrics.CHARACTERS.labels("cache").inc(len(normalized))
            return SpeakResult(cached, cached=True, characters=len(normalized))
        metrics.CACHE_MISSES.inc()

        job = partial(self.engine.synthesize, normalized, info.id, speed, sample_rate)
        try:
            synthesis, _shared = await self.scheduler.submit(
                key,
                job,
                max_wait=self.settings.request_timeout_seconds,
                on_result=partial(self._record, key, len(normalized)),
            )
        except QueueFull:
            metrics.ERRORS.labels("queue_full").inc()
            log.warning("tts.queue_full", queued=self.scheduler.queued, running=self.scheduler.running)
            raise
        except JobTimeout as exc:
            metrics.ERRORS.labels("timeout").inc()
            log.warning("tts.timeout", started=exc.started, chars=len(normalized))
            raise
        except Exception as exc:
            metrics.ERRORS.labels("synthesis_failed").inc()
            log.exception("tts.synthesis_failed", voice=info.id, chars=len(normalized))
            raise SynthesisFailed(str(exc)) from exc
        return SpeakResult(synthesis, cached=False, characters=len(normalized))

    def _record(self, key: CacheKey, characters: int, synthesis: Synthesis) -> None:
        """Runs on the event loop once per completed synthesis (also when its callers timed out)."""
        try:
            self.cache.put(key, synthesis)
            metrics.CACHE_ENTRIES.set(len(self.cache))
            metrics.CACHE_BYTES.set(self.cache.nbytes)
            metrics.SYNTHESIS_SECONDS.observe(synthesis.synthesis_seconds)
            metrics.CHARACTERS.labels("synthesized").inc(characters)
            audio_seconds = synthesis.audio_seconds
            rtf = synthesis.synthesis_seconds / audio_seconds if audio_seconds > 0 else None
            if rtf is not None:
                metrics.AUDIO_SECONDS.inc(audio_seconds)
                metrics.REAL_TIME_FACTOR.observe(rtf)
            self.last_latency_ms = round(synthesis.synthesis_seconds * 1000, 1)
            log.info(
                "tts.synthesized",
                voice=synthesis.voice,
                chars=characters,
                audio_ms=synthesis.duration_ms,
                synth_ms=self.last_latency_ms,
                rtf=round(rtf, 3) if rtf is not None else None,
                visemes=len(synthesis.visemes),
                viseme_timing=synthesis.timing,
            )
        except Exception:  # bookkeeping must never fail a synthesis
            log.exception("tts.record_failed")

    def _queue_changed(self, queued: int, running: int) -> None:
        metrics.QUEUE_DEPTH.set(queued)
        metrics.INFLIGHT.set(running)
