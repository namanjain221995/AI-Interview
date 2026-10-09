"""Staff user DTOs (``/api/v1/users``)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import User
from app.domain.enums import Role
from app.schemas.api.common import Email


class UserOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    email: str
    full_name: str
    role: Role
    is_active: bool
    is_locked: bool
    last_login_at: datetime | None
    password_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            organization_id=user.organization_id,
            email=user.email,
            full_name=user.full_name,
            role=Role(user.role),
            is_active=user.is_active,
            is_locked=bool(user.locked_until and user.locked_until > datetime.now(UTC)),
            last_login_at=user.last_login_at,
            password_changed_at=user.password_changed_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    password: str = Field(min_length=1, max_length=256)
    organization_id: uuid.UUID | None = Field(default=None, description="SUPER_ADMIN only; defaults to the caller's org")


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=256, description="Admin password reset")
