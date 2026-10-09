"""Organization DTOs (``/api/v1/organizations``, SUPER_ADMIN)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.policies import OrganizationSettings


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
    settings: OrganizationSettings | None = None


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
    is_active: bool | None = None
