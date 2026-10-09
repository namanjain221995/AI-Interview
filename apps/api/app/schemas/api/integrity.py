"""Integrity DTOs (``/api/v1/integrity/*``, ``/api/v1/sessions/{id}/integrity``).

Integrity events are automated observations for human review. They are
never determinations of misconduct, and a reviewer's status is a note on the
observation, not a decision about the candidate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import (
    IntegrityAction,
    IntegrityEventType,
    IntegrityReviewStatus,
    IntegritySeverity,
    IntegritySource,
)
from app.schemas.api.common import APIModel

OBSERVATION_NOTICE = "Integrity signals are automated observations for human review, not determinations."


class PersonRef(BaseModel):
    id: uuid.UUID
    full_name: str


class JobRef(BaseModel):
    id: uuid.UUID
    title: str


class IntegrityEventOut(APIModel):
    """One observation (possibly aggregating repeats: ``count``, ``duration_ms``)."""

    id: uuid.UUID
    session_id: uuid.UUID
    organization_id: uuid.UUID
    type: IntegrityEventType
    label: str = Field("", description="Plain-language name of the observation")
    source: IntegritySource
    severity: IntegritySeverity
    confidence: float | None = Field(None, description="Model confidence (vision/screen observations only)")
    occurred_at: datetime
    elapsed_seconds: float | None = None
    duration_ms: int | None = None
    count: int = Field(1, description="Aggregated repeats of the same observation")
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_media_id: uuid.UUID | None = None
    evidence_url: str | None = Field(None, description="Short-lived signed URL of the evidence frame")
    evidence_url_expires_at: datetime | None = None
    evidence_available: bool | None = Field(
        None, description="False when the evidence frame was deleted (retention) or is unavailable"
    )
    action: IntegrityAction
    review_status: IntegrityReviewStatus
    reviewed_by_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    created_at: datetime
    candidate: PersonRef | None = None
    job: JobRef | None = None


class IntegrityReviewUpdate(BaseModel):
    """A reviewer's note on an observation (never a verdict about the candidate)."""

    review_status: IntegrityReviewStatus
    review_note: str | None = Field(None, max_length=2000)

    @field_validator("review_note")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class IntegrityBulkReview(IntegrityReviewUpdate):
    event_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)


class IntegrityBulkReviewResult(BaseModel):
    updated: int
    items: list[IntegrityEventOut]
    not_found: list[uuid.UUID] = Field(default_factory=list)


class SessionIntegrityOut(BaseModel):
    """Timeline of a session's observations with objective counts."""

    session_id: uuid.UUID
    notice: str = OBSERVATION_NOTICE
    events: list[IntegrityEventOut]
    counts: dict[str, int] = Field(default_factory=dict, description="Events per type")
    occurrences: dict[str, int] = Field(
        default_factory=dict, description="Occurrences (aggregated repeats) per type"
    )
    counts_by_severity: dict[str, int] = Field(default_factory=dict)
    counts_by_action: dict[str, int] = Field(default_factory=dict)
    counts_by_review_status: dict[str, int] = Field(default_factory=dict)
    total_events: int = 0
    unreviewed_flags: int = 0
    truncated: bool = False
    policy: dict[str, Any] = Field(
        default_factory=dict, description="Integrity policy frozen for this interview"
    )
