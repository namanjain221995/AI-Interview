"""Question bank DTOs (CRUD, semantic search, AI generation)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import BankStatus, Difficulty, QuestionKind, QuestionSource, RoundType
from app.schemas.api.catalog import CompetencyKey
from app.schemas.api.common import APIModel

ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionRubricIn(_Input):
    excellent: str | None = Field(None, max_length=1000)
    good: str | None = Field(None, max_length=1000)
    weak: str | None = Field(None, max_length=1000)
    dimensions: dict[str, str] = Field(default_factory=dict)


class BankQuestionCreate(_Input):
    competency_key: CompetencyKey
    difficulty: Difficulty
    kind: QuestionKind
    text: str = Field(..., min_length=10, max_length=2000)
    topic: str | None = Field(None, max_length=120)
    role_tags: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=20
    )
    round_types: list[RoundType] = Field(default_factory=list, max_length=12)
    expected_concepts: list[ShortText] = Field(default_factory=list, max_length=20)
    rubric: QuestionRubricIn = Field(default_factory=QuestionRubricIn)
    followups: list[ShortText] = Field(default_factory=list, max_length=10)
    common_errors: list[ShortText] = Field(default_factory=list, max_length=10)
    status: BankStatus = BankStatus.ACTIVE


class BankQuestionUpdate(_Input):
    competency_key: CompetencyKey | None = None
    difficulty: Difficulty | None = None
    kind: QuestionKind | None = None
    text: str | None = Field(None, min_length=10, max_length=2000)
    topic: str | None = Field(None, max_length=120)
    role_tags: list[Annotated[str, Field(min_length=1, max_length=60)]] | None = Field(None, max_length=20)
    round_types: list[RoundType] | None = Field(None, max_length=12)
    expected_concepts: list[ShortText] | None = Field(None, max_length=20)
    rubric: QuestionRubricIn | None = None
    followups: list[ShortText] | None = Field(None, max_length=10)
    common_errors: list[ShortText] | None = Field(None, max_length=10)
    status: BankStatus | None = None


class BankQuestionOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    external_key: str | None
    role_tags: list[str]
    round_types: list[str]
    competency_key: str
    topic: str | None
    difficulty: str
    kind: str
    text: str
    expected_concepts: list[str]
    rubric: dict[str, Any]
    followups: list[str]
    common_errors: list[str]
    source: str
    status: str
    usage_count: int
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    is_global: bool = False
    has_embedding: bool = False
    score: float | None = Field(
        None, description="Search relevance (reranker probability or cosine similarity)"
    )
    vector_similarity: float | None = None
    rerank_score: float | None = None

    @classmethod
    def from_row(cls, row: Any, *, has_embedding: bool | None = None, **extra: Any) -> BankQuestionOut:
        """``has_embedding``: pass it when the query deferred the vector."""
        out = cls.model_validate(row)
        out.is_global = row.organization_id is None
        out.has_embedding = has_embedding if has_embedding is not None else row.embedding is not None
        for key, value in extra.items():
            setattr(out, key, value)
        return out


class QuestionSearchRequest(_Input):
    query: str = Field(..., min_length=2, max_length=1000)
    competency_key: CompetencyKey | None = None
    difficulty: Difficulty | None = None
    kind: QuestionKind | None = None
    round_type: RoundType | None = None
    status: BankStatus | None = BankStatus.ACTIVE
    limit: int = Field(10, ge=1, le=50)


class QuestionSearchResponse(BaseModel):
    items: list[BankQuestionOut]
    total: int
    mode: str = Field(..., description="semantic | lexical (fallback when no embeddings are available)")
    reranked: bool = False


class QuestionGenerateRequest(_Input):
    competency_key: CompetencyKey
    difficulty: Difficulty
    count: int = Field(5, ge=1, le=20)
    role: str = Field(..., min_length=2, max_length=120)
    topic: str | None = Field(None, max_length=120)
    kind: QuestionKind | None = None
    round_type: RoundType | None = None


class SkippedQuestion(BaseModel):
    text: str
    reason: str
    duplicate_of: uuid.UUID | None = None
    similarity: float | None = None


class QuestionGenerateResponse(BaseModel):
    created: list[BankQuestionOut]
    skipped: list[SkippedQuestion]
    requested: int
    generated: int
    model_call_id: str | None = None
    source: QuestionSource = QuestionSource.GENERATED
