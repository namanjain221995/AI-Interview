"""Open interview links (docs/design/OPEN_LINKS.md section 1).

* ``interview_links``: one shareable ``/j/<token>`` link for a job and an
  ordered pipeline of its rounds, with a recruiter-defined registration form.
* ``link_registrations``: one row per person per link (plus ``blocked`` rows
  for refused attempts, which have no candidate).
* ``link_registration_rounds``: one row per round attempt; each attempt is a
  normal ``interviews`` row. Per-round state is derived from the interview and
  its latest session, never stored here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_DUPLICATE_POLICY_SQL = """'{"scope": "job", "device": "flag", "network": "flag"}'::jsonb"""


class InterviewLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "interview_links"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Public share token (192 bits, base64url). Plain on purpose: it is a share
    # URL recruiters copy again, not a credential to any data.
    public_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    # Ordered round pipeline (no FK: a removed round never 500s, it is reported missing).
    round_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    progression: Mapped[str] = mapped_column(
        String(16), nullable=False, default="immediate", server_default="immediate"
    )
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_registrations: Mapped[int | None] = mapped_column(Integer)
    round_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72, server_default="72")
    require_resume: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    access_code_hash: Mapped[str | None] = mapped_column(String(255))
    per_ip_hourly_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default="120"
    )
    duplicate_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text(DEFAULT_DUPLICATE_POLICY_SQL)
    )
    form_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    acknowledged_warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_interview_links_org_created", "organization_id", "created_at"),
        Index("ix_interview_links_job", "job_id"),
    )


class LinkRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "link_registrations"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_links.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # NULL for blocked rows; candidate erasure cascades.
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(40))
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_key: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    phone_key: Mapped[str | None] = mapped_column(String(20))
    # {"form_version": n, "fields": [{"key", "label", "type", "value"}]}
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    privacy_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disclosure_hash: Mapped[str | None] = mapped_column(String(64))
    personal_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    personal_token_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    personal_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    device_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    ip_ua_hash: Mapped[str | None] = mapped_column(String(64))
    resume_sha256: Mapped[str | None] = mapped_column(String(64))
    # [{"code", "severity", "message", "related_registration_id", "at"}]
    flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    flag_review: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )
    flag_reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    flag_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flag_review_note: Mapped[str | None] = mapped_column(Text)
    current_round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signals_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_link_registrations_link_email",
            "link_id",
            "email_key",
            unique=True,
            postgresql_where=text("status <> 'blocked'"),
        ),
        Index(
            "uq_link_registrations_link_phone",
            "link_id",
            "phone_key",
            unique=True,
            postgresql_where=text("phone_key IS NOT NULL AND status <> 'blocked'"),
        ),
        Index("ix_link_registrations_job_email", "organization_id", "job_id", "email_key"),
        Index("ix_link_registrations_job_phone", "organization_id", "job_id", "phone_key"),
        Index("ix_link_registrations_link_created", "link_id", "created_at"),
        Index("ix_link_registrations_link_status", "link_id", "status"),
        Index("ix_link_registrations_link_device", "link_id", "device_hash"),
        Index("ix_link_registrations_link_resume", "link_id", "resume_sha256"),
        Index("ix_link_registrations_link_ipua", "link_id", "ip_ua_hash", "created_at"),
        Index("ix_link_registrations_candidate", "candidate_id"),
    )


class LinkRegistrationRound(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "link_registration_rounds"

    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("link_registrations.id", ondelete="CASCADE"), nullable=False
    )
    round_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    round_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    interview_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    unlocked_by: Mapped[str] = mapped_column(String(16), nullable=False)
    unlocked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "registration_id", "sequence", "attempt", name="uq_link_registration_rounds_attempt"
        ),
    )
