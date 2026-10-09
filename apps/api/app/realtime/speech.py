"""Interviewer speech out: sentence chunks -> TTS pipeline -> client
(REALTIME_DESIGN.md section 5).

One :class:`UtteranceSpeech` per interviewer utterance:

* text arrives as deltas (``feed``) or at once (``set_text``) and is cut by
  :class:`~app.realtime.chunker.SentenceChunker`;
* the FIRST chunk of an utterance synthesizes alone (measured: a concurrent
  second synthesis slows both ~1.6x on the shared CPU, and the second chunk is
  only needed once the first has played); after it, up to ``max_inflight``
  chunks synthesize ahead of the sender; results are sent strictly in order as
  ``tts.chunk`` immediately followed by the binary 0x02 frame with the same
  ``chunk_id`` (the sink does both under one lock);
* a TTS failure is retried once; then the rest of the utterance falls back
  to ``tts.fallback`` (browser speech or text) and :class:`TtsHealth` enters
  degraded mode, so later utterances skip straight to the fallback until a
  periodic health probe sees the provider recover;
* ``interview_time_to_first_audio_seconds`` (utterance begin -> first chunk
  sent) and ``interview_turn_latency_seconds`` (end of turn -> first chunk)
  are observed when the first chunk of an utterance goes out.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.ai.base import AIError, TtsProvider, TtsResult
from app.core import metrics
from app.core.logging import get_logger
from app.realtime.chunker import SentenceChunker
from app.realtime.turns import ChunkRecord
from app.schemas.realtime import TtsChunkMsg, TtsFallbackMsg, Viseme

log = get_logger(__name__)

FALLBACK_MS_PER_CHAR = 65
FALLBACK_MIN_MS = 500
SYNTH_TIMEOUT_SECONDS = 15.0


def estimate_speech_ms(text: str) -> int:
    """Rough spoken duration for text without audio (browser speech)."""
    return max(FALLBACK_MIN_MS, len(text.strip()) * FALLBACK_MS_PER_CHAR)


class SpeechSink(Protocol):
    """Implemented by the runtime: sequencing + transport."""

    async def send_tts_chunk(self, message: TtsChunkMsg, pcm16: bytes, *, final: bool) -> None: ...

    async def send_tts_fallback(self, message: TtsFallbackMsg) -> None: ...


# --------------------------------------------------------------------- health


class TtsHealth:
    """Degraded-mode flag with a background recovery probe."""

    def __init__(
        self,
        provider: Callable[[], TtsProvider],
        *,
        probe_interval: float = 10.0,
        on_change: Callable[[bool, str | None], Awaitable[None] | None] | None = None,
    ) -> None:
        self._provider = provider
        self.probe_interval = probe_interval
        self.degraded = False
        self.reason: str | None = None
        self.since: float | None = None
        self.failures = 0
        self._probe: asyncio.Task[None] | None = None
        self._on_change = on_change

    async def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            result = self._on_change(self.degraded, self.reason)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            log.warning("realtime.tts.health_callback_failed", error=str(exc))

    async def mark_failure(self, reason: str) -> None:
        self.failures += 1
        if not self.degraded:
            self.degraded = True
            self.reason = reason
            self.since = time.monotonic()
            log.warning("realtime.tts.degraded", reason=reason)
            await self._notify()
        self._ensure_probe()

    async def mark_success(self) -> None:
        self.failures = 0
        if self.degraded:
            await self._recover()

    async def _recover(self) -> None:
        self.degraded = False
        log.info(
            "realtime.tts.recovered",
            degraded_for_s=round(time.monotonic() - (self.since or time.monotonic()), 1),
        )
        self.reason = None
        self.since = None
        await self._notify()

    def _ensure_probe(self) -> None:
        if self._probe is None or self._probe.done():
            self._probe = asyncio.create_task(self._probe_loop(), name="realtime.tts.probe")

    async def _probe_loop(self) -> None:
        while self.degraded:
            await asyncio.sleep(self.probe_interval)
            provider = self._provider()
            if getattr(provider, "name", "") == "browser":
                continue  # server TTS disabled by configuration: nothing to recover
            try:
                ok = await asyncio.wait_for(provider.health(), timeout=3.0)
            except (TimeoutError, AIError, OSError) as exc:
                log.debug("realtime.tts.probe_failed", error=type(exc).__name__)
                ok = False
            except Exception as exc:  # noqa: BLE001
                log.warning("realtime.tts.probe_error", error=str(exc))
                ok = False
            if ok and self.degraded:
                await self._recover()

    async def aclose(self) -> None:
        if self._probe is not None:
            self._probe.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._probe
            self._probe = None


# ------------------------------------------------------------------ utterance


@dataclass(slots=True)
class _ChunkJob:
    index: int
    text: str
    task: asyncio.Task[TtsResult | BaseException] | None = None


@dataclass(slots=True)
class SpeechTimings:
    begun_at: float
    turn_end_at: float | None = None
    first_chunk_at: float | None = None
    on_first_audio: Callable[[float], None] | None = None  # called with the send time of the first chunk
    first_text_at: float | None = None  # first text fed
    first_job_at: float | None = None  # first chunk cut and handed to TTS
    first_job_chars: int | None = None
    first_synth_ms: float | None = None  # TTS time of the first chunk


@dataclass
class UtteranceSpeech:
    """Pipeline for one utterance. Create through :meth:`SpeechStreamer.start`."""

    utterance_id: str
    streamer: SpeechStreamer
    timings: SpeechTimings
    fallback_reason: str | None = None
    chunks: list[ChunkRecord] = field(default_factory=list)
    text: str = ""  # everything fed so far
    total_ms: int = 0
    finished: bool = False
    cancelled: bool = False
    _chunker: SentenceChunker = field(default_factory=SentenceChunker)
    _jobs: list[_ChunkJob] = field(default_factory=list)
    _new_job: asyncio.Event = field(default_factory=asyncio.Event)
    _sender: asyncio.Task[None] | None = None
    _sem: asyncio.Semaphore | None = None
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    _sent_index: int = -1
    _first_sent: asyncio.Event = field(default_factory=asyncio.Event)
    _first_synthesized: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(max(1, self.streamer.max_inflight))
        self._sender = asyncio.create_task(self._send_loop(), name=f"realtime.speech.{self.utterance_id}")

    # ---------------------------------------------------------------- input
    @property
    def fallback(self) -> bool:
        return self.fallback_reason is not None

    @property
    def sent_chunks(self) -> int:
        return self._sent_index + 1

    def feed(self, delta: str) -> None:
        if self.finished or self.cancelled or not delta:
            return
        if self.timings.first_text_at is None:
            self.timings.first_text_at = time.monotonic()
        self.text += delta
        for chunk in self._chunker.feed(delta):
            self._add_job(chunk)

    def set_text(self, text: str) -> None:
        self.feed(text)

    def _add_job(self, text: str) -> None:
        job = _ChunkJob(index=len(self._jobs), text=text)
        if job.index == 0:
            self.timings.first_job_at = time.monotonic()
            self.timings.first_job_chars = len(text)
        if not self.fallback:
            job.task = asyncio.create_task(
                self._synthesize(job), name=f"realtime.tts.{self.utterance_id}.{job.index}"
            )
        self._jobs.append(job)
        self._new_job.set()

    async def finish(self) -> None:
        """Flush the chunker and wait until every chunk has been sent."""
        if self.cancelled:
            return
        if not self.finished:
            self.finished = True
            for chunk in self._chunker.flush():
                self._add_job(chunk)
            self._new_job.set()
        await self._done.wait()

    async def wait_first_chunk(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109
        try:
            await asyncio.wait_for(self._first_sent.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def cancel(self) -> None:
        """Stop synthesis and sending (barge-in, disconnect)."""
        if self.cancelled:
            return
        self.cancelled = True
        tasks = [j.task for j in self._jobs if j.task is not None and not j.task.done()]
        if self._sender is not None and not self._sender.done():
            tasks.append(self._sender)  # type: ignore[arg-type]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._done.set()

    # ------------------------------------------------------------- pipeline
    async def _synthesize(self, job: _ChunkJob) -> TtsResult | BaseException:
        assert self._sem is not None
        if job.index > 0:
            await self._first_synthesized.wait()  # the first chunk gets the TTS CPU to itself
        await self._sem.acquire()
        provider = self.streamer.provider()
        last: BaseException | None = None
        started = time.monotonic()
        try:
            for attempt in range(2):  # one retry
                try:
                    return await asyncio.wait_for(
                        provider.synthesize(job.text, voice=self.streamer.voice, speed=self.streamer.speed),
                        timeout=SYNTH_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - TtsUnavailable, timeouts, unexpected
                    last = exc
                    if getattr(provider, "name", "") == "browser":
                        break
                    log.warning(
                        "realtime.tts.chunk_failed",
                        utterance_id=self.utterance_id,
                        index=job.index,
                        attempt=attempt + 1,
                        error=f"{type(exc).__name__}: {exc}"[:200],
                    )
            return last if last is not None else RuntimeError("tts failed")
        finally:
            if job.index == 0:
                self.timings.first_synth_ms = (time.monotonic() - started) * 1000
                self._first_synthesized.set()

    async def _send_loop(self) -> None:
        try:
            index = 0
            while True:
                while index >= len(self._jobs):
                    if self.finished:
                        return
                    self._new_job.clear()
                    if index < len(self._jobs) or self.finished:
                        continue
                    await self._new_job.wait()
                job = self._jobs[index]
                if self.fallback or job.task is None:
                    await self._send_fallback(job)
                else:
                    outcome = await job.task
                    if isinstance(outcome, TtsResult):
                        # The flag is set only when known at send time; ai.utterance.end is authoritative.
                        is_last = self.finished and index == len(self._jobs) - 1
                        await self._send_audio(job, outcome, final=is_last)
                        self._release()
                        if self.streamer.health.degraded:
                            await self.streamer.health.mark_success()
                    else:
                        self._release()
                        await self._enter_fallback(f"{type(outcome).__name__}")
                        await self._send_fallback(job)
                self._sent_index = index
                index += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the transport went away mid-send
            metrics.ERRORS.labels(component="realtime_speech").inc()
            log.warning(
                "realtime.speech.send_failed",
                utterance_id=self.utterance_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._done.set()

    def _release(self) -> None:
        if self._sem is not None:
            self._sem.release()

    async def _enter_fallback(self, reason: str) -> None:
        if self.fallback:
            return
        self.fallback_reason = reason
        self._first_synthesized.set()
        for job in self._jobs:
            if job.task is not None and not job.task.done():
                job.task.cancel()
            job.task = None
        await self.streamer.health.mark_failure(reason)

    def _mark_first(self) -> None:
        if self.timings.first_chunk_at is not None:
            return
        now = time.monotonic()
        self.timings.first_chunk_at = now
        metrics.TIME_TO_FIRST_AUDIO.observe(max(0.0, now - self.timings.begun_at))
        if self.timings.turn_end_at is not None:
            metrics.TURN_LATENCY.observe(max(0.0, now - self.timings.turn_end_at))
        self._first_sent.set()
        if self.timings.on_first_audio is not None:
            try:
                self.timings.on_first_audio(now)
            except Exception as exc:  # noqa: BLE001 - bookkeeping must never break speech
                log.debug("realtime.speech.first_audio_hook_failed", error=type(exc).__name__)

    async def _send_audio(self, job: _ChunkJob, result: TtsResult, *, final: bool) -> None:
        chunk_id = self.streamer.next_chunk_id()
        duration = int(result.duration_ms)
        message = TtsChunkMsg(
            utterance_id=self.utterance_id,
            chunk_id=chunk_id,
            index=job.index,
            text=job.text,
            sample_rate=int(result.sample_rate),
            duration_ms=duration,
            visemes=[Viseme(t=int(v.t_ms), v=v.viseme, w=float(v.weight)) for v in result.visemes],
            word_timings=list(result.word_timings or []),
        )
        self.chunks.append(ChunkRecord(chunk_id, job.index, job.text, duration, self.total_ms))
        self.total_ms += duration
        await self.streamer.sink.send_tts_chunk(message, result.pcm16, final=final)
        self._mark_first()

    async def _send_fallback(self, job: _ChunkJob) -> None:
        duration = estimate_speech_ms(job.text)
        self.chunks.append(ChunkRecord(-1, job.index, job.text, duration, self.total_ms))
        self.total_ms += duration
        await self.streamer.sink.send_tts_fallback(
            TtsFallbackMsg(
                utterance_id=self.utterance_id,
                text=job.text,
                reason=self.fallback_reason or "tts_unavailable",
            )
        )
        self._mark_first()


class SpeechStreamer:
    """Per-session factory for utterance pipelines (owns chunk ids + health)."""

    def __init__(
        self,
        sink: SpeechSink,
        provider: Callable[[], TtsProvider],
        *,
        voice: str | None,
        max_inflight: int = 2,
        speed: float = 1.0,
        health: TtsHealth | None = None,
        client_tts_mode: str = "server",
    ) -> None:
        self.sink = sink
        self.provider = provider
        self.voice = voice
        self.max_inflight = max(1, max_inflight)
        self.speed = speed
        self.health = health or TtsHealth(provider)
        self.client_tts_mode = client_tts_mode
        self._chunk_id = 0

    def next_chunk_id(self) -> int:
        self._chunk_id = (self._chunk_id + 1) % 0xFFFFFFFF or 1
        return self._chunk_id

    def initial_fallback_reason(self) -> str | None:
        if self.client_tts_mode in ("browser", "text"):
            return f"client_{self.client_tts_mode}"
        if getattr(self.provider(), "name", "") == "browser":
            return "server_tts_disabled"
        if self.health.degraded:
            return "tts_degraded"
        return None

    def start(
        self,
        utterance_id: str,
        *,
        begun_at: float | None = None,
        turn_end_at: float | None = None,
        on_first_audio: Callable[[float], None] | None = None,
    ) -> UtteranceSpeech:
        return UtteranceSpeech(
            utterance_id=utterance_id,
            streamer=self,
            timings=SpeechTimings(
                begun_at=begun_at or time.monotonic(), turn_end_at=turn_end_at, on_first_audio=on_first_audio
            ),
            fallback_reason=self.initial_fallback_reason(),
        )
