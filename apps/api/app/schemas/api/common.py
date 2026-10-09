"""Shared API DTOs: base model, pagination envelope, error envelope, email type,
audit log and health payloads."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

T = TypeVar("T")

_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def _normalize_email(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _check_email(value: str) -> str:
    if len(value) > 320 or not _EMAIL.match(value):
        raise ValueError("value is not a valid email address")
    return value


# Lower-cased, syntax-checked address. Unlike ``EmailStr`` it accepts
# special-use domains such as ``demo.local`` used by seed/dev accounts.
Email = Annotated[str, BeforeValidator(_normalize_email), AfterValidator(_check_email)]


def _to_str(value: Any) -> Any:
    return None if value is None else str(value)


StrLike = Annotated[str | None, BeforeValidator(_to_str)]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):  # noqa: UP046  (pydantic generic model)
    """List envelope used by every list endpoint."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"


class AuditLogOut(APIModel):
    id: int
    organization_id: uuid.UUID | None
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    ip_address: StrLike = None
    user_agent: str | None
    request_id: str | None
    details: dict[str, Any]
    created_at: datetime


ComponentStatus = Literal["ok", "degraded", "down", "unknown", "disabled"]


class HealthComponent(BaseModel):
    name: str
    status: ComponentStatus
    latency_ms: float | None = None
    detail: str | None = None
    info: dict[str, Any] = Field(default_factory=dict)


class SystemHealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    environment: str
    instance_id: str
    checked_at: datetime
    components: list[HealthComponent]


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Forbidden"},
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Validation failed"},
}
