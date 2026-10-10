"""Accepting recording segments from the candidate's browser.

``POST /portal/sessions/{id}/recording`` hands every MediaRecorder timeslice
to :func:`accept_segment`. Segments are stored per part
(``orgs/{org}/sessions/{session}/{video|screen}/{part_id}/{segment:05d}.{ext}``;
uploads without a part id keep the original flat layout), so arrival order
never matters: the finalize job orders them by index. A retry of a stored
segment with the same bytes is answered as a duplicate; different bytes for
the same index are a conflict. Uploads and the finalize job serialize on a
per-part advisory lock, so no segment can slip into a part after the part was
closed and its segments removed.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, UnsupportedMediaType
from app.core.logging import get_logger
from app.db.models import InterviewSession, MediaAsset
from app.domain.enums import MediaKind
from app.services.recordings.constants import (
    LEGACY_PART_PREFIX,
    MAX_MEDIA_MS,
    RECORDING_KIND,
    UPLOAD_GRACE,
)
from app.services.recordings.parts import TERMINAL, lock_part, parse_time, to_epoch_ms
from app.services.storage.media import store_media

log = get_logger(__name__)

VIDEO_TYPES = frozenset({"video/webm", "video/mp4"})
AUDIO_TYPES = frozenset({"audio/webm", "audio/mp4"})
RecordingKind = Literal["camera", "screen"]


def base_type(declared: str | None, *, kind: RecordingKind = "camera") -> str | None:
    """Accepted container type (parameters dropped) or ``None``. Audio-only
    parts (no camera) are camera recordings without a picture."""
    value = (declared or "").split(";")[0].strip().lower()
    if value in VIDEO_TYPES or (kind == "camera" and value in AUDIO_TYPES):
        return value
    return None


def container_ok(data: bytes, content_type: str) -> bool:
    """The first segment of a part must start with the container header."""
    if content_type.endswith("/webm"):
        return data[:4] == b"\x1a\x45\xdf\xa3"
    return data[4:8] == b"ftyp"


def extension(content_type: str) -> str:
    return "webm" if content_type.endswith("/webm") else "mp4"


def folder(kind: RecordingKind) -> str:
    return "video" if kind == "camera" else "screen"


def segment_key(
    organization_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    kind: RecordingKind,
    segment: int,
    content_type: str,
    part_id: uuid.UUID | None,
) -> str:
    base = f"orgs/{organization_id}/sessions/{session_id}/{folder(kind)}"
    name = f"{segment:05d}.{extension(content_type)}"
    return f"{base}/{part_id}/{name}" if part_id is not None else f"{base}/{name}"


def part_key_for(part_id: uuid.UUID | None, kind: RecordingKind) -> str:
    return str(part_id) if part_id is not None else f"{LEGACY_PART_PREFIX}{kind}"


def uploads_open(row: InterviewSession, *, now: datetime | None = None) -> bool:
    """Recording segments are accepted while the session runs, while it is
    closing, and for ``UPLOAD_GRACE`` after it ended (the final timeslice is
    uploaded as the interview closes)."""
    now = now or datetime.now(UTC)
    if row.status not in TERMINAL:
        return True
    ended = row.ended_at or row.updated_at
    return ended is not None and now - ended <= UPLOAD_GRACE


@dataclass(frozen=True, slots=True)
class SegmentTiming:
    """Browser-reported timing of one segment (all optional, clamped)."""

    part_started_at: str | None = None
    media_start_ms: int | None = None
    media_end_ms: int | None = None
    session_ms: int | None = None
    client_sent_at: int | None = None
    audio_only: bool = False

    def details(self, *, received_at: datetime) -> dict[str, object]:
        out: dict[str, object] = {
            "received_at": to_epoch_ms(received_at),
            "audio_only": bool(self.audio_only),
        }
        started = parse_time(self.part_started_at)
        if started is not None:
            out["part_started_at"] = started.isoformat()
        for name in ("media_start_ms", "media_end_ms"):
            value = getattr(self, name)
            if isinstance(value, int) and 0 <= value <= MAX_MEDIA_MS:
                out[name] = value
        if isinstance(self.session_ms, int) and 0 <= self.session_ms <= 48 * 3600 * 1000:
            out["session_ms"] = self.session_ms
        if isinstance(self.client_sent_at, int) and 10**12 <= self.client_sent_at <= 10**13:
            out["client_sent_at"] = self.client_sent_at
        return out


@dataclass(frozen=True, slots=True)
class SegmentResult:
    media: MediaAsset
    duplicate: bool
    part_key: str


async def _closed_part(db: AsyncSession, session_id: uuid.UUID, part_key: str) -> bool:
    rows = (
        await db.scalars(
            select(MediaAsset).where(
                MediaAsset.session_id == session_id,
                MediaAsset.kind == RECORDING_KIND,
                MediaAsset.deleted_at.is_(None),
                MediaAsset.details["part_id"].astext == part_key,
            )
        )
    ).all()
    return any(bool((row.details or {}).get("closed")) for row in rows)


# Extra recording time on top of the round's maximum duration: the lobby, reconnects and the closing words.
RECORDING_BUDGET_GRACE_S = 15 * 60


def budget_lock_key(session_id: uuid.UUID | str, kind: str) -> int:
    """Signed 64-bit ``pg_advisory_xact_lock`` key for one session's uploads of one kind."""
    digest = hashlib.sha256(f"recording-budget:{session_id}:{kind}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def recording_budget_bytes(row: InterviewSession) -> int:
    """Upload budget of one recording kind for this session (S3, audit D2).

    ``(max duration + 15 min) x recording_max_bitrate_bps``: about 825 MB for a round with a 40-minute maximum,
    roughly 3x what the browser recorder produces. A session that has not started only gets the pre-start
    budget (the recorder starts in the lobby).
    """
    settings = get_settings()
    config = row.round_config if isinstance(row.round_config, dict) else {}
    minutes = config.get("max_duration_minutes") or config.get("duration_minutes") or 60
    try:
        seconds = float(minutes) * 60.0 + RECORDING_BUDGET_GRACE_S
    except (TypeError, ValueError):
        seconds = 60 * 60.0 + RECORDING_BUDGET_GRACE_S
    budget = int(seconds * settings.recording_max_bitrate_bps / 8)
    if row.started_at is None:
        return min(budget, settings.recording_prestart_budget_bytes)
    return budget


async def _enforce_budget(
    db: AsyncSession, row: InterviewSession, kind: RecordingKind, part_id: uuid.UUID | None, *, incoming: int
) -> None:
    """409 ``recording_quota_exceeded`` past the byte budget or the part cap; the browser stops recording.

    Every segment ever stored counts, also those already removed after finalisation (their rows keep the
    size), so finishing a part never resets the budget.
    """
    media_kind = (MediaKind.VIDEO_SEGMENT if kind == "camera" else MediaKind.SCREEN_SEGMENT).value
    scope = (MediaAsset.session_id == row.id, MediaAsset.kind == media_kind)
    used = int(await db.scalar(select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(*scope)) or 0)
    budget = recording_budget_bytes(row)
    if used + incoming > budget:
        log.warning(
            "recording.quota_exceeded",
            session_id=str(row.id),
            kind=kind,
            used_bytes=used,
            incoming_bytes=incoming,
            budget_bytes=budget,
            started=row.started_at is not None,
        )
        raise Conflict(
            "This interview's recording has reached its size limit; the interview itself continues.",
            code="recording_quota_exceeded",
            details={"limit": "bytes"},
        )
    if part_id is None:
        return
    part = MediaAsset.details["part_id"].astext
    if await db.scalar(select(MediaAsset.id).where(*scope, part == str(part_id)).limit(1)) is not None:
        return  # a known part
    parts = int(
        await db.scalar(select(func.count(func.distinct(part))).where(*scope, part.is_not(None))) or 0
    )
    cap = get_settings().recording_max_parts_per_session
    if parts >= cap:
        log.warning("recording.parts_exceeded", session_id=str(row.id), kind=kind, parts=parts, cap=cap)
        raise Conflict(
            "This interview's recording has been restarted too many times; the interview itself continues.",
            code="recording_quota_exceeded",
            details={"limit": "parts"},
        )


async def accept_segment(
    db: AsyncSession,
    *,
    row: InterviewSession,
    kind: RecordingKind,
    segment: int,
    data: bytes,
    content_type: str,
    declared_type: str | None,
    part_id: uuid.UUID | None,
    timing: SegmentTiming,
    retention_days: int,
    max_bytes: int,
) -> SegmentResult:
    """Store one segment (idempotent per part + index)."""
    if segment == 0 and not container_ok(data, content_type):
        raise UnsupportedMediaType(
            "The first recording chunk is not a valid video container.", code="invalid_recording"
        )
    part_key = part_key_for(part_id, kind)
    key = segment_key(
        row.organization_id, row.id, kind=kind, segment=segment, content_type=content_type, part_id=part_id
    )
    # Session-and-kind lock first (the budget below is exact under concurrent parts), then the part lock
    # (upload vs finalize). Finalize only takes part locks, so the order cannot deadlock.
    await db.execute(select(func.pg_advisory_xact_lock(budget_lock_key(row.id, kind))))
    await lock_part(db, row.id, part_key)
    existing = await db.scalar(select(MediaAsset).where(MediaAsset.storage_key == key))
    if existing is not None and existing.deleted_at is None:
        if existing.sha256 == hashlib.sha256(data).hexdigest():
            return SegmentResult(media=existing, duplicate=True, part_key=part_key)
        raise Conflict("That recording segment was already uploaded.", code="segment_exists")
    if part_id is not None and await _closed_part(db, row.id, part_key):
        raise Conflict("This recording part is already closed.", code="recording_part_closed")
    await _enforce_budget(db, row, kind, part_id, incoming=len(data))
    received = datetime.now(UTC)
    details: dict[str, object] = {"segment": segment, "kind": kind, **timing.details(received_at=received)}
    if part_id is not None:
        details["part_id"] = str(part_id)
    mime = (declared_type or content_type).strip()[:120]
    if mime:
        details["mime"] = mime
    media = await store_media(
        db,
        organization_id=row.organization_id,
        kind=MediaKind.VIDEO_SEGMENT if kind == "camera" else MediaKind.SCREEN_SEGMENT,
        key=key,
        data=data,
        content_type=content_type,
        session_id=row.id,
        candidate_id=row.candidate_id,
        retention_days=retention_days,
        details=details,
        max_bytes=max_bytes,
    )
    if row.status in TERMINAL:  # accepted inside UPLOAD_GRACE
        log.info("recording.late_segment", session_id=str(row.id), part=part_key, segment=segment)
    return SegmentResult(media=media, duplicate=False, part_key=part_key)


__all__ = [
    "AUDIO_TYPES",
    "VIDEO_TYPES",
    "RecordingKind",
    "SegmentResult",
    "SegmentTiming",
    "accept_segment",
    "recording_budget_bytes",
    "base_type",
    "container_ok",
    "extension",
    "folder",
    "part_key_for",
    "segment_key",
    "uploads_open",
]
