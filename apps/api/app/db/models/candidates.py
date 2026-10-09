"""Candidates, resumes, resume chunks (RAG) and normalized candidate profiles."""

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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidates"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # lower-cased
    phone: Mapped[str | None] = mapped_column(String(40))
    location: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(80))
    external_ref: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    # Soft delete: archived candidates are hidden from lists by default and can
    # be restored; permanent (data-subject) deletion removes the row entirely.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_candidates_org_email"),
        # Candidate list: newest first within an organization.
        Index("ix_candidates_org_created", "organization_id", "created_at"),
    )


class CandidateResume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_resumes"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    interview_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="SET NULL"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)  # detected, not client-declared
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded", server_default="uploaded")
    status_detail: Mapped[str | None] = mapped_column(String(255))
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str | None] = mapped_column(String(16))  # ExtractionMethod
    extracted_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    # {"validation": {...}, "malware": {"engine": "clamav", "result": "clean"}, "warnings": [...]}
    scan_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Section-aware resume chunk with embedding, for evidence retrieval."""

    __tablename__ = "resume_chunks"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        Index(
            "ix_resume_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class CandidateProfile(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Normalized, LLM-derived and schema-validated candidate profile.

    ``profile`` holds the full ``app.schemas.ai.CandidateProfile`` document;
    the remaining columns are denormalized for listing/filtering.
    """

    __tablename__ = "candidate_profiles"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready", server_default="ready")
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    summary: Mapped[str | None] = mapped_column(Text)
    experience_years: Mapped[float | None] = mapped_column(Float)
    relevant_experience_years: Mapped[float | None] = mapped_column(Float)
    job_match_score: Mapped[float | None] = mapped_column(Float)  # 0-100
    claims_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    model_call_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


# Candidate search by skill: ``(profile ->> 'skills') ILIKE '%term%'`` (pg_trgm GIN).
Index(
    "ix_candidate_profiles_skills_trgm",
    CandidateProfile.profile["skills"].astext.label("skills_text"),
    postgresql_using="gin",
    postgresql_ops={"skills_text": "gin_trgm_ops"},
)
