"""Realtime interview protocol (WebSocket ``/ws/sessions/{session_id}``).

One multiplexed WebSocket per live session carries JSON control/event
messages (text frames) and audio (binary frames). The TypeScript mirror lives
in ``apps/web/src/lib/realtime/protocol.ts``; keep both in sync.

Binary frame layout (little endian)::

    byte 0      frame type   0x01 = candidate microphone PCM16 mono 16 kHz
                             0x02 = interviewer TTS PCM16 mono (sample rate in tts.chunk)
    byte 1      flags        bit0 = final chunk of utterance (0x02 only)
    bytes 2-3   reserved     0
    bytes 4-7   uint32       0x01: client audio sequence number
                             0x02: chunk_id announced by the preceding tts.chunk message
    bytes 8..   payload      raw PCM16 samples

Every server JSON message carries a monotonically increasing ``seq``. On
reconnect the client sends ``hello.last_seq`` and the server replays buffered
JSON messages after that seq (audio is not replayed: an interrupted utterance
is re-spoken from its start).

Authentication: the client first obtains a single-use ticket from
``POST /api/v1/portal/sessions/{id}/ws-ticket`` and connects with
``?ticket=...``. The ticket is bound to the session and the candidate.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domain.enums import (
    AvatarExpression,
    AvatarGesture,
    AvatarState,
    CodeLanguage,
    GestureIntensity,
    IntegrityEventType,
    RunKind,
    RunStatus,
    SessionStatus,
    SpeechTone,
    TurnState,
)

FRAME_MIC_AUDIO = 0x01
FRAME_TTS_AUDIO = 0x02
FRAME_HEADER_BYTES = 8
MIC_SAMPLE_RATE = 16000
PROTOCOL_VERSION = 1


class _Msg(BaseModel):
    model_config = ConfigDict(extra="ignore")


# =============================================================================
# Client -> server
# =============================================================================


class ClientInfo(_Msg):
    user_agent: str | None = None
    webgl: bool = True
    avatar_mode: Literal["3d", "2d", "none"] = "3d"
    tts_mode: Literal["server", "browser", "text"] = "server"
    sample_rate: int = MIC_SAMPLE_RATE
    timezone: str | None = None


class HelloMsg(_Msg):
    type: Literal["hello"] = "hello"
    protocol: int = PROTOCOL_VERSION
    last_seq: int = 0
    client: ClientInfo = Field(default_factory=ClientInfo)


class StartInterviewMsg(_Msg):
    """Candidate pressed 'Begin interview' (after fullscreen/permissions)."""

    type: Literal["control.start"] = "control.start"


class EndTurnMsg(_Msg):
    """Candidate pressed 'Done answering' — immediate end of turn."""

    type: Literal["control.end_turn"] = "control.end_turn"


class RepeatQuestionMsg(_Msg):
    type: Literal["control.repeat"] = "control.repeat"


class EndInterviewMsg(_Msg):
    """Candidate confirmed ending the interview early."""

    type: Literal["control.end_interview"] = "control.end_interview"
    reason: str | None = None


class TypedAnswerMsg(_Msg):
    """Typed answer (accessibility or STT recovery) when policy allows."""

    type: Literal["answer.text"] = "answer.text"
    text: str = Field(..., min_length=1, max_length=8000)


class PlaybackMsg(_Msg):
    """Client playback progress for an interviewer utterance."""

    type: Literal["playback.started", "playback.progress", "playback.finished", "playback.interrupted"]
    utterance_id: str
    played_ms: int = 0
    chunk_id: int | None = None


class LocalVadMsg(_Msg):
    """Client-side voice activity hint (used to duck playback instantly; the
    server VAD remains the authority for barge-in)."""

    type: Literal["vad.local"] = "vad.local"
    speaking: bool


class CodeUpdateMsg(_Msg):
    """Debounced (~800 ms) full editor contents."""

    type: Literal["code.update"] = "code.update"
    coding_session_id: str
    language: CodeLanguage
    code: str = Field(..., max_length=200_000)
    version: int = Field(..., ge=0, description="Monotonic editor version")
    cursor_line: int | None = None


class CodeRunMsg(_Msg):
    type: Literal["code.run"] = "code.run"
    coding_session_id: str
    kind: RunKind = RunKind.TEST
    language: CodeLanguage
    code: str = Field(..., max_length=200_000)
    stdin: str | None = Field(None, max_length=100_000)


class CodeSubmitMsg(_Msg):
    type: Literal["code.submit"] = "code.submit"
    coding_session_id: str
    language: CodeLanguage
    code: str = Field(..., max_length=200_000)


class HintRequestMsg(_Msg):
    type: Literal["code.hint_request"] = "code.hint_request"
    coding_session_id: str


class LanguageChangeMsg(_Msg):
    type: Literal["code.language"] = "code.language"
    coding_session_id: str
    language: CodeLanguage


class ClientIntegrityEvent(_Msg):
    event_type: IntegrityEventType
    occurred_at: str  # ISO-8601 from the browser clock
    duration_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class IntegrityEventsMsg(_Msg):
    type: Literal["integrity.events"] = "integrity.events"
    events: list[ClientIntegrityEvent] = Field(..., max_length=50)


class HeartbeatMsg(_Msg):
    type: Literal["heartbeat"] = "heartbeat"
    t: float  # client performance.now() or epoch ms


class ClientErrorMsg(_Msg):
    """Non-fatal client failure, e.g. avatar renderer lost its WebGL context."""

    type: Literal["client.error"] = "client.error"
    component: Literal["avatar", "audio_capture", "audio_playback", "editor", "screen_share", "camera", "other"]
    message: str = Field(..., max_length=500)
    fatal: bool = False


ActivityKind = Literal["reading_aloud", "read_relay", "topic_card"]
ActivityPhase = Literal["read", "relay", "prepare", "speak"]


class ActivityFinishMsg(_Msg):
    """Intake activity finish button ("Finish reading", "I'm ready", "Start now", "I'm done")."""

    type: Literal["activity.finish"] = "activity.finish"
    activity_id: str = Field(..., max_length=64)
    phase: ActivityPhase


ClientMessage = Annotated[
    HelloMsg
    | StartInterviewMsg
    | EndTurnMsg
    | RepeatQuestionMsg
    | EndInterviewMsg
    | TypedAnswerMsg
    | PlaybackMsg
    | LocalVadMsg
    | CodeUpdateMsg
    | CodeRunMsg
    | CodeSubmitMsg
    | HintRequestMsg
    | LanguageChangeMsg
    | IntegrityEventsMsg
    | HeartbeatMsg
    | ClientErrorMsg
    | ActivityFinishMsg,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# =============================================================================
# Server -> client (every message gets ``seq`` assigned by the outbox)
# =============================================================================


class _ServerMsg(_Msg):
    seq: int = 0


class AvatarBehaviorMsg(_ServerMsg):
    type: Literal["avatar.behavior"] = "avatar.behavior"
    utterance_id: str | None = None
    state: AvatarState
    expression: AvatarExpression = AvatarExpression.NEUTRAL
    gesture: AvatarGesture = AvatarGesture.NONE
    gesture_intensity: GestureIntensity = GestureIntensity.LOW
    tone: SpeechTone = SpeechTone.PROFESSIONAL


class CodingQuestionPublic(_Msg):
    """Candidate-visible coding problem (never includes hidden tests or
    reference solutions)."""

    coding_question_id: str
    title: str
    prompt_markdown: str
    difficulty: str
    languages: list[CodeLanguage]
    starter_code: dict[str, str]
    visible_tests: list[dict[str, Any]]
    signature: dict[str, Any] | None = None


class CodingStateMsg(_Msg):
    coding_session_id: str
    question: CodingQuestionPublic
    language: CodeLanguage
    code: str | None = None
    time_limit_s: int
    elapsed_s: float = 0
    hints_available: int = 3
    hints_used: int = 0
    hint_texts: list[str] = Field(default_factory=list)


class IntakeStageMsg(_Msg):
    key: str
    number: int
    title: str  # candidate-safe title
    status: Literal["pending", "current", "done", "incomplete"]


class ActivitySnapshotMsg(_Msg):
    """Content-free snapshot of the current intake activity (inside session.state)."""

    activity_id: str
    kind: ActivityKind
    phase: ActivityPhase
    status: Literal["armed", "running", "ended"]
    remaining_ms: int | None = None


class IntakeStateMsg(_Msg):
    stage: str
    stage_number: int
    stage_title: str
    stages: list[IntakeStageMsg]
    activity: ActivitySnapshotMsg | None = None
    answer_mode: Literal["standard", "long"] = "standard"
    soft_limit_s: int | None = None  # UI hint only (90 for the self-introduction)


class SessionStateMsg(_ServerMsg):
    """Full state sync; sent on connect, on resume and on phase changes."""

    type: Literal["session.state"] = "session.state"
    session_id: str
    status: SessionStatus
    turn_state: TurnState
    round_name: str
    round_type: str
    interviewer_name: str
    elapsed_s: float
    remaining_s: float
    time_limit_s: float
    segment: str | None = None
    question_count: int = 0
    current_question: str | None = None
    show_subtitles: bool = True
    allow_typed_answers: bool = True
    integrity: dict[str, Any] = Field(default_factory=dict, description="Active monitoring flags for the UI")
    coding: CodingStateMsg | None = None
    avatar_state: AvatarState = AvatarState.IDLE
    intake: IntakeStateMsg | None = None  # intake rounds only (never any activity content)


class TurnStateMsg(_ServerMsg):
    type: Literal["turn.state"] = "turn.state"
    state: TurnState
    reason: str | None = None


class TranscriptMsg(_ServerMsg):
    type: Literal["transcript.partial", "transcript.final"]
    speaker: Literal["candidate", "interviewer"] = "candidate"
    segment_id: str
    text: str
    answer_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class UtteranceStartMsg(_ServerMsg):
    type: Literal["ai.utterance.start"] = "ai.utterance.start"
    utterance_id: str
    kind: Literal["intro", "question", "followup", "acknowledgment", "comment", "hint", "transition", "closing", "resume"]
    question_id: str | None = None
    behavior: AvatarBehaviorMsg | None = None


class TextDeltaMsg(_ServerMsg):
    """Subtitle text as it is generated (shown only when policy allows)."""

    type: Literal["ai.text.delta"] = "ai.text.delta"
    utterance_id: str
    text: str


class Viseme(_Msg):
    t: int  # ms offset from chunk start
    v: str  # Oculus viseme id: sil PP FF TH DD kk CH SS nn RR aa E I O U
    w: float = 1.0  # weight 0-1


class TtsChunkMsg(_ServerMsg):
    """Announces the next binary 0x02 frame carrying this chunk's audio."""

    type: Literal["tts.chunk"] = "tts.chunk"
    utterance_id: str
    chunk_id: int
    index: int  # 0-based position inside the utterance
    text: str
    sample_rate: int
    duration_ms: int
    visemes: list[Viseme] = Field(default_factory=list)
    word_timings: list[dict[str, Any]] = Field(default_factory=list)


class TtsFallbackMsg(_ServerMsg):
    """Server TTS unavailable: client should use browser speech or show text."""

    type: Literal["tts.fallback"] = "tts.fallback"
    utterance_id: str
    text: str
    reason: str


class UtteranceEndMsg(_ServerMsg):
    type: Literal["ai.utterance.end"] = "ai.utterance.end"
    utterance_id: str
    text: str
    interrupted: bool = False


class BargeInMsg(_ServerMsg):
    type: Literal["barge_in"] = "barge_in"
    utterance_id: str


class CodingStartMsg(_ServerMsg):
    type: Literal["coding.start"] = "coding.start"
    coding: CodingStateMsg


class CodingRunResultMsg(_ServerMsg):
    type: Literal["coding.run_result"] = "coding.run_result"
    coding_session_id: str
    run_id: str
    kind: RunKind
    status: RunStatus
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    exit_code: int | None = None
    runtime_ms: int | None = None
    passed: int = 0
    total: int = 0
    tests: list[dict[str, Any]] = Field(default_factory=list, description="Visible test details only")


class CodingHintMsg(_ServerMsg):
    type: Literal["coding.hint"] = "coding.hint"
    coding_session_id: str
    level: int
    text: str
    hints_used: int
    hints_remaining: int


class CodingEndMsg(_ServerMsg):
    type: Literal["coding.end"] = "coding.end"
    coding_session_id: str
    reason: Literal["submitted", "time_limit", "interviewer_moved_on", "session_ended"]


class IntegrityWarningMsg(_ServerMsg):
    type: Literal["integrity.warning"] = "integrity.warning"
    event_type: IntegrityEventType
    message: str


class NoticeMsg(_ServerMsg):
    """Degradation / recovery notices (STT retrying, reconnecting, etc.)."""

    type: Literal["notice"] = "notice"
    level: Literal["info", "warning", "error"] = "info"
    code: str
    message: str


class ErrorMsg(_ServerMsg):
    type: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool = True


class ActivityContentMsg(_Msg):
    title: str
    paragraphs: list[str] | None = None  # passage text (exact kit paragraphs)
    bullets: list[str] | None = None  # topic card: exactly 4
    instruction: str | None = None  # card line "Speak for about 2 minutes, connecting all four points ..."


class ActivityPhasePlanMsg(_Msg):
    phase: ActivityPhase
    duration_ms: int
    capture: bool  # microphone capture phase (reading / relay / speak)


class ActivityStartMsg(_ServerMsg):
    """An intake activity is armed (waiting for its instruction to finish). NON-REPLAYABLE; never content."""

    type: Literal["activity.start"] = "activity.start"
    activity_id: str
    kind: ActivityKind
    stage: Literal["reading", "topic"]
    title: str  # passage / card title (no body text)
    phases: list[ActivityPhasePlanMsg]
    status: Literal["armed"] = "armed"
    attempt: int = 1
    resumed: bool = False


class ActivityPhaseMsg(_ServerMsg):
    """A phase started or ended (server-authoritative timers). NON-REPLAYABLE. ``content`` only in the
    message that starts a phase in which it may be shown; ``hide_content`` removes shown content now."""

    type: Literal["activity.phase"] = "activity.phase"
    activity_id: str
    kind: ActivityKind
    phase: ActivityPhase
    status: Literal["running", "ended"]
    duration_ms: int
    remaining_ms: int  # authoritative remaining time when sent (pauses excluded)
    capture: bool
    can_finish: bool
    content: ActivityContentMsg | None = None
    hide_content: bool = False
    server_time_ms: float  # epoch ms when sent (diagnostics only)


class ActivityEndMsg(_ServerMsg):
    type: Literal["activity.end"] = "activity.end"
    activity_id: str
    kind: ActivityKind
    reason: Literal["completed", "finished_early", "deadline", "interrupted", "skipped", "session_ended"]


class SessionEndedMsg(_ServerMsg):
    type: Literal["session.ended"] = "session.ended"
    reason: str
    report_status: str = "pending"
    candidate_report_available: bool = False


class HeartbeatAckMsg(_ServerMsg):
    type: Literal["heartbeat.ack"] = "heartbeat.ack"
    t: float
    server_time: float


ServerMessage = (
    AvatarBehaviorMsg
    | SessionStateMsg
    | TurnStateMsg
    | TranscriptMsg
    | UtteranceStartMsg
    | TextDeltaMsg
    | TtsChunkMsg
    | TtsFallbackMsg
    | UtteranceEndMsg
    | BargeInMsg
    | CodingStartMsg
    | CodingRunResultMsg
    | CodingHintMsg
    | CodingEndMsg
    | IntegrityWarningMsg
    | NoticeMsg
    | ErrorMsg
    | SessionEndedMsg
    | HeartbeatAckMsg
    | ActivityStartMsg
    | ActivityPhaseMsg
    | ActivityEndMsg
)


def pack_frame(frame_type: int, seq_or_chunk: int, payload: bytes, *, final: bool = False) -> bytes:
    header = bytes([frame_type, 0x01 if final else 0x00, 0, 0]) + int(seq_or_chunk).to_bytes(4, "little")
    return header + payload


def unpack_frame(data: bytes) -> tuple[int, int, int, bytes]:
    """Return (frame_type, flags, seq_or_chunk, payload)."""
    if len(data) < FRAME_HEADER_BYTES:
        raise ValueError("binary frame shorter than header")
    return data[0], data[1], int.from_bytes(data[4:8], "little"), data[FRAME_HEADER_BYTES:]
