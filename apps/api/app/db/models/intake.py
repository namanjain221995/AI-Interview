"""Intake Interview tables (docs/design/INTAKE_INTERVIEW.md section 3, migration ``0005``).

Single writer per table: T3 writes ``intake_interviews`` and ``intake_org_settings``; T1 writes
``intake_stage_results`` and ``intake_activity_results``; T2 writes the evaluation, evaluator-run,
competency-score and decision tables. Every table carries ``organization_id`` for org scoping. JSONB documents
follow the pydantic contracts in :mod:`app.schemas.intake`; enum-like strings are validated there (no CHECK
constraints, as elsewhere in the schema).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Nullable JSONB documents: assigning ``None`` stores SQL NULL (not the JSON ``null``), so ``IS NULL`` works.
_NULLABLE_JSONB = JSONB(none_as_null=True)

DEFAULT_WORK_AUTHORIZATION_SQL = (
    """'{"enabled": false, "country": null, "ask_sponsorship": true, "variant": "default"}'::jsonb"""
)


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


class IntakeInterview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One intake invitation (also drafts, which have no ``interviews`` row yet)."""

    __tablename__ = "intake_interviews"

    organization_id: Mapped[uuid.UUID] = _org_fk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    round_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rounds.id", ondelete="SET NULL")
    )
    # NULL while status = draft; unique when set.
    interview_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="CASCADE")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # draft | active | cancelled (staff lifecycle only; everything else is derived).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, default="Intake Interview", server_default="Intake Interview"
    )
    target_role: Mapped[str | None] = mapped_column(String(40))  # staff preset; NULL = candidate chooses
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    onboarding: Mapped[dict[str, Any] | None] = mapped_column(_NULLABLE_JSONB)  # IntakeOnboarding
    onboarding_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # IntakeEligibility (MQ1): human review only; never to models, never in reports.
    eligibility: Mapped[dict[str, Any] | None] = mapped_column(_NULLABLE_JSONB)
    resume_review: Mapped[dict[str, Any] | None] = mapped_column(_NULLABLE_JSONB)  # ResumeReviewRecord
    resume_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)  # staff only

    __table_args__ = (
        Index("ix_intake_interviews_org_created", "organization_id", "created_at"),
        Index("ix_intake_interviews_org_status", "organization_id", "status"),
        Index("ix_intake_interviews_candidate", "candidate_id"),
        Index(
            "uq_intake_interviews_interview",
            "interview_id",
            unique=True,
            postgresql_where=text("interview_id IS NOT NULL"),
        ),
        Index("ix_intake_interviews_batch", "batch_id", postgresql_where=text("batch_id IS NOT NULL")),
        Index(
            "ix_intake_interviews_reviewer",
            "assigned_reviewer_id",
            postgresql_where=text("assigned_reviewer_id IS NOT NULL"),
        ),
    )


class IntakeOrgSettings(TimestampMixin, Base):
    """Organisation intake policy (one row per organisation; defaults apply until the first PUT)."""

    __tablename__ = "intake_org_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    program_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default="AI/ML Training Program", server_default="AI/ML Training Program"
    )
    work_authorization: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text(DEFAULT_WORK_AUTHORIZATION_SQL)
    )
    approved_info: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    defaults: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class IntakeStageResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per (session, stage); T1 upserts at stage start/end and on checkpoints."""

    __tablename__ = "intake_stage_results"

    organization_id: Mapped[uuid.UUID] = _org_fk()
    intake_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_interviews.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    stage_key: Mapped[str] = mapped_column(String(16), nullable=False)
    stage_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    started_elapsed_s: Mapped[float | None] = mapped_column(Float)  # interview clock (pauses excluded)
    ended_elapsed_s: Mapped[float | None] = mapped_column(Float)
    budget_s: Mapped[float | None] = mapped_column(Float)
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("session_id", "stage_key", name="uq_intake_stage_results_session_stage"),
        Index("ix_intake_stage_results_intake", "intake_id"),
    )


class IntakeActivityResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per (session, activity kind); ``id`` is the realtime ``activity_id``."""

    __tablename__ = "intake_activity_results"

    organization_id: Mapped[uuid.UUID] = _org_fk()
    intake_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_interviews.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # reading_aloud | read_relay | topic_card
    stage_key: Mapped[str] = mapped_column(String(16), nullable=False)  # reading | topic
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    material_key: Mapped[str] = mapped_column(String(8), nullable=False)  # passage A-F or card 1-8
    kit_version: Mapped[str] = mapped_column(String(32), nullable=False)
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL")
    )
    answer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("answers.id", ondelete="SET NULL")
    )
    phases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    reveal: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    capture: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    attempts_log: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    transcript_live: Mapped[str | None] = mapped_column(Text)
    transcript_final: Mapped[str | None] = mapped_column(Text)
    transcript_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    transcript_source: Mapped[str | None] = mapped_column(String(24))
    audio_media_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    ended_reason: Mapped[str | None] = mapped_column(String(24))

    __table_args__ = (
        UniqueConstraint("session_id", "kind", name="uq_intake_activity_results_session_kind"),
        Index("ix_intake_activity_results_intake", "intake_id"),
    )


class IntakeEvaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One evaluation run (versioned per session)."""

    __tablename__ = "intake_evaluations"

    organization_id: Mapped[uuid.UUID] = _org_fk()
    intake_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_interviews.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)  # session_completed | reevaluate
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    job_id: Mapped[str | None] = mapped_column(String(64))  # reserving queue job (retries reuse the row)
    input_digest: Mapped[str | None] = mapped_column(String(64))
    kit_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False)
    weighted_total: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))  # exact; NULL when insufficient
    partial_total: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))  # only when insufficient (labelled)
    normalized_100: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))  # display conversion
    band: Mapped[str | None] = mapped_column(String(24))
    evidence_sufficiency: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    validator: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    review_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    training_plan: Mapped[dict[str, Any] | None] = mapped_column(_NULLABLE_JSONB)
    model_call_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_intake_evaluations_session_version"),
        Index("ix_intake_evaluations_intake", "intake_id", "version"),
        Index("ix_intake_evaluations_org_created", "organization_id", "created_at"),
    )


class IntakeEvaluatorRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Checkpoint of one evaluator inside one evaluation."""

    __tablename__ = "intake_evaluator_runs"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = _org_fk()
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evaluator: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    input_digest: Mapped[str | None] = mapped_column(String(64))
    output: Mapped[dict[str, Any] | None] = mapped_column(_NULLABLE_JSONB)
    model_call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("evaluation_id", "evaluator", name="uq_intake_evaluator_runs_evaluation_evaluator"),
        Index("ix_intake_evaluator_runs_session", "session_id"),
    )


class IntakeCompetencyScore(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Seven rows per ready evaluation (score 1.0-5.0, NULL when evidence is insufficient)."""

    __tablename__ = "intake_competency_scores"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = _org_fk()
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    competency_key: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # scored | insufficient
    score: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    weight: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    weighted: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    evaluator: Mapped[str] = mapped_column(String(32), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    missing_evidence: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    strengths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    improvements: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    sub_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    __table_args__ = (
        UniqueConstraint(
            "evaluation_id", "competency_key", name="uq_intake_competency_scores_evaluation_key"
        ),
        Index("ix_intake_competency_scores_org_key", "organization_id", "competency_key"),
        Index("ix_intake_competency_scores_session", "session_id"),
    )


class IntakeDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Human decision (append-only; the newest row is current), mirrored to a generic ``review_decisions`` row."""

    __tablename__ = "intake_decisions"

    organization_id: Mapped[uuid.UUID] = _org_fk()
    intake_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_interviews.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intake_evaluations.id", ondelete="SET NULL")
    )
    review_decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_decisions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    ai_band_at_decision: Mapped[str | None] = mapped_column(String(24))
    ai_total_at_decision: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    overrides_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    review_items_resolution: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    competency_notes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    requested_evidence: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    __table_args__ = (
        Index("ix_intake_decisions_intake_created", "intake_id", "created_at"),
        Index("ix_intake_decisions_org_outcome", "organization_id", "outcome"),
    )


__all__ = [
    "DEFAULT_WORK_AUTHORIZATION_SQL",
    "IntakeActivityResult",
    "IntakeCompetencyScore",
    "IntakeDecision",
    "IntakeEvaluation",
    "IntakeEvaluatorRun",
    "IntakeInterview",
    "IntakeOrgSettings",
    "IntakeStageResult",
]
