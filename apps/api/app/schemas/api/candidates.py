"""Candidate and candidate-profile DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api.common import APIModel, Email


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateCreate(_Input):
    full_name: str = Field(..., min_length=1, max_length=200)
    email: Email
    phone: str | None = Field(None, max_length=40)
    location: str | None = Field(None, max_length=120)
    source: str | None = Field(None, max_length=80)
    external_ref: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=10_000)


class CandidateUpdate(_Input):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    email: Email | None = None
    phone: str | None = Field(None, max_length=40)
    location: str | None = Field(None, max_length=120)
    source: str | None = Field(None, max_length=80)
    external_ref: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=10_000)


class CandidateOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    location: str | None
    source: str | None
    external_ref: str | None
    notes: str | None
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = Field(
        None, description="Set while the candidate is archived (hidden from lists, restorable)"
    )
    latest_resume_status: str | None = None
    latest_resume_id: uuid.UUID | None = None
    interviews_count: int | None = None
    latest_interview_status: str | None = None
    headline: str | None = None
    experience_years: float | None = None
    top_skills: list[str] = Field(default_factory=list)

    @classmethod
    def from_row(cls, row: Any, extra: dict[str, Any] | None = None) -> CandidateOut:
        out = cls.model_validate(row)
        for key, value in (extra or {}).items():
            setattr(out, key, value)
        return out


class CandidateDeletionOut(BaseModel):
    """What a permanent (data-subject) deletion removed. Counts only."""

    candidate_id: uuid.UUID
    deleted_at: datetime
    interviews: int
    sessions: int
    resumes: int
    profiles: int
    media_files_deleted: int
    media_bytes_deleted: int
    media_by_kind: dict[str, int] = Field(default_factory=dict)
    media_records_anonymized: int = 0
    unregistered_files_deleted: int = 0
    model_calls_scrubbed: int = 0
    audit_entries_scrubbed: int = 0
    auth_sessions_revoked: int = 0
    link_registrations: int = 0
    link_blocked_registrations_deleted: int = 0
    intake_interviews: int = 0
    reports: int = 0
    archived_in_request: bool = False


class CandidateDeletionPreviewOut(BaseModel):
    """What ``DELETE /candidates/{id}`` would remove right now (counts only)."""

    candidate_id: uuid.UUID
    archived: bool
    interviews: int
    sessions: int
    resumes: int
    reports: int
    intake_interviews: int
    link_registrations: int
    stored_files: int
    stored_bytes: int
    blockers: list[dict[str, Any]] = Field(default_factory=list)


class CandidateProfileOut(APIModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    resume_id: uuid.UUID
    job_id: uuid.UUID | None
    version: int
    status: str
    profile: dict[str, Any]
    summary: str | None
    experience_years: float | None
    relevant_experience_years: float | None
    job_match_score: float | None
    claims_count: int
    model_call_ids: list[str]
    created_at: datetime
    match_breakdown: dict[str, Any] | None = Field(
        None, description="Deterministic job-match evidence per requirement and the blend weights"
    )
