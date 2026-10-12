"""``SessionRuntime``: one live interview session inside one API process
(REALTIME_DESIGN.md sections 2-8).

Responsibilities (the engine owns every interview decision):

* transport: attach/detach WebSocket connections (a runtime survives
  reconnects), ordered sends through one writer task, outbox sequencing and
  replay, heartbeat watchdog;
* audio in: VAD segmentation, ordered STT finals, throttled partials, adaptive
  end of turn, typed answers;
* the floor: one engine interaction at a time (``FloorJob`` queue), turn FSM,
  acknowledgement backchannels, interviewer speech (TTS pipeline), playback
  tracking -> ``mark_utterance_delivered``, silence prompts and time limits;
* barge-in: cancel the engine iterator + TTS, ``barge_in`` message, spoken
  portion from played chunk durations -> ``handle_interruption``, then
  backchannel resume or a new turn;
* coding / integrity / session-bus bridges; disconnect grace, pause and
  resume; session end.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from app.ai.base import AIError, CallContext, PartialSkipped
from app.ai.registry import get_ai
from app.core import metrics
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.queue import subscribe_session_events
from app.core.redis import get_redis
from app.db.session import session_scope
from app.domain.enums import (
    AvatarExpression,
    AvatarGesture,
    AvatarState,
    IntegrityEventType,
    ModelTask,
    SessionEndReason,
    SessionStatus,
    TurnState,
)
from app.realtime import close_codes
from app.realtime.acknowledgements import (
    FILLER_PHRASES,
    REPEAT_PROMPT,
    AcknowledgementCache,
    get_acknowledgement_cache,
    opener_phrases,
)
from app.realtime.audio.pcm import bytes_to_samples, pcm16_to_wav
from app.realtime.audio.segmenter import AudioSegmenter, HighSpeech, SegmentClosed, SpeechEnded, SpeechStarted
from app.realtime.audio.vad import create_vad
from app.realtime.bridges import CodingBridge, IntegrityBridge
from app.realtime.context import SessionContext, record_connection, refresh_status, touch_heartbeat
from app.realtime.endpointing import Endpointer, EndpointParams, commit_required_ms
from app.realtime.glossary import Glossary, log_corrections
from app.realtime.latency import observe_turn_latency
from app.realtime.lock import SessionLock
from app.realtime.outbox import Outbox
from app.realtime.revision import is_material_revision, revise_answer_transcript, word_difference
from app.realtime.speech import SpeechStreamer, TtsHealth, UtteranceSpeech, estimate_speech_ms
from app.realtime.stt import (
    PartialScheduler,
    SegmentJob,
    TurnTranscriber,
    accepts_stt_option,
    build_whisper_prompt,
    stt_has_spare_capacity,
)
from app.realtime.transport import Transport
from app.realtime.turns import (
    TurnController,
    is_backchannel,
    is_repeat_complaint,
    is_repeat_or_clarification,
    resume_after_backchannel,
    spoken_portion,
    unspoken_words,
)
from app.schemas.realtime import (
    FRAME_MIC_AUDIO,
    MIC_SAMPLE_RATE,
    PROTOCOL_VERSION,
    ActivityContentMsg,
    ActivityEndMsg,
    ActivityFinishMsg,
    ActivityPhaseMsg,
    ActivityPhasePlanMsg,
    ActivitySnapshotMsg,
    ActivityStartMsg,
    AvatarBehaviorMsg,
    BargeInMsg,
    ClientErrorMsg,
    ClientInfo,
    CodeRunMsg,
    CodeSubmitMsg,
    CodeUpdateMsg,
    CodingEndMsg,
    CodingHintMsg,
    CodingStartMsg,
    CodingStateMsg,
    EndInterviewMsg,
    EndTurnMsg,
    ErrorMsg,
    HeartbeatAckMsg,
    HeartbeatMsg,
    HelloMsg,
    HintRequestMsg,
    IntakeStageMsg,
    IntakeStateMsg,
    IntegrityEventsMsg,
    LanguageChangeMsg,
    LocalVadMsg,
    NoticeMsg,
    PlaybackMsg,
    RepeatQuestionMsg,
    SessionEndedMsg,
    SessionStateMsg,
    StartInterviewMsg,
    TextDeltaMsg,
    TranscriptMsg,
    TtsChunkMsg,
    TtsFallbackMsg,
    TurnStateMsg,
    TypedAnswerMsg,
    UtteranceEndMsg,
    UtteranceStartMsg,
    _ServerMsg,
    pack_frame,
    unpack_frame,
)
from app.services.integrity import reply_window
from app.services.interview.contracts import (
    ActivityCapture,
    ActivityEnded,
    ActivityEvent,
    ActivityPhaseChanged,
    ActivityStarted,
    BehaviorSpec,
    CandidateTurn,
    CodingActivity,
    CodingEnded,
    CodingHintIssued,
    CodingStarted,
    InterruptedUtterance,
    SessionClosing,
    SpeechDelta,
    StateChanged,
    TimerEvent,
    TurnEvent,
    UtteranceBegin,
    UtteranceMeta,
)
from app.services.interview.records import log_event
from app.services.interview.understanding import understand as understand_turn

log = get_logger(__name__)

# Timing constants (seconds unless noted). Settings carry the tunables named in
# the design; these are internal safety margins.
DELIVERY_GRACE_S = 1.5
INTERRUPTION_REPORT_WAIT_S = 1.2
SILENCE_PROMPT_S = 25.0
TICK_S = 0.25
ENGINE_TICK_S = 10.0  # coding-time ticks forwarded to the engine
STT_FINAL_TIMEOUT_S = 25.0
STT_RETRY_TIMEOUT_S = 20.0  # inline retry of failed segments before the turn becomes "unresolved"
AUDIO_STALL_S = 3.0
REPORT_LINGER_S = 120.0  # report generation takes 15-65 s after the end
HEARTBEAT_DB_INTERVAL_S = 30.0
FILLER_AFTER_S = 3.5  # "One moment." when the reply has not started this long after the end of the turn
ACK_MIN_SPEECH_MS = 1200  # a finished answer this long hears "Okay." ~1 s after it ends (was 2500)
MAX_TURN_AUDIO_BYTES = 16000 * 2 * 300  # 5 minutes of PCM16 for answer audio
SEND_QUEUE_LIMIT = 2000
UNRESOLVED_RETRY_DELAYS = (2.0, 4.0, 8.0, 15.0, 30.0)
# The "trouble hearing you" notice needs real evidence: Whisper failed on this much
# voiced candidate audio since the last good final. Segments shorter or less
# speech-like than the floor below (silence, breaths, room noise, a cough) never
# count, so a busy STT replica during the intro or a quiet room cannot raise it.
STT_DEGRADED_SPEECH_MS = 1500
STT_DEGRADED_SEGMENT_SPEECH_MS = 600
STT_DEGRADED_SEGMENT_PROB = 0.6
TIME_WARNING_REMAINING_S = 300.0
# Segmentation, tuned with tests/integration/realtime/test_stt_harness.py
# (2026-10-07, real Whisper): once a segment holds 2.5 s of audio a 200 ms pause
# closes it, and no segment exceeds 6 s (STT_MAX_SEGMENT_SECONDS stays an upper
# bound), so the segment left at the end of an answer - the only STT the end of
# turn waits for - is short. STT wait after end of turn: p50 1412 -> 555 ms,
# same word error rate.
EAGER_SEGMENT_PAUSE_MS = 200
EAGER_SEGMENT_AFTER_MS = 2500
MAX_SEGMENT_MS = 6000
# Answers that could not be understood are not passed on: the interviewer asks
# again (at most MAX_UNCLEAR_PROMPTS times in a row, then the answer goes on).
UNCLEAR_MIN_VOICED_MS = 800  # an empty transcript after this much speech
UNCLEAR_CONFIDENCE = 0.5
MAX_UNCLEAR_PROMPTS = 2
HESITATION_WORDS = frozenset(
    {"um", "uh", "uhm", "umm", "erm", "er", "ah", "eh", "hmm", "hm", "mm", "mmm", "huh"}
)
AFFIRMATIVE_SOUNDS = frozenset({"mm hmm", "mmhmm", "mhm", "uh huh", "uhhuh", "mm hm"})
# Second, whole-answer transcription (full context) after the turn is committed.
FULL_PASS_MIN_SEGMENTS = 2
FULL_PASS_MIN_VOICED_MS = 3000
FULL_PASS_MAX_AUDIO_BYTES = 16000 * 2 * 120
FULL_PASS_WAIT_S = 15.0  # waiting for a spare Whisper replica
FULL_PASS_MATERIAL_RATIO = 0.08  # share of words that must differ to revise the stored transcript

# Intake activities (INTAKE_INTERVIEW.md 7.2, 7.4): capture-phase silence rules. A capture phase ends after
# ``silence_ms`` of silence once the condition holds (words heard, share of the passage read); it never ends on
# silence before the candidate has spoken (the deadline governs). Deadline grace: a candidate still speaking
# at the deadline gets up to ACTIVITY_GRACE_MAX_S for a >= ACTIVITY_GRACE_PAUSE_MS pause.


@dataclass(frozen=True, slots=True)
class SilenceRule:
    silence_ms: int
    min_words: int = 0
    min_reference_ratio: float = 0.0


ACTIVITY_SILENCE: dict[tuple[str, str], tuple[SilenceRule, ...]] = {
    ("reading_aloud", "read"): (SilenceRule(4000, min_reference_ratio=0.70), SilenceRule(8000)),
    ("read_relay", "relay"): (SilenceRule(6000, min_words=15),),
    ("topic_card", "speak"): (SilenceRule(10000, min_words=30),),
}
ACTIVITY_GRACE_PAUSE_MS = 300
ACTIVITY_GRACE_MAX_S = 3.0
ACTIVITY_PAUSE_MIN_MS = 300  # VAD pauses shorter than this inside a capture are not recorded
ACTIVITY_RETRY_TIMEOUT_S = 10.0
ACTIVITY_TIMER_STEP_S = 0.25
# Long-answer endpointing for intake items marked answer_mode "long" (7.11).
LONG_ANSWER_ENDPOINT = {"base_ms": 1500, "short_ms": 1100, "long_ms": 2600}

PRIORITY_END = 0
PRIORITY_TURN = 1
PRIORITY_CONTROL = 2
PRIORITY_CODING_URGENT = 3
PRIORITY_CODING = 4
PRIORITY_TIMER = 5

AVATAR_FOR_TURN = {
    TurnState.IDLE: AvatarState.IDLE,
    TurnState.AI_SPEAKING: AvatarState.SPEAKING,
    TurnState.LISTENING: AvatarState.LISTENING,
    TurnState.CANDIDATE_SPEAKING: AvatarState.LISTENING,
    TurnState.PROCESSING: AvatarState.THINKING,
    TurnState.CODING: AvatarState.LISTENING,
    TurnState.PAUSED: AvatarState.IDLE,
    TurnState.ENDED: AvatarState.CLOSING,
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _as_iterator(source: Any) -> AsyncIterator[TurnEvent]:
    """Engine methods may be async generators or coroutines returning one."""
    if inspect.isawaitable(source) and not hasattr(source, "__aiter__"):
        source = await source
    if source is None:
        return _empty()
    return source


async def _empty() -> AsyncIterator[TurnEvent]:
    return
    yield  # pragma: no cover


def _end_reason(value: str) -> SessionEndReason:
    try:
        return SessionEndReason(value)
    except ValueError:
        return SessionEndReason.SUFFICIENT_EVIDENCE


async def _aclose(iterator: Any) -> None:
    closer = getattr(iterator, "aclose", None)
    if closer is None:
        return
    with contextlib.suppress(Exception):
        await closer()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class RuntimeClosed(Exception):
    pass


def build_segmenter(settings: Settings) -> AudioSegmenter:
    """The runtime's VAD segmenter for one session."""
    return AudioSegmenter(
        create_vad(settings.vad_engine, settings.vad_model_path),
        speech_threshold=settings.vad_speech_threshold,
        barge_in_threshold=settings.vad_barge_in_threshold,
        barge_in_min_speech_ms=settings.barge_in_min_speech_ms,
        segment_pause_ms=settings.segment_pause_ms,
        eager_pause_ms=EAGER_SEGMENT_PAUSE_MS,
        eager_after_ms=EAGER_SEGMENT_AFTER_MS,
        max_segment_ms=min(int(settings.stt_max_segment_seconds * 1000), MAX_SEGMENT_MS),
    )


# ----------------------------------------------------------------- structures


@dataclass
class ActiveUtterance:
    utterance_id: str
    kind: str
    behavior: BehaviorSpec
    question_id: str | None = None
    speech: UtteranceSpeech | None = None
    is_ack: bool = False
    offline: bool = False  # generated while no client was connected
    full_text: str = ""
    begun_at: float = field(default_factory=time.monotonic)
    ended: bool = False
    ended_at: float | None = None
    playback_started_at: float | None = None
    played_ms: int = 0
    last_progress_at: float | None = None
    playback_reported: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    interrupted_evt: asyncio.Event = field(default_factory=asyncio.Event)
    interrupted: bool = False
    delivered: bool = False
    delivery_task: asyncio.Task[None] | None = None
    audio_ms: int | None = None  # known audio length (pre-synthesized acknowledgements)
    answer_mode: str | None = None  # intake: "standard" | "long" (endpointing of the next answer)

    @property
    def text(self) -> str:
        if self.full_text:
            return self.full_text
        return " ".join((self.speech.text if self.speech else "").split())

    @property
    def total_ms(self) -> int:
        if self.audio_ms is not None:
            return self.audio_ms
        if self.speech is not None and self.speech.chunks:
            return self.speech.total_ms
        return estimate_speech_ms(self.text) if self.text else 0

    @property
    def first_chunk_at(self) -> float | None:
        return self.speech.timings.first_chunk_at if self.speech else None

    def estimated_end(self) -> float:
        base = self.playback_started_at or self.first_chunk_at or self.ended_at or time.monotonic()
        end = base + self.total_ms / 1000.0
        if self.last_progress_at is not None:
            end = max(end, self.last_progress_at + max(0, self.total_ms - self.played_ms) / 1000.0)
        return end

    def estimated_played_ms(self, now: float | None = None) -> int:
        now = now or time.monotonic()
        if self.playback_reported:
            return self.played_ms
        start = self.playback_started_at or self.first_chunk_at
        if start is None:
            return 0
        return int(max(0.0, min(self.total_ms, (now - start) * 1000)))


@dataclass
class TurnCapture:
    """Audio + transcript of one candidate turn while it is being captured."""

    capture_id: str
    start_ms: int  # audio clock
    started_session_ms: int
    transcriber: TurnTranscriber
    started_at: float = field(default_factory=time.monotonic)
    audio: bytearray = field(default_factory=bytearray)
    record_audio: bool = False
    voiced_ms: int = 0
    end_ms: int | None = None
    ended_session_ms: int | None = None
    end_at: float | None = None
    end_reason: str | None = None
    required_ms: int | None = None
    acknowledged: str | None = None  # the acknowledgement phrase already played after this turn
    replied: bool = False  # the first audio of the interviewer's reply to this turn was sent
    reply_text: bool = False  # the reply's first words arrived (its audio is under a second away)
    # The candidate's speech cut off a reply that had not started playing: nothing was heard, so this is the
    # same answer going on (fast transcription ends turns at short pauses), not an interruption of the interviewer.
    interrupted_unheard: bool = False
    # Silence since the candidate's last voiced audio when the end of turn was decided (P0.4): the part of the
    # wait the candidate experiences before any end-of-turn timer starts.
    silence_at_end_ms: int | None = None
    interrupted_task: asyncio.Task[InterruptedUtterance | None] | None = None
    engine_called: bool = False
    silence_episode: int = 0
    notified_episode: int = 0
    typed_text: str | None = None
    audio_until_ms: int = 0  # audio clock up to which ``audio`` holds the turn's PCM

    def wav(self) -> bytes | None:
        if not self.record_audio or not self.audio:
            return None
        return pcm16_to_wav(bytes(self.audio))


@dataclass
class RuntimeActivity:
    """An intake activity as the runtime runs it (phase timers, capture, reveal accounting). ``content`` lives
    only here (memory) and is dropped when it may no longer be shown (read_relay after its read phase)."""

    activity_id: str
    kind: str
    stage: str
    title: str
    phases: list[dict[str, Any]]
    rules: dict[str, dict[str, Any]]
    content: dict[str, Any] | None
    attempt: int = 1
    resumed: bool = False
    reference_words: int = 0
    status: str = "armed"  # armed | running | waiting (between phases) | interrupted | ended
    phase: str = ""
    duration_ms: int = 0
    start_after: str | None = None
    phase_started_elapsed: float | None = None  # runtime clock (s)
    deadline: float | None = None  # runtime clock (s)
    capture: TurnCapture | None = None
    pauses: list[dict[str, int]] = field(default_factory=list)
    pause_started_ms: int | None = None  # audio clock of the last SpeechEnded inside the capture
    spoken: bool = False
    ending: bool = False
    finish_announced: bool = False
    shown_ms: int = 0  # read_relay: passage visible so far (all reveal windows)
    timer: asyncio.Task[None] | None = None

    def rule(self, phase: str | None = None) -> dict[str, Any]:
        return self.rules.get(phase or self.phase) or {}

    @property
    def capture_phase(self) -> bool:
        return bool(self.rule().get("capture"))


@dataclass(order=True)
class FloorJob:
    priority: int
    order: int
    kind: str = field(compare=False)
    run: Callable[[], Awaitable[None]] = field(compare=False)
    coalesce: str | None = field(default=None, compare=False)
    capture: TurnCapture | None = field(default=None, compare=False)
    needs_idle: bool = field(default=True, compare=False)


@dataclass
class _OutItem:
    seq: int
    text: str | None
    binary: bytes | None = None
    future: asyncio.Future[None] | None = None
    replay_after: int | None = None


class RuntimeClock:
    """Interview clock as seen by the runtime (pauses while disconnected)."""

    def __init__(self, elapsed_s: float = 0.0) -> None:
        self.elapsed_before = elapsed_s
        self.running_since: float | None = None

    def elapsed(self, now: float | None = None) -> float:
        if self.running_since is None:
            return self.elapsed_before
        return self.elapsed_before + max(0.0, (now or time.monotonic()) - self.running_since)

    def start(self) -> None:
        if self.running_since is None:
            self.running_since = time.monotonic()

    def pause(self) -> None:
        if self.running_since is not None:
            self.elapsed_before = self.elapsed()
            self.running_since = None

    def sync(self, elapsed_s: float, running: bool) -> None:
        self.elapsed_before = elapsed_s
        self.running_since = time.monotonic() if running else None


# ===================================================================== runtime


class SessionRuntime:
    def __init__(
        self,
        ctx: SessionContext,
        *,
        settings: Settings,
        engine: Any,
        coding: Any | None,
        integrity: Any,
        lock: SessionLock,
        redis: Any | None,
        on_closed: Callable[[SessionRuntime], Awaitable[None] | None] | None = None,
        ack_cache: AcknowledgementCache | None = None,
    ) -> None:
        self.ctx = ctx
        self.session_id = ctx.session_id
        self.settings = settings
        self.engine = engine
        self.coding = coding
        self.integrity = integrity
        self.lock = lock
        self._on_closed = on_closed
        self.ack_cache = ack_cache or get_acknowledgement_cache()
        self.outbox = Outbox(ctx.session_id, redis, buffer_size=settings.session_resume_buffer_messages)
        self.turns = TurnController(on_change=self._on_turn_change)
        self.endpointer = Endpointer(EndpointParams.from_settings(settings))
        self.segmenter = build_segmenter(settings)
        self.voice = ctx.policy.voice or settings.tts_voice
        self.tts_health = TtsHealth(lambda: get_ai().tts, on_change=self._on_tts_health)
        self.speech = SpeechStreamer(
            self,
            lambda: get_ai().tts,
            voice=self.voice,
            max_inflight=settings.tts_max_inflight_per_session,
            health=self.tts_health,
        )
        self.partials = PartialScheduler(
            base_interval_ms=settings.stt_partial_interval_ms,
            min_audio_ms=settings.stt_partial_min_audio_ms,
            enabled=settings.stt_partials_enabled,
        )
        self.coding_bridge = CodingBridge(self)
        self.integrity_bridge = IntegrityBridge(self)
        self.clock = RuntimeClock(ctx.elapsed_seconds)
        self.state_cache: Any | None = None

        self.transport: Transport | None = None
        self.client: ClientInfo = ClientInfo()
        self.connections = 0
        self.started = ctx.status in (SessionStatus.LIVE, SessionStatus.PAUSED)
        self.was_live_before = self.started
        self._start_requested = False
        # Begin was pressed but the engine deferred the start (profile still being generated).
        self._awaiting_ready = False
        self._last_state_reason: str | None = None
        self._last_engine_tick = 0.0
        # Closing in progress (closing utterance seen or engine announced session.closing):
        # candidate speech must not cancel the goodbye or lose the end of the session.
        self._closing_seen = False
        self._closing_reason: str | None = None
        self.closed = False
        self.ending: SessionEndReason | None = None
        self.ended = False
        self.paused_reason: str | None = None  # "disconnect" | "policy"

        self._out: asyncio.Queue[_OutItem] = asyncio.Queue()
        self._last_sent_seq = 0
        self._generation = 0
        self._writer: asyncio.Task[None] | None = None
        self._jobs: list[FloorJob] = []
        self._job_order = 0
        self._job_event = asyncio.Event()
        self._current_job: FloorJob | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._floor_task: asyncio.Task[None] | None = None
        self._tick_task: asyncio.Task[None] | None = None
        self._bus_task: asyncio.Task[None] | None = None
        self._grace_task: asyncio.Task[None] | None = None
        self._linger_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._attach_lock = asyncio.Lock()

        self._capture: TurnCapture | None = None
        self._unresolved: TurnCapture | None = None
        self._unresolved_task: asyncio.Task[None] | None = None
        self.utterances: dict[str, ActiveUtterance] = {}
        self._last_engine_utt: ActiveUtterance | None = None
        self._last_ack: str | None = None
        self._fillers = 0
        self._listening_since: float | None = None
        self._silence_prompts = 0
        self._time_warned = False
        self._time_up_sent = False
        self._max_time_sent = False
        self._last_rx = time.monotonic()
        self._last_audio_rx: float | None = None
        self._last_heartbeat_db = 0.0
        self._audio_seq: int | None = None
        self.audio_gaps = 0
        self.audio_frames = 0
        self.local_vad_hints = 0
        self._stt_degraded_notified = False
        self._stt_failed_speech_ms = 0  # voiced audio Whisper failed on since the last good final
        self._pause_warned = False
        self.latency: list[dict[str, Any]] = []
        self._drive_capture: TurnCapture | None = None
        self._deferred: list[ActiveUtterance] = []
        self._state_dirty = False
        self._engine_spoke = False  # the engine produced an utterance in this runtime
        self.glossary = Glossary(ctx.glossary_terms or ctx.vocabulary)
        self._unclear_prompts = 0  # consecutive "didn't catch that" prompts
        # Intake (INTAKE_INTERVIEW.md 7): the current activity and the answer mode of the last delivered question.
        self.activity: RuntimeActivity | None = None
        self._answer_mode = "standard"
        self._base_endpoint = self.endpointer.params
        self._long_endpoint = replace(self._base_endpoint, **LONG_ANSWER_ENDPOINT)

    # ================================================================ start/stop
    async def start(self) -> None:
        await self.outbox.init()
        self._last_sent_seq = self.outbox.seq
        self._writer = asyncio.create_task(self._writer_loop(), name=f"realtime.writer.{self.session_id}")
        self._floor_task = asyncio.create_task(self._floor_loop(), name=f"realtime.floor.{self.session_id}")
        self._tick_task = asyncio.create_task(self._tick_loop(), name=f"realtime.tick.{self.session_id}")
        self._bus_task = asyncio.create_task(self._bus_loop(), name=f"realtime.bus.{self.session_id}")
        self.lock.start_refresh(self._on_lock_lost)
        self.ack_cache.warm_in_background(get_ai().tts, self.voice)
        if not self.started and self.settings.acknowledgment_enabled:
            # Fill the TTS service cache with likely first sentences until "Begin".
            self.ack_cache.warm_openers(
                get_ai().tts,
                self.voice,
                opener_phrases(self.ctx.candidate_first_name),
                stop=lambda: self.started or self.closed,
            )
        await self._refresh_state_cache()
        # The engine's state is authoritative for "was this interview already live?"
        # (the Postgres status column may lag behind the hot copy).
        engine_status = getattr(self.state_cache, "status", None)
        if (
            engine_status in (SessionStatus.LIVE, SessionStatus.PAUSED)
            or self.outbox.seq > 0
            and self.ctx.started_at
        ):
            self.started = self.was_live_before = True
        metrics.ACTIVE_SESSIONS.inc()
        log.info(
            "realtime.runtime.started",
            session_id=self.session_id,
            status=self.ctx.status.value,
            seq=self.outbox.seq,
        )

    def track(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        return self.track(
            asyncio.create_task(self._guard(coro, name), name=f"realtime.{name}.{self.session_id}")
        )

    async def _guard(self, coro: Awaitable[Any], name: str) -> Any:
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            metrics.ERRORS.labels(component="realtime").inc()
            log.error(
                "realtime.task_failed",
                task=name,
                session_id=self.session_id,
                error=f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )
            return None

    async def aclose(self, reason: str = "closed", *, close_code: int | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        log.info("realtime.runtime.closing", session_id=self.session_id, reason=reason)
        transport = self.transport
        if transport is not None and close_code is not None:
            await self.outbox.flush(timeout=0.5)
            await self._drain_writer(timeout=1.0)
            await transport.close(close_code)
        self.transport = None
        current = asyncio.current_task()
        for task in (
            self._floor_task,
            self._current_task,
            self._tick_task,
            self._bus_task,
            self._grace_task,
            self._linger_task,
            self._unresolved_task,
        ):
            if task is not None and task is not current and not task.done():
                task.cancel()
        for task in list(self._tasks):
            if task is not current and not task.done():
                task.cancel()
        for utt in list(self.utterances.values()):
            if utt.speech is not None:
                with contextlib.suppress(Exception):
                    await utt.speech.cancel()
        if self._capture is not None:
            await self._capture.transcriber.aclose()
        await self.tts_health.aclose()
        if self._writer is not None and self._writer is not current:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer
        await self.outbox.aclose()
        await self.lock.release()
        metrics.ACTIVE_SESSIONS.dec()
        if self._on_closed is not None:
            with contextlib.suppress(Exception):
                await _maybe_await(self._on_closed(self))

    async def _on_lock_lost(self) -> None:
        log.warning("realtime.runtime.lock_lost", session_id=self.session_id)
        self.send_error(
            "session_active_elsewhere", "This interview was opened in another window.", recoverable=False
        )
        await self.aclose("lock_lost", close_code=close_codes.ACTIVE_ELSEWHERE)

    # ================================================================== sending
    def post(self, message: _ServerMsg, *, binary: bytes | None = None) -> asyncio.Future[None]:
        """Sequence, record and enqueue a server message (ordered, non-blocking)."""
        seq = self.outbox.next_seq()
        message.seq = seq
        payload = message.model_dump_json()
        self.outbox.record(seq, payload, msg_type=getattr(message, "type", ""))
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        if self.transport is None or self.closed:
            future.set_result(None)
            return future
        if self._out.qsize() >= SEND_QUEUE_LIMIT:
            log.warning("realtime.send_queue_overflow", session_id=self.session_id)
            transport = self.transport
            self.spawn(transport.close(close_codes.INTERNAL_ERROR), "close_slow_client")
            future.set_result(None)
            return future
        self._out.put_nowait(_OutItem(seq=seq, text=payload, binary=binary, future=future))
        return future

    async def send(self, message: _ServerMsg) -> None:
        await self.post(message)

    def send_error(self, code: str, message: str, *, recoverable: bool = True) -> None:
        self.post(ErrorMsg(code=code, message=message, recoverable=recoverable))

    def notice(self, code: str, message: str, level: str = "info") -> None:
        self.post(NoticeMsg(level=level, code=code, message=message))  # type: ignore[arg-type]

    async def send_tts_chunk(self, message: TtsChunkMsg, pcm16: bytes, *, final: bool) -> None:
        frame = pack_frame(0x02, message.chunk_id, pcm16, final=final)
        await self.post(message, binary=frame)

    async def send_tts_fallback(self, message: TtsFallbackMsg) -> None:
        await self.post(message)

    async def _writer_loop(self) -> None:
        while True:
            item = await self._out.get()
            try:
                transport = self.transport
                generation = self._generation
                if transport is None:
                    continue
                if item.replay_after is not None:
                    for raw in await self.outbox.replay_after(item.replay_after):
                        seq = _seq_from_json(raw)
                        if seq <= self._last_sent_seq or generation != self._generation:
                            continue
                        await transport.send_text(raw)
                        if generation == self._generation:
                            self._last_sent_seq = seq
                    continue
                if item.seq <= self._last_sent_seq:
                    continue
                if item.text is not None:
                    await transport.send_text(item.text)
                if item.binary is not None:
                    await transport.send_bytes(item.binary)
                if generation == self._generation:
                    self._last_sent_seq = item.seq
            except Exception as exc:  # noqa: BLE001 - socket gone: the receive loop will detach
                log.debug("realtime.send_failed", session_id=self.session_id, error=type(exc).__name__)
            finally:
                if item.future is not None and not item.future.done():
                    item.future.set_result(None)
                self._out.task_done()

    def _reset_send_queue(self) -> None:
        """Drop items queued for a previous connection (the replay covers them)."""
        while True:
            try:
                item = self._out.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item.future is not None and not item.future.done():
                item.future.set_result(None)
            self._out.task_done()

    async def _drain_writer(self, timeout: float = 1.0) -> None:  # noqa: ASYNC109
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._out.join(), timeout)

    # ================================================================ connection
    async def attach(self, transport: Transport, hello: HelloMsg) -> bool:
        """Bind a new connection; returns False when it was refused (and closed)."""
        async with self._attach_lock:
            if self.closed:
                raise RuntimeClosed()
            if hello.protocol != PROTOCOL_VERSION:
                await self._refuse(
                    transport,
                    "unsupported_protocol",
                    "Please reload the page to update the interview app.",
                    close_codes.BAD_REQUEST,
                )
                return False
            if hello.client.sample_rate != MIC_SAMPLE_RATE:
                await self._refuse(
                    transport,
                    "unsupported_sample_rate",
                    "Microphone audio must be 16 kHz PCM16.",
                    close_codes.BAD_REQUEST,
                )
                return False
            # Reconnects / reloads only count once the interview has really begun
            # (the engine went live): the system-check probe, reloading the waiting
            # room, or reconnecting while "Begin" is still being prepared are harmless.
            await self._refresh_state_cache()
            reconnect = self._interview_began()
            page_reload = reconnect and hello.last_seq == 0 and self.outbox.seq > 0
            client_info = hello.client.model_dump(mode="json")
            try:
                count = await record_connection(
                    uuid.UUID(self.session_id), reconnect=reconnect, client_info=client_info
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("realtime.record_connection_failed", error=type(exc).__name__)
                count = self.ctx.reconnect_count
            self.ctx.reconnect_count = count
            # Refusals happen before touching the current connection (a refused
            # second tab must not kill the working one).
            if reconnect and count > self.ctx.policy.max_reconnects:
                # The integrity service derives unusual_disconnect_pattern from reconnects.
                await self.integrity_bridge.on_server_event(
                    IntegrityEventType.RECONNECT, {"reconnects": count, "refused": True}
                )
                await self._refuse(
                    transport,
                    "too_many_reconnects",
                    "This interview has been interrupted too many times. The recruiter has been notified.",
                    close_codes.TOO_MANY_RECONNECTS,
                )
                return False
            if page_reload:
                await self.integrity_bridge.on_server_event(
                    IntegrityEventType.PAGE_RELOAD, {"detected_by": "server", "reconnects": count}
                )
                if not self.ctx.policy.allow_page_reload_resume:
                    await self._refuse(
                        transport,
                        "page_reload_not_allowed",
                        "Reloading the page is not allowed for this interview. The recruiter has been notified.",
                        close_codes.SESSION_ENDED,
                    )
                    return False
            elif reconnect:
                await self.integrity_bridge.on_server_event(
                    IntegrityEventType.RECONNECT, {"last_seq": hello.last_seq, "reconnects": count}
                )
            previous = self.transport
            if previous is not None and previous is not transport:
                self.transport = None
                await previous.close(close_codes.REPLACED)

            if self._grace_task is not None:
                self._grace_task.cancel()
                self._grace_task = None
            self.client = hello.client
            self.speech.client_tts_mode = hello.client.tts_mode
            self._last_rx = time.monotonic()
            self._audio_seq = None
            self.connections += 1
            self._reset_send_queue()
            self._generation += 1
            self.transport = transport
            if hello.last_seq > self.outbox.seq:
                # Redis lost the counter (or the client saw a newer runtime): new
                # messages must stay above what the client has, or it drops them.
                log.warning(
                    "realtime.client_seq_ahead",
                    session_id=self.session_id,
                    client=hello.last_seq,
                    server=self.outbox.seq,
                )
                self.outbox.seq = hello.last_seq
            self._last_sent_seq = max(0, min(hello.last_seq, self.outbox.seq))
            self._out.put_nowait(_OutItem(seq=0, text=None, replay_after=self._last_sent_seq))
            await self._refresh_state_cache()
            self.post(await self.build_state())
            log.info(
                "realtime.attached",
                session_id=self.session_id,
                connection=self.connections,
                reconnect=reconnect,
                last_seq=hello.last_seq,
                seq=self.outbox.seq,
            )
            if self.ended:
                self.post(SessionEndedMsg(reason=(self.ending or SessionEndReason.CANDIDATE_ENDED).value))
                return True
            if self.paused_reason == "policy":
                self.notice(
                    "interview_paused",
                    "The interview is paused. Resolve the issue and press continue.",
                    "warning",
                )
                return True
            if self.started or self.was_live_before:
                self._enqueue(
                    "resume", lambda: self._run_resume(reconnect=True), PRIORITY_CONTROL, needs_idle=False
                )
            else:
                self.turns.to(TurnState.IDLE, "connected")
            return True

    async def _refuse(self, transport: Transport, code: str, message: str, close_code: int) -> None:
        with contextlib.suppress(Exception):
            await transport.send_text(
                ErrorMsg(code=code, message=message, recoverable=False).model_dump_json()
            )
        await transport.close(close_code)
        log.info("realtime.connection_refused", session_id=self.session_id, code=code)

    async def detach(self, transport: Transport, *, reason: str = "disconnect") -> None:
        async with self._attach_lock:
            if transport is not self.transport or self.closed:
                return
            self.transport = None
            log.info("realtime.detached", session_id=self.session_id, reason=reason)
            if self.ended:
                await self.aclose("ended_disconnect")
                return
            if not self.started:
                # A Begin deferred while the interview was being prepared must not start it with nobody here
                # (on session.ready or the start retry): the candidate presses Begin again on reconnect.
                self._awaiting_ready = False
                self._start_requested = False
                self._schedule_grace()
                return
            if not self._interview_began():
                # "Begin" was pressed but the engine has not started the interview yet
                # (still preparing): nothing was said, so this is not a disconnect from
                # the interview - drop the pending start; the candidate presses Begin again.
                await self._interrupt_current("disconnect_before_start", notify_client=False)
                self.started = False
                self._start_requested = False
                self._awaiting_ready = False
                self.clock.pause()
                self.turns.to(TurnState.IDLE, "disconnect_before_start")
                self._schedule_grace()
                return
            job = self._current_job
            keep_running = (
                job is not None
                and job.kind in ("turn", "typed_turn")
                and job.capture is not None
                and not job.capture.engine_called
            )
            if not keep_running:
                await self._interrupt_current("disconnect", notify_client=False)
            await self._activity_detached()
            await self._discard_capture()
            self.paused_reason = "disconnect"
            self.turns.to(TurnState.PAUSED, "disconnect")
            self.clock.pause()
            self.spawn(self._engine_pause("disconnect"), "engine_pause")
            self.spawn(
                self.integrity_bridge.on_server_event(IntegrityEventType.DISCONNECT, {"reason": reason}),
                "integrity_disconnect",
            )
            self._schedule_grace()

    def _schedule_grace(self) -> None:
        if self._grace_task is not None and not self._grace_task.done():
            return

        async def grace() -> None:
            await asyncio.sleep(self.settings.ws_disconnect_grace_seconds)
            if self.transport is None and not self.closed:
                log.info("realtime.grace_expired", session_id=self.session_id)
                await self.aclose("grace_expired")

        self._grace_task = asyncio.create_task(grace(), name=f"realtime.grace.{self.session_id}")

    async def shutdown(self) -> None:
        """Process shutdown: tell the client to reconnect elsewhere, pause, release."""
        if self.closed:
            return
        if self.transport is not None:
            self.notice("server_restarting", "Reconnecting you to the interview...", "warning")
        if self.started and not self.ended:
            await self._interrupt_current("shutdown", notify_client=False)
            self.clock.pause()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._engine_pause("shutdown"), 3.0)
        await self.aclose("shutdown", close_code=close_codes.SERVICE_RESTART)

    async def _engine_pause(self, reason: str) -> None:
        pause = getattr(self.engine, "pause", None) or getattr(self.engine, "pause_session", None)
        if pause is None:
            if not self._pause_warned:
                self._pause_warned = True
                log.warning("realtime.engine_pause_unsupported", session_id=self.session_id)
            return
        await _maybe_await(pause(self.session_id, reason=reason))

    # ================================================================== state
    async def _refresh_state_cache(self) -> None:
        try:
            state = await _maybe_await(self.engine.load_state(self.session_id))
        except Exception as exc:  # noqa: BLE001
            log.debug("realtime.load_state_failed", session_id=self.session_id, error=type(exc).__name__)
            return
        if state is None:
            return
        self.state_cache = state
        try:
            running = getattr(state, "last_resumed_at", None) is not None and self.transport is not None
            self.clock.sync(float(state.elapsed_seconds()), running and self.started)
        except Exception as exc:  # noqa: BLE001 - engine state without a clock
            log.debug("realtime.clock_sync_failed", error=type(exc).__name__)

    def _interview_began(self) -> bool:
        """The interview really started: it was live before this runtime, the
        engine's state says so, or the engine already spoke here (pressing
        "Begin" alone is not enough - the start may still be deferred)."""
        if self.was_live_before or self._engine_spoke:
            return True
        status = getattr(self.state_cache, "status", None)
        return status in (SessionStatus.LIVE, SessionStatus.PAUSED) or self.ended

    def _time_limits(self) -> tuple[float, float]:
        state = self.state_cache
        limit = float(getattr(state, "time_limit_s", 0) or self.ctx.time_limit_s)
        max_time = float(getattr(state, "max_time_s", 0) or self.ctx.max_time_s)
        return limit, max(max_time, limit)

    def _status(self) -> SessionStatus:
        if self.ended:
            return SessionStatus.COMPLETING
        if self.paused_reason:
            return SessionStatus.PAUSED
        state = self.state_cache
        status = getattr(state, "status", None)
        if isinstance(status, SessionStatus):
            if status is SessionStatus.PAUSED and self.transport is not None and self.started:
                return SessionStatus.LIVE
            return status
        if self.started:
            return SessionStatus.LIVE
        return self.ctx.status

    async def build_state(self, *, coding: CodingStateMsg | None = None) -> SessionStateMsg:
        state = self.state_cache
        limit, _ = self._time_limits()
        elapsed = self.clock.elapsed()
        coding_payload: CodingStateMsg | None = coding
        coding_id = self.coding_bridge.coding_session_id
        if (
            coding_id is None
            and state is not None
            and getattr(getattr(state, "coding", None), "active", False)
        ):
            coding_id = state.coding.coding_session_id
        if coding_payload is None and coding_id and self.coding is not None:
            try:
                coding_payload = await self.coding.state_payload(coding_id)
                if not self.coding_bridge.active:
                    self.coding_bridge.started(coding_payload)
            except Exception as exc:  # noqa: BLE001
                log.warning("realtime.coding_state_failed", error=type(exc).__name__)
        current_question = None
        question_count = 0
        segment = None
        interviewer = self.ctx.policy.interviewer_name or self.settings.interviewer_name
        if state is not None:
            with contextlib.suppress(Exception):
                q = state.current_question()
                current_question = q.text if q is not None else None
            question_count = len(getattr(state, "questions", []) or [])
            segment = getattr(state, "current_segment", None)
            interviewer = getattr(state, "interviewer_name", None) or interviewer
        return SessionStateMsg(
            session_id=self.session_id,
            status=self._status(),
            turn_state=self.turns.state,
            round_name=self.ctx.round_name,
            round_type=self.ctx.round_type,
            interviewer_name=interviewer,
            elapsed_s=round(elapsed, 1),
            remaining_s=round(max(0.0, limit - elapsed), 1),
            time_limit_s=limit,
            segment=segment,
            question_count=question_count,
            current_question=current_question,
            show_subtitles=self.ctx.policy.show_subtitles,
            allow_typed_answers=self.ctx.policy.allow_typed_answers,
            integrity=self.ctx.policy.integrity_flags(),
            coding=coding_payload,
            avatar_state=AVATAR_FOR_TURN.get(self.turns.state, AvatarState.IDLE),
            intake=self._intake_state(state),
        )

    def _intake_state(self, state: Any) -> IntakeStateMsg | None:
        """``session.state.intake`` (7.10): stage, candidate-safe titles, statuses and a content-free activity
        snapshot (remaining time from the runtime's authoritative deadline)."""
        ik = getattr(state, "intake", None)
        if ik is None or not getattr(ik, "stages", None):
            return None
        from app.services.intake.stages import CANDIDATE_TITLES

        current = ik.stage
        stages = []
        for index, stage in enumerate(ik.stages):
            if stage.status == "completed":
                status = "done"
            elif stage.status == "incomplete":
                status = "incomplete"
            elif index == ik.stage_index:
                status = "current"
            else:
                status = "pending"
            stages.append(
                IntakeStageMsg(
                    key=stage.key, number=stage.number, title=CANDIDATE_TITLES.get(stage.key, stage.key), status=status
                )
            )
        snapshot = None
        act = self.activity
        if act is not None and act.status != "ended":
            remaining = None
            if act.status == "running" and act.deadline is not None:
                remaining = max(0, int((act.deadline - self.clock.elapsed()) * 1000))
            snapshot = ActivitySnapshotMsg(
                activity_id=act.activity_id,
                kind=act.kind,  # type: ignore[arg-type]
                phase=act.phase,  # type: ignore[arg-type]
                status="running" if act.status == "running" else "armed",
                remaining_ms=remaining,
            )
        elif ik.activity is not None and ik.activity.status in ("armed", "running"):
            snapshot = ActivitySnapshotMsg(
                activity_id=ik.activity.activity_id,
                kind=ik.activity.kind,
                phase=ik.activity.phase,
                status="armed",
                remaining_ms=None,
            )
        key = current.key if current is not None else ik.stages[0].key
        return IntakeStateMsg(
            stage=key,
            stage_number=current.number if current is not None else 1,
            stage_title=CANDIDATE_TITLES.get(key, key),
            stages=stages,
            activity=snapshot,
            answer_mode="long" if ik.answer_mode == "long" else "standard",
            soft_limit_s=ik.soft_limit_s,
        )

    async def push_state(self, reason: str, *, coding: CodingStateMsg | None = None) -> None:
        await self._refresh_state_cache()
        self.post(await self.build_state(coding=coding))
        log.debug("realtime.state_pushed", session_id=self.session_id, reason=reason)

    def _on_turn_change(self, state: TurnState, previous: TurnState, reason: str | None) -> None:
        self.post(TurnStateMsg(state=state, reason=reason))
        if state is TurnState.LISTENING:
            self._listening_since = time.monotonic()
        elif state is not TurnState.CODING:
            self._listening_since = None
        if state in (TurnState.LISTENING, TurnState.CODING):
            self._job_event.set()

    def avatar(
        self,
        state: AvatarState,
        *,
        expression: AvatarExpression = AvatarExpression.NEUTRAL,
        gesture: AvatarGesture = AvatarGesture.NONE,
        utterance_id: str | None = None,
    ) -> None:
        self.post(
            AvatarBehaviorMsg(utterance_id=utterance_id, state=state, expression=expression, gesture=gesture)
        )

    async def _on_tts_health(self, degraded: bool, reason: str | None) -> None:
        if degraded:
            self.notice(
                "tts_degraded",
                "The interviewer's voice is temporarily unavailable; continuing with your browser's voice or captions.",
                "warning",
            )
        else:
            self.notice("tts_recovered", "The interviewer's voice is back.", "info")

    # ============================================================= floor queue
    def _enqueue(
        self,
        kind: str,
        run: Callable[[], Awaitable[None]],
        priority: int,
        *,
        coalesce: str | None = None,
        capture: TurnCapture | None = None,
        needs_idle: bool = True,
    ) -> None:
        if self.closed or self.ended and kind not in ("end",):
            return
        if coalesce is not None:
            self._jobs = [j for j in self._jobs if j.coalesce != coalesce]
        self._job_order += 1
        self._jobs.append(FloorJob(priority, self._job_order, kind, run, coalesce, capture, needs_idle))
        self._jobs.sort()
        self._job_event.set()

    def _floor_idle(self) -> bool:
        return (
            self._capture is None
            and self.paused_reason is None
            and self.turns.state in (TurnState.LISTENING, TurnState.CODING, TurnState.IDLE)
        )

    def _pick_job(self) -> FloorJob | None:
        idle = self._floor_idle()
        for job in self._jobs:
            if not job.needs_idle or idle:
                if self.paused_reason is not None and job.kind not in ("resume", "end", "turn", "typed_turn"):
                    continue
                self._jobs.remove(job)
                return job
        return None

    async def _floor_loop(self) -> None:
        while not self.closed:
            job = self._pick_job()
            if job is None:
                self._job_event.clear()
                await self._job_event.wait()
                continue
            task = asyncio.create_task(self._run_job(job), name=f"realtime.job.{job.kind}.{self.session_id}")
            self._current_job, self._current_task = job, task
            await asyncio.wait({task})
            if self._current_task is task:
                self._current_job, self._current_task = None, None

    async def _run_job(self, job: FloorJob) -> None:
        try:
            await job.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the interview going
            metrics.ERRORS.labels(component="realtime_floor").inc()
            log.error(
                "realtime.job_failed",
                kind=job.kind,
                session_id=self.session_id,
                error=f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )
            if not self.ended and self.transport is not None:
                self.notice(
                    "interviewer_delay",
                    "Sorry, something went wrong on our side. Please continue.",
                    "warning",
                )
                self._go_idle("job_failed")

    async def _interrupt_current(self, reason: str, *, notify_client: bool = True) -> ActiveUtterance | None:
        """Cancel the current floor job (engine iterator + TTS). Returns the
        engine utterance that was being spoken, already reported to the engine
        via ``handle_interruption`` when ``reason`` is not a barge-in."""
        task = self._current_task
        utt = self._speaking_utterance()
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._current_task is task:
            self._current_job, self._current_task = None, None
        for other in list(self.utterances.values()):
            if not other.ended or not other.delivered:
                if other.speech is not None:
                    await other.speech.cancel()
                if other.delivery_task is not None and not other.delivery_task.done():
                    other.delivery_task.cancel()
        if utt is not None and not utt.interrupted:
            utt.interrupted = True
            if notify_client:
                self.post(BargeInMsg(utterance_id=utt.utterance_id))
                self.post(UtteranceEndMsg(utterance_id=utt.utterance_id, text=utt.text, interrupted=True))
            if reason != "barge_in" and not utt.is_ack:
                await self._report_interruption(utt, wait_report=False)
        return utt

    def _speaking_utterance(self) -> ActiveUtterance | None:
        """The utterance the candidate may be hearing now: the latest engine
        utterance not yet delivered (playback not reported finished), else an
        acknowledgement still inside its playback window."""
        now = time.monotonic()
        utt = self._last_engine_utt
        if utt is not None and not utt.delivered and not utt.interrupted and not utt.finished.is_set():
            return utt
        for candidate in reversed(list(self.utterances.values())):
            if (
                candidate.is_ack
                and not candidate.interrupted
                and not candidate.finished.is_set()
                and now <= candidate.estimated_end() + 0.5
            ):
                return candidate
        return None

    async def _report_interruption(
        self, utt: ActiveUtterance, *, wait_report: bool
    ) -> InterruptedUtterance | None:
        if utt.is_ack or utt.offline and not utt.text:
            return None
        if wait_report and not utt.playback_reported:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(utt.interrupted_evt.wait(), INTERRUPTION_REPORT_WAIT_S)
        played = utt.played_ms if utt.playback_reported else utt.estimated_played_ms()
        chunks = utt.speech.chunks if utt.speech is not None else []
        spoken, _next = spoken_portion(chunks, played)
        interrupted = InterruptedUtterance(
            utterance_id=utt.utterance_id,
            question_id=utt.question_id,
            full_text=utt.text,
            spoken_text=spoken,
            played_ms=int(played),
            text_complete=utt.ended,
        )
        try:
            await _maybe_await(self.engine.handle_interruption(self.session_id, interrupted))
        except Exception as exc:  # noqa: BLE001
            metrics.ERRORS.labels(component="realtime_engine").inc()
            log.warning("realtime.handle_interruption_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        log.info(
            "realtime.interruption",
            session_id=self.session_id,
            utterance_id=utt.utterance_id,
            played_ms=played,
            spoken_chars=len(spoken),
        )
        return interrupted

    def _go_idle(self, reason: str) -> None:
        if self.ended or self.closed or self.paused_reason is not None:
            return
        target = TurnState.CODING if self.coding_bridge.active else TurnState.LISTENING
        if self._capture is not None:
            return
        if self.turns.state is not target:
            self.turns.to(target, reason)
            self.avatar(AvatarState.LISTENING)

    # ================================================================== engine
    async def _drive(
        self,
        source: Any,
        *,
        origin: str,
        turn_end_at: float | None = None,
        capture: TurnCapture | None = None,
    ) -> int:
        """Consume one engine event stream and speak it; returns the number of utterances produced."""
        iterator: Any = None
        utt: ActiveUtterance | None = None
        first_event_logged = False
        first_text_logged = False
        produced: list[ActiveUtterance] = []
        self._drive_capture = capture
        try:
            iterator = await _as_iterator(source)
            async for event in iterator:
                if capture is not None and capture.end_at is not None:
                    if not first_event_logged:
                        first_event_logged = True
                        self._latency_note(
                            capture, engine_first_event_ms=round((time.monotonic() - capture.end_at) * 1000)
                        )
                    if not first_text_logged and (
                        isinstance(event, SpeechDelta)
                        and event.text
                        or isinstance(event, UtteranceBegin)
                        and event.text
                    ):
                        first_text_logged = True  # model time to first spoken text
                        capture.reply_text = True
                        self._latency_note(
                            capture, first_delta_ms=round((time.monotonic() - capture.end_at) * 1000)
                        )
                if isinstance(event, UtteranceBegin):
                    if event.kind == "closing":
                        self._closing_seen = True
                    if utt is not None:
                        await self._end_utterance(utt)
                    utt = self._begin_utterance(event, turn_end_at=turn_end_at if not produced else None)
                    produced.append(utt)
                elif isinstance(event, SpeechDelta):
                    if utt is None or utt.ended:
                        utt = self._begin_utterance(
                            UtteranceBegin(
                                utterance_id=event.utterance_id or _new_id("utt"),
                                kind="question",
                                behavior=BehaviorSpec(),
                            ),
                            turn_end_at=turn_end_at if not produced else None,
                        )
                        produced.append(utt)
                    self._feed(utt, event.text)
                elif isinstance(event, UtteranceMeta):
                    target = utt if utt is not None and not utt.ended else None
                    if target is None:
                        continue
                    target.question_id = event.question_id or target.question_id
                    if event.behavior != target.behavior:
                        target.behavior = event.behavior
                        self.post(_behavior_msg(event.behavior, target.utterance_id))
                    streamed = " ".join((target.speech.text if target.speech else "").split())
                    full = " ".join((event.full_text or "").split())
                    if full and not streamed:
                        self._feed(target, full)
                    elif full and full.startswith(streamed) and len(full) > len(streamed):
                        self._feed(target, full[len(streamed) :])
                    target.full_text = full or streamed
                    intake_meta = (event.decision or {}).get("intake") if isinstance(event.decision, dict) else None
                    if isinstance(intake_meta, dict):
                        target.answer_mode = str(intake_meta.get("answer_mode") or "standard")
                    await self._end_utterance(target)
                    utt = None
                elif isinstance(event, CodingStarted):
                    self.coding_bridge.started(event.coding)
                    self.post(CodingStartMsg(coding=event.coding))
                elif isinstance(event, CodingHintIssued):
                    self.post(
                        CodingHintMsg(
                            coding_session_id=event.coding_session_id,
                            level=event.level,
                            text=event.text,
                            hints_used=event.hints_used,
                            hints_remaining=event.hints_remaining,
                        )
                    )
                elif isinstance(event, CodingEnded):
                    self.coding_bridge.ended(event.coding_session_id)
                    self.post(CodingEndMsg(coding_session_id=event.coding_session_id, reason=event.reason))
                elif isinstance(event, ActivityStarted):
                    self._on_activity_started(event)
                elif isinstance(event, ActivityPhaseChanged):
                    self._on_activity_phase_changed(event)
                elif isinstance(event, ActivityEnded):
                    self._on_activity_ended(event)
                elif isinstance(event, SessionClosing):
                    self.ending = event.reason
                elif isinstance(event, StateChanged):
                    # No engine call while its iterator is open: post from the
                    # cache now, reload once the stream is finished.
                    self._last_state_reason = event.reason
                    self._state_dirty = True
                    self.post(await self.build_state())
            if utt is not None and not utt.ended:
                await self._end_utterance(utt)
                utt = None
        except asyncio.CancelledError:
            if iterator is not None:
                await asyncio.shield(_aclose(iterator))
            if utt is not None and utt.speech is not None:
                await asyncio.shield(utt.speech.cancel())
            raise
        finally:
            self._drive_capture = None
            deferred, self._deferred = self._deferred, []
            for offline in deferred:
                await asyncio.shield(self._report_interruption(offline, wait_report=False))
        if self._state_dirty:
            self._state_dirty = False
            await self.push_state("engine_state_changed")
        if self.ending is None and self._closing_reason is not None:
            self.ending = _end_reason(self._closing_reason)
        # Wait for every utterance to be delivered (playback finished or estimated).
        pending = [
            u.delivery_task for u in produced if u.delivery_task is not None and not u.delivery_task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self.ending is not None:
            await self._finalize_end(self.ending)
            return len(produced)
        if self.transport is None:
            return len(produced)
        self._go_idle(f"{origin}_done")
        return len(produced)

    def _begin_utterance(self, event: UtteranceBegin, *, turn_end_at: float | None) -> ActiveUtterance:
        offline = self.transport is None
        utt = ActiveUtterance(
            utterance_id=event.utterance_id or _new_id("utt"),
            kind=event.kind,
            behavior=event.behavior,
            question_id=event.question_id,
            offline=offline,
        )
        if not offline:
            capture = self._drive_capture if turn_end_at is not None else None
            hook = None
            if capture is not None and capture.end_at is not None:
                end_at = capture.end_at

                def hook(sent_at: float, capture: TurnCapture = capture, end_at: float = end_at) -> None:
                    capture.replied = True
                    if self.settings.integrity_frames_yield_to_replies:
                        self.spawn(reply_window.release(get_redis(), self.session_id), "integrity_reply_release")
                    fields: dict[str, Any] = {"first_audio_ms": round((sent_at - end_at) * 1000)}
                    timings = utt.speech.timings if utt.speech is not None else None
                    if timings is not None and timings.first_job_at is not None:
                        fields["first_chunk_ready_ms"] = round((timings.first_job_at - end_at) * 1000)
                        fields["first_chunk_chars"] = timings.first_job_chars
                        if timings.first_synth_ms is not None:
                            fields["tts_first_ms"] = round(timings.first_synth_ms)
                    self._latency_note(capture, **fields)

            utt.speech = self.speech.start(
                utt.utterance_id, begun_at=utt.begun_at, turn_end_at=turn_end_at, on_first_audio=hook
            )
        self.utterances[utt.utterance_id] = utt
        self._trim_utterances()
        self._last_engine_utt = utt
        self._engine_spoke = True
        self.post(
            UtteranceStartMsg(
                utterance_id=utt.utterance_id,
                kind=event.kind,
                question_id=event.question_id,
                behavior=_behavior_msg(event.behavior, utt.utterance_id),
            )
        )
        if not offline and self.paused_reason is None:
            self.turns.to(TurnState.AI_SPEAKING, event.kind)
        if event.text:
            self._feed(utt, event.text)
        return utt

    def _feed(self, utt: ActiveUtterance, text: str) -> None:
        if not text:
            return
        if utt.speech is not None:
            utt.speech.feed(text)
        else:
            utt.full_text = (utt.full_text + text) if utt.full_text else text
        if self.ctx.policy.show_subtitles:
            self.post(TextDeltaMsg(utterance_id=utt.utterance_id, text=text))

    async def _end_utterance(self, utt: ActiveUtterance) -> None:
        if utt.ended:
            return
        if utt.speech is not None:
            await utt.speech.finish()
        utt.ended = True
        utt.ended_at = time.monotonic()
        if not utt.full_text:
            utt.full_text = utt.text
        self.post(UtteranceEndMsg(utterance_id=utt.utterance_id, text=utt.full_text, interrupted=False))
        if utt.offline:
            # Nobody heard it: tell the engine (after the stream closes) so the
            # resume re-speaks it.
            utt.interrupted = True
            self._deferred.append(utt)
            return
        utt.delivery_task = asyncio.create_task(
            self._await_delivery(utt), name=f"realtime.delivery.{utt.utterance_id}"
        )
        self.track(utt.delivery_task)

    async def _await_delivery(self, utt: ActiveUtterance) -> None:
        while not utt.finished.is_set() and not utt.interrupted:
            timeout = utt.estimated_end() + DELIVERY_GRACE_S - time.monotonic()
            if timeout <= 0:
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(utt.finished.wait(), min(timeout, 1.0))
        if utt.interrupted:
            return
        utt.delivered = True
        try:
            await _maybe_await(
                self.engine.mark_utterance_delivered(
                    self.session_id, utt.utterance_id, spoken_text=utt.full_text
                )
            )
        except Exception as exc:  # noqa: BLE001
            metrics.ERRORS.labels(component="realtime_engine").inc()
            log.warning("realtime.mark_delivered_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        self._on_delivered(utt)

    def _trim_utterances(self) -> None:
        if len(self.utterances) <= 64:
            return
        now = time.monotonic()
        for key in list(self.utterances)[: len(self.utterances) - 64]:
            utt = self.utterances[key]
            if utt.delivered or utt.interrupted or (utt.is_ack and now > utt.estimated_end() + 5):
                del self.utterances[key]

    # --------------------------------------------------------------- jobs
    async def _run_start(self) -> None:
        if self.started:
            return
        self.started = True
        self.clock.start()
        self.turns.to(TurnState.PROCESSING, "starting")
        self.avatar(AvatarState.THINKING)
        # Tell the client immediately that the interview is live (the intro may take seconds to speak).
        await self.push_state("starting")
        self._last_state_reason = None
        try:
            spoken = await self._drive(self.engine.start(self.session_id), origin="start")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - let the candidate retry "Begin interview"
            metrics.ERRORS.labels(component="realtime_engine").inc()
            log.error(
                "realtime.start_failed",
                session_id=self.session_id,
                error=f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )
            self.started = False
            self._start_requested = False
            self.clock.pause()
            self.turns.to(TurnState.IDLE, "start_failed")
            self.send_error(
                "start_failed",
                "The interview could not start. Please try again in a moment.",
                recoverable=True,
            )
            return
        if spoken == 0 and self._last_state_reason == "preparing" and not self.ended:
            # The engine is still preparing (the candidate's profile is being generated).
            # Keep the clock stopped and begin automatically on session.ready (or retry).
            self.started = False
            self._awaiting_ready = True
            self.clock.pause()
            self.turns.to(TurnState.IDLE, "preparing")
            self.avatar(AvatarState.IDLE)
            self.post(
                NoticeMsg(
                    level="info",
                    code="preparing",
                    message="We're finishing preparing your interview. It will begin automatically in a moment.",
                )
            )
            await self.push_state("preparing")
            self._schedule_start_retry()
            return
        self._awaiting_ready = False
        await self.push_state("started")

    def _schedule_start_retry(self, delay_s: float = 20.0) -> None:
        """Fallback when session.ready is missed: retry the deferred start."""

        async def _retry() -> None:
            await asyncio.sleep(delay_s)
            if self._awaiting_ready and not self.started and not self.ended:
                self._awaiting_ready = False
                self._enqueue("start", self._run_start, PRIORITY_CONTROL, needs_idle=False)

        self.spawn(_retry(), "start_retry")

    async def _run_resume(self, *, reconnect: bool) -> None:
        self.started = True
        self.paused_reason = None
        self.clock.start()
        if self.turns.state is TurnState.PAUSED or self.turns.state is TurnState.IDLE:
            self.turns.to(TurnState.PROCESSING, "resuming")
        await self._drive(self.engine.resume(self.session_id, reconnect=reconnect), origin="resume")
        await self.push_state("resumed")
        self._go_idle("resumed")

    async def _run_timer(self, kind: str, detail: dict[str, Any] | None = None) -> None:
        timer = TimerEvent(kind=kind, elapsed_s=round(self.clock.elapsed(), 1), detail=detail or {})  # type: ignore[arg-type]
        await self._drive(self.engine.handle_timer(self.session_id, timer), origin=f"timer_{kind}")

    async def _run_max_time(self) -> None:
        await self._drive(
            self.engine.handle_timer(
                self.session_id, TimerEvent(kind="max_time", elapsed_s=round(self.clock.elapsed(), 1))
            ),
            origin="timer_max_time",
        )
        if not self.ended:
            await self._run_end(SessionEndReason.TIME_LIMIT)

    async def request_end(self, reason: str) -> bool:
        """End the session the way a ``control.end_interview`` message would; False when already ending or
        when the interview has not started (nothing to end, like ``control.end_interview`` before the start)."""
        if self.ended or self.ending is not None or not self.started:
            return False
        try:
            end_reason = SessionEndReason(reason)
        except ValueError:
            end_reason = SessionEndReason.CANDIDATE_ENDED
        await self._interrupt_current("end_interview", notify_client=True)
        await self._discard_capture()
        self._enqueue("end", lambda: self._run_end(end_reason), PRIORITY_END, needs_idle=False)
        return True

    async def _run_end(self, reason: SessionEndReason) -> None:
        if self.ended:
            return
        self.ending = self.ending or reason
        if self._capture is not None:
            await self._discard_capture()
        await self._drive(self.engine.end_session(self.session_id, reason), origin="end")
        if not self.ended:
            await self._finalize_end(self.ending or reason)

    async def _run_repeat(self) -> None:
        await self._drive(self.engine.repeat_question(self.session_id), origin="repeat")

    def enqueue_coding_activity(
        self, activity: CodingActivity, *, coalesce: str | None = None, priority_boost: bool = False
    ) -> None:
        async def run() -> None:
            await self._drive(
                self.engine.handle_coding_activity(self.session_id, activity),
                origin=f"coding_{activity.kind}",
            )

        self._enqueue(
            f"coding_{activity.kind}",
            run,
            PRIORITY_CODING_URGENT if priority_boost else PRIORITY_CODING,
            coalesce=f"coding:{coalesce}" if coalesce else None,
        )

    async def _finalize_end(self, reason: SessionEndReason) -> None:
        if self.ended:
            return
        self.ended = True
        self.ending = reason
        self._jobs.clear()
        if self.activity is not None:
            self._stop_activity(self.activity)
        await self._discard_capture()
        self.turns.to(TurnState.ENDED, reason.value)
        status, end_reason = None, None
        with contextlib.suppress(Exception):
            status, end_reason = await refresh_status(uuid.UUID(self.session_id))
        self.post(
            SessionEndedMsg(
                reason=(end_reason or reason.value), report_status="pending", candidate_report_available=False
            )
        )
        log.info(
            "realtime.session_ended",
            session_id=self.session_id,
            reason=reason.value,
            status=status.value if status else None,
        )
        self._linger_task = asyncio.create_task(self._linger(), name=f"realtime.linger.{self.session_id}")

    async def _closing_watchdog(self, budget_s: float = 60.0) -> None:
        """Safety net: the engine committed the end of the session (session.closing on
        the bus) but its event stream may have been cancelled before SessionClosing
        reached us. Give the closing utterance time to play, then finalize."""
        deadline = time.monotonic() + budget_s
        while not self.ended and time.monotonic() < deadline:
            task = self._current_task
            if task is None or task.done():
                break
            await asyncio.sleep(0.5)
        if not self.ended and self._closing_reason is not None:
            await asyncio.sleep(1.0)  # let a just-finished drive finalize on its own
            if not self.ended:
                log.info("realtime.closing_watchdog_finalize", session_id=self.session_id)
                await self._finalize_end(_end_reason(self._closing_reason))

    async def _linger(self) -> None:
        """Keep the socket briefly so ``report.ready`` can still be announced."""
        await asyncio.sleep(REPORT_LINGER_S if self.transport is not None else 0)
        await self.aclose("ended", close_code=close_codes.NORMAL)

    async def on_report_ready(self, event: dict[str, Any]) -> None:
        """``report.ready`` / ``report.released`` (reports module) while the
        socket lingers after the end: announce it, then close normally."""
        if not self.ended:
            return
        available = bool(
            event.get("candidate_report_available")
            or event.get("candidate_released")
            or event.get("released")
        )
        if available:
            self.notice("report_ready", "Your interview summary is ready.", "info")
        self.post(
            SessionEndedMsg(
                reason=(self.ending or SessionEndReason.SUFFICIENT_EVIDENCE).value,
                report_status="ready",
                candidate_report_available=available,
            )
        )
        await self.aclose("report_ready", close_code=close_codes.NORMAL)

    # ============================================================ policy hooks
    async def policy_pause(self, event_type: IntegrityEventType, message: str | None) -> None:
        if self.ended or self.paused_reason == "policy":
            return
        await self._interrupt_current("policy_pause", notify_client=True)
        await self._activity_detached(notify=True)
        await self._discard_capture()
        self.paused_reason = "policy"
        self.turns.to(TurnState.PAUSED, f"policy:{event_type.value}")
        self.clock.pause()
        await self._engine_pause(f"policy:{event_type.value}")
        self.notice(
            "interview_paused",
            message or "The interview is paused. Resolve the issue and press continue.",
            "warning",
        )
        log.warning("realtime.policy_pause", session_id=self.session_id, event_type=event_type.value)

    async def policy_terminate(self, event_type: IntegrityEventType, message: str | None) -> None:
        if self.ended:
            return
        log.warning("realtime.policy_terminate", session_id=self.session_id, event_type=event_type.value)
        if message:
            self.notice("interview_terminated", message, "error")
        await self._interrupt_current("policy_terminate", notify_client=True)
        self.paused_reason = None
        self._enqueue(
            "end", lambda: self._run_end(SessionEndReason.POLICY_TERMINATION), PRIORITY_END, needs_idle=False
        )

    # ================================================================ messages
    async def handle_message(self, msg: Any) -> None:
        self._last_rx = time.monotonic()
        if isinstance(msg, HeartbeatMsg):
            self.post(HeartbeatAckMsg(t=msg.t, server_time=time.time() * 1000))
            now = time.monotonic()
            if now - self._last_heartbeat_db >= HEARTBEAT_DB_INTERVAL_S:
                self._last_heartbeat_db = now
                self.spawn(touch_heartbeat(uuid.UUID(self.session_id)), "heartbeat_db")
            return
        if isinstance(msg, PlaybackMsg):
            self._on_playback(msg)
            return
        if isinstance(msg, HelloMsg):
            return  # already attached; a repeated hello is ignored
        if isinstance(msg, LocalVadMsg):
            self.local_vad_hints += 1
            log.debug(
                "realtime.vad_local",
                session_id=self.session_id,
                speaking=msg.speaking,
                state=self.turns.state.value,
            )
            return
        if self.ended:
            return
        if isinstance(msg, StartInterviewMsg):
            if self.paused_reason == "policy":
                self.paused_reason = None
                self._enqueue(
                    "resume", lambda: self._run_resume(reconnect=False), PRIORITY_CONTROL, needs_idle=False
                )
            elif not self.started and not self._start_requested:
                self._start_requested = True
                self._enqueue("start", self._run_start, PRIORITY_CONTROL, needs_idle=False)
            return
        if not self.started:
            if isinstance(msg, (IntegrityEventsMsg, ClientErrorMsg)):
                pass
            else:
                self.send_error("not_started", "The interview has not started yet.", recoverable=True)
                return
        if self.paused_reason == "policy" and not isinstance(
            msg, (IntegrityEventsMsg, ClientErrorMsg, EndInterviewMsg)
        ):
            self.send_error(
                "interview_paused", "The interview is paused. Press continue to resume.", recoverable=True
            )
            return
        if isinstance(msg, EndTurnMsg):
            act = self.activity
            if act is not None and act.status == "running" and act.capture is not None:
                await self._on_activity_finish(act.activity_id, act.phase)  # "Done answering" = finish (7.4)
            else:
                await self.end_turn("manual")
        elif isinstance(msg, ActivityFinishMsg):
            await self._on_activity_finish(msg.activity_id, msg.phase)
        elif isinstance(msg, TypedAnswerMsg):
            await self._on_typed_answer(msg.text)
        elif isinstance(msg, RepeatQuestionMsg):
            await self._on_repeat()
        elif isinstance(msg, EndInterviewMsg):
            await self._interrupt_current("end_interview", notify_client=True)
            await self._discard_capture()
            self._enqueue(
                "end", lambda: self._run_end(SessionEndReason.CANDIDATE_ENDED), PRIORITY_END, needs_idle=False
            )
        elif isinstance(msg, CodeUpdateMsg):
            await self.coding_bridge.on_update(msg)
        elif isinstance(msg, (CodeRunMsg, CodeSubmitMsg)):
            await self.coding_bridge.on_run(msg)
        elif isinstance(msg, HintRequestMsg):
            await self.coding_bridge.on_hint(msg)
        elif isinstance(msg, LanguageChangeMsg):
            await self.coding_bridge.on_language(msg)
        elif isinstance(msg, IntegrityEventsMsg):
            self.spawn(self.integrity_bridge.on_client_events(list(msg.events)), "integrity_events")
        elif isinstance(msg, ClientErrorMsg):
            log.info(
                "realtime.client_error",
                session_id=self.session_id,
                component=msg.component,
                fatal=msg.fatal,
                message=msg.message[:200],
            )
            if msg.component == "audio_capture" and msg.fatal and self.ctx.policy.allow_typed_answers:
                self.notice(
                    "microphone_unavailable",
                    "Your microphone isn't available. You can type your answers instead.",
                    "warning",
                )

    def _on_playback(self, msg: PlaybackMsg) -> None:
        utt = self.utterances.get(msg.utterance_id)
        if utt is None:
            return
        now = time.monotonic()
        utt.played_ms = max(utt.played_ms, int(msg.played_ms))
        if msg.type == "playback.started":
            if utt.playback_started_at is None:
                utt.playback_started_at = now - msg.played_ms / 1000.0
        elif msg.type == "playback.progress":
            utt.last_progress_at = now
            if utt.playback_started_at is None:
                utt.playback_started_at = now - msg.played_ms / 1000.0
        elif msg.type == "playback.finished":
            utt.playback_reported = True
            utt.finished.set()
        elif msg.type == "playback.interrupted":
            utt.playback_reported = True
            utt.played_ms = int(msg.played_ms)
            utt.interrupted_evt.set()

    async def _on_repeat(self) -> None:
        if self.turns.state is TurnState.AI_SPEAKING:
            await self._interrupt_current("repeat", notify_client=True)
        await self._discard_capture()
        self._go_idle("repeat")
        self._enqueue("repeat", self._run_repeat, PRIORITY_CONTROL)

    async def _on_typed_answer(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        act = self.activity
        if act is not None and act.status in ("running", "waiting"):
            await self._activity_typed(act, text)
            return
        if not self.ctx.policy.allow_typed_answers and self._unresolved is None:
            self.send_error(
                "typed_answers_disabled",
                "Typed answers are not enabled for this interview.",
                recoverable=True,
            )
            return
        interrupted_task: asyncio.Task[InterruptedUtterance | None] | None = None
        engine_busy = self._current_job is not None and (
            self._current_job.capture is None or self._current_job.capture.engine_called
        )
        if self.turns.state is TurnState.AI_SPEAKING or (
            self.turns.state is TurnState.PROCESSING and engine_busy
        ):
            utt = await self._barge_in_cancel("typed")
            if utt is not None:
                interrupted_task = self.track(
                    asyncio.create_task(self._report_interruption(utt, wait_report=True))
                )
        audio_capture = self._unresolved
        if self._unresolved_task is not None:
            self._unresolved_task.cancel()
            self._unresolved_task = None
        self._unresolved = None
        await self._discard_capture()
        now_ms = self._session_ms()
        capture = TurnCapture(
            capture_id=_new_id("turn"),
            start_ms=self.segmenter.now_ms,
            started_session_ms=now_ms,
            transcriber=self._new_transcriber(),
            typed_text=text,
            end_at=time.monotonic(),
            end_reason="typed",
            ended_session_ms=now_ms,
            interrupted_task=interrupted_task,
        )
        if audio_capture is not None:
            capture.audio = audio_capture.audio
            capture.record_audio = audio_capture.record_audio
            capture.started_session_ms = audio_capture.started_session_ms
        self.turns.to(TurnState.PROCESSING, "typed_answer")
        self.avatar(AvatarState.THINKING, expression=AvatarExpression.THOUGHTFUL)
        self._enqueue(
            "typed_turn", lambda: self._run_turn(capture), PRIORITY_TURN, capture=capture, needs_idle=False
        )

    # ================================================================== audio
    def _session_ms(self) -> int:
        return int(self.clock.elapsed() * 1000)

    def _accepting_audio(self) -> bool:
        if not self.started or self.ended or self.paused_reason is not None:
            return False
        return self.turns.state in (
            TurnState.LISTENING,
            TurnState.CANDIDATE_SPEAKING,
            TurnState.PROCESSING,
            TurnState.AI_SPEAKING,
            TurnState.CODING,
        )

    async def handle_audio(self, data: bytes) -> None:
        self._last_rx = time.monotonic()
        try:
            frame_type, _flags, seq, payload = unpack_frame(data)
        except ValueError:
            self.send_error("bad_frame", "Binary frame too short.", recoverable=True)
            return
        if frame_type != FRAME_MIC_AUDIO:
            self.send_error("unsupported_frame", "Unsupported binary frame type.", recoverable=True)
            return
        if self._audio_seq is not None:
            if seq <= self._audio_seq and self._audio_seq - seq < 1_000_000:
                return  # duplicate / out of order: drop
            if seq > self._audio_seq + 1:
                self.audio_gaps += seq - self._audio_seq - 1
        self._audio_seq = seq
        self.audio_frames += 1
        self._last_audio_rx = time.monotonic()
        if not self._accepting_audio() or not payload:
            return
        samples = bytes_to_samples(payload)
        events = self.segmenter.process(samples)
        capture = self._capture
        if capture is not None:
            # Kept for the whole-answer second pass; stored only when recording is allowed.
            if len(capture.audio) < MAX_TURN_AUDIO_BYTES:
                capture.audio.extend(payload[: len(payload) - (len(payload) % 2)])
            capture.audio_until_ms = self.segmenter.received_ms
        for event in events:
            await self._on_vad_event(event)
        await self._check_endpoint()
        self._maybe_partial()

    def _new_transcriber(self) -> TurnTranscriber:
        state = self.state_cache
        question = None
        if state is not None:
            with contextlib.suppress(Exception):
                q = state.current_question()
                question = (q.focus or q.text) if q is not None else None
        vocabulary = list(self.ctx.vocabulary)

        def prompt(answer_so_far: str) -> str:
            return build_whisper_prompt(question=question, vocabulary=vocabulary, answer_so_far=answer_so_far)

        return TurnTranscriber(
            lambda: get_ai().stt,
            context=CallContext(
                task=ModelTask.STT, session_id=self.session_id, organization_id=self.ctx.organization_id
            ),
            prompt=prompt,
            language=self.settings.whisper_language or None,
            on_final=self._on_segment_final,
            on_failure=self._on_segment_failure,
        )

    def _open_capture(self, start_ms: int, *, reason: str) -> TurnCapture:
        # Intake long-answer items (7.11) get a more patient end of turn; every other answer the settings values.
        self.endpointer.params = self._long_endpoint if self._answer_mode == "long" else self._base_endpoint
        now_ms = self.segmenter.now_ms
        session_now = self._session_ms()
        capture = TurnCapture(
            capture_id=_new_id("turn"),
            start_ms=start_ms,
            started_session_ms=max(0, session_now - (now_ms - start_ms)),
            transcriber=self._new_transcriber(),
            record_audio=self.ctx.policy.record_answer_audio,
        )
        pre = self.segmenter.read_ms(start_ms, self.segmenter.received_ms)
        capture.audio.extend(pre.astype("<i2").tobytes())
        capture.audio_until_ms = self.segmenter.received_ms
        self._capture = capture
        self.partials.reset_segment()
        self._listening_since = None
        log.debug("realtime.capture_open", session_id=self.session_id, reason=reason, start_ms=start_ms)
        return capture

    async def _on_vad_event(self, event: Any) -> None:
        state = self.turns.state
        act = self.activity
        if act is not None and act.capture is not None and act.capture is self._capture:
            self._activity_vad(act, event)
        if isinstance(event, SpeechStarted):
            if self._capture is not None:
                return
            if self._activity_blocks_speech():
                return  # non-capture activity phase: thinking aloud is never captured or transcribed (7.2)
            if state in (TurnState.LISTENING, TurnState.CODING):
                self._open_capture(event.segment_start_ms, reason="speech")
                self.turns.to(TurnState.CANDIDATE_SPEAKING, "speech")
                self.avatar(AvatarState.LISTENING)
            elif state is TurnState.PROCESSING:
                job = self._pending_turn_job()
                if job is not None and job.capture is not None:
                    await self._merge_back(job)
        elif isinstance(event, HighSpeech):
            if self.ending is not None or self._closing_seen:
                return  # never interrupt the closing; the session is over
            if state is TurnState.AI_SPEAKING or (
                state is TurnState.PROCESSING
                and self._current_job is not None
                and self._current_job.capture is not None
                and self._current_job.capture.engine_called
            ):
                await self.barge_in(event)
        elif isinstance(event, SegmentClosed):
            capture = self._capture
            if capture is not None and event.end_ms > capture.start_ms:
                capture.transcriber.submit(
                    event.samples,
                    start_ms=max(event.start_ms, capture.start_ms),
                    end_ms=event.end_ms,
                    speech_ms=event.speech_ms,
                    vad_prob=event.mean_prob,
                )
                capture.voiced_ms += event.speech_ms
                self.partials.reset_segment()
            else:
                log.debug(
                    "realtime.segment_discarded",
                    session_id=self.session_id,
                    state=state.value,
                    duration_ms=event.duration_ms,
                )
        elif isinstance(event, SpeechEnded):
            if self._capture is not None:
                self._capture.silence_episode += 1

    async def _partial_to_engine(self, text: str) -> None:
        await _maybe_await(self.engine.on_partial_transcript(self.session_id, text, silence_started=True))

    def _pending_turn_job(self) -> FloorJob | None:
        """The spoken-turn job (running or queued) whose engine call has not happened."""
        job = self._current_job
        if (
            job is not None
            and job.kind == "turn"
            and job.capture is not None
            and not job.capture.engine_called
        ):
            return job
        for queued in self._jobs:
            if queued.kind == "turn" and queued.capture is not None and not queued.capture.engine_called:
                return queued
        return None

    async def _merge_back(self, job: FloorJob) -> None:
        """Speech resumed before the engine was called: the turn continues."""
        capture = job.capture
        assert capture is not None
        if job in self._jobs:
            self._jobs.remove(job)
        elif job is self._current_job:
            task = self._current_task
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._current_job, self._current_task = None, None
        capture.end_ms = None
        capture.end_at = None
        if capture.audio_until_ms and len(capture.audio) < MAX_TURN_AUDIO_BYTES:
            gap = self.segmenter.read_ms(capture.audio_until_ms, self.segmenter.received_ms)
            capture.audio.extend(gap.astype("<i2").tobytes())
            capture.audio_until_ms = self.segmenter.received_ms
        self._capture = capture
        self.turns.to(TurnState.CANDIDATE_SPEAKING, "speech_resumed")
        metrics.TURN_RESUMED.inc()  # the end of turn was decided too early
        self._latency_reopen(capture)
        log.debug("realtime.turn_merged", session_id=self.session_id, capture=capture.capture_id)

    def _notify_silence(self, capture: TurnCapture) -> None:
        """Once per silence episode, let the engine prepare speculatively - after
        ``segment_pause_ms`` of silence (a short pause that only closed a segment
        early is not worth an engine call)."""
        if capture.notified_episode == capture.silence_episode or self.segmenter.in_speech:
            return
        if self.segmenter.silence_ms < self.settings.segment_pause_ms:
            return
        text = capture.transcriber.text_with_partial()
        if not text:
            return  # wait for the segment final
        capture.notified_episode = capture.silence_episode
        self.spawn(self._partial_to_engine(text), "engine_partial")

    async def _check_endpoint(self, *, wallclock_silence_ms: int | None = None) -> None:
        capture = self._capture
        act = self.activity
        if capture is not None and act is not None and act.capture is capture:
            await self._check_activity_silence(act, wallclock_silence_ms=wallclock_silence_ms)
            return  # activity capture mode: the activity rules replace endpointing (7.4)
        if capture is None or self.turns.state is not TurnState.CANDIDATE_SPEAKING:
            return
        self._notify_silence(capture)
        silence = self.segmenter.silence_ms if not self.segmenter.in_speech else 0
        speaking = self.segmenter.in_speech
        if wallclock_silence_ms is not None:
            silence = max(silence, wallclock_silence_ms)
            speaking = False
        turn_ms = self.segmenter.now_ms - capture.start_ms
        decision = self.endpointer.decide(
            capture.transcriber.text_with_partial(),
            silence_ms=silence,
            turn_ms=turn_ms,
            speaking=speaking,
            settled=capture.transcriber.pending == 0,
        )
        if decision.end:
            capture.required_ms = decision.required_ms
            await self.end_turn(
                decision.reason if decision.reason == "max_utterance" else "silence",
                endpoint_reason=decision.reason,
            )

    def _maybe_partial(self) -> None:
        capture = self._capture
        if capture is None or not self.segmenter.segment_open:
            return
        start = self.segmenter.open_segment_start_ms or 0
        open_ms = self.segmenter.now_ms - start
        now = time.monotonic()
        if not self.partials.due(
            now=now,
            open_audio_ms=open_ms,
            finals_pending=capture.transcriber.pending,
            silence_ms=self.segmenter.silence_ms,
        ):
            return
        if not stt_has_spare_capacity(get_ai().stt):
            return  # never let a caption delay a segment final; try again on a later frame
        audio = self.segmenter.open_segment_audio()
        self.partials.started(now=now, open_audio_ms=open_ms)
        self.spawn(self._run_partial(capture, audio), "stt_partial")

    async def _run_partial(self, capture: TurnCapture, audio: Any) -> None:
        started = time.perf_counter()
        result = None
        try:
            result = await capture.transcriber.partial(audio)
        finally:
            self.partials.finished(
                latency_ms=(time.perf_counter() - started) * 1000 if result else None, skipped=result is None
            )
        if result is not None and self._capture is capture:
            text = capture.transcriber.text_with_partial()
            if text:
                self.post(TranscriptMsg(type="transcript.partial", segment_id=capture.capture_id, text=text))

    def _on_segment_final(self, job: SegmentJob) -> None:
        self._stt_failed_speech_ms = 0
        if self._stt_degraded_notified:
            self._stt_degraded_notified = False
            self.notice("stt_recovered", "Speech recognition is working again.", "info")

    def _on_segment_failure(self, job: SegmentJob, exc: BaseException) -> None:
        metrics.ERRORS.labels(component="realtime_stt").inc()
        speech_ms = job.speech_ms or 0
        if speech_ms < STT_DEGRADED_SEGMENT_SPEECH_MS or (job.vad_prob or 0.0) < STT_DEGRADED_SEGMENT_PROB:
            return  # not candidate speech: nothing the candidate could do differently
        self._stt_failed_speech_ms += speech_ms
        if self._stt_failed_speech_ms >= STT_DEGRADED_SPEECH_MS and not self._stt_degraded_notified:
            self._stt_degraded_notified = True
            self.notice(
                "stt_degraded",
                "Speech recognition is having trouble. Your audio is kept; you can also type your answer.",
                "warning",
            )

    async def end_turn(self, reason: str, *, endpoint_reason: str | None = None) -> None:
        capture = self._capture
        if capture is None:
            return
        self._capture = None
        if reason in ("manual", "max_utterance") or self.segmenter.segment_open:
            seg = self.segmenter.flush()
            if seg is not None and seg.end_ms > capture.start_ms:
                capture.transcriber.submit(
                    seg.samples,
                    start_ms=max(seg.start_ms, capture.start_ms),
                    end_ms=seg.end_ms,
                    speech_ms=seg.speech_ms,
                    vad_prob=seg.mean_prob,
                )
                capture.voiced_ms += seg.speech_ms
        if not capture.transcriber.segments:
            # Nothing usable (noise or an instant button press).
            await capture.transcriber.aclose()
            if capture.interrupted_task is not None:
                interrupted_task = capture.interrupted_task
                self.turns.to(TurnState.PROCESSING, "empty_barge_in")
                self._enqueue(
                    "turn",
                    lambda: self._resume_after_empty_barge_in(interrupted_task),
                    PRIORITY_TURN,
                    capture=capture,
                    needs_idle=False,
                )
                return
            self._go_idle("no_speech")
            return
        capture.end_ms = self.segmenter.now_ms
        capture.ended_session_ms = self._session_ms()
        capture.end_at = time.monotonic()
        capture.end_reason = endpoint_reason or reason
        capture.silence_at_end_ms = int(self.segmenter.silence_ms) if capture.typed_text is None else None
        self.turns.to(TurnState.PROCESSING, reason)
        self.avatar(AvatarState.THINKING, expression=AvatarExpression.THOUGHTFUL)
        self._enqueue(
            "turn", lambda: self._run_turn(capture), PRIORITY_TURN, capture=capture, needs_idle=False
        )
        self.spawn(self._filler_watch(capture), "reply_filler")
        if self.settings.integrity_frames_yield_to_replies:
            self.spawn(reply_window.hold(get_redis(), self.session_id), "integrity_reply_hold")

    async def _filler_watch(self, capture: TurnCapture) -> None:
        """No long silences (Tier 1): when the reply has not started FILLER_AFTER_S after the end of the turn, say
        a short pre-synthesized "One moment." once, so the candidate knows they were heard."""
        end_at = capture.end_at
        if end_at is None:
            return
        await asyncio.sleep(max(0.0, end_at + FILLER_AFTER_S - time.monotonic()))
        if (
            capture.replied
            # The reply's words are already here: its audio follows within a second, and a filler would land on
            # top of the question (and, played after the question was announced, used to take over its caption).
            or capture.reply_text
            or capture.end_at != end_at  # the candidate kept talking: the turn was reopened
            or self.ended
            or self.closed
            or self.transport is None
            # PROCESSING: still transcribing / deciding; AI_SPEAKING before any reply audio: the model is writing
            # (the engine announces the utterance before its first words) - both are silence for the candidate.
            or self.turns.state not in (TurnState.PROCESSING, TurnState.AI_SPEAKING)
        ):
            return
        phrase = FILLER_PHRASES[self._fillers % len(FILLER_PHRASES)]
        if self.ack_cache.prompt(get_ai().tts, self.voice, phrase) is None:
            return  # not synthesized yet: silence rather than the browser's robotic voice
        self._fillers += 1
        self._play_prompt(phrase, kind="acknowledgment")
        self.avatar(AvatarState.THINKING, expression=AvatarExpression.THOUGHTFUL)
        self._latency_note(capture, filler_ms=round((time.monotonic() - end_at) * 1000), filler=phrase)

    async def _resume_after_empty_barge_in(self, task: asyncio.Task[InterruptedUtterance | None]) -> None:
        interrupted = await task
        if interrupted is not None:
            await self._drive(
                self.engine.handle_backchannel_resume(self.session_id, interrupted),
                origin="backchannel_resume",
            )
        else:
            self._go_idle("empty_barge_in")

    async def _discard_capture(self) -> None:
        capture = self._capture
        if capture is None:
            return
        self._capture = None
        self.segmenter.reset_speech()
        await capture.transcriber.aclose()

    # ------------------------------------------------------------ the turn
    def _ack_eligible(self, capture: TurnCapture, text_so_far: str) -> bool:
        if not self.settings.acknowledgment_enabled or capture.typed_text is not None:
            return False
        if capture.interrupted_task is not None and not capture.interrupted_unheard or self.coding_bridge.active:
            return False
        if self.speech.initial_fallback_reason() is not None:
            return False
        if capture.voiced_ms < ACK_MIN_SPEECH_MS:
            return False
        if is_repeat_or_clarification(text_so_far):
            return False
        # "Can you hear me?", "Should I start?", "okay", "so for that": the engine's flow reply is the
        # acknowledgement ("Thanks, that helps." before "Yes, I can hear you clearly." would sound absurd).
        return not understand_turn(text_so_far).flow

    def _play_ack(self, capture: TurnCapture, *, soft: bool = False) -> bool:
        pick = self.ack_cache.pick(get_ai().tts, self.voice, last=self._last_ack, soft_only=soft)
        if pick is None:
            return False
        phrase, result = pick
        self._last_ack = phrase
        utt = ActiveUtterance(
            utterance_id=_new_id("ack"),
            kind="acknowledgment",
            behavior=BehaviorSpec(
                state=AvatarState.ACKNOWLEDGING, expression=AvatarExpression.WARM, gesture=AvatarGesture.NOD
            ),
            is_ack=True,
            full_text=phrase,
        )
        self.utterances[utt.utterance_id] = utt
        self.post(
            UtteranceStartMsg(
                utterance_id=utt.utterance_id,
                kind="acknowledgment",
                behavior=_behavior_msg(utt.behavior, utt.utterance_id),
            )
        )
        message = TtsChunkMsg(
            utterance_id=utt.utterance_id,
            chunk_id=self.speech.next_chunk_id(),
            index=0,
            text=phrase,
            sample_rate=int(result.sample_rate),
            duration_ms=int(result.duration_ms),
            visemes=[{"t": int(v.t_ms), "v": v.viseme, "w": float(v.weight)} for v in result.visemes],  # type: ignore[misc]
        )
        self.post(message, binary=pack_frame(0x02, message.chunk_id, result.pcm16, final=True))
        self.post(UtteranceEndMsg(utterance_id=utt.utterance_id, text=phrase))
        self.avatar(AvatarState.THINKING, expression=AvatarExpression.THOUGHTFUL)
        utt.ended = True
        utt.ended_at = utt.begun_at = time.monotonic()
        utt.audio_ms = int(result.duration_ms)  # not an engine utterance: never reported to the engine
        if capture.end_at is not None:
            self._latency_note(capture, ack_ms=round((time.monotonic() - capture.end_at) * 1000), ack=phrase)
        return True

    async def _run_turn(self, capture: TurnCapture) -> None:  # noqa: PLR0915
        started = time.monotonic()
        corrections: list[Any] = []
        if capture.typed_text is None:
            text_so_far = capture.transcriber.text_with_partial()
            if self._ack_eligible(capture, text_so_far):
                # The end of turn is provisional until the transcript is final: only a
                # soft backchannel unless the words so far already form a finished answer.
                finished = (
                    capture.transcriber.pending == 0
                    and commit_required_ms(text_so_far, self.endpointer.params)
                    <= self.endpointer.params.short_ms
                )
                if self._play_ack(capture, soft=not finished):
                    capture.acknowledged = self._last_ack
            ok = await capture.transcriber.wait_finals(timeout=STT_FINAL_TIMEOUT_S)
            if not ok or not capture.transcriber.resolved:
                # A segment still in flight after the wait is not "done": keep the turn
                # (notice + background retries) instead of answering without its audio.
                recovered = ok and await capture.transcriber.retry_failed(timeout=STT_RETRY_TIMEOUT_S)
                if not recovered:
                    await self._unresolved_turn(capture)
                    return
            text = capture.transcriber.text()
            earlier = await self._take_unresolved(capture)
            if earlier:
                text = f"{earlier} {text}".strip()
            text, corrections = self.glossary.correct(text)
            log_corrections(corrections, session_id=self.session_id)
        else:
            text = capture.typed_text
        stt_wait_ms = round((time.monotonic() - started) * 1000)
        if is_repeat_complaint(text):
            metrics.CANDIDATE_REPEAT_COMPLAINTS.inc()  # "I already told you": the interviewer asked again
        stt_fields: dict[str, Any] = {}
        jobs = capture.transcriber.segments
        if capture.typed_text is None and jobs:
            tail = max(jobs, key=lambda j: j.submitted_at)
            stt_fields = {
                "tail_audio_ms": tail.duration_ms,
                "tail_stt_ms": round(tail.latency_ms),
                # how long before the end of turn the last segment went to STT
                "tail_lead_ms": round((capture.end_at - tail.submitted_at) * 1000)
                if capture.end_at
                else None,
                "stt_max_ms": round(max(j.latency_ms for j in jobs)),
                "glossary": len(corrections),
            }
        self._latency_note(
            capture,
            turn_id=capture.capture_id,
            end_reason=capture.end_reason,
            endpoint_silence_ms=capture.silence_at_end_ms,
            required_silence_ms=capture.required_ms,
            voiced_ms=capture.voiced_ms,
            stt_wait_ms=stt_wait_ms,
            segments=len(capture.transcriber.segments),
            typed=capture.typed_text is not None,
            chars=len(text),
            **stt_fields,
        )
        self.post(
            TranscriptMsg(
                type="transcript.final",
                segment_id=capture.capture_id,
                text=text,
                start_ms=capture.started_session_ms,
                end_ms=capture.ended_session_ms,
            )
        )
        interrupted = await capture.interrupted_task if capture.interrupted_task is not None else None
        if resume_after_backchannel(interrupted, text, typed=capture.typed_text is not None):
            capture.engine_called = True
            await self._drive(
                self.engine.handle_backchannel_resume(self.session_id, interrupted),
                origin="backchannel_resume",
                turn_end_at=capture.end_at,
                capture=capture,
            )
            return
        if interrupted is not None and capture.typed_text is None and is_backchannel(text):
            # Spoken over the last moments of the question: a short "Yes." answers it instead of resuming (S10).
            log.info(
                "realtime.overlap_answer",
                session_id=self.session_id,
                utterance_id=interrupted.utterance_id,
                unspoken_words=unspoken_words(interrupted.full_text, interrupted.spoken_text),
                words=len(text.split()),
            )
        if capture.typed_text is None and capture.end_reason not in ("manual", "max_utterance"):
            waited = await self._await_commit(capture, text)
            if waited:
                self._latency_note(capture, commit_wait_ms=waited)
        if capture.typed_text is None and self._unclear(capture, text) and self._ask_to_repeat(capture, text):
            await capture.transcriber.aclose()
            return
        if not text.strip():
            self._go_idle("no_speech")
            return
        self._unclear_prompts = 0
        turn = CandidateTurn(
            text=text,
            started_ms=int(capture.started_session_ms),
            ended_ms=int(capture.ended_session_ms or self._session_ms()),
            typed=capture.typed_text is not None,
            interrupted_ai=capture.interrupted_task is not None,
            stt_confidence=capture.transcriber.confidence() if capture.typed_text is None else None,
            audio_wav=capture.wav(),
            acknowledged=capture.acknowledged,
        )
        capture.engine_called = True
        self._silence_prompts = 0
        await capture.transcriber.aclose()
        if capture.typed_text is None:
            self._schedule_full_pass(capture, text, turn)
        await self._drive(
            self.engine.handle_candidate_turn(self.session_id, turn),
            origin="turn",
            turn_end_at=capture.end_at,
            capture=capture,
        )

    async def _await_commit(self, capture: TurnCapture, text: str) -> int:
        """Stage 2 of the end of turn (``endpointing.commit_required_ms``): hold the
        answer until the silence also suits the FINAL transcript - an unfinished
        sentence, a trailing "and"/"um" or a request for time waits longer.
        Speech that resumes meanwhile merges back into this turn (the
        SpeechStarted handler cancels this job). Returns the extra wait (ms)."""
        required = commit_required_ms(text, self.endpointer.params)
        started = time.monotonic()
        budget_s = required / 1000.0 + 1.0
        long_wait_announced = False
        while True:
            silence = 0 if self.segmenter.in_speech else self.segmenter.silence_ms
            if silence >= required:
                break
            now = time.monotonic()
            stalled = self._last_audio_rx is None or now - self._last_audio_rx > AUDIO_STALL_S
            if (stalled and now - started >= budget_s) or now - started >= budget_s + 30.0:
                break  # the microphone stopped sending: wall-clock bound
            if not long_wait_announced and required - silence > 1500:
                long_wait_announced = True
                self.avatar(AvatarState.LISTENING)  # still listening: the candidate is mid-thought
            await asyncio.sleep(0.05)
        return round((time.monotonic() - started) * 1000)

    def _unclear(self, capture: TurnCapture, text: str) -> bool:
        """The answer could not be understood: nothing usable after real speech,
        hesitation sounds only, or a few words with low confidence. Short real
        answers ("Yes.", "No.", "Mm-hmm.") are never treated as unclear."""
        tokens = re.findall(r"[a-z']+", text.lower())
        if not tokens:
            return capture.voiced_ms >= UNCLEAR_MIN_VOICED_MS
        if " ".join(tokens) in AFFIRMATIVE_SOUNDS:
            return False
        if all(token in HESITATION_WORDS for token in tokens):
            return True
        confidence = capture.transcriber.confidence()
        return confidence is not None and confidence < UNCLEAR_CONFIDENCE and len(tokens) <= 3

    def _ask_to_repeat(self, capture: TurnCapture, text: str) -> bool:
        """Ask the candidate to say it again instead of passing an unusable answer
        on (the engine never sees it). After MAX_UNCLEAR_PROMPTS prompts in a row
        the answer goes through as it is. Returns True when the candidate was asked."""
        if self._unclear_prompts >= MAX_UNCLEAR_PROMPTS:
            self._unclear_prompts = 0
            if self.ctx.policy.allow_typed_answers:
                self.notice(
                    "speech_unclear",
                    "We're having trouble understanding the audio. You can also type your answer.",
                    "warning",
                )
            return False
        self._unclear_prompts += 1
        log.info(
            "realtime.turn_unclear",
            session_id=self.session_id,
            words=len(text.split()),
            voiced_ms=capture.voiced_ms,
            confidence=capture.transcriber.confidence(),
            attempt=self._unclear_prompts,
        )
        self.notice("not_understood", REPEAT_PROMPT, "info")
        self._play_prompt(REPEAT_PROMPT)
        self._go_idle("not_understood")
        return True

    def _play_prompt(self, phrase: str, *, kind: str = "comment") -> None:
        """Speak a runtime prompt (never reported to the engine): pre-synthesized
        audio when available, otherwise the browser's voice/captions. A filler is sent as an "acknowledgment"
        (a quiet caption that never stands in for the last question); "Sorry, I didn't catch that" as a comment."""
        utt = ActiveUtterance(
            utterance_id=_new_id("prompt"),
            kind=kind,
            behavior=BehaviorSpec(state=AvatarState.SPEAKING, expression=AvatarExpression.WARM),
            is_ack=True,
            full_text=phrase,
        )
        self.utterances[utt.utterance_id] = utt
        self.post(
            UtteranceStartMsg(
                utterance_id=utt.utterance_id,
                kind=kind,  # type: ignore[arg-type]
                behavior=_behavior_msg(utt.behavior, utt.utterance_id),
            )
        )
        result = self.ack_cache.prompt(get_ai().tts, self.voice, phrase)
        if result is not None and self.speech.initial_fallback_reason() is None:
            message = TtsChunkMsg(
                utterance_id=utt.utterance_id,
                chunk_id=self.speech.next_chunk_id(),
                index=0,
                text=phrase,
                sample_rate=int(result.sample_rate),
                duration_ms=int(result.duration_ms),
                visemes=[{"t": int(v.t_ms), "v": v.viseme, "w": float(v.weight)} for v in result.visemes],  # type: ignore[misc]
            )
            self.post(message, binary=pack_frame(0x02, message.chunk_id, result.pcm16, final=True))
            utt.audio_ms = int(result.duration_ms)
        else:
            self.post(
                TtsFallbackMsg(
                    utterance_id=utt.utterance_id,
                    text=phrase,
                    reason=self.speech.initial_fallback_reason() or "not_synthesized",
                )
            )
        self.post(UtteranceEndMsg(utterance_id=utt.utterance_id, text=phrase))
        utt.ended = True
        utt.ended_at = utt.begun_at = time.monotonic()

    def _schedule_full_pass(self, capture: TurnCapture, text: str, turn: CandidateTurn) -> None:
        """Background whole-answer transcription for answers built from several segments."""
        if (
            len(capture.transcriber.segments) < FULL_PASS_MIN_SEGMENTS
            or capture.voiced_ms < FULL_PASS_MIN_VOICED_MS
            or not capture.audio
            or len(capture.audio) > FULL_PASS_MAX_AUDIO_BYTES
            or not accepts_stt_option(get_ai().stt, "background")
        ):
            return
        self.spawn(
            self._full_pass(bytes(capture.audio), text, started_ms=turn.started_ms, ended_ms=turn.ended_ms),
            "stt_full_pass",
        )

    async def _full_pass(self, audio: bytes, live_text: str, *, started_ms: int, ended_ms: int) -> None:
        """Transcribe the whole answer once more (full context), only on a spare Whisper
        replica, and store it when it differs materially - before answer.evaluate
        reads the transcript (see ``app.realtime.revision``)."""
        started = time.monotonic()
        stt: Any = get_ai().stt  # ``background`` is an option of the Whisper client, not of the protocol
        wav = pcm16_to_wav(audio)
        context = CallContext(
            task=ModelTask.STT, session_id=self.session_id, organization_id=self.ctx.organization_id
        )
        result = None
        while result is None:
            try:
                result = await stt.transcribe(
                    wav,
                    content_type="audio/wav",
                    language=self.settings.whisper_language or None,
                    context=context,
                    background=True,
                )
            except PartialSkipped:
                if time.monotonic() - started >= FULL_PASS_WAIT_S:
                    log.info("realtime.stt.full_pass_skipped", session_id=self.session_id, reason="busy")
                    return
                await asyncio.sleep(0.5)
            except AIError as exc:
                log.info(
                    "realtime.stt.full_pass_failed", session_id=self.session_id, error=type(exc).__name__
                )
                return
        revised, _ = self.glossary.correct(result.text or "")
        difference = word_difference(live_text, revised)
        applied = False
        if is_material_revision(live_text, revised, min_ratio=FULL_PASS_MATERIAL_RATIO):
            for attempt in range(2):  # the engine may still be persisting the answer row
                applied = await revise_answer_transcript(
                    self.session_id,
                    started_ms=started_ms,
                    ended_ms=ended_ms,
                    live_text=live_text,
                    revised_text=revised,
                )
                if applied or attempt:
                    break
                await asyncio.sleep(1.5)
        log.info(
            "realtime.stt.full_pass",
            session_id=self.session_id,
            words=len(revised.split()),
            difference=round(difference, 3),
            applied=applied,
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    async def _take_unresolved(self, current: TurnCapture) -> str:
        """A newer answer arrived while an older one is still untranscribed:
        try that one last time and fold its text in front (never drop it silently)."""
        old = self._unresolved
        if old is None or old is current:
            return ""
        self._unresolved = None
        if self._unresolved_task is not None:
            self._unresolved_task.cancel()
            self._unresolved_task = None
        try:
            recovered = await asyncio.wait_for(old.transcriber.retry_failed(), timeout=8.0)
        except TimeoutError:
            recovered = False
        text = old.transcriber.text()
        if old.record_audio and old.audio and current.record_audio:
            current.audio[:0] = old.audio
        if not recovered:
            log.error(
                "realtime.stt_unresolved_superseded",
                session_id=self.session_id,
                capture=old.capture_id,
                chars=len(text),
            )
        return text

    async def _unresolved_turn(self, capture: TurnCapture) -> None:
        """STT failed persistently: keep the audio, keep retrying, offer typing."""
        self._unresolved = capture
        self.notice(
            "stt_unavailable",
            "We couldn't transcribe your last answer yet. We'll keep trying - you can also type your answer.",
            "error",
        )
        self._go_idle("stt_failed")

        async def retry() -> None:
            for delay in UNRESOLVED_RETRY_DELAYS:
                await asyncio.sleep(delay)
                if self._unresolved is not capture or self.closed:
                    return
                if await capture.transcriber.retry_failed(timeout=STT_RETRY_TIMEOUT_S):
                    self._unresolved = None
                    self.notice("stt_recovered", "Got it - your answer was transcribed.", "info")
                    if self._floor_idle():
                        self.turns.to(TurnState.PROCESSING, "stt_recovered")
                    self._enqueue(
                        "turn",
                        lambda: self._run_turn(capture),
                        PRIORITY_TURN,
                        capture=capture,
                        needs_idle=False,
                    )
                    return
            log.error("realtime.stt_unresolved", session_id=self.session_id, capture=capture.capture_id)

        self._unresolved_task = self.track(
            asyncio.create_task(retry(), name=f"realtime.stt_retry.{self.session_id}")
        )

    def _latency_note(self, capture: TurnCapture, **fields: Any) -> None:
        """Add timings (ms since the end-of-turn decision) to this turn's record. The "Okay." (ack_ms) and the
        "One moment." (filler_ms) are noted before the reply's first audio; that one completes the record: the
        candidate-facing totals and the metrics are computed once, and the record is persisted (turn.latency)."""
        record = next((r for r in reversed(self.latency) if r.get("turn_id") == capture.capture_id), None)
        if record is None:
            record = {"turn_id": capture.capture_id}
            self.latency.append(record)
            if len(self.latency) > 100:
                del self.latency[:50]
        completes = "first_audio_ms" in fields and "first_audio_ms" not in record
        record.update(fields)
        if completes:
            observe_turn_latency(record)
            log.info("realtime.turn_latency", session_id=self.session_id, **record)
            self.spawn(self._persist_latency(dict(record)), "turn_latency_record")

    def _latency_reopen(self, capture: TurnCapture) -> None:
        """The candidate kept talking after the end of turn was decided: timings noted so far (an "Okay." already
        played, the STT wait, a commit wait) count from a decision that no longer stands. Start the record again so
        the turn is measured from the decision that does, keeping only how often it was reopened."""
        record = next((r for r in reversed(self.latency) if r.get("turn_id") == capture.capture_id), None)
        reopened = int(record.get("reopened", 0)) + 1 if record is not None else 1
        if record is not None:
            self.latency.remove(record)
        self._latency_note(capture, reopened=reopened)

    async def _persist_latency(self, record: dict[str, Any]) -> None:
        """Keep the per-turn breakdown as an interview event: log lines vanish with the container."""
        try:
            async with session_scope() as db:
                await log_event(db, self.session_id, "turn.latency", record)
        except Exception as exc:  # noqa: BLE001 - telemetry must never disturb the interview
            # Visible (not debug): a turn missing from the persisted record would bias the numbers unnoticed.
            log.warning(
                "realtime.turn_latency_persist_failed", session_id=self.session_id, error=type(exc).__name__
            )

    # ========================================================= intake activities
    # INTAKE_INTERVIEW.md 7: the engine arms an activity (ActivityStarted after its instruction utterance); the
    # runtime starts each phase when the arming utterance is delivered, owns the phase deadline on the interview
    # clock, ends non-capture phases itself (read_relay: hide the passage at exactly 30 s) and turns a capture
    # phase into one CandidateTurn with ``activity`` (capture mode: no endpointing, acknowledgements, commit wait
    # or "didn't catch that" prompts).

    def _activity_blocks_speech(self) -> bool:
        act = self.activity
        if act is None:
            return False
        if act.status == "waiting":
            return True
        return act.status == "running" and not act.capture_phase

    @staticmethod
    def _capture_session_ms(capture: TurnCapture, t_ms: int) -> int:
        return int(capture.started_session_ms + max(0, int(t_ms) - capture.start_ms))

    def _phase_msg(
        self,
        act: RuntimeActivity,
        *,
        status: str,
        remaining_ms: int,
        can_finish: bool,
        content: dict[str, Any] | None = None,
        hide_content: bool = False,
    ) -> ActivityPhaseMsg:
        return ActivityPhaseMsg(
            activity_id=act.activity_id,
            kind=act.kind,  # type: ignore[arg-type]
            phase=act.phase,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            duration_ms=int(act.duration_ms),
            remaining_ms=max(0, int(remaining_ms)),
            capture=act.capture_phase,
            can_finish=can_finish,
            content=ActivityContentMsg(**content) if content else None,
            hide_content=hide_content,
            server_time_ms=time.time() * 1000.0,
        )

    def _on_activity_started(self, event: ActivityStarted) -> None:
        data = event.activity
        activity_id = str(data.get("activity_id") or "")
        old = self.activity
        shown_ms = old.shown_ms if old is not None and old.activity_id == activity_id else 0
        if old is not None:
            self._stop_activity(old)
        phases = [dict(p) for p in data.get("phases") or []]
        first: dict[str, Any] = (
            phases[0] if phases else {"phase": str(data.get("start_phase") or "read"), "duration_ms": 0}
        )
        act = RuntimeActivity(
            activity_id=activity_id,
            kind=str(data.get("kind") or ""),
            stage=str(data.get("stage") or ""),
            title=str(data.get("title") or ""),
            phases=phases,
            rules={str(k): dict(v) for k, v in (data.get("phase_rules") or {}).items()},
            content=dict(data["content"]) if isinstance(data.get("content"), dict) else None,
            attempt=int(data.get("attempt") or 1),
            resumed=bool(data.get("resumed")),
            reference_words=int(data.get("reference_words") or 0),
            phase=str(data.get("start_phase") or first.get("phase") or ""),
            duration_ms=int(first.get("duration_ms") or 0),
            start_after=event.start_after_utterance_id,
            shown_ms=shown_ms,
        )
        if act.kind == "read_relay" and act.phase != "read":
            act.content = None  # never shown again once the read phase is over (7.9)
        self.activity = act
        self.post(
            ActivityStartMsg(
                activity_id=act.activity_id,
                kind=act.kind,  # type: ignore[arg-type]
                stage=act.stage,  # type: ignore[arg-type]
                title=act.title,
                phases=[
                    ActivityPhasePlanMsg(
                        phase=p["phase"], duration_ms=int(p.get("duration_ms") or 0), capture=bool(p.get("capture"))
                    )
                    for p in phases
                ],
                attempt=act.attempt,
                resumed=act.resumed,
            )
        )
        log.info(
            "realtime.activity_armed",
            session_id=self.session_id,
            kind=act.kind,
            phase=act.phase,
            attempt=act.attempt,
            resumed=act.resumed,
        )
        self._maybe_start_armed(act)

    def _on_activity_phase_changed(self, event: ActivityPhaseChanged) -> None:
        act = self.activity
        if act is None or act.activity_id != event.activity_id:
            return
        self._cancel_activity_timer(act)
        act.phase = event.phase
        act.duration_ms = int(event.duration_ms)
        act.status = "armed"
        act.start_after = event.start_after_utterance_id
        act.phase_started_elapsed = None
        act.deadline = None
        act.ending = False
        act.finish_announced = False
        if event.hide_content or act.kind == "read_relay":
            act.content = None
        if event.content is not None and act.rule(event.phase).get("content_visible"):
            act.content = dict(event.content)
        self._maybe_start_armed(act)

    def _on_activity_ended(self, event: ActivityEnded) -> None:
        act = self.activity
        if act is not None and act.activity_id == event.activity_id:
            self._stop_activity(act)
        reason = event.reason if event.reason in (
            "completed", "finished_early", "deadline", "interrupted", "skipped", "session_ended"
        ) else "completed"  # fmt: skip
        self.post(
            ActivityEndMsg(activity_id=event.activity_id, kind=event.kind, reason=reason)  # type: ignore[arg-type]
        )

    def _maybe_start_armed(self, act: RuntimeActivity) -> None:
        if act.status != "armed":
            return
        if act.start_after is None:
            self._start_activity_phase(act)
            return
        utt = self.utterances.get(act.start_after)
        if utt is not None and utt.delivered:
            self._start_activity_phase(act)

    def _on_delivered(self, utt: ActiveUtterance) -> None:
        if utt.answer_mode in ("standard", "long"):
            self._answer_mode = utt.answer_mode
        act = self.activity
        if act is not None and act.status == "armed" and act.start_after == utt.utterance_id:
            self._start_activity_phase(act)

    def _start_activity_phase(self, act: RuntimeActivity) -> None:
        if self.ended or self.closed or self.transport is None or self.paused_reason is not None:
            return  # offline: the resume re-arms the activity
        rule = act.rule()
        duration = int(act.duration_ms or rule.get("duration_ms") or 0)
        if act.kind == "read_relay" and act.phase == "read":
            total = int(rule.get("duration_ms") or 30_000)
            duration = min(duration, max(0, total - act.shown_ms))  # never more than 30 s in total (7.9)
        now = self.clock.elapsed()
        act.status = "running"
        act.phase_started_elapsed = now
        act.duration_ms = duration
        act.deadline = now + duration / 1000.0
        act.ending = False
        phase = act.phase
        if duration <= 0:
            # Nothing left to show (the 30 s reveal is used up): the engine still sees phase_started first, so
            # the phase_deadline that follows ends a phase it knows about.
            self._enqueue(
                "activity",
                lambda: self._run_activity_event("phase_started", act, phase, {"duration_ms": 0}, now),
                PRIORITY_TURN,
                needs_idle=False,
            )
            self.spawn(self._end_noncapture_phase(act, "phase_deadline"), "activity_skip_phase")
            return
        finish_after = int(rule.get("finish_after_ms") or 0)
        act.finish_announced = finish_after == 0
        content = act.content if rule.get("content_visible") else None
        self.post(
            self._phase_msg(
                act,
                status="running",
                remaining_ms=duration,
                can_finish=finish_after == 0,
                content=content,
                hide_content=bool(rule.get("hide_on_entry")),
            )
        )
        if act.capture_phase:
            self._open_activity_capture(act)
        act.timer = self.track(
            asyncio.create_task(self._activity_timer(act), name=f"realtime.activity_timer.{self.session_id}")
        )
        self._enqueue(
            "activity",
            lambda: self._run_activity_event("phase_started", act, phase, {"duration_ms": duration}, now),
            PRIORITY_TURN,  # same priority as turns: ordered before a capture that ends right away
            needs_idle=False,
        )
        log.info(
            "realtime.activity_phase",
            session_id=self.session_id,
            kind=act.kind,
            phase=phase,
            duration_ms=duration,
            capture=act.capture_phase,
        )

    def _open_activity_capture(self, act: RuntimeActivity) -> None:
        old = self._capture
        if old is not None:
            self._capture = None
            self.spawn(old.transcriber.aclose(), "capture_superseded")
        start = (
            self.segmenter.open_segment_start_ms
            if self.segmenter.segment_open and self.segmenter.open_segment_start_ms is not None
            else self.segmenter.now_ms
        )
        capture = self._open_capture(start, reason="activity")
        act.capture = capture
        act.pauses = []
        act.pause_started_ms = None
        act.spoken = bool(self.segmenter.in_speech)
        self.turns.to(TurnState.CANDIDATE_SPEAKING, "activity")
        self.avatar(AvatarState.LISTENING)

    def _activity_vad(self, act: RuntimeActivity, event: Any) -> None:
        """VAD pauses inside a capture (audio clock -> session ms), recorded for the Drill A metrics (7.4)."""
        capture = act.capture
        if capture is None:
            return
        if isinstance(event, SpeechStarted):
            act.spoken = True
            if act.pause_started_ms is not None:
                if event.t_ms - act.pause_started_ms >= ACTIVITY_PAUSE_MIN_MS:
                    start = self._capture_session_ms(capture, act.pause_started_ms)
                    end = self._capture_session_ms(capture, event.t_ms)
                    act.pauses.append({"start_ms": start, "end_ms": end, "duration_ms": end - start})
            act.pause_started_ms = None
        elif isinstance(event, SpeechEnded):
            act.pause_started_ms = int(event.t_ms)
        elif isinstance(event, SegmentClosed) and event.speech_ms > 0:
            act.spoken = True

    async def _check_activity_silence(self, act: RuntimeActivity, *, wallclock_silence_ms: int | None = None) -> None:
        capture = act.capture
        if capture is None or act.ending or act.status != "running":
            return
        if len(capture.audio) >= MAX_TURN_AUDIO_BYTES:
            await self._end_activity_capture(act, "max_audio")
            return
        if not (act.spoken or capture.voiced_ms > 0 or capture.transcriber.segments):
            return  # never on silence before the candidate has spoken: the deadline governs
        silence = 0 if self.segmenter.in_speech else self.segmenter.silence_ms
        if wallclock_silence_ms is not None:
            silence = max(silence, wallclock_silence_ms)
        if silence <= 0:
            return
        words = len(capture.transcriber.text_with_partial().split())
        for rule in ACTIVITY_SILENCE.get((act.kind, act.phase), ()):
            if silence < rule.silence_ms or words < rule.min_words:
                continue
            if rule.min_reference_ratio and words < rule.min_reference_ratio * max(1, act.reference_words):
                continue
            await self._end_activity_capture(act, "silence")
            return

    async def _activity_timer(self, act: RuntimeActivity) -> None:
        """Phase deadline on the interview clock (pauses excluded); also enables a delayed finish button."""
        while True:
            if self.activity is not act or act.status != "running" or act.deadline is None or self.closed:
                return
            now = self.clock.elapsed()
            step = ACTIVITY_TIMER_STEP_S
            if not act.finish_announced and act.phase_started_elapsed is not None:
                enable_at = act.phase_started_elapsed + int(act.rule().get("finish_after_ms") or 0) / 1000.0
                if now >= enable_at:
                    act.finish_announced = True
                    self.post(
                        self._phase_msg(
                            act, status="running", remaining_ms=int((act.deadline - now) * 1000), can_finish=True
                        )
                    )
                else:
                    step = min(step, max(0.01, enable_at - now))
            remaining = act.deadline - now
            if remaining <= 0:
                break
            await asyncio.sleep(min(step, remaining))
        await self._activity_deadline(act)

    async def _activity_deadline(self, act: RuntimeActivity) -> None:
        if self.activity is not act or act.status != "running":
            return
        if not act.capture_phase:
            await self._end_noncapture_phase(act, "phase_deadline")
            return
        started = time.monotonic()
        while self.activity is act and act.status == "running" and not act.ending:
            if not self.segmenter.in_speech and self.segmenter.silence_ms >= ACTIVITY_GRACE_PAUSE_MS:
                break
            if time.monotonic() - started >= ACTIVITY_GRACE_MAX_S:
                break
            if self._last_audio_rx is None or time.monotonic() - self._last_audio_rx > AUDIO_STALL_S:
                break
            await asyncio.sleep(0.05)
        if self.activity is act and act.status == "running" and not act.ending:
            await self._end_activity_capture(act, "deadline")

    def _cancel_activity_timer(self, act: RuntimeActivity) -> None:
        timer = act.timer
        act.timer = None
        if timer is not None and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()

    async def _end_noncapture_phase(self, act: RuntimeActivity, kind: str) -> None:
        """End a phase without capture (read_relay read: hide the passage now; topic prepare), then ask the
        engine for the next phase's prompt."""
        if act.status != "running" or act.capture_phase:
            return
        now = self.clock.elapsed()
        shown = max(0.0, now - (act.phase_started_elapsed if act.phase_started_elapsed is not None else now))
        remaining = int(max(0.0, ((act.deadline if act.deadline is not None else now) - now)) * 1000)
        hide = False
        if act.kind == "read_relay" and act.phase == "read":
            act.shown_ms += int(round(shown * 1000))
            act.content = None  # dropped from memory once hidden (7.5)
            hide = True
        act.status = "waiting"
        self._cancel_activity_timer(act)
        self.post(self._phase_msg(act, status="ended", remaining_ms=remaining, can_finish=False, hide_content=hide))
        phase = act.phase
        detail = {"shown_ms": act.shown_ms} if act.kind == "read_relay" else {}
        self._enqueue(
            "activity",
            lambda: self._run_activity_event(kind, act, phase, detail, now),
            PRIORITY_TURN,
            needs_idle=False,
        )
        log.info("realtime.activity_phase_ended", session_id=self.session_id, kind=act.kind, phase=phase, reason=kind)

    async def _run_activity_event(
        self, kind: str, act: RuntimeActivity, phase: str, detail: dict[str, Any], elapsed: float
    ) -> None:
        handler = getattr(self.engine, "handle_activity_event", None)
        if handler is None:
            return
        event = ActivityEvent(
            kind=kind,  # type: ignore[arg-type]
            activity_id=act.activity_id,
            phase=phase,
            elapsed_s=round(float(elapsed), 3),
            detail=dict(detail),
        )
        expects_transition = kind != "phase_started"
        try:
            await self._drive(handler(self.session_id, event), origin=f"activity_{kind}")
        except asyncio.CancelledError:
            raise
        except Exception:
            if expects_transition:
                self._release_stalled_activity(act, origin=f"activity_{kind}")
            raise
        if expects_transition:
            self._release_stalled_activity(act, origin=f"activity_{kind}")

    async def _end_activity_capture(
        self, act: RuntimeActivity, reason: str, *, typed_text: str | None = None
    ) -> None:
        """End the capture phase (finish_button | silence | deadline | disconnect | max_audio) and run it as one
        activity turn."""
        capture = act.capture
        if capture is None or act.ending:
            return
        act.ending = True
        self._cancel_activity_timer(act)
        if self._capture is capture:
            self._capture = None
        if typed_text is None:
            seg = self.segmenter.flush()
            if seg is not None and seg.end_ms > capture.start_ms:
                capture.transcriber.submit(
                    seg.samples,
                    start_ms=max(seg.start_ms, capture.start_ms),
                    end_ms=seg.end_ms,
                    speech_ms=seg.speech_ms,
                    vad_prob=seg.mean_prob,
                )
                capture.voiced_ms += seg.speech_ms
        else:
            capture.typed_text = typed_text
        capture.end_ms = self.segmenter.now_ms
        capture.ended_session_ms = self._session_ms()
        capture.end_at = time.monotonic()
        capture.end_reason = f"activity_{reason}"
        now = self.clock.elapsed()
        remaining = int(max(0.0, ((act.deadline if act.deadline is not None else now) - now)) * 1000)
        if self.transport is not None:
            self.post(self._phase_msg(act, status="ended", remaining_ms=remaining, can_finish=False))
        act.status = "waiting"
        if self.paused_reason is None and reason != "disconnect":
            self.turns.to(TurnState.PROCESSING, f"activity_{reason}")
            self.avatar(AvatarState.THINKING, expression=AvatarExpression.THOUGHTFUL)
        self._enqueue(
            "turn", lambda: self._run_activity_turn(act, capture, reason), PRIORITY_TURN, capture=capture,
            needs_idle=False,
        )  # fmt: skip
        log.info(
            "realtime.activity_capture_ended",
            session_id=self.session_id,
            kind=act.kind,
            phase=act.phase,
            reason=reason,
            voiced_ms=capture.voiced_ms,
            segments=len(capture.transcriber.segments),
            pauses=len(act.pauses),
        )

    async def _run_activity_turn(self, act: RuntimeActivity, capture: TurnCapture, reason: str) -> None:
        typed = capture.typed_text is not None
        degraded = False
        corrections: list[Any] = []
        if typed:
            text = capture.typed_text or ""
        else:
            ok = await capture.transcriber.wait_finals(timeout=STT_FINAL_TIMEOUT_S)
            if not ok or not capture.transcriber.resolved:
                degraded = not await capture.transcriber.retry_failed(timeout=ACTIVITY_RETRY_TIMEOUT_S)
            text, corrections = self.glossary.correct(capture.transcriber.text())
            log_corrections(corrections, session_id=self.session_id)
        segments = (
            []
            if typed
            else [
                {
                    "start_ms": self._capture_session_ms(capture, job.start_ms),
                    "end_ms": self._capture_session_ms(capture, job.end_ms),
                    "speech_ms": job.speech_ms,
                    "vad_prob": job.vad_prob,
                    "no_speech_prob": job.no_speech_prob,
                    "text": job.text,
                }
                for job in capture.transcriber.segments
                if job.status == "done"
            ]
        )
        ended_ms = int(capture.ended_session_ms if capture.ended_session_ms is not None else self._session_ms())
        activity_capture = ActivityCapture(
            activity_id=act.activity_id,
            kind=act.kind,  # type: ignore[arg-type]
            phase=act.phase,  # type: ignore[arg-type]
            end_reason=reason,  # type: ignore[arg-type]
            started_ms=int(capture.started_session_ms),
            ended_ms=ended_ms,
            voiced_ms=int(capture.voiced_ms),
            segments=segments,
            pauses=list(act.pauses),
            stt_confidence=None if typed else capture.transcriber.confidence(),
            stt_degraded=degraded or self._stt_degraded_notified,
            typed=typed,
        )
        turn = CandidateTurn(
            text=text,
            started_ms=int(capture.started_session_ms),
            ended_ms=ended_ms,
            typed=typed,
            stt_confidence=activity_capture.stt_confidence,
            audio_wav=None if typed else capture.wav(),
            activity=activity_capture,
        )
        capture.engine_called = True
        await capture.transcriber.aclose()
        self._latency_note(
            capture,
            turn_id=capture.capture_id,
            end_reason=capture.end_reason,
            voiced_ms=capture.voiced_ms,
            segments=len(segments),
            typed=typed,
            activity=act.kind,
        )
        if self.transport is not None and text:
            self.post(
                TranscriptMsg(
                    type="transcript.final",
                    segment_id=capture.capture_id,
                    text=text,
                    start_ms=capture.started_session_ms,
                    end_ms=ended_ms,
                )
            )
        if not typed and reason != "disconnect" and segments and capture.audio:
            self._schedule_activity_final_pass(act, capture, activity_capture, text)
        try:
            await self._drive(
                self.engine.handle_candidate_turn(self.session_id, turn),
                origin="activity_turn",
                turn_end_at=capture.end_at,
                capture=capture,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._release_stalled_activity(act, origin="activity_turn")
            raise
        self._release_stalled_activity(act, origin="activity_turn")

    def _release_stalled_activity(self, act: RuntimeActivity, *, origin: str) -> None:
        """The engine answered a finished phase without a next step (a failed or stale job): never leave the
        candidate's speech blocked between phases. The engine re-arms the activity on the next turn."""
        if self.activity is not act or act.status != "waiting" or self.paused_reason is not None or self.closed:
            return
        act.status = "interrupted"
        if act.kind == "read_relay":
            act.content = None
        log.warning(
            "realtime.activity_stalled", session_id=self.session_id, kind=act.kind, phase=act.phase, origin=origin
        )

    def _schedule_activity_final_pass(
        self, act: RuntimeActivity, capture: TurnCapture, activity_capture: ActivityCapture, live_text: str
    ) -> None:
        """Windowed whole-audio transcription (<= 25 s windows) on a spare replica; worker fallback (7.7)."""
        pcm = bytes(capture.audio)
        attempt = act.attempt
        activity_id = act.activity_id
        segments = list(activity_capture.segments)
        pauses = list(activity_capture.pauses)
        origin = int(activity_capture.started_ms)
        started_ms, ended_ms = activity_capture.started_ms, activity_capture.ended_ms

        async def run() -> None:
            from app.core.queue import enqueue
            from app.services.intake import capture_store
            from app.services.intake.transcribe import keeps_words, windowed_final_pass

            result = await windowed_final_pass(
                pcm,
                segments,
                pauses,
                origin_ms=origin,
                stt=get_ai().stt,
                context=CallContext(
                    task=ModelTask.STT, session_id=self.session_id, organization_id=self.ctx.organization_id
                ),
                language=self.settings.whisper_language or None,
                wait_s=float(getattr(self.settings, "intake_activity_final_pass_wait_seconds", 60)),
                correct=lambda value: self.glossary.correct(value)[0],
            )
            applied = False
            if result.status == "final":
                stored = await capture_store.store_final_transcript(
                    self.session_id,
                    activity_id,
                    attempt=attempt,
                    text_value=result.text,
                    windows=result.window_records(),
                    source="final_pass_live",
                )
                if stored and result.text != live_text and keeps_words(live_text, result.text):
                    for retry in range(2):  # the engine may still be persisting the answer row
                        applied = await revise_answer_transcript(
                            self.session_id,
                            started_ms=started_ms,
                            ended_ms=ended_ms,
                            live_text=live_text,
                            revised_text=result.text,
                        )
                        if applied or retry:
                            break
                        await asyncio.sleep(1.5)
            elif result.status == "empty":
                await capture_store.mark_transcript_status(
                    self.session_id, activity_id, attempt=attempt, status="unavailable"
                )
            else:
                await capture_store.mark_transcript_status(
                    self.session_id, activity_id, attempt=attempt, status="pending_worker"
                )
                await enqueue(
                    "intake.activity_transcribe",
                    {"session_id": self.session_id, "activity_id": activity_id, "attempt": attempt},
                    dedupe_key=f"intake.activity_transcribe:{activity_id}:{attempt}",
                    delay_seconds=30,
                )
            log.info(
                "realtime.intake.final_pass",
                session_id=self.session_id,
                kind=act.kind,
                status=result.status,
                windows=len(result.windows),
                answer_revised=applied,
                latency_ms=round(result.latency_ms),
            )

        self.spawn(run(), "intake_final_pass")

    async def _on_activity_finish(self, activity_id: str, phase: str) -> None:
        act = self.activity
        if act is None or act.activity_id != activity_id or act.phase != phase or act.status != "running" or act.ending:
            return  # stale button press (another phase already started): ignored
        elapsed_ms = (self.clock.elapsed() - (act.phase_started_elapsed or 0.0)) * 1000.0
        if elapsed_ms + 50 < int(act.rule().get("finish_after_ms") or 0):
            self.send_error("finish_not_available", "You can finish in a few seconds.", recoverable=True)
            return
        if act.capture_phase:
            await self._end_activity_capture(act, "finish_button")
        else:
            await self._end_noncapture_phase(act, "finish_requested")

    async def _activity_typed(self, act: RuntimeActivity, text: str) -> None:
        if act.status == "running" and act.capture_phase and act.capture is not None and not act.ending:
            if act.kind == "reading_aloud":
                self.send_error(
                    "typed_not_allowed_for_reading",
                    "The reading exercise needs your voice: please read the passage aloud.",
                    recoverable=True,
                )
                return
            if not self.ctx.policy.allow_typed_answers:
                self.send_error(
                    "typed_answers_disabled", "Typed answers are not enabled for this interview.", recoverable=True
                )
                return
            await self._end_activity_capture(act, "finish_button", typed_text=text)
            return
        self.send_error("activity_in_progress", "Please wait for the exercise to continue.", recoverable=True)

    async def _activity_detached(self, *, notify: bool = False) -> None:
        """Disconnect / policy pause during an activity: a running capture ends ``disconnect`` (the turn still runs
        offline and is kept as an interrupted attempt); a non-capture phase stops (reveal time accounted). The
        engine's resume decides what restarts (7.8)."""
        act = self.activity
        if act is None:
            return
        self._jobs = [job for job in self._jobs if job.kind != "activity"]
        self._cancel_activity_timer(act)
        if act.status == "running" and act.capture_phase and act.capture is not None and not act.ending:
            await self._end_activity_capture(act, "disconnect")
        elif act.status == "running":
            now = self.clock.elapsed()
            if act.kind == "read_relay" and act.phase == "read":
                start = act.phase_started_elapsed if act.phase_started_elapsed is not None else now
                act.shown_ms += int(round(max(0.0, now - start) * 1000))
            if notify and self.transport is not None:
                self.post(
                    self._phase_msg(
                        act, status="ended", remaining_ms=0, can_finish=False, hide_content=act.kind == "read_relay"
                    )
                )
        if act.kind == "read_relay":
            act.content = None
        act.status = "interrupted"

    def _stop_activity(self, act: RuntimeActivity) -> None:
        self._cancel_activity_timer(act)
        capture = act.capture
        if capture is not None and self._capture is capture and not capture.engine_called:
            self._capture = None
            self.segmenter.reset_speech()
            self.spawn(capture.transcriber.aclose(), "activity_capture_close")
        act.capture = None
        act.content = None
        act.status = "ended"
        if self.activity is act:
            self.activity = None

    # ------------------------------------------------------- test hook helpers
    async def force_activity_deadline(self, activity_id: str) -> bool:
        """Test hook: the current phase of ``activity_id`` reaches its deadline now."""
        act = self.activity
        if act is None or act.activity_id != activity_id or act.status != "running":
            return False
        if act.capture_phase:
            await self._end_activity_capture(act, "deadline")
        else:
            await self._end_noncapture_phase(act, "phase_deadline")
        return True

    async def inject_activity_capture(
        self,
        activity_id: str,
        *,
        phase: str,
        text: str,
        segments: list[dict[str, Any]] | None = None,
        pauses: list[dict[str, Any]] | None = None,
        end_reason: str = "finish_button",
        typed: bool = False,
    ) -> bool:
        """Test hook: replace the running capture of ``activity_id`` with ``text`` (as the runtime would send it)."""
        act = self.activity
        if act is None or act.activity_id != activity_id:
            return False
        self._cancel_activity_timer(act)
        capture = act.capture
        if capture is not None and self._capture is capture:
            self._capture = None
            self.segmenter.reset_speech()
            await capture.transcriber.aclose()
        act.capture = None
        act.ending = True
        now_ms = self._session_ms()
        segs = segments
        if segs is None:
            segs = (
                [{"start_ms": max(0, now_ms - 4000), "end_ms": now_ms, "speech_ms": 3600, "vad_prob": 0.95,
                  "no_speech_prob": 0.0, "text": text}]
                if text and not typed
                else []
            )  # fmt: skip
        started_ms = int(segs[0]["start_ms"]) if segs else now_ms
        activity_capture = ActivityCapture(
            activity_id=act.activity_id,
            kind=act.kind,  # type: ignore[arg-type]
            phase=phase,  # type: ignore[arg-type]
            end_reason=end_reason,  # type: ignore[arg-type]
            started_ms=started_ms,
            ended_ms=now_ms,
            voiced_ms=sum(int(s.get("speech_ms") or 0) for s in segs),
            segments=[dict(s) for s in segs],
            pauses=[dict(p) for p in pauses or []],
            typed=typed,
        )
        turn = CandidateTurn(text=text, started_ms=started_ms, ended_ms=now_ms, typed=typed, activity=activity_capture)
        if self.transport is not None and act.status == "running":
            self.post(self._phase_msg(act, status="ended", remaining_ms=0, can_finish=False))
        act.status = "waiting"
        attempt = act.attempt

        async def run() -> None:
            await self._drive(self.engine.handle_candidate_turn(self.session_id, turn), origin="activity_turn")
            if segs and not typed:
                from app.services.intake import capture_store

                await capture_store.mark_transcript_status(
                    self.session_id, activity_id, attempt=attempt, status="live_only", source="live_segments"
                )

        self.turns.to(TurnState.PROCESSING, "activity_injected")
        self._enqueue("turn", run, PRIORITY_TURN, needs_idle=False)
        return True

    # ---------------------------------------------------------------- barge-in
    async def _barge_in_cancel(self, reason: str) -> ActiveUtterance | None:
        utt = self._speaking_utterance()
        task = self._current_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._current_job, self._current_task = None, None
        for other in list(self.utterances.values()):
            if other.speech is not None and not other.delivered:
                await other.speech.cancel()
            if other.delivery_task is not None and not other.delivery_task.done():
                other.delivery_task.cancel()
        if utt is not None and not utt.interrupted:
            utt.interrupted = True
            self.post(BargeInMsg(utterance_id=utt.utterance_id))
            self.post(UtteranceEndMsg(utterance_id=utt.utterance_id, text=utt.text, interrupted=True))
        metrics.BARGE_INS.inc()
        log.info(
            "realtime.barge_in",
            session_id=self.session_id,
            reason=reason,
            utterance_id=utt.utterance_id if utt else None,
        )
        return utt if utt is not None and not utt.is_ack else None

    async def barge_in(self, event: HighSpeech | None = None) -> None:
        utt = await self._barge_in_cancel("vad")
        self.turns.to(TurnState.CANDIDATE_SPEAKING, "barge_in")
        self.avatar(AvatarState.INTERRUPTED)
        self.avatar(AvatarState.LISTENING)
        start = self.segmenter.open_segment_start_ms
        if start is None:
            start = max(0, self.segmenter.now_ms - (event.sustained_ms if event else 300) - 300)
        capture = self._open_capture(start, reason="barge_in")
        if utt is not None:
            capture.interrupted_unheard = utt.playback_started_at is None and utt.played_ms == 0
            capture.interrupted_task = self.track(
                asyncio.create_task(
                    self._report_interruption(utt, wait_report=True),
                    name=f"realtime.interruption.{utt.utterance_id}",
                )
            )
        else:
            capture.interrupted_task = self.track(
                asyncio.create_task(_none(), name="realtime.interruption.none")
            )

    # ===================================================================== tick
    async def _tick_loop(self) -> None:
        while not self.closed:
            await asyncio.sleep(TICK_S)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                metrics.ERRORS.labels(component="realtime_tick").inc()
                log.warning(
                    "realtime.tick_failed",
                    session_id=self.session_id,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                )

    async def _tick(self) -> None:
        now = time.monotonic()
        transport = self.transport
        if transport is not None:
            if now - self._last_rx > 3 * self.settings.ws_heartbeat_interval_seconds:
                log.info("realtime.heartbeat_timeout", session_id=self.session_id)
                await transport.close(close_codes.HEARTBEAT_TIMEOUT)
                await self.detach(transport, reason="heartbeat_timeout")
                return
        if transport is None or not self.started or self.ended or self.paused_reason is not None:
            return
        # Audio stopped arriving mid-turn (mic muted, tab throttled): end on wall-clock silence.
        if (
            self._capture is not None
            and self._last_audio_rx is not None
            and now - self._last_audio_rx > AUDIO_STALL_S
        ):
            await self._check_endpoint(
                wallclock_silence_ms=int((now - self._last_audio_rx) * 1000) + self.segmenter.silence_ms
            )
        idle = self._floor_idle() and self._current_task is None and not self._jobs
        act = self.activity
        if act is not None and act.status == "running" and act.deadline is not None:
            if act.timer is None or act.timer.done():
                act.timer = self.track(asyncio.create_task(self._activity_timer(act), name="realtime.activity_timer"))
        # Silence prompt: no answer ~25 s after the question was delivered (never during an intake activity).
        if (
            idle
            and (act is None or act.status in ("ended", "interrupted"))
            and self.turns.state is TurnState.LISTENING
            and self._listening_since is not None
            and now - self._listening_since >= SILENCE_PROMPT_S * (1 + self._silence_prompts)
        ):
            self._silence_prompts += 1
            self._listening_since = now
            self._enqueue(
                "timer",
                lambda: self._run_timer("silence_timeout", {"count": self._silence_prompts}),
                PRIORITY_TIMER,
                coalesce="silence",
            )
        # Interview clock.
        limit, max_time = self._time_limits()
        elapsed = self.clock.elapsed()
        if not self._max_time_sent and elapsed >= max_time:
            self._max_time_sent = True
            await self._interrupt_current("max_time", notify_client=True)
            await self._discard_capture()
            self._enqueue("end", self._run_max_time, PRIORITY_END, needs_idle=False)
            return
        if not self._time_up_sent and elapsed >= limit:
            self._time_up_sent = True
            self._enqueue("timer", lambda: self._run_timer("time_up"), PRIORITY_TIMER, coalesce="time_up")
        elif (
            not self._time_warned
            and limit > 2 * TIME_WARNING_REMAINING_S
            and limit - elapsed <= TIME_WARNING_REMAINING_S
        ):
            self._time_warned = True
            self._enqueue(
                "timer",
                lambda: self._run_timer("time_warning", {"remaining_s": round(limit - elapsed)}),
                PRIORITY_TIMER,
                coalesce="time_warning",
            )
        # The engine owns the coding clock (pauses excluded): give it a tick every ~10 s while coding.
        if idle and self.turns.state is TurnState.CODING and now - self._last_engine_tick >= ENGINE_TICK_S:
            self._last_engine_tick = now
            self._enqueue("timer", lambda: self._run_timer("tick"), PRIORITY_TIMER, coalesce="tick")
        self.coding_bridge.tick(now, floor_idle=self._floor_idle() and self.turns.state is TurnState.CODING)

    # ===================================================================== bus
    async def _bus_loop(self) -> None:
        backoff = 1.0
        while not self.closed:
            try:
                async with subscribe_session_events(self.session_id) as sub:
                    backoff = 1.0
                    while not self.closed:
                        event = await sub.get(timeout=1.0)
                        if event is not None:
                            await self._on_bus_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Redis hiccup: resubscribe
                log.warning("realtime.bus_failed", session_id=self.session_id, error=type(exc).__name__)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    async def _on_bus_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        if kind in ("integrity.event", "integrity.action"):
            await self.integrity_bridge.on_bus_event(event)
        elif kind == "integrity.warning":
            await self.integrity_bridge.on_bus_warning(event)
        elif kind in ("report.ready", "report.released"):
            await self.on_report_ready(event)
        elif kind in ("state.changed", "session.state", "session.refresh", "session.ready", "clock.advanced"):
            if kind == "session.ready" and self._awaiting_ready and not self.started and not self.ended:
                self._awaiting_ready = False
                self._enqueue("start", self._run_start, PRIORITY_CONTROL, needs_idle=False)
            if self.transport is not None:
                await self.push_state(kind)
            else:
                await self._refresh_state_cache()
        elif kind == "session.closing":
            if not self.ended and self._closing_reason is None:
                self._closing_seen = True
                self._closing_reason = str(event.get("reason") or SessionEndReason.SUFFICIENT_EVIDENCE.value)
                self.spawn(self._closing_watchdog(), "closing_watchdog")
        elif kind in ("session.cancelled", "interview.cancelled", "session.abort"):
            if not self.ended:
                await self._interrupt_current("cancelled", notify_client=True)
                self._enqueue(
                    "end", lambda: self._run_end(SessionEndReason.CANCELLED), PRIORITY_END, needs_idle=False
                )
        elif kind == "session.end_requested":
            # The candidate asked to end over REST because the socket was down (S6): same path as the
            # control.end_interview message, on whichever instance holds the runtime.
            await self.request_end(str(event.get("reason") or SessionEndReason.CANDIDATE_ENDED.value))
        elif kind == "evaluation.ready":
            await self._refresh_state_cache()
        elif kind == "runtime.takeover":
            if event.get("instance_id") == self.settings.instance_id:
                return
            if self.transport is None:
                log.info(
                    "realtime.handover", session_id=self.session_id, to=str(event.get("instance_id"))[:80]
                )
                await self.aclose("taken_over")
            else:
                log.info("realtime.handover_refused", session_id=self.session_id, reason="connected_here")


async def _none() -> None:
    return None


def _behavior_msg(spec: BehaviorSpec, utterance_id: str | None) -> AvatarBehaviorMsg:
    return AvatarBehaviorMsg(
        utterance_id=utterance_id,
        state=spec.state,
        expression=spec.expression,
        gesture=spec.gesture,
        gesture_intensity=spec.gesture_intensity,
        tone=spec.tone,
    )


def _seq_from_json(raw: str) -> int:
    import json

    try:
        return int(json.loads(raw).get("seq", 0))
    except (ValueError, TypeError, AttributeError):
        return 0


__all__ = ["ActiveUtterance", "RuntimeClosed", "SessionRuntime", "TurnCapture"]
