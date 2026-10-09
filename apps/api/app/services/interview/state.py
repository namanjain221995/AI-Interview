"""Persistent structured interview state.

``InterviewState`` is owned by the interview engine. The realtime runtime may
read it but changes it only through ``TurnEngine`` methods. It is persisted to
Redis (hot copy, every change) and to ``interview_sessions.state`` (durable
snapshot at every committed turn and every 15 s), guarded by ``version``.

Competency state is *derived*: ``app.services.scoring`` recomputes it from the
evidence records at any time, so a lost Redis copy never loses scoring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    AnswerQuality,
    AvatarState,
    ClaimStatus,
    CodeLanguage,
    Difficulty,
    SessionEndReason,
    SessionStatus,
    TurnAction,
    TurnState,
)
from app.schemas.ai import ClaimToVerify, InterviewPlan
from app.schemas.rounds import RoundConfig
from app.services.intake.state import IntakeState


class _State(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)


def utcnow() -> datetime:
    return datetime.now(UTC)


class CompetencyState(_State):
    key: str
    name: str | None = None
    weight: float = 1.0
    required: bool = True
    score_estimate: float = 5.0  # 0-10, Bayesian-shrunk toward 5 with little evidence
    confidence: float = 0.0  # 0-1
    evidence_count: int = 0
    positive_evidence: int = 0
    negative_evidence: int = 0
    positive_evidence_ids: list[str] = Field(default_factory=list)
    negative_evidence_ids: list[str] = Field(default_factory=list)
    dimensions_covered: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list, description="Expected evidence not yet heard")
    difficulty_seen: dict[str, int] = Field(default_factory=dict)
    current_difficulty: Difficulty = Difficulty.MEDIUM
    questions_asked: int = 0
    followups_on_current_topic: int = 0
    last_quality: AnswerQuality | None = None
    sufficient_evidence: bool = False
    score_history: list[float] = Field(default_factory=list)
    last_updated_s: float = 0.0
    # Additive (engine flow tracking; not derived from evidence).
    category: str | None = None  # CompetencyCategory value when known
    focus_areas: list[str] = Field(default_factory=list)
    weak_answers_at_level: int = 0  # weak answers at current_difficulty (two -> step down)


class ClaimState(_State):
    claim: ClaimToVerify
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    attempts: int = 0
    question_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class ContradictionState(_State):
    id: str
    description: str
    refs: list[str] = Field(default_factory=list, description="answer/question ids or 'resume'")
    severity: str = "low"
    status: str = "open"  # open | verifying | resolved | confirmed


class PendingFollowup(_State):
    text: str
    reason: str
    competency_key: str | None = None
    priority: int = 3
    source_answer_id: str | None = None


class QuestionRecord(_State):
    id: str
    sequence: int
    action: TurnAction
    text: str
    focus: str | None = None
    competency_key: str | None = None
    topic: str | None = None
    difficulty: Difficulty = Difficulty.MEDIUM
    parent_id: str | None = None
    claim_id: str | None = None
    bank_question_id: str | None = None
    asked_at_s: float = 0.0
    interrupted: bool = False
    # Additive
    kind: str | None = None  # QuestionKind value
    expected_evidence: list[str] = Field(default_factory=list)
    spoken_text: str | None = None
    delivered: bool = False
    answered: bool = False
    coding_question_id: str | None = None


class AnswerRecord(_State):
    id: str
    question_id: str | None
    sequence: int
    text: str
    quality: AnswerQuality | None = None
    evaluated: bool = False
    typed: bool = False
    started_s: float = 0.0
    ended_s: float = 0.0
    # Additive
    interrupted_ai: bool = False
    special: str | None = None  # SpecialKind detected by fast heuristics
    word_count: int = 0
    quality_source: str | None = None  # quick | special | model | evaluation


class UtteranceRecord(_State):
    """Additive: an interviewer utterance (for delivery, interruption,
    backchannel resume and reconnect re-orientation)."""

    id: str
    kind: str
    action: TurnAction | None = None
    question_id: str | None = None
    text: str = ""
    spoken_text: str | None = None
    delivered: bool = False
    interrupted: bool = False
    at_s: float = 0.0
    transcript_id: str | None = None


class ExchangeTurn(_State):
    """Compact conversation memory entry used for prompts."""

    speaker: str  # interviewer | candidate
    text: str
    at_s: float
    question_id: str | None = None
    answer_id: str | None = None
    interrupted: bool = False


class CodingRunSummary(_State):
    run_id: str
    kind: str
    status: str
    passed: int = 0
    total: int = 0
    at_s: float = 0.0
    error_excerpt: str | None = None


class CodingState(_State):
    active: bool = False
    completed: bool = False
    coding_session_id: str | None = None
    coding_question_id: str | None = None
    question_id: str | None = None
    title: str | None = None
    language: CodeLanguage | None = None
    started_at_s: float = 0.0
    time_limit_s: int = 1200
    hints_available: int = 3
    hints_used: int = 0
    last_code_version: int = 0
    last_snapshot_seq: int = 0
    last_edit_at_s: float = 0.0
    last_comment_at_s: float = 0.0
    comments_made: int = 0
    last_run: CodingRunSummary | None = None
    runs: int = 0
    problems_done: int = 0
    # Additive
    observer_state: dict[str, Any] = Field(default_factory=dict, description="State dict owned by coding.observer.decide")
    followup_pending: bool = False
    ended_reason: str | None = None
    time_warnings_spoken: list[int] = Field(default_factory=list)
    last_hint_level: int = 0
    unavailable: bool = False  # no coding problem could be selected / service missing
    coding_session_ids: list[str] = Field(default_factory=list, description="Every coding session started in this interview")
    evaluated_session_ids: list[str] = Field(default_factory=list, description="Coding sessions whose evaluation was applied")


class IntegrityCounters(_State):
    counts: dict[str, int] = Field(default_factory=dict)
    warnings_issued: int = 0
    flagged_for_review: int = 0
    last_event_at_s: float = 0.0


class InterviewState(_State):
    version: int = 0
    session_id: str
    interview_id: str
    organization_id: str
    candidate_id: str
    job_id: str
    round_id: str
    status: SessionStatus = SessionStatus.CREATED
    turn_state: TurnState = TurnState.IDLE
    avatar_state: AvatarState = AvatarState.IDLE

    # Context (compact copies; full documents live in Postgres)
    candidate_name: str | None = None
    candidate_profile: dict[str, Any] = Field(default_factory=dict, description="Compact CandidateProfile")
    job_summary: dict[str, Any] = Field(default_factory=dict)
    round_config: RoundConfig
    plan: InterviewPlan | None = None
    interviewer_name: str = "Maya"

    # Time accounting (pauses excluded from elapsed)
    started_at: datetime | None = None
    last_resumed_at: datetime | None = None
    elapsed_before_pause_s: float = 0.0
    paused_total_s: float = 0.0
    time_limit_s: float = 1800.0
    max_time_s: float = 2400.0

    # Flow
    segment_index: int = 0
    current_segment: str | None = None
    current_topic: str | None = None
    current_competency: str | None = None
    current_difficulty: Difficulty = Difficulty.MEDIUM
    difficulty_counts: dict[str, int] = Field(default_factory=lambda: {"easy": 0, "medium": 0, "hard": 0})
    turn_index: int = 0
    current_question_id: str | None = None
    awaiting_answer: bool = False
    candidate_questions_invited: bool = False
    closing_started: bool = False
    end_reason: SessionEndReason | None = None

    # Records
    questions: list[QuestionRecord] = Field(default_factory=list)
    answers: list[AnswerRecord] = Field(default_factory=list)
    partial_transcript: str = ""
    last_final_transcript: str = ""
    conversation: list[ExchangeTurn] = Field(default_factory=list, description="Recent exchanges (bounded)")
    memory_summary: str = ""

    # Evidence
    competencies: dict[str, CompetencyState] = Field(default_factory=dict)
    claims: dict[str, ClaimState] = Field(default_factory=dict)
    contradictions: list[ContradictionState] = Field(default_factory=list)
    pending_followups: list[PendingFollowup] = Field(default_factory=list)
    skills_covered: list[str] = Field(default_factory=list)
    skills_uncovered: list[str] = Field(default_factory=list)
    pending_evaluations: list[str] = Field(default_factory=list, description="answer ids awaiting deep evaluation")

    # Sub-states
    coding: CodingState = Field(default_factory=CodingState)
    hints_used_total: int = 0
    integrity: IntegrityCounters = Field(default_factory=IntegrityCounters)

    # ------------------------------------------------------------ additive
    # Preparation (session.prepare)
    opening_text: str | None = None
    prepared_first_question: dict[str, Any] | None = None
    plan_source: str | None = None  # model | fallback
    prepared_at: datetime | None = None
    # Delivery tracking
    utterances: list[UtteranceRecord] = Field(default_factory=list, description="Recent interviewer utterances (bounded)")
    awaiting_since_s: float | None = None
    silence_prompts: int = 0
    simplify_attempts: int = 0  # clarify/simplify attempts on the current question chain
    last_turn_key: str | None = None
    # Time
    segment_started_s: float = 0.0
    time_warnings: list[int] = Field(default_factory=list, description="Remaining-seconds thresholds already handled")
    pending_time_notice: str | None = None
    # Flags / policy
    force_coding: bool = False
    audio_recording_allowed: bool = False
    monitoring_enabled: bool = False
    candidate_report_mode: str | None = None
    tts_voice: str | None = None
    audio_retention_days: int | None = None
    profile_status: str | None = None  # ready | partial | missing | timeout
    profile_wait_started_at: datetime | None = None
    prepare_attempts: int = 0
    ungrounded_reference: str | None = None  # last reply referenced something never said
    # Turn understanding (understanding.py): flow replies (audio checks, "go ahead", nudges) on the current question
    flow_question_id: str | None = None
    flow_counts: dict[str, int] = Field(default_factory=dict)
    closing_utterance_id: str | None = None
    # Memory
    memory_upto: int = 0  # number of candidate answers covered by memory_summary
    memory_key_facts: list[str] = Field(default_factory=list)
    # Monitoring counters
    deviations: int = 0
    fallbacks_used: int = 0
    reconnects: int = 0
    # Intake mode (round_type intake; INTAKE_INTERVIEW.md 6.7): None for every other round type.
    intake: IntakeState | None = None

    # ------------------------------------------------------------------ time
    def elapsed_seconds(self, now: datetime | None = None) -> float:
        if self.last_resumed_at is None:
            return self.elapsed_before_pause_s
        now = now or utcnow()
        return self.elapsed_before_pause_s + max(0.0, (now - self.last_resumed_at).total_seconds())

    def remaining_seconds(self, now: datetime | None = None) -> float:
        return max(0.0, self.time_limit_s - self.elapsed_seconds(now))

    def pause(self, now: datetime | None = None) -> None:
        if self.last_resumed_at is not None:
            self.elapsed_before_pause_s = self.elapsed_seconds(now)
            self.last_resumed_at = None

    def resume(self, now: datetime | None = None) -> None:
        if self.last_resumed_at is None:
            self.last_resumed_at = now or utcnow()
            if self.started_at is None:
                self.started_at = self.last_resumed_at

    # --------------------------------------------------------------- helpers
    @property
    def claims_verified(self) -> list[str]:
        return [k for k, c in self.claims.items() if c.status in (ClaimStatus.VERIFIED, ClaimStatus.PARTIALLY_VERIFIED)]

    @property
    def claims_unverified(self) -> list[str]:
        return [k for k, c in self.claims.items() if c.status in (ClaimStatus.UNVERIFIED, ClaimStatus.IN_PROGRESS)]

    @property
    def candidate_first_name(self) -> str | None:
        if not self.candidate_name:
            return None
        first = self.candidate_name.strip().split()[0] if self.candidate_name.strip() else ""
        return first or None

    def question_by_id(self, question_id: str | None) -> QuestionRecord | None:
        if not question_id:
            return None
        for q in reversed(self.questions):
            if q.id == question_id:
                return q
        return None

    def utterance_by_id(self, utterance_id: str | None) -> UtteranceRecord | None:
        if not utterance_id:
            return None
        for u in reversed(self.utterances):
            if u.id == utterance_id:
                return u
        return None

    def current_question(self) -> QuestionRecord | None:
        if not self.current_question_id:
            return None
        for q in reversed(self.questions):
            if q.id == self.current_question_id:
                return q
        return None
