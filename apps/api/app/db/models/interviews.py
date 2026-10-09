"""Interview invitations, consents, live sessions and everything a session
produces: event log, questions, answers, transcripts, evaluations, evidence,
scores, timeline snapshots, reports and human review decisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Interview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An invitation for one candidate to one job (and optionally one round)."""

    __tablename__ = "interviews"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    # NULL until chosen when the candidate may select among selectable rounds.
    round_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rounds.id", ondelete="SET NULL"))
    allow_round_selection: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="invited", server_default="invited")
    invite_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invite_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    access_code_hash: Mapped[str | None] = mapped_column(String(255))  # argon2 of optional access code
    invite_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_verification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Effective policies frozen at invite time: integrity, recording, retention,
    # candidate report mode, typed answers, subtitles.
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    system_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_interviews_org_status", "organization_id", "status"),
        # Interview list: newest first within an organization.
        Index("ix_interviews_org_created", "organization_id", "created_at"),
    )


class Consent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Recorded consent acknowledgment for the disclosed monitoring policy."""

    __tablename__ = "consents"

    interview_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of disclosed text + settings
    # {"microphone": true, "camera": true, "transcript": true, "integrity_monitoring": true,
    #  "screen_share": false, "audio_recording": true, "video_recording": false,
    #  "retention_days": 180, "ai_assessment": true}
    items: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))


class InterviewSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One live run of an interview round.

    ``state`` is the durable snapshot of ``app.services.interview.state.InterviewState``
    (Redis holds the hot copy). ``state_version`` increases monotonically and
    guards against lost updates.
    """

    __tablename__ = "interview_sessions"

    interview_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rounds.id", ondelete="RESTRICT"), nullable=False)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_profiles.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created", server_default="created")
    round_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # InterviewPlan
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(32))  # SessionEndReason
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    reconnect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_info: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_interview_sessions_org_status", "organization_id", "status"),
        # Session list order: started_at DESC NULLS LAST, created_at DESC.
        Index(
            "ix_interview_sessions_org_started",
            "organization_id",
            text("started_at DESC NULLS LAST"),
            text("created_at DESC"),
        ),
    )


class InterviewEvent(Base):
    """Append-only event log for a session (audit + replay + debugging)."""

    __tablename__ = "interview_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_interview_events_session_seq"),
        Index("ix_interview_events_session_type", "session_id", "type"),
    )


class Question(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A question actually asked in a session (including follow-ups)."""

    __tablename__ = "questions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL")
    )
    bank_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_bank.id", ondelete="SET NULL")
    )
    coding_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_questions.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # QuestionKind
    action: Mapped[str] = mapped_column(String(40), nullable=False)  # TurnAction
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="generated")  # QuestionSource
    segment: Mapped[str | None] = mapped_column(String(32))  # SegmentKind
    topic: Mapped[str | None] = mapped_column(String(120))
    competency_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    text: Mapped[str] = mapped_column(Text, nullable=False)  # full utterance generated
    spoken_text: Mapped[str | None] = mapped_column(Text)  # what was actually delivered (barge-in)
    question_focus: Mapped[str | None] = mapped_column(String(300))  # canonical short form
    reason: Mapped[str | None] = mapped_column(Text)
    expected_evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    claim_id: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_questions_session_sequence"),)


class Answer(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "answers"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    typed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    started_ms: Mapped[int | None] = mapped_column(BigInteger)  # relative to session start
    ended_ms: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    stt_confidence: Mapped[float | None] = mapped_column(Float)
    audio_storage_key: Mapped[str | None] = mapped_column(String(512))
    interrupted_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    quick_assessment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received", server_default="received")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_answers_session_sequence"),)


class Transcript(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Final transcript segments for both speakers (partials stay in Redis)."""

    __tablename__ = "transcripts"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)  # candidate | interviewer | system
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="final")  # final | partial
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int | None] = mapped_column(BigInteger)
    end_ms: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[float | None] = mapped_column(Float)
    question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"))
    answer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("answers.id", ondelete="SET NULL"))
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_transcripts_session_seq"),
        Index("ix_transcripts_session_start", "session_id", "start_ms"),
    )


class AnswerEvaluation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "answer_evaluations"

    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    overall_quality: Mapped[str] = mapped_column(String(32), nullable=False)  # AnswerQuality
    # Dimension scores 0-10 (NULL when not applicable to the question).
    correctness: Mapped[float | None] = mapped_column(Float)
    relevance: Mapped[float | None] = mapped_column(Float)
    completeness: Mapped[float | None] = mapped_column(Float)
    depth: Mapped[float | None] = mapped_column(Float)
    specificity: Mapped[float | None] = mapped_column(Float)
    practical_experience: Mapped[float | None] = mapped_column(Float)
    reasoning: Mapped[float | None] = mapped_column(Float)
    consistency: Mapped[float | None] = mapped_column(Float)
    architecture_awareness: Mapped[float | None] = mapped_column(Float)
    tradeoff_understanding: Mapped[float | None] = mapped_column(Float)
    communication_clarity: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class CompetencyEvidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One atomic, attributable piece of evidence about one competency."""

    __tablename__ = "competency_evidence"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    competency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"))
    answer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("answers.id", ondelete="SET NULL"))
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answer_evaluations.id", ondelete="CASCADE")
    )
    coding_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="answer")  # EvidenceSource
    signal: Mapped[str] = mapped_column(String(16), nullable=False)  # EvidenceSignal
    dimension: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)  # 0-1
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)  # 0-1
    difficulty: Mapped[str | None] = mapped_column(String(16))
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    # {"answer_id": "...", "start_ms": 1200, "end_ms": 5400, "quote": "..."}
    transcript_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (Index("ix_competency_evidence_session_key", "session_id", "competency_key"),)


class Score(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Computed score (competency, rubric category or overall)."""

    __tablename__ = "scores"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # ScoreCategory
    key: Mapped[str] = mapped_column(String(80), nullable=False)  # competency key / rubric category / "overall"
    label: Mapped[str | None] = mapped_column(String(200))
    # competency 0-10; rubric/overall 0-100; NULL = not enough evidence to score (never a made-up midpoint)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weight: Mapped[float | None] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sufficient_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unanswered_areas: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    supporting_question_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    positive_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    negative_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    transcript_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        Index("ix_scores_session_category_key", "session_id", "category", "key"),
        # Newest score set of a session: max(created_at) per session (index-only).
        Index("ix_scores_session_created", "session_id", "created_at"),
    )


class ScoreSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Competency estimate over time (performance timeline charts)."""

    __tablename__ = "score_snapshots"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    competency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-10
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger: Mapped[str | None] = mapped_column(String(32))  # answer_evaluated | coding_evaluated | final

    __table_args__ = (Index("ix_score_snapshots_session_elapsed", "session_id", "elapsed_seconds"),)


class Report(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reports"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # ReportType
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    recommendation: Mapped[str | None] = mapped_column(String(32))  # Recommendation (recruiter only)
    overall_score: Mapped[float | None] = mapped_column(Float)  # 0-100
    confidence: Mapped[float | None] = mapped_column(Float)
    artifact_storage_key: Mapped[str | None] = mapped_column(String(512))
    model_call_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_to_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("session_id", "type", "version", name="uq_reports_session_type_version"),)


class ReviewDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """The human decision. The AI recommendation is advisory only."""

    __tablename__ = "review_decisions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # ReviewDecisionValue
    notes: Mapped[str | None] = mapped_column(Text)
    ai_recommendation_at_review: Mapped[str | None] = mapped_column(String(32))
    overrides_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
