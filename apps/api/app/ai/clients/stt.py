"""Whisper speech-to-text client (OpenAI-compatible
``POST {base}/audio/transcriptions``).

Measured against the deployed server (2026-10-07, ``whisper-large-v3`` on
HF transformers, GB10, two replicas):

* each replica transcribes ONE request at a time - a second concurrent request
  waits for the first, and cancelling a request does not stop the server's work;
* ``response_format=verbose_json`` (segment timestamps) costs ~0.65 s per
  request and the server's no-speech check ~0.3 s: a 4 s clip takes ~2.3 s
  with both, ~1.6 s with ``json`` + check and ~1.35 s with ``json`` alone
  (roughly 0.65 s + 0.18 s per second of audio);
* the server ignores ``prompt`` and ``temperature`` (sent anyway: other
  OpenAI-compatible servers honour them).

Scheduling across ``WHISPER_ENDPOINTS`` replicas, each taking
``replica_parallelism`` requests at once (default 1, the deployed server;
``max_concurrency`` stays an upper bound):

* a final transcription goes to an idle replica - the one with the best recent
  speed (EWMA of observed / expected latency, so a replica slowed down by
  another tenant is avoided) - or waits in one FIFO queue for the first replica
  that frees up; it is never sent to a busy replica where it would queue
  behind someone else's request;
* a final that runs much longer than expected is hedged: the same audio goes to
  an idle replica and the first answer wins (the slower request keeps its
  replica reserved until the server is done with it, so it is never handed new
  work while still busy);
* a replica that fails (unavailable) gets one failover try on another replica;
* partial (interim) transcriptions never queue and never take the last idle
  replica while there are several: :class:`~app.ai.base.PartialSkipped` is
  raised immediately when finals are waiting, the partial budget is used, or no
  spare replica is idle; partials skip the server-side no-speech check, are
  not retried and are only measured (finals go to the model_calls ledger);
* ``background=True`` (a second, whole-answer pass) follows the same "spare
  replica only" rule as partials but runs like a final otherwise (full time
  budget, silence check, ledgered).

Silence hallucinations ("Thank you.", "Thanks for watching!") are removed
when Whisper itself reports likely silence (high ``no_speech_prob``), the
clip is tiny, or the WAV signal is near-silent.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import wave
from collections import deque
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from app.ai.base import (
    AIError,
    AITimeout,
    AIUnavailable,
    CallContext,
    PartialSkipped,
    TranscriptionResult,
    TranscriptSegment,
)
from app.ai.http import EndpointClient, call_status, failure_reason
from app.ai.ledger import LedgerSink, ModelCallRecord, NullLedger
from app.core import metrics
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus, ModelTask

log = get_logger(__name__)

MAX_PROMPT_CHARS = 800
PARTIAL_TIMEOUT_SECONDS = 6.0
NO_SPEECH_THRESHOLD = 0.5
TINY_CLIP_SECONDS = 0.6
SILENCE_DBFS = -55.0

# Latency model of one request (seconds), measured on the deployed server.
BASE_SECONDS = 0.65
NO_SPEECH_CHECK_SECONDS = 0.3
VERBOSE_JSON_SECONDS = 0.65
SECONDS_PER_AUDIO_SECOND = 0.18
UNKNOWN_AUDIO_SECONDS = 5.0
# Hedge a final after max(HEDGE_MIN_SECONDS, HEDGE_FACTOR x expected latency):
# catches a stalled replica (8-10 s seen live) without duplicating normal jitter.
HEDGE_FACTOR = 2.0
HEDGE_MIN_SECONDS = 1.5
SPEED_EWMA_ALPHA = 0.3
SPEED_MIN, SPEED_MAX = 0.3, 10.0

ResponseFormat = Literal["json", "verbose_json"]

_EXTENSIONS = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}

# Phrases Whisper emits for silence / noise (normalised: lower case, no
# punctuation). Real short answers ("yes", "okay", "no") are deliberately
# absent.
KNOWN_HALLUCINATIONS: frozenset[str] = frozenset(
    {
        "thank you",
        "thank you very much",
        "thank you so much",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "thanks for listening",
        "thank you for listening",
        "please subscribe",
        "subscribe",
        "like and subscribe",
        "please like and subscribe",
        "dont forget to subscribe",
        "subscribe to my channel",
        "see you next time",
        "see you in the next video",
        "bye",
        "bye bye",
        "goodbye",
        "you",
        "so",
        "uh",
        "um",
        "hmm",
        "music",
        "applause",
        "silence",
        "blank audio",
        "subtitles by the amaraorg community",
        "transcribed by otterai",
    }
)

_NON_WORD = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def normalize_phrase(text: str) -> str:
    return _SPACES.sub(" ", _NON_WORD.sub("", text.lower())).strip()


def is_hallucination_text(text: str) -> bool:
    """True when ``text`` consists only of known silence phrases (possibly
    repeated: "Thank you. Thank you.")."""
    normalized = normalize_phrase(text)
    if not normalized:
        return bool(text.strip())  # pure punctuation like "..." or "♪"
    if normalized in KNOWN_HALLUCINATIONS:
        return True
    sentences = [normalize_phrase(s) for s in _SENTENCE_SPLIT.split(text.strip())]
    sentences = [s for s in sentences if s]
    return bool(sentences) and all(s in KNOWN_HALLUCINATIONS for s in sentences)


def wav_dbfs(audio: bytes) -> float | None:
    """RMS level of a PCM16 WAV in dBFS (None when not a parseable WAV)."""
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if wav.getsampwidth() != 2:
                return None
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError):
        return None
    if not frames:
        return -120.0
    samples = np.frombuffer(frames[: len(frames) // 2 * 2], dtype="<i2").astype(np.float32)
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples / 32768.0))))
    return -120.0 if rms <= 1e-6 else 20.0 * float(np.log10(rms))


def wav_seconds(audio: bytes) -> float | None:
    """Duration of a WAV clip from its header (None when not a WAV)."""
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate > 0 else None
    except (wave.Error, EOFError):
        return None


def expected_latency(audio_seconds: float, *, no_speech_check: bool, verbose: bool = False) -> float:
    """Nominal latency (seconds) of one request on an idle replica."""
    return (
        BASE_SECONDS
        + (NO_SPEECH_CHECK_SECONDS if no_speech_check else 0.0)
        + (VERBOSE_JSON_SECONDS if verbose else 0.0)
        + SECONDS_PER_AUDIO_SECOND * max(0.0, audio_seconds)
    )


def clean_transcription(
    result: TranscriptionResult, *, audio_dbfs: float | None = None
) -> tuple[TranscriptionResult, bool]:
    """Remove silence hallucinations. Returns (result, cleaned?)."""
    silence_like = (
        (result.no_speech_prob is not None and result.no_speech_prob >= NO_SPEECH_THRESHOLD)
        or (result.duration_s is not None and result.duration_s < TINY_CLIP_SECONDS)
        or (audio_dbfs is not None and audio_dbfs < SILENCE_DBFS)
    )
    if silence_like and result.text.strip() and is_hallucination_text(result.text):
        result.text = ""
        result.segments = []
        return result, True
    return result, False


def truncate_prompt(prompt: str | None, limit: int = MAX_PROMPT_CHARS) -> str | None:
    """Keep the most recent context (the tail), cut at a word boundary."""
    if not prompt:
        return None
    prompt = " ".join(prompt.split())
    if len(prompt) <= limit:
        return prompt
    tail = prompt[-limit:]
    space = tail.find(" ")
    if 0 <= space < 40:
        tail = tail[space + 1 :]
    return tail


@dataclass(slots=True, eq=False)
class _Replica:
    client: EndpointClient
    inflight: int = 0
    speed: float = 1.0  # EWMA of observed / expected latency (1.0 = nominal)

    @property
    def url(self) -> str:
        return self.client.base_url

    @property
    def healthy(self) -> bool:
        return not self.client.breaker.would_reject()


@dataclass(slots=True, eq=False)
class _Waiter:
    future: asyncio.Future[_Replica]
    exclude: Collection[_Replica] = ()


@dataclass(slots=True)
class _Call:
    """One transcription request being scheduled."""

    data: dict[str, Any]
    files: dict[str, Any]
    task: str
    expected: float
    deadline: float
    partial: bool


class WhisperSttClient:
    """``SttClient`` with speed-aware, never-queue-behind-a-busy-replica scheduling."""

    def __init__(
        self,
        replicas: Sequence[EndpointClient],
        *,
        model: str,
        language: str | None = "en",
        max_concurrency: int = 4,
        partial_budget: int | None = None,
        timeout: float = 30.0,
        ledger: LedgerSink | None = None,
        replica_parallelism: int = 1,
        response_format: ResponseFormat = "json",
        no_speech_check: bool = True,
        hedge: bool = True,
    ) -> None:
        if not replicas:
            raise ValueError("at least one Whisper endpoint is required")
        self._replicas = [_Replica(client) for client in replicas]
        self.model = model
        self.language = language
        self.max_concurrency = max(1, max_concurrency)
        self.slots_per_replica = max(1, min(self.max_concurrency, replica_parallelism))
        self.partial_budget = max(1, partial_budget if partial_budget is not None else 1)
        self.timeout = timeout
        self.response_format: ResponseFormat = response_format
        self.no_speech_check = no_speech_check
        self.hedge = hedge
        self._ledger: LedgerSink = ledger or NullLedger()
        self._partials_inflight = 0
        self._waiters: deque[_Waiter] = deque()
        self._background: set[asyncio.Task[Any]] = set()
        self.hedges = 0
        self.hedge_wins = 0

    # -------------------------------------------------------------- stats
    @property
    def inflight(self) -> dict[str, int]:
        return {r.url: r.inflight for r in self._replicas}

    @property
    def partials_inflight(self) -> int:
        return self._partials_inflight

    @property
    def queued(self) -> int:
        return sum(1 for w in self._waiters if not w.future.done())

    @property
    def replica_speeds(self) -> dict[str, float]:
        return {r.url: round(r.speed, 2) for r in self._replicas}

    def has_spare_capacity(self) -> bool:
        """True when a partial transcription could start right now."""
        return self._partial_refusal() is None

    # ---------------------------------------------------------- scheduling
    def _free_slots(self, replica: _Replica) -> int:
        return max(0, self.slots_per_replica - replica.inflight)

    def _idle(self, exclude: Collection[_Replica] = ()) -> list[_Replica]:
        return [r for r in self._replicas if self._free_slots(r) > 0 and r not in exclude]

    def _best(self, candidates: list[_Replica], *, slowest: bool = False) -> _Replica | None:
        if not candidates:
            return None
        pool = [r for r in candidates if r.healthy] or candidates
        if slowest:
            return max(pool, key=lambda r: (-r.inflight, r.speed))
        return min(pool, key=lambda r: (r.inflight, r.speed))

    def _take(self, exclude: Collection[_Replica] = ()) -> _Replica | None:
        replica = self._best(self._idle(exclude))
        if replica is not None:
            replica.inflight += 1
        return replica

    def _partial_refusal(self, *, count_budget: bool = True) -> str | None:
        if count_budget and self._partials_inflight >= self.partial_budget:
            return "partial STT budget exhausted"
        if self.queued:
            return "final transcriptions are waiting"
        free = sum(self._free_slots(r) for r in self._replicas)
        if free == 0:
            return "all STT slots busy"
        total = self.slots_per_replica * len(self._replicas)
        if total > 1 and free < 2:
            return "keeping the last free STT slot for finals"
        return None

    def _reserve_spare(self) -> _Replica:
        """A replica for background work: same spare-capacity rule as partials."""
        reason = self._partial_refusal(count_budget=False)
        if reason is not None:
            raise PartialSkipped(reason, endpoint="whisper")
        replica = self._best(self._idle(), slowest=True)
        assert replica is not None
        replica.inflight += 1
        return replica

    def _reserve_partial(self) -> _Replica:
        reason = self._partial_refusal()
        if reason is not None:
            raise PartialSkipped(reason, endpoint="whisper")
        # The slower idle replica: keep the faster one free for the next final.
        replica = self._best(self._idle(), slowest=True)
        assert replica is not None
        replica.inflight += 1
        self._partials_inflight += 1
        return replica

    async def _reserve_final(self, budget: float, exclude: Collection[_Replica] = ()) -> _Replica:
        self._dispatch()  # earlier finals first; what is still idle afterwards none of them can use
        replica = self._take(exclude)
        if replica is not None:
            return replica
        if budget <= 0:
            raise AITimeout("whisper: no time left to wait for an STT replica", endpoint="whisper")
        waiter = _Waiter(asyncio.get_running_loop().create_future(), exclude)
        self._waiters.append(waiter)
        try:
            async with asyncio.timeout(budget):
                return await waiter.future
        except TimeoutError:
            if waiter.future.done() and not waiter.future.cancelled():
                self._release(waiter.future.result(), partial=False)
            raise AITimeout(f"whisper: waited {budget:.1f}s for an STT replica", endpoint="whisper") from None
        except asyncio.CancelledError:
            if waiter.future.done() and not waiter.future.cancelled():
                self._release(waiter.future.result(), partial=False)
            raise
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def _release(self, replica: _Replica, *, partial: bool) -> None:
        replica.inflight = max(0, replica.inflight - 1)
        if partial:
            self._partials_inflight = max(0, self._partials_inflight - 1)
        self._dispatch()

    def _dispatch(self) -> None:
        """Hand idle replicas to waiting finals, oldest first."""
        for waiter in list(self._waiters):
            if waiter.future.done():
                self._waiters.remove(waiter)
                continue
            if not self._idle():
                break
            granted = self._take(waiter.exclude)
            if granted is None:
                continue  # this waiter excludes the free replica; a later one may take it
            self._waiters.remove(waiter)
            waiter.future.set_result(granted)

    def _observe(self, replica: _Replica, elapsed: float, expected: float) -> None:
        ratio = min(SPEED_MAX, max(SPEED_MIN, elapsed / max(expected, 0.05)))
        replica.speed = (1 - SPEED_EWMA_ALPHA) * replica.speed + SPEED_EWMA_ALPHA * ratio

    # -------------------------------------------------------------- public
    async def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str = "audio/wav",
        prompt: str | None = None,
        language: str | None = None,
        partial: bool = False,
        context: CallContext | None = None,
        no_speech_check: bool | None = None,
        background: bool = False,
    ) -> TranscriptionResult:
        """Transcribe one clip. ``partial=True`` raises ``PartialSkipped``
        instead of waiting when no spare replica is idle; ``background=True``
        does the same for a full-budget, ledgered transcription (a second pass
        that must never delay live work). ``no_speech_check`` overrides the
        server-side silence check (finals: on by default, partials: off);
        without it ``no_speech_prob`` is reported as unknown."""
        if not audio:
            return TranscriptionResult(text="", language=language or self.language, duration_s=0.0)
        kind = "partial" if partial else "final"
        task = (context.task if context else ModelTask.STT).value
        loop = asyncio.get_running_loop()
        budget = min(self.timeout, PARTIAL_TIMEOUT_SECONDS) if partial else self.timeout
        started = time.perf_counter()
        ctype = content_type.split(";")[0].strip().lower() or "audio/wav"
        is_wav = ctype in _WAV_TYPES
        check = (not partial and self.no_speech_check) if no_speech_check is None else no_speech_check
        lang = language or self.language
        data: dict[str, Any] = {
            "model": self.model,
            "response_format": self.response_format,
            "temperature": "0",
            "no_speech_check": "true" if check else "false",
        }
        if lang:
            data["language"] = lang
        clipped = truncate_prompt(prompt)
        if clipped:
            data["prompt"] = clipped
        audio_seconds = wav_seconds(audio) if is_wav else None
        call = _Call(
            data=data,
            files={"file": (f"audio.{_EXTENSIONS.get(ctype, 'wav')}", audio, ctype)},
            task=task,
            expected=expected_latency(
                audio_seconds if audio_seconds is not None else UNKNOWN_AUDIO_SECONDS,
                no_speech_check=check,
                verbose=self.response_format == "verbose_json",
            ),
            deadline=loop.time() + budget,
            partial=partial,
        )
        attempts = 0
        replica: _Replica | None = None
        try:
            if partial or background:
                replica = self._reserve_partial() if partial else self._reserve_spare()
                try:
                    payload, attempts = await self._send(replica, call)
                finally:
                    self._release(replica, partial=partial)
            else:
                payload, attempts, replica = await self._final(call)
            result = self._parse(payload, replica, lang)
        except asyncio.CancelledError:
            if not partial:
                self._record(context, ModelCallStatus.CANCELLED, started, max(1, attempts), None)
            raise
        except PartialSkipped:
            raise
        except AIError as exc:
            metrics.MODEL_CALL_FAILURES.labels(
                endpoint="whisper", task=task, reason=failure_reason(exc)
            ).inc()
            if not partial:
                self._record(context, call_status(exc), started, max(1, attempts), exc)
            raise
        elapsed = time.perf_counter() - started
        metrics.STT_LATENCY.labels(kind=kind).observe(elapsed)
        result.latency_ms = elapsed * 1000
        if not check:
            result.no_speech_prob = None  # the server did not compute it
        if result.duration_s is None and audio_seconds is not None:
            result.duration_s = round(audio_seconds, 3)
        result, cleaned = clean_transcription(result, audio_dbfs=wav_dbfs(audio) if is_wav else None)
        if cleaned:
            log.info("ai.stt.hallucination_removed", kind=kind, no_speech_prob=result.no_speech_prob)
        if not partial:
            self._record(context, ModelCallStatus.OK, started, max(1, attempts), None)
        return result

    # ------------------------------------------------------------ internal
    async def _send(self, replica: _Replica, call: _Call) -> tuple[Any, int]:
        """One HTTP exchange on a reserved replica (the caller releases it)."""
        loop = asyncio.get_running_loop()
        sent = loop.time()
        response = await replica.client.request(
            "POST",
            "/audio/transcriptions",
            data=call.data,
            files=call.files,
            timeout=max(call.deadline - sent, 0.001),
            idempotent=True,
            max_retries=0 if call.partial or len(self._replicas) > 1 else None,
            acquire_slot=False,
            task=call.task,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AIUnavailable("whisper: response is not JSON", endpoint="whisper") from exc
        self._observe(replica, loop.time() - sent, call.expected)
        return payload, response.attempts

    async def _attempt(self, replica: _Replica, call: _Call) -> tuple[Any, int]:
        try:
            return await self._send(replica, call)
        finally:
            self._release(replica, partial=False)

    def _spawn(self, replica: _Replica, call: _Call) -> asyncio.Task[tuple[Any, int]]:
        task = asyncio.create_task(self._attempt(replica, call), name=f"stt.final.{replica.url}")
        self._background.add(task)
        task.add_done_callback(_settle(self._background))
        return task

    async def _final(self, call: _Call) -> tuple[Any, int, _Replica]:
        """Run a final with failover and straggler hedging; returns (payload, http attempts, replica)."""
        loop = asyncio.get_running_loop()
        first = await self._reserve_final(call.deadline - loop.time())
        tried: list[_Replica] = [first]
        running: dict[asyncio.Task[tuple[Any, int]], _Replica] = {self._spawn(first, call): first}
        hedge_at = (
            loop.time() + max(HEDGE_MIN_SECONDS, HEDGE_FACTOR * call.expected)
            if self.hedge and len(self._replicas) > 1
            else None
        )
        failover_left = 1
        http_attempts = 0
        last_error: AIError | None = None
        while running:
            now = loop.time()
            wait = call.deadline + 0.5 - now
            if hedge_at is not None:
                wait = min(wait, max(0.0, hedge_at - now))
            if wait <= 0 and hedge_at is None:
                break
            done, _ = await asyncio.wait(running, timeout=max(0.0, wait), return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                replica = running.pop(finished)
                error = (
                    AIUnavailable("whisper: request cancelled", endpoint="whisper")
                    if finished.cancelled()
                    else finished.exception()
                )
                if error is None:
                    payload, attempts = finished.result()
                    http_attempts += attempts
                    if len(tried) > 1:
                        self.hedge_wins += replica is not first
                        log.info(
                            "ai.stt.hedge_result",
                            winner=replica.url,
                            first=first.url,
                            hedged=replica is not first,
                        )
                    return payload, http_attempts, replica
                http_attempts += 1
                if not isinstance(error, AIError):
                    raise error
                last_error = error
                if (
                    isinstance(error, AIUnavailable)
                    and not running
                    and failover_left > 0
                    and len(tried) < len(self._replicas)
                ):
                    failover_left -= 1
                    log.warning("ai.stt.replica_failover", failed=replica.url, error=str(error)[:200])
                    fallback = await self._reserve_final(call.deadline - loop.time(), exclude=tried)
                    tried.append(fallback)
                    running[self._spawn(fallback, call)] = fallback
            if hedge_at is not None and loop.time() >= hedge_at and running:
                hedge_at = None
                self._dispatch()  # queued finals come before a hedge
                spare = self._take(exclude=tried)
                if spare is not None:
                    self.hedges += 1
                    tried.append(spare)
                    log.info(
                        "ai.stt.hedged",
                        slow=first.url,
                        to=spare.url,
                        after_ms=round(max(HEDGE_MIN_SECONDS, HEDGE_FACTOR * call.expected) * 1000),
                    )
                    running[self._spawn(spare, call)] = spare
        if running:
            # Past the deadline: give up waiting; the requests finish in the background.
            raise AITimeout("whisper: transcription timed out", endpoint="whisper")
        raise last_error or AIUnavailable("whisper: transcription failed", endpoint="whisper")

    @staticmethod
    def _parse(data: Any, replica: _Replica | None, language: str | None) -> TranscriptionResult:
        if not isinstance(data, dict) or not isinstance(data.get("text", ""), str):
            raise AIUnavailable("whisper: malformed transcription response", endpoint="whisper")
        segments: list[TranscriptSegment] = []
        for seg in data.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            try:
                segments.append(
                    TranscriptSegment(
                        start=float(seg.get("start", 0.0)),
                        end=float(seg.get("end", 0.0)),
                        text=str(seg.get("text", "")).strip(),
                    )
                )
            except (TypeError, ValueError):
                continue
        no_speech = data.get("no_speech_prob")
        if no_speech is None and data.get("segments"):
            probs: list[float] = [
                float(s["no_speech_prob"])
                for s in data["segments"]
                if isinstance(s, dict) and isinstance(s.get("no_speech_prob"), int | float)
            ]
            no_speech = max(probs) if probs else None
        duration = data.get("duration")
        return TranscriptionResult(
            text=str(data.get("text") or "").strip(),
            language=data.get("language") or data.get("language_code") or language,
            duration_s=float(duration) if isinstance(duration, int | float) else None,
            no_speech_prob=float(no_speech) if isinstance(no_speech, int | float) else None,
            segments=segments,
            endpoint=replica.url if replica is not None else None,
        )

    def _record(
        self,
        context: CallContext | None,
        status: ModelCallStatus,
        started: float,
        attempts: int,
        error: BaseException | None,
    ) -> None:
        try:
            self._ledger.record(
                ModelCallRecord(
                    task=(context.task if context else ModelTask.STT).value,
                    endpoint="whisper",
                    model=self.model,
                    status=status.value,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=None if error is None else f"{type(error).__name__}: {error}",
                    session_id=context.session_id if context else None,
                    organization_id=context.organization_id if context else None,
                    prompt_version=context.prompt_version if context else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai.ledger.record_failed", error=str(exc))


def _settle(tasks: set[asyncio.Task[Any]]) -> Any:
    """Done-callback: forget the task and mark its exception as retrieved
    (hedge losers and requests whose caller went away finish unobserved)."""

    def callback(task: asyncio.Task[Any]) -> None:
        tasks.discard(task)
        if not task.cancelled():
            task.exception()

    return callback


_WAV_TYPES = frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"})
