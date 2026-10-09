"""Curated + generated question bank and coding question bank."""

from __future__ import annotations

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, TimestampMixin, UUIDPrimaryKeyMixin


class BankQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Interview question template (schema shared by curated and generated)."""

    __tablename__ = "question_bank"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    external_key: Mapped[str | None] = mapped_column(String(120))  # stable id for curated seeds
    role_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    round_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    competency_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    topic: Mapped[str | None] = mapped_column(String(120))
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # QuestionKind
    text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_concepts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # {"excellent": "...", "good": "...", "weak": "...", "dimensions": {...}}
    rubric: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    followups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    common_errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="curated")  # QuestionSource
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "external_key", name="uq_question_bank_org_external_key", postgresql_nulls_not_distinct=True
        ),
        Index("ix_question_bank_competency_difficulty", "competency_key", "difficulty"),
        Index(
            "ix_question_bank_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class CodingQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Coding problem with a language-neutral signature and test cases.

    ``signature`` drives harness generation for every language:
    {"function_name": "two_sum",
     "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
     "return_type": "int[]",
     "comparison": "exact" | "unordered" | "float_tolerance"}
    Supported types: int, long, float, bool, string, int[], long[], float[],
    string[], bool[], int[][], string[][]. SQL problems use ``sql_setup``
    and compare result sets instead.
    """

    __tablename__ = "coding_questions"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    competency_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    role_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    prompt_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    signature: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    starter_code: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    reference_solutions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # [{"id": "t1", "args": [[2,7,11,15], 9], "expected": [0,1], "hidden": false, "description": "..."}]
    # SQL: [{"id": "t1", "setup_sql": "...", "expected_rows": [[...]], "ordered": false, "hidden": false}]
    tests: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    sql_setup: Mapped[str | None] = mapped_column(Text)
    expected_concepts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    optimal_complexity: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    rubric: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    hints: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")  # exactly 3 levels
    followups: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    common_errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    time_limit_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default="20")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="curated")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "slug", name="uq_coding_questions_org_slug", postgresql_nulls_not_distinct=True
        ),
    )
