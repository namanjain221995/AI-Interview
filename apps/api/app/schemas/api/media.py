"""Media DTOs (``/api/v1/media``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import MediaKind


class MediaUrlResponse(BaseModel):
    """Short-lived API-signed URL; the only way to read a stored file."""

    url: str
    expires_at: datetime
    media_id: uuid.UUID
    kind: MediaKind
    content_type: str
    size_bytes: int
