"""Interview recording DTOs (staff Recording tab + candidate upload completion)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RecordingStatus = Literal["ready", "recording", "processing", "failed"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RecordingAnchorOut(_Out):
    """Media time of the part <-> interview clock (transcript ``start_ms``)."""

    media_ms: int
    session_ms: int


class RecordingPartOut(_Out):
    part_id: str
    kind: Literal["camera", "screen"]
    ordinal: int = Field(description="1-based number of the part within its kind, in time order")
    status: RecordingStatus
    started_at: datetime | None = Field(None, description="Media time 0 on the server clock")
    ended_at: datetime | None = None
    duration_ms: int | None = None
    media_id: uuid.UUID | None = Field(None, description="The finalized file (None until processed)")
    content_type: str | None = None
    size_bytes: int = 0
    has_video: bool = True
    has_audio: bool = True
    audio_only: bool = False
    seekable: bool = Field(False, description="Remuxed with an index; joined-only files seek poorly")
    version: int | None = None
    segments: int = 0
    pending_segments: int = Field(0, description="Uploaded segments not in the finalized file yet")
    gaps: list[int] = Field(default_factory=list, description="Segment numbers that never arrived")
    anchors: list[RecordingAnchorOut] = Field(default_factory=list)
    retention_until: datetime | None = None
    error: str | None = None


class RecordingPolicyOut(_Out):
    record_video: bool
    record_screen: bool
    video_days: int
    consented: bool = Field(description="The candidate consented to video recording")


class SessionRecordingsOut(_Out):
    session_id: uuid.UUID
    status: str
    session_started_at: datetime | None = None
    session_ended_at: datetime | None = None
    policy: RecordingPolicyOut
    can_download: bool = False
    processing: bool = False
    parts: list[RecordingPartOut] = Field(default_factory=list)


class RecordingUrlOut(_Out):
    part_id: str
    media_id: uuid.UUID
    url: str
    expires_at: datetime
    content_type: str
    download: bool = False
    filename: str | None = None


class RecordingFinalizeOut(_Out):
    queued: list[str] = Field(default_factory=list, description="Part ids queued for processing")


class RecordingCompleteRequest(BaseModel):
    """The browser stopped a recorder and every segment it kept was uploaded."""

    model_config = ConfigDict(extra="ignore")

    part_id: uuid.UUID
    kind: Literal["camera", "screen"] = "camera"
    segments: int = Field(0, ge=0, le=100_000)


class RecordingCompleteResponse(_Out):
    part_id: uuid.UUID
    queued: bool


__all__ = [
    "RecordingAnchorOut",
    "RecordingCompleteRequest",
    "RecordingCompleteResponse",
    "RecordingFinalizeOut",
    "RecordingPartOut",
    "RecordingPolicyOut",
    "RecordingStatus",
    "RecordingUrlOut",
    "SessionRecordingsOut",
]
