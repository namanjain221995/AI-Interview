"""Catalog DTOs: competencies, interview templates, rounds and round-type defaults."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.domain.enums import CompetencyCategory, Difficulty, RoundType
from app.schemas.api.common import APIModel

_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{1,79}$")


def _check_key(value: str) -> str:
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not _KEY.match(value):
        raise ValueError("key must be 2-80 characters: lower-case letters, digits and underscores")
    return value


CompetencyKey = Annotated[str, AfterValidator(_check_key)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------- competencies


class CompetencyRubricIn(_Input):
    levels: dict[str, str] = Field(default_factory=dict)
    dimensions: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=20)


class CompetencyCreate(_Input):
    key: CompetencyKey
    name: str = Field(..., min_length=1, max_length=200)
    category: CompetencyCategory
    description: str | None = Field(None, max_length=4000)
    rubric: CompetencyRubricIn = Field(default_factory=CompetencyRubricIn)
    is_active: bool = True


class CompetencyUpdate(_Input):
    name: str | None = Field(None, min_length=1, max_length=200)
    category: CompetencyCategory | None = None
    description: str | None = Field(None, max_length=4000)
    rubric: CompetencyRubricIn | None = None
    is_active: bool | None = None


class CompetencyOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    key: str
    name: str
    category: str
    description: str | None
    rubric: dict[str, Any]
    is_active: bool
    is_global: bool = False
    has_embedding: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any, *, has_embedding: bool | None = None) -> CompetencyOut:
        """``has_embedding``: pass it when the query deferred the vector."""
        out = cls.model_validate(row)
        out.is_global = row.organization_id is None
        out.has_embedding = has_embedding if has_embedding is not None else row.embedding is not None
        return out


# ------------------------------------------------------------------ templates


class TemplateCreate(_Input):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    round_type: RoundType
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="RoundConfig (partial values are merged onto the round-type default)",
    )
    is_default: bool = False


class TemplateUpdate(_Input):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    round_type: RoundType | None = None
    config: dict[str, Any] | None = None
    is_default: bool | None = None


class TemplateOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    name: str
    description: str | None
    round_type: str
    config: dict[str, Any]
    is_default: bool
    is_global: bool = False
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> TemplateOut:
        out = cls.model_validate(row)
        out.is_global = row.organization_id is None
        return out


class RoundTypeDefault(BaseModel):
    round_type: RoundType
    label: str
    description: str | None = None
    template_id: uuid.UUID | None = None
    source: str = Field(..., description="database | samples | builtin")
    config: dict[str, Any]


# --------------------------------------------------------------------- rounds


class RoundCreate(_Input):
    name: str | None = Field(None, min_length=1, max_length=200)
    round_type: RoundType | None = Field(None, description="Required unless template_id is given")
    template_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(None, ge=5, le=240)
    max_duration_minutes: int | None = Field(None, ge=5, le=300)
    expected_difficulty: Difficulty | None = None
    candidate_selectable: bool | None = None
    is_active: bool = True
    instructions: str | None = Field(None, max_length=4000)
    config: dict[str, Any] | None = Field(
        None, description="Partial RoundConfig merged onto the template/default"
    )


class RoundUpdate(_Input):
    name: str | None = Field(None, min_length=1, max_length=200)
    round_type: RoundType | None = None
    duration_minutes: int | None = Field(None, ge=5, le=240)
    max_duration_minutes: int | None = Field(None, ge=5, le=300)
    expected_difficulty: Difficulty | None = None
    candidate_selectable: bool | None = None
    is_active: bool | None = None
    instructions: str | None = Field(None, max_length=4000)
    config: dict[str, Any] | None = Field(
        None, description="Partial RoundConfig merged onto the current config"
    )
    replace_config: bool = Field(
        False, description="Treat config as the complete RoundConfig instead of a patch"
    )


class RoundReorder(_Input):
    round_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)


class RoundOut(APIModel):
    id: uuid.UUID
    job_id: uuid.UUID
    template_id: uuid.UUID | None
    name: str
    round_type: str
    sequence: int
    duration_minutes: int
    max_duration_minutes: int
    expected_difficulty: str
    candidate_selectable: bool
    is_active: bool
    instructions: str | None
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
