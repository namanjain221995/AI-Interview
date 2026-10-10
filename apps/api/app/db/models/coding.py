"""Live coding sessions, code snapshots, executions and hints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CodingSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "coding_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    coding_question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_questions.id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"))
    language: Mapped[str] = mapped_column(String(16), nullable=False)  # CodeLanguage
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1200)
    hints_available: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    hints_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    paste_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    final_code: Mapped[str | None] = mapped_column(Text)
    final_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    observer_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class CodeSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "code_snapshots"

    coding_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    diff_from_previous: Mapped[str | None] = mapped_column(Text)  # unified diff
    lines_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="autosave")  # SnapshotTrigger
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("coding_session_id", "seq", name="uq_code_snapshots_session_seq"),)


class CodeRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "code_runs"

    coding_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("code_snapshots.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # RunKind
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # RunStatus
    stdout: Mapped[str | None] = mapped_column(Text)  # truncated
    stderr: Mapped[str | None] = mapped_column(Text)  # truncated
    compile_output: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    # [{"id": "t1", "passed": true, "hidden": false, "args": ..., "expected": ..., "actual": ...,
    #   "runtime_ms": 3, "error": null}]  (hidden test inputs are never sent to candidates)
    test_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    runtime_ms: Mapped[int | None] = mapped_column(Integer)
    memory_kb: Mapped[int | None] = mapped_column(BigInteger)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    sandbox_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    __table_args__ = (Index("ix_code_runs_session_created", "coding_session_id", "created_at"),)


class CodingHint(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "coding_hints"

    coding_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_sessions.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..3
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="curated")  # curated | generated
    requested_via: Mapped[str] = mapped_column(String(16), nullable=False, default="button")  # button | voice
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("code_snapshots.id", ondelete="SET NULL"))
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("coding_session_id", "level", name="uq_coding_hints_session_level"),)
