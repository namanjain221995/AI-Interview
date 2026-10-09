"""Job and job-requirement DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import CandidateReportMode, CodeLanguage, Difficulty, JobStatus, RequirementKind
from app.schemas.api.catalog import CompetencyKey, RoundOut
from app.schemas.api.common import APIModel


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodingRequirementsIn(_Input):
    enabled: bool = False
    languages: list[CodeLanguage] = Field(default_factory=list, max_length=7)
    difficulty: Difficulty = Difficulty.MEDIUM


class RequirementCreate(_Input):
    kind: RequirementKind
    name: str = Field(..., min_length=1, max_length=200)
    competency_key: CompetencyKey | None = None
    description: str | None = Field(None, max_length=2000)
    weight: float = Field(1.0, ge=0, le=10)
    min_years: float | None = Field(None, ge=0, le=40)
    sort_order: int | None = Field(None, ge=0, le=10_000)


class RequirementUpdate(_Input):
    kind: RequirementKind | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    competency_key: CompetencyKey | None = None
    description: str | None = Field(None, max_length=2000)
    weight: float | None = Field(None, ge=0, le=10)
    min_years: float | None = Field(None, ge=0, le=40)
    sort_order: int | None = Field(None, ge=0, le=10_000)


class RequirementOut(APIModel):
    id: uuid.UUID
    job_id: uuid.UUID
    kind: str
    name: str
    competency_key: str | None
    description: str | None
    weight: float
    min_years: float | None
    sort_order: int
    has_embedding: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any, *, has_embedding: bool | None = None) -> RequirementOut:
        """``has_embedding``: pass it when the query deferred the vector."""
        out = cls.model_validate(row)
        out.has_embedding = has_embedding if has_embedding is not None else row.embedding is not None
        return out


class _JobFields(_Input):
    department: str | None = Field(None, max_length=120)
    location: str | None = Field(None, max_length=120)
    employment_type: str | None = Field(None, max_length=40)
    seniority: str | None = Field(None, max_length=40)
    experience_min_years: float | None = Field(None, ge=0, le=50)
    experience_max_years: float | None = Field(None, ge=0, le=60)
    interview_instructions: str | None = Field(None, max_length=8000)
    coding_requirements: CodingRequirementsIn | None = None
    integrity_settings: dict[str, Any] | None = Field(None, description="Partial IntegrityPolicy overrides")
    recording_settings: dict[str, Any] | None = Field(
        None, description="Partial RecordingPolicy overrides, optional nested 'retention' (RetentionPolicy)"
    )
    candidate_report_mode: CandidateReportMode | None = None
    allow_candidate_round_selection: bool | None = None

    @model_validator(mode="after")
    def _experience_range(self) -> _JobFields:
        lo, hi = self.experience_min_years, self.experience_max_years
        if lo is not None and hi is not None and hi < lo:
            raise ValueError("experience_max_years must be >= experience_min_years")
        return self


class JobCreate(_JobFields):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=50_000)
    status: JobStatus = JobStatus.DRAFT
    requirements: list[RequirementCreate] = Field(default_factory=list, max_length=60)


class JobUpdate(_JobFields):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=50_000)
    status: JobStatus | None = None


class JobOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    department: str | None
    location: str | None
    employment_type: str | None
    seniority: str | None
    description: str
    experience_min_years: float | None
    experience_max_years: float | None
    status: str
    interview_instructions: str | None
    coding_requirements: dict[str, Any]
    integrity_settings: dict[str, Any]
    recording_settings: dict[str, Any]
    candidate_report_mode: str
    allow_candidate_round_selection: bool
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    has_embedding: bool = False
    rounds_count: int | None = None
    requirements_count: int | None = None
    candidates_count: int | None = None
    sessions_count: int | None = None

    @classmethod
    def from_row(
        cls, row: Any, counts: dict[str, int] | None = None, *, has_embedding: bool | None = None
    ) -> JobOut:
        """``has_embedding``: pass it when the query deferred the vector."""
        out = cls.model_validate(row)
        out.has_embedding = has_embedding if has_embedding is not None else row.embedding is not None
        for key, value in (counts or {}).items():
            setattr(out, key, value)
        return out


class JobDetail(JobOut):
    requirements: list[RequirementOut] = Field(default_factory=list)
    rounds: list[RoundOut] = Field(default_factory=list)
    effective_policy: dict[str, Any] | None = Field(
        None, description="PolicySnapshot an invite would freeze now"
    )
