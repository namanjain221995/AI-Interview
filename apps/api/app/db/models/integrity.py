"""Integrity events (observations for human review, never verdicts) and the
media asset registry used for retention enforcement."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class IntegrityEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "integrity_events"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)  # IntegrityEventType
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # IntegritySource
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="low")  # IntegritySeverity
    confidence: Mapped[float | None] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")  # aggregated repeats
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    evidence_media_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="log_only")  # IntegrityAction
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unreviewed", server_default="unreviewed")
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_integrity_events_org_review", "organization_id", "review_status", "occurred_at"),
        Index("ix_integrity_events_session_type", "session_id", "type"),
    )


class MediaAsset(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Every stored file (local filesystem storage) with its retention deadline."""

    __tablename__ = "media_assets"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="SET NULL"), index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # MediaKind
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
