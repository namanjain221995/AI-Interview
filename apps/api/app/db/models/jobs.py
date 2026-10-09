"""Competency catalog, jobs, requirements, interview templates and rounds."""

from __future__ import annotations

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, TimestampMixin, UUIDPrimaryKeyMixin


class Competency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A measurable skill/behaviour. ``organization_id`` NULL = global catalog."""

    __tablename__ = "competencies"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)  # slug, e.g. "kubernetes"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # CompetencyCategory
    description: Mapped[str | None] = mapped_column(Text)
    # {"levels": {"1": "...", "3": "...", "5": "..."}, "dimensions": ["availability", ...]}
    rubric: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_competencies_org_key", postgresql_nulls_not_distinct=True),
    )


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(120))
    employment_type: Mapped[str | None] = mapped_column(String(40))
    seniority: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    experience_min_years: Mapped[float | None] = mapped_column(Float)
    experience_max_years: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    interview_instructions: Mapped[str | None] = mapped_column(Text)
    # CodingRequirements: {"enabled": bool, "languages": [...], "difficulty": "medium"}
    coding_requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # IntegrityPolicy (app.schemas.policies): monitoring toggles, thresholds, actions
    integrity_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # RecordingPolicy + retention overrides
    recording_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    candidate_report_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="after_review", server_default="after_review"
    )
    allow_candidate_round_selection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (Index("ix_jobs_org_status", "organization_id", "status"),)


class JobRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "job_requirements"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # RequirementKind
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # e.g. "Kubernetes"
    competency_key: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1.0")
    min_years: Mapped[float | None] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class InterviewTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reusable round configuration. ``organization_id`` NULL = system default."""

    __tablename__ = "interview_templates"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    round_type: Mapped[str] = mapped_column(String(32), nullable=False)  # RoundType
    # Full RoundConfig (app.schemas.rounds.RoundConfig) used to seed new rounds.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (
        # At most one global default template per round type.
        Index(
            "uq_interview_templates_global_default",
            "round_type",
            unique=True,
            postgresql_where=text("organization_id IS NULL AND is_default"),
        ),
    )


class Round(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One configured interview round of a job.

    Columns hold the most queried fields; the complete, validated
    configuration lives in ``config`` (RoundConfig) so the engine always reads
    one consistent document.
    """

    __tablename__ = "rounds"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_templates.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    round_type: Mapped[str] = mapped_column(String(32), nullable=False)  # RoundType
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    max_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=40, server_default="40")
    expected_difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", server_default="medium")
    candidate_selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    instructions: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (Index("ix_rounds_job_sequence", "job_id", "sequence"),)
