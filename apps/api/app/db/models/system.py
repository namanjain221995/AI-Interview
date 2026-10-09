"""Model call ledger and durable background job records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ModelCall(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One call to a local AI endpoint (LLM, vision, embeddings, reranker, STT,
    OCR, TTS). Payloads are only stored when MODEL_CALL_LOG_PAYLOADS=true."""

    __tablename__ = "model_calls"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    task: Mapped[str] = mapped_column(String(48), nullable=False)  # ModelTask
    endpoint: Mapped[str] = mapped_column(String(32), nullable=False)  # main | vision | embedding | reranker | whisper | ocr | tts
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # ModelCallStatus
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    repaired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    used_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_model_calls_task_created", "task", "created_at"),
        # Model-usage analytics: one organization's calls in a period.
        Index("ix_model_calls_org_created", "organization_id", "created_at"),
    )


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable record of queued work. Redis Streams dispatch; this table is the
    source of truth for recovery after a Redis loss."""

    __tablename__ = "background_jobs"

    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")  # JobState
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_background_jobs_status_type", "status", "type"),)
