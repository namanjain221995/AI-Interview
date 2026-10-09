"""Interview invitation DTOs. The invite link and access code are returned
exactly once (create / regenerate) and never stored in clear text."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api.common import APIModel


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InterviewCreate(_Input):
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID | None = None
    allow_round_selection: bool = False
    expires_in_hours: int | None = Field(None, ge=1, le=24 * 60, description="Default INVITE_TTL_HOURS")
    require_access_code: bool = False
    scheduled_for: datetime | None = None
    notes: str | None = Field(None, max_length=4000)


class RegenerateLinkRequest(_Input):
    expires_in_hours: int | None = Field(None, ge=1, le=24 * 60)
    rotate_access_code: bool | None = Field(
        None, description="Issue a new access code (default: yes when the interview requires one)"
    )


class CancelRequest(_Input):
    reason: str | None = Field(None, max_length=500)


class PersonRef(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None


class JobRef(BaseModel):
    id: uuid.UUID
    title: str


class RoundRef(BaseModel):
    id: uuid.UUID
    name: str
    round_type: str


class SessionRef(BaseModel):
    id: uuid.UUID
    status: str


class InterviewOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID | None
    allow_round_selection: bool
    status: str
    invite_expires_at: datetime
    invite_opened_at: datetime | None
    verified_at: datetime | None
    requires_access_code: bool = False
    is_expired: bool = False
    policy_snapshot: dict[str, Any]
    system_check: dict[str, Any]
    invited_by_id: uuid.UUID | None
    scheduled_for: datetime | None
    completed_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    candidate: PersonRef | None = None
    job: JobRef | None = None
    round: RoundRef | None = None
    latest_session: SessionRef | None = None


class InterviewCreateResponse(BaseModel):
    interview: InterviewOut
    invite_url: str
    access_code: str | None = None
    expires_at: datetime
