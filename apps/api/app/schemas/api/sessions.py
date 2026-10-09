"""Recruiter-facing session DTOs (``/api/v1/sessions``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import (
    Recommendation,
    ReportStatus,
    ReviewDecisionValue,
    SessionEndReason,
    SessionStatus,
)
from app.schemas.api.common import APIModel


class PersonRef(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None


class JobRef(BaseModel):
    id: uuid.UUID
    title: str


class RoundRef(BaseModel):
    id: uuid.UUID
    name: str
    round_type: str


class SessionSummaryOut(BaseModel):
    id: uuid.UUID
    interview_id: uuid.UUID
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    status: SessionStatus
    started_at: datetime | None
    ended_at: datetime | None
    end_reason: SessionEndReason | None
    elapsed_seconds: float
    reconnect_count: int
    created_at: datetime
    updated_at: datetime
    candidate: PersonRef | None = None
    job: JobRef | None = None
    round: RoundRef | None = None
    overall_score: float | None = Field(None, description="0-100, newest computed score")
    confidence: float | None = None
    recommendation: Recommendation | None = Field(None, description="Assistive only")
    report_status: ReportStatus | None = None
    review_decision: ReviewDecisionValue | None = None
    integrity_flags: int = Field(0, description="Unreviewed integrity signals flagged for human review")
    question_count: int = 0
    live: bool = False


class SessionDurations(BaseModel):
    elapsed_seconds: float = Field(..., description="Interview clock (pauses excluded)")
    wall_seconds: float | None = Field(None, description="Started -> ended (or now while running)")
    paused_seconds: float | None = None
    scheduled_seconds: float
    max_seconds: float
    remaining_seconds: float | None = None


class ReportStatusSummary(BaseModel):
    id: uuid.UUID
    version: int
    status: ReportStatus
    generated_at: datetime | None = None
    released_to_candidate: bool = False


class SessionDetailOut(SessionSummaryOut):
    round_config: dict[str, Any]
    plan: dict[str, Any] | None = None
    state_digest: dict[str, Any] | None = None
    client_info: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    durations: SessionDurations
    recommendation_rationale: str | None = None
    human_decision_note: str = "This recommendation is assistive. A human reviewer owns the hiring decision."
    scores_final: bool = False
    scores_computed_at: datetime | None = None
    rubric: list[dict[str, Any]] = Field(default_factory=list)
    reports: dict[str, ReportStatusSummary] = Field(default_factory=dict)
    candidate_report_mode: str | None = None
    claims: list[dict[str, Any]] = Field(default_factory=list)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    review_count: int = 0


class TranscriptSegmentOut(APIModel):
    id: uuid.UUID
    session_id: uuid.UUID
    seq: int
    speaker: str
    kind: str
    text: str
    start_ms: int | None
    end_ms: int | None
    confidence: float | None
    question_id: uuid.UUID | None
    answer_id: uuid.UUID | None
    interrupted: bool
    created_at: datetime
    audio_media_id: uuid.UUID | None = None


class TranscriptOut(BaseModel):
    session_id: uuid.UUID
    items: list[TranscriptSegmentOut]
    total: int
    limit: int
    offset: int
    interruptions: int = 0
    answer_audio: dict[str, uuid.UUID] = Field(
        default_factory=dict, description="answer id -> audio media id"
    )


class EvidenceOut(APIModel):
    id: uuid.UUID
    session_id: uuid.UUID
    competency_key: str
    question_id: uuid.UUID | None
    answer_id: uuid.UUID | None
    evaluation_id: uuid.UUID | None
    coding_session_id: uuid.UUID | None
    source: str
    signal: str
    dimension: str
    evidence: str
    strength: float
    confidence: float
    difficulty: str | None
    elapsed_seconds: float | None
    transcript_ref: dict[str, Any]
    created_at: datetime


class AnswerEvaluationOut(APIModel):
    id: uuid.UUID
    answer_id: uuid.UUID
    question_id: uuid.UUID | None
    session_id: uuid.UUID
    overall_quality: str
    correctness: float | None = None
    relevance: float | None = None
    completeness: float | None = None
    depth: float | None = None
    specificity: float | None = None
    practical_experience: float | None = None
    reasoning: float | None = None
    consistency: float | None = None
    architecture_awareness: float | None = None
    tradeoff_understanding: float | None = None
    communication_clarity: float | None = None
    confidence: float
    is_fallback: bool
    evaluation: dict[str, Any]
    created_at: datetime


class SessionAnswerOut(APIModel):
    id: uuid.UUID
    question_id: uuid.UUID | None
    sequence: int
    transcript_text: str
    typed: bool
    started_ms: int | None
    ended_ms: int | None
    duration_ms: int | None
    word_count: int
    stt_confidence: float | None
    interrupted_ai: bool
    status: str
    audio_media_id: uuid.UUID | None = None
    evaluation: AnswerEvaluationOut | None = None
    evidence: list[EvidenceOut] = Field(default_factory=list)


class SessionQuestionOut(APIModel):
    id: uuid.UUID
    session_id: uuid.UUID
    sequence: int
    parent_question_id: uuid.UUID | None
    bank_question_id: uuid.UUID | None
    coding_question_id: uuid.UUID | None
    kind: str
    action: str
    source: str
    segment: str | None
    topic: str | None
    competency_keys: list[str]
    difficulty: str
    text: str
    spoken_text: str | None
    question_focus: str | None
    reason: str | None
    expected_evidence: list[str]
    claim_id: str | None
    interrupted: bool
    asked_at: datetime
    elapsed_seconds: float
    followup_ids: list[uuid.UUID] = Field(default_factory=list)
    followup_depth: int = 0
    chain_root_id: uuid.UUID | None = None
    answers: list[SessionAnswerOut] = Field(default_factory=list)


class ScoreOut(APIModel):
    id: uuid.UUID
    session_id: uuid.UUID
    category: str
    key: str
    label: str | None
    score: float | None = Field(
        ..., description="NULL = not enough evidence to score (never a made-up midpoint)"
    )
    confidence: float
    weight: float | None
    evidence_count: int
    positive_count: int
    negative_count: int
    sufficient_evidence: bool
    unanswered_areas: list[str]
    supporting_question_ids: list[str]
    positive_evidence_ids: list[str]
    negative_evidence_ids: list[str]
    transcript_refs: list[dict[str, Any]]
    details: dict[str, Any]
    is_final: bool
    created_at: datetime


class QuestionRef(BaseModel):
    id: uuid.UUID
    sequence: int
    text: str
    focus: str | None = None
    difficulty: str | None = None


class ScoreWhy(BaseModel):
    """The "why this score" view of one competency."""

    positive_evidence: list[EvidenceOut] = Field(default_factory=list)
    negative_evidence: list[EvidenceOut] = Field(default_factory=list)
    unanswered_areas: list[str] = Field(default_factory=list)
    supporting_questions: list[QuestionRef] = Field(default_factory=list)
    transcript_refs: list[dict[str, Any]] = Field(default_factory=list)
    calculation: dict[str, Any] | None = None
    observations: list[dict[str, Any]] = Field(default_factory=list)


class CompetencyScoreOut(ScoreOut):
    why: ScoreWhy


class SessionScoresOut(BaseModel):
    session_id: uuid.UUID
    source: str = Field(
        ...,
        description="persisted | live (computed on the fly, not stored) | intake_pending (intake evaluation not ready)",
    )
    is_final: bool = False
    computed_at: datetime | None = None
    overall: ScoreOut | None = None
    rubric: list[ScoreOut] = Field(default_factory=list)
    competencies: list[CompetencyScoreOut] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    rationale: str | None = None
    factors: dict[str, Any] = Field(default_factory=dict)
    human_decision_note: str = "This recommendation is assistive. A human reviewer owns the hiring decision."


class TimelineSnapshotOut(BaseModel):
    elapsed_seconds: float
    competency_key: str
    score: float
    confidence: float
    evidence_count: int
    trigger: str | None = None


class TimelineEventOut(BaseModel):
    seq: int
    type: str
    elapsed_seconds: float | None
    payload: dict[str, Any]
    created_at: datetime


class TimelineWindowOut(BaseModel):
    index: int
    start_seconds: float
    end_seconds: float
    label: str
    scores: dict[str, float]
    details: dict[str, dict[str, Any]]
    summary: str


class TimelineOut(BaseModel):
    session_id: uuid.UUID
    window_minutes: float
    duration_seconds: float
    source: str = Field(..., description="snapshots | replay (reconstructed from evidence)")
    competencies: list[dict[str, Any]]
    windows: list[TimelineWindowOut]
    snapshots: list[TimelineSnapshotOut]
    series: dict[str, list[dict[str, Any]]]
    events: list[TimelineEventOut]


class ReviewPointOut(BaseModel):
    competency_key: str
    competency: str
    text: str
    quote: str | None = None
    evidence_id: str | None = None
    question_id: str | None = None
    answer_id: str | None = None
    start_ms: int | None = None


class ReviewFactsOut(BaseModel):
    duration_seconds: float
    planned_seconds: float
    end_reason: str | None = None
    ended_early: bool = False
    questions_asked: int
    questions_answered: int
    questions_skipped: int
    unprompted_answers: int = 0
    candidate_words: int
    candidate_speaking_seconds: float
    minutes_answered: float
    candidate_talk_share: float | None = None
    silence_share: float | None = None
    speaking_time_estimated: bool = False


class AIAssessmentOut(BaseModel):
    text: str
    source: str | None = Field(None, description="recruiter_report | intake_evaluation")
    generated_at: str | None = None


class ReviewSummaryOut(BaseModel):
    """Deterministic facts first, evidence-backed strengths/weaknesses, AI text only with enough evidence."""

    session_id: uuid.UUID
    kind: str = Field(..., description="interview | intake")
    completed: bool
    evidence_level: str = Field(..., description="none | thin | partial | sufficient")
    headline: str
    insufficient: bool
    facts: ReviewFactsOut
    statements: list[str]
    evidence_statement: str | None = None
    assessed_competencies: int
    total_competencies: int
    overall_score: float | None = None
    recommendation: str | None = None
    strengths: list[ReviewPointOut] = Field(default_factory=list)
    weaknesses: list[ReviewPointOut] = Field(default_factory=list)
    ai_assessment: AIAssessmentOut | None = None
    ai_withheld_reason: str | None = None
    note: str
