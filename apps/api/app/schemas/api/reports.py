"""Report DTOs (``/api/v1/sessions/{id}/reports``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import Recommendation, ReportStatus, ReportType
from app.schemas.api.common import APIModel


class ReportVersionOut(APIModel):
    id: uuid.UUID
    type: ReportType
    version: int
    status: ReportStatus
    generated_at: datetime | None
    released_to_candidate: bool
    released_at: datetime | None
    created_at: datetime


class ReportOut(APIModel):
    id: uuid.UUID
    session_id: uuid.UUID
    type: ReportType
    status: ReportStatus
    version: int
    content: dict[str, Any]
    recommendation: Recommendation | None = Field(None, description="Recruiter reports only; advisory")
    overall_score: float | None
    confidence: float | None
    generated_at: datetime | None
    released_to_candidate: bool
    released_at: datetime | None
    released_by_id: uuid.UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    artifact_media_id: uuid.UUID | None = None
    candidate_report_mode: str | None = None
    versions: list[ReportVersionOut] = Field(default_factory=list)


class ReportListOut(BaseModel):
    session_id: uuid.UUID
    items: list[ReportVersionOut]
    total: int


class ReevaluateResponse(BaseModel):
    queued: int
    fallback_evaluations: int


class RegenerateResponse(BaseModel):
    session_id: uuid.UUID
    job_id: uuid.UUID
    status: str = "queued"
    message: str = "Report generation queued. A new version will appear when it is ready."


class ReleaseRequest(BaseModel):
    version: int | None = Field(None, ge=1, description="Defaults to the newest ready candidate report")
    acknowledge_policy_override: bool = Field(
        False, description="Required when the interview policy is 'never' (explicit override)"
    )
    note: str | None = Field(None, max_length=1000)
