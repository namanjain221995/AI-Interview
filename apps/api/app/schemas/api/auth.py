"""Auth DTOs (``/api/v1/auth``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Role, SubjectType


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Plain string: the lookup normalizes it and unknown addresses get the same
    # answer as wrong passwords, so no syntax error can leak information.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


def _enforced_policy_fields() -> list[str]:
    from app.core.config import get_settings
    from app.services.jobs.policy import CAMERA_ENFORCED_FIELDS

    return list(CAMERA_ENFORCED_FIELDS) if get_settings().require_camera_for_all_interviews else []


class OrganizationBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    # Plan features of the organisation (G8), e.g. {"intake": true}; the console hides what is not included.
    features: dict[str, Any] = Field(default_factory=dict)
    # Policy fields the platform forces on (e.g. the camera for every interview); settings screens lock them.
    enforced_policy_fields: list[str] = Field(default_factory=lambda: _enforced_policy_fields())


class MeResponse(BaseModel):
    subject_type: SubjectType
    id: uuid.UUID
    role: Role
    email: str | None = None
    display_name: str | None = None
    organization_id: uuid.UUID | None = None
    organization: OrganizationBrief | None = None
    interview_id: uuid.UUID | None = None
    permissions: list[str]
    # Additive (S13): the account still has an administrator-issued password; the app shows only the
    # change-password page until it is changed.
    must_change_password: bool = False


class SessionResponse(BaseModel):
    """Returned by login and refresh. Tokens travel only in HttpOnly cookies;
    ``csrf_token`` mirrors the readable ``csrf_token`` cookie."""

    principal: MeResponse
    csrf_token: str
    access_token_expires_at: datetime
    session_expires_at: datetime


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
