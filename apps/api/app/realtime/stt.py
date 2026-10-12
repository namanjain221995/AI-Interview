"""STT scheduling for one candidate turn (REALTIME_DESIGN.md section 3).

* Every closed VAD segment is transcribed once ("final" for that segment) as
  soon as it closes, concurrently with the speech that follows; the answer
  transcript is the ordered join of segment finals, so end of turn only waits
  for the last (short) segment.
* A failed segment is retried once immediately; a persistent failure keeps
  the audio (``SegmentJob.wav``) so the runtime can retry later or ask for a
  typed answer - a turn is never silently dropped.
* Partials (interim transcripts of the open segment) are low priority: at
  most one in flight, only with enough new audio, skipped while finals are
  pending or while the STT client has no spare replica (a partial must never
  delay a final), and the interval adapts to ``PartialSkipped`` and to
  measured latency.
* Segments the VAD heard as clear speech (``STRONG_SPEECH_MS`` of voiced audio
  at a mean probability >= ``STRONG_SPEECH_PROB``) skip Whisper's server-side
  no-speech check (~0.3 s per request); their STT confidence then comes from
  the VAD probability instead of Whisper's ``no_speech_prob``.
* The Whisper prompt carries the current question focus, vocabulary from the
  candidate profile and the job, and the last ~200 characters of the answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from app.ai.base import AIError, CallContext, PartialSkipped, SttClient, TranscriptionResult
from app.core.logging import get_logger
from app.realtime.audio.pcm import pcm16_to_wav

log = get_logger(__name__)

PROMPT_ANSWER_TAIL_CHARS = 200
PROMPT_MAX_CHARS = 760
MAX_VOCABULARY_TERMS = 40
RETRY_DELAY_SECONDS = 0.25
STRONG_SPEECH_MS = 1200
STRONG_SPEECH_PROB = 0.8

_ACCEPTS_NO_SPEECH_CHECK: dict[type, bool] = {}


_ACCEPTS_OPTION: dict[tuple[type, str], bool] = {}


def accepts_stt_option(client: Any, option: str) -> bool:
    """Whether ``client.transcribe`` declares the optional keyword ``option``
    (``no_speech_check``, ``background``: the Whisper client does; the
    ``SttClient`` protocol and the mocks do not)."""
    key = (type(client), option)
    known = _ACCEPTS_OPTION.get(key)
    if known is None:
        try:
            known = option in inspect.signature(client.transcribe).parameters
        except (TypeError, ValueError, AttributeError):
            known = False
        _ACCEPTS_OPTION[key] = known
    return known


def accepts_no_speech_check(client: Any) -> bool:
    return _ACCEPTS_NO_SPEECH_CHECK.get(type(client), True) and accepts_stt_option(client, "no_speech_check")


def stt_has_spare_capacity(client: Any) -> bool:
    """Ask the STT client whether a partial could run now (True when it cannot tell)."""
    check = getattr(client, "has_spare_capacity", None)
    if check is None:
        return True
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 - a probe must never break audio handling
        return True


def build_whisper_prompt(*, question: str | None, vocabulary: Sequence[str], answer_so_far: str) -> str:
    """Prompt = vocabulary + question focus + answer tail (most relevant last:
    the STT client keeps the tail when it has to truncate)."""
    parts: list[str] = []
    terms = [t.strip() for t in vocabulary if t and t.strip()][:MAX_VOCABULARY_TERMS]
    if terms:
        parts.append("Technical terms: " + ", ".join(dict.fromkeys(terms)) + ".")
    if question:
        q = " ".join(question.split())
        parts.append(q if q.endswith(("?", ".", "!")) else q + "?")
    tail = " ".join(answer_so_far.split())[-PROMPT_ANSWER_TAIL_CHARS:]
    if tail:
        space = tail.find(" ")
        if len(answer_so_far) > PROMPT_ANSWER_TAIL_CHARS and 0 <= space < 30:
            tail = tail[space + 1 :]
        parts.append(tail)
    prompt = " ".join(parts)
    return prompt[-PROMPT_MAX_CHARS:]


_PERIOD_TOKEN = re.compile(r"([A-Za-z]{2,})\.$")


def join_texts(texts: Sequence[str]) -> str:
    """Join segment transcripts. Whisper ends every segment with a period, so a
    period followed by a segment starting in lower case was not a sentence
    end ("the response. rotated the certificate. and" -> "the response,
    rotated the certificate, and"); that also keeps end-of-turn detection from
    reading a mid-sentence pause as a finished sentence."""
    out = ""
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        if out and text[0].islower() and _PERIOD_TOKEN.search(out) and not out.endswith(".."):
            out = out[:-1] + ","
        out = f"{out} {text}" if out else text
    return out


@dataclass(slots=True)
class SegmentJob:
    index: int
    start_ms: int
    end_ms: int
    wav: bytes
    status: Literal["pending", "done", "failed"] = "pending"
    text: str = ""
    attempts: int = 0
    latency_ms: float = 0.0
    no_speech_prob: float | None = None
    speech_ms: int | None = None  # voiced audio according to the VAD
    vad_prob: float | None = None  # mean VAD speech probability of the voiced windows
    submitted_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    endpoint: str | None = None
    task: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def clear_speech(self) -> bool:
        """The VAD heard this segment as clear speech (no silence check needed)."""
        return (
            self.speech_ms is not None
            and self.vad_prob is not None
            and self.speech_ms >= STRONG_SPEECH_MS
            and self.vad_prob >= STRONG_SPEECH_PROB
        )


#: No interim transcript once the candidate has been silent this long (see ``PartialScheduler.due``).
PARTIAL_PAUSE_MS = 300

class PartialScheduler:
    """Throttle for interim transcripts (adaptive interval)."""

    def __init__(
        self, *, base_interval_ms: int, min_audio_ms: int, enabled: bool = True, max_factor: float = 4.0
    ) -> None:
        self.base_interval_ms = max(200, base_interval_ms)
        self.min_audio_ms = max(0, min_audio_ms)
        self.enabled = enabled
        self.max_interval_ms = self.base_interval_ms * max_factor
        self.interval_ms = float(self.base_interval_ms)
        self.last_at = 0.0
        self.last_audio_ms = 0
        self.inflight = False
        self.skipped = 0
        self.completed = 0

    def reset_segment(self) -> None:
        self.last_audio_ms = 0

    def due(self, *, now: float, open_audio_ms: int, finals_pending: int, silence_ms: int = 0) -> bool:
        if not self.enabled or self.inflight or finals_pending > 0:
            return False
        if silence_ms >= PARTIAL_PAUSE_MS:
            # The candidate paused: there are no new words to show, and if they finished, the segment's final is
            # about to need the (single) Whisper slot - a preview started now would hold it for ~1 s.
            return False
        if open_audio_ms - self.last_audio_ms < self.min_audio_ms:
            return False
        return (now - self.last_at) * 1000 >= self.interval_ms

    def started(self, *, now: float, open_audio_ms: int) -> None:
        self.inflight = True
        self.last_at = now
        self.last_audio_ms = open_audio_ms

    def finished(self, *, latency_ms: float | None, skipped: bool) -> None:
        self.inflight = False
        if skipped:
            self.skipped += 1
            self.interval_ms = min(self.max_interval_ms, self.interval_ms * 1.5)
            return
        self.completed += 1
        if latency_ms is not None and latency_ms > 0.7 * self.interval_ms:
            self.interval_ms = min(self.max_interval_ms, self.interval_ms * 1.25)
        else:
            self.interval_ms = max(float(self.base_interval_ms), self.interval_ms * 0.9)


class TurnTranscriber:
    """Ordered segment finals + partials for one candidate turn."""

    def __init__(
        self,
        stt: Callable[[], SttClient],
        *,
        context: CallContext,
        prompt: Callable[[str], str],
        language: str | None = None,
        on_final: Callable[[SegmentJob], None] | None = None,
        on_failure: Callable[[SegmentJob, BaseException], None] | None = None,
        max_attempts: int = 2,
    ) -> None:
        self._stt = stt
        self.context = context
        self._prompt = prompt
        self.language = language
        self._on_final = on_final
        self._on_failure = on_failure
        self.max_attempts = max(1, max_attempts)
        self.segments: list[SegmentJob] = []
        self.partial_text = ""
        self.closed = False

    # -------------------------------------------------------------- finals
    @property
    def pending(self) -> int:
        return sum(1 for s in self.segments if s.status == "pending")

    @property
    def failed(self) -> list[SegmentJob]:
        return [s for s in self.segments if s.status == "failed"]

    def text(self) -> str:
        """Ordered join of the finals received so far."""
        return join_texts([s.text for s in self.segments if s.status == "done"])

    def text_with_partial(self) -> str:
        return join_texts([self.text(), self.partial_text])

    def speech_ms(self) -> int:
        return sum(s.duration_ms for s in self.segments)

    def submit(
        self,
        samples: np.ndarray,
        *,
        start_ms: int,
        end_ms: int,
        speech_ms: int | None = None,
        vad_prob: float | None = None,
    ) -> SegmentJob:
        job = SegmentJob(
            index=len(self.segments),
            start_ms=start_ms,
            end_ms=end_ms,
            wav=pcm16_to_wav(samples),
            speech_ms=speech_ms,
            vad_prob=vad_prob,
        )
        self.segments.append(job)
        self.partial_text = ""
        job.task = asyncio.create_task(self._run(job), name=f"realtime.stt.final.{job.index}")
        return job

    async def _run(self, job: SegmentJob) -> None:
        prompt = self._prompt(self.text())
        last_error: BaseException | None = None
        try:
            while job.attempts < self.max_attempts:
                job.attempts += 1
                started = time.perf_counter()
                stt = self._stt()
                options: dict[str, Any] = {}
                if job.clear_speech and accepts_no_speech_check(stt):
                    options["no_speech_check"] = False
                try:
                    try:
                        result = await stt.transcribe(
                            job.wav,
                            content_type="audio/wav",
                            prompt=prompt,
                            language=self.language,
                            context=self.context,
                            **options,
                        )
                    except TypeError as exc:
                        if not options or "no_speech_check" not in str(exc):
                            raise
                        _ACCEPTS_NO_SPEECH_CHECK[type(stt)] = False  # a wrapper that cannot pass it on
                        result = await stt.transcribe(
                            job.wav,
                            content_type="audio/wav",
                            prompt=prompt,
                            language=self.language,
                            context=self.context,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - AIError or transport failure: retry once
                    last_error = exc
                    log.warning(
                        "realtime.stt.segment_failed",
                        segment=job.index,
                        attempt=job.attempts,
                        error=f"{type(exc).__name__}: {exc}"[:200],
                    )
                    if job.attempts < self.max_attempts:
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                job.latency_ms = (time.perf_counter() - started) * 1000
                job.finished_at = time.monotonic()
                job.text = (result.text or "").strip()
                job.no_speech_prob = result.no_speech_prob
                job.endpoint = result.endpoint
                job.status = "done"
                if self._on_final is not None:
                    self._on_final(job)
                return
            job.status = "failed"
            if self._on_failure is not None:
                self._on_failure(job, last_error or RuntimeError("stt failed"))
        finally:
            job.done.set()

    async def wait_finals(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109
        """Wait for every submitted segment to finish (done or failed)."""
        waits = [s.done.wait() for s in self.segments if not s.done.is_set()]
        if not waits:
            return True
        try:
            await asyncio.wait_for(asyncio.gather(*waits), timeout)
            return True
        except TimeoutError:
            return False

    @property
    def resolved(self) -> bool:
        """Every submitted segment has its transcript (none pending, none failed)."""
        return all(s.status == "done" for s in self.segments)

    async def retry_failed(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109
        """Retry failed segments once more and wait (up to ``timeout``) for every
        segment still in flight. True only when EVERY segment is transcribed: a
        segment that is still pending is never treated as resolved, so its audio
        can never be silently left out of the answer."""
        failed = self.failed
        for job in failed:
            job.status = "pending"
            job.attempts = max(0, self.max_attempts - 1)
            job.done = asyncio.Event()
            job.task = asyncio.create_task(self._run(job), name=f"realtime.stt.retry.{job.index}")
        await self.wait_finals(timeout)
        return self.resolved

    def confidence(self) -> float | None:
        """Duration-weighted speech confidence: 1 - Whisper's ``no_speech_prob``,
        or the VAD's speech probability where the silence check was skipped."""
        scored: list[tuple[float, int]] = []
        for s in self.segments:
            if s.status != "done":
                continue
            if s.no_speech_prob is not None:
                scored.append((1.0 - float(s.no_speech_prob), max(1, s.duration_ms)))
            elif s.vad_prob is not None and s.vad_prob > 0:
                scored.append((float(s.vad_prob), max(1, s.duration_ms)))
        if not scored:
            return None
        total = sum(weight for _, weight in scored)
        return round(sum(value * weight for value, weight in scored) / total, 3)

    # ------------------------------------------------------------- partials
    async def partial(self, samples: np.ndarray) -> TranscriptionResult | None:
        """Interim transcript of the open segment; ``None`` when skipped."""
        if self.closed or samples.size == 0:
            return None
        generation = len(self.segments)
        try:
            result = await self._stt().transcribe(
                pcm16_to_wav(samples),
                content_type="audio/wav",
                prompt=self._prompt(self.text()),
                language=self.language,
                partial=True,
                context=self.context,
            )
        except PartialSkipped:
            return None
        except AIError as exc:
            log.debug("realtime.stt.partial_failed", error=type(exc).__name__)
            return None
        if self.closed or generation != len(self.segments):
            return None  # the segment closed meanwhile: its final supersedes this partial
        self.partial_text = (result.text or "").strip()
        return result

    async def aclose(self) -> None:
        self.closed = True
        tasks = [s.task for s in self.segments if s.task is not None and not s.task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
