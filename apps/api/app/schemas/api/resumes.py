"""Resume DTOs (upload result, status/progress, reprocess)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api.common import APIModel


class ResumeOut(APIModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    organization_id: uuid.UUID
    interview_id: uuid.UUID | None
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    status_detail: str | None
    progress: dict[str, Any]
    page_count: int | None
    extraction_method: str | None
    error: str | None
    uploaded_by_candidate: bool
    processing_started_at: datetime | None
    processing_finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    media_id: uuid.UUID | None = None
    warnings: list[str] = Field(default_factory=list)
    scan: dict[str, Any] | None = Field(None, description="Malware scan result")
    extraction_summary: dict[str, Any] | None = None
    profile_ready: bool = False
    media_url: str | None = Field(None, description="Short-lived API-signed download URL (detail view only)")
    media_url_expires_at: datetime | None = None


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID | None = Field(None, description="Analyse against this job (default: the original job)")
    force: bool = Field(False, description="Restart even if the resume looks like it is still processing")
