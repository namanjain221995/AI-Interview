"""Seam between the realtime runtime (``app.realtime``) and the interview
engine (``app.services.interview``).

The realtime layer owns transport concerns: audio, VAD, STT scheduling, TTS,
barge-in, playback tracking, reconnection, the outbox. The engine owns every
interview decision and the ``InterviewState``. They communicate only through
``TurnEngine`` and the ``TurnEvent`` stream it yields.

Flow for one candidate answer::

    realtime: VAD endpoint -> final transcript -> CandidateTurn
    engine:   handle_candidate_turn(turn) -> AsyncIterator[TurnEvent]
                UtteranceBegin  (kind, provisional avatar behaviour)
                SpeechDelta*    (text deltas, streamed as generated)
                UtteranceMeta   (validated TurnDecision -> final behaviour, question id)
                CodingStarted?  (coding payload when the move starts coding)
                SessionClosing? (after the closing utterance)
    realtime: SpeechDelta -> sentence chunker -> TTS -> client; on barge-in it
              cancels the iterator (``aclose``) and calls handle_interruption().
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from app.domain.enums import (
    AvatarExpression,
    AvatarGesture,
    AvatarState,
    GestureIntensity,
    SessionEndReason,
    SpeechTone,
)
from app.schemas.realtime import CodingRunResultMsg, CodingStateMsg

UtteranceKind = Literal[
    "intro", "question", "followup", "acknowledgment", "comment", "hint", "transition", "closing", "resume"
]


# ----------------------------------------------------------------- inputs


@dataclass(slots=True)
class CandidateTurn:
    """A completed candidate turn (spoken or typed)."""

    text: str
    started_ms: int  # relative to session start
    ended_ms: int
    typed: bool = False
    interrupted_ai: bool = False  # the candidate barged in on the interviewer
    stt_confidence: float | None = None
    audio_wav: bytes | None = None  # stored when recording policy allows
    speculative: bool = False  # True while the endpoint is not yet confirmed


@dataclass(slots=True)
class InterruptedUtterance:
    utterance_id: str
    question_id: str | None
    full_text: str
    spoken_text: str  # what the candidate actually heard (played chunks)
    played_ms: int


@dataclass(slots=True)
class CodingActivity:
    """Code-related signal from the runtime."""

    kind: Literal["edit", "run_completed", "submitted", "hint_requested", "language_changed", "idle", "time_warning", "time_up"]
    coding_session_id: str
    code: str | None = None
    language: str | None = None
    version: int | None = None
    run: CodingRunResultMsg | None = None
    requested_via: Literal["button", "voice"] = "button"
    idle_seconds: float | None = None


@dataclass(slots=True)
class TimerEvent:
    """Timer signal from the runtime.

    ``time_warning`` carries ``detail={"remaining_s": 300 | 60}``;
    ``silence_timeout`` may carry ``detail={"count": n}``. ``disconnect``
    (additive) pauses the interview clock and marks the current utterance
    for re-speaking; ``resume_after_disconnect`` is equivalent to
    ``resume(reconnect=True)``.
    """

    kind: Literal[
        "tick", "time_warning", "time_up", "max_time", "silence_timeout", "resume_after_disconnect", "disconnect"
    ]
    elapsed_s: float
    detail: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------- outputs


@dataclass(slots=True)
class BehaviorSpec:
    state: AvatarState = AvatarState.SPEAKING
    expression: AvatarExpression = AvatarExpression.NEUTRAL
    gesture: AvatarGesture = AvatarGesture.NONE
    gesture_intensity: GestureIntensity = GestureIntensity.LOW
    tone: SpeechTone = SpeechTone.PROFESSIONAL


@dataclass(slots=True)
class UtteranceBegin:
    utterance_id: str
    kind: UtteranceKind
    behavior: BehaviorSpec
    question_id: str | None = None  # known up front for pre-generated utterances
    text: str | None = None  # full text when not streamed (cached/pre-generated)


@dataclass(slots=True)
class SpeechDelta:
    utterance_id: str
    text: str


@dataclass(slots=True)
class UtteranceMeta:
    """Sent once the decision is complete and validated."""

    utterance_id: str
    question_id: str | None
    behavior: BehaviorSpec
    decision: dict[str, Any]
    full_text: str


@dataclass(slots=True)
class CodingStarted:
    coding: CodingStateMsg


@dataclass(slots=True)
class CodingHintIssued:
    coding_session_id: str
    level: int
    text: str
    hints_used: int
    hints_remaining: int


@dataclass(slots=True)
class CodingEnded:
    coding_session_id: str
    reason: Literal["submitted", "time_limit", "interviewer_moved_on", "session_ended"]


@dataclass(slots=True)
class SessionClosing:
    reason: SessionEndReason


@dataclass(slots=True)
class StateChanged:
    """Ask the runtime to push a fresh ``session.state`` to the client."""

    reason: str


TurnEvent = (
    UtteranceBegin | SpeechDelta | UtteranceMeta | CodingStarted | CodingHintIssued | CodingEnded | SessionClosing | StateChanged
)


@runtime_checkable
class TurnEngine(Protocol):
    """Implemented by ``app.services.interview.engine.InterviewEngine``."""

    async def prepare_session(self, session_id: str) -> None:
        """Build plan + opening ahead of time (called from the session.prepare
        job while the candidate runs the system check). Idempotent."""
        ...

    async def load_state(self, session_id: str):  # -> InterviewState
        ...

    async def start(self, session_id: str) -> AsyncIterator[TurnEvent]:
        """Session goes LIVE: introduction + first question."""
        ...

    async def resume(self, session_id: str, *, reconnect: bool) -> AsyncIterator[TurnEvent]:
        """After reconnect/restart: brief re-orientation and repeat of the
        pending question (or nothing when the candidate was mid-answer)."""
        ...

    async def on_partial_transcript(self, session_id: str, text: str, *, silence_started: bool) -> None:
        """Speculative preparation (topic detection, evidence retrieval,
        quick assessment). Must never commit state."""
        ...

    async def handle_candidate_turn(self, session_id: str, turn: CandidateTurn) -> AsyncIterator[TurnEvent]: ...

    async def handle_interruption(self, session_id: str, interrupted: InterruptedUtterance) -> None:
        """Record that an utterance was cut off (spoken portion only)."""
        ...

    async def handle_backchannel_resume(self, session_id: str, interrupted: InterruptedUtterance) -> AsyncIterator[TurnEvent]:
        """Barge-in turned out to be a backchannel ('mm-hmm'): continue the
        interrupted utterance from where it stopped."""
        ...

    async def repeat_question(self, session_id: str) -> AsyncIterator[TurnEvent]: ...

    async def handle_coding_activity(self, session_id: str, activity: CodingActivity) -> AsyncIterator[TurnEvent]:
        """May yield nothing (silence is the default while coding)."""
        ...

    async def handle_timer(self, session_id: str, timer: TimerEvent) -> AsyncIterator[TurnEvent]: ...

    async def end_session(self, session_id: str, reason: SessionEndReason) -> AsyncIterator[TurnEvent]:
        """Closing utterance (if appropriate), status COMPLETING, enqueue
        scoring.finalize + report.generate."""
        ...

    async def mark_utterance_delivered(self, session_id: str, utterance_id: str, *, spoken_text: str) -> None:
        """Runtime confirms playback finished (question asked -> awaiting answer)."""
        ...


@runtime_checkable
class SessionService(Protocol):
    """Session lifecycle used by the candidate portal (``app.services.interview.sessions``)."""

    async def create_or_resume(self, db: Any, *, interview_id: Any, round_id: Any | None = None) -> Any:
        """Return the open session for the interview (status created/preparing/
        ready/live/paused) or create one: freeze RoundConfig into
        interview_sessions.round_config, link the latest candidate profile,
        initialise InterviewState, enqueue session.prepare. Idempotent."""
        ...

    async def candidate_view(self, db: Any, session_id: Any) -> dict[str, Any]:
        """Candidate-safe session summary (status, round, durations, coding, report availability)."""
        ...
