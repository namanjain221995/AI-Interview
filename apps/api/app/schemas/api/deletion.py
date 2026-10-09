"""Responses of the permanent, admin-only deletes (counts only, no personal data)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SessionDeletionCounts(BaseModel):
    sessions: int = 0
    transcripts: int = 0
    reports: int = 0
    integrity_events: int = 0
    coding_sessions: int = 0
    media_files_deleted: int = 0
    media_bytes_deleted: int = 0
    media_by_kind: dict[str, int] = Field(default_factory=dict)
    media_records_anonymized: int = 0
    unregistered_files_deleted: int = 0
    model_calls_scrubbed: int = 0


class SessionDeletionOut(SessionDeletionCounts):
    """``DELETE /sessions/{id}``: the session and everything it produced are gone."""

    session_id: uuid.UUID
    interview_id: uuid.UUID
    candidate_id: uuid.UUID
    deleted_at: datetime


class IntakeDeletionOut(BaseModel):
    """``DELETE /intake/interviews/{id}``."""

    intake_id: uuid.UUID
    candidate_id: uuid.UUID
    deleted_at: datetime
    was_draft: bool
    interviews: int
    evaluations: int
    decisions: int
    auth_sessions_revoked: int
    sessions: SessionDeletionCounts


class LinkDeletionOut(BaseModel):
    """``DELETE /open-links/{id}``."""

    link_id: uuid.UUID
    deleted_at: datetime
    previous_status: str
    registrations_deleted: int
