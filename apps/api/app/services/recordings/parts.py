"""Recording parts: the segments of one browser MediaRecorder run and their
finalized file.

A *part* starts every time the candidate's browser starts a recorder (page
load, reload, camera lost -> audio only, screen re-shared). The browser sends
each MediaRecorder timeslice as a *segment* (``video_segment`` /
``screen_segment`` media rows, ``details.part_id`` + ``details.segment``); the
``recording.finalize`` job joins a part's segments into one
``session_recording`` media row (``details.part_id``, ``details.version``).

Everything here is derived from ``media_assets`` rows: no extra tables.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset
from app.domain.enums import SessionStatus
from app.services.recordings.constants import (
    ANCHOR_TOLERANCE_MS,
    FINALIZE_SETTLE,
    LEGACY_PART_PREFIX,
    MAX_FINALIZE_ATTEMPTS,
    MAX_MEDIA_MS,
    PART_IDLE,
    RECORDING_ACTIVE,
    RECORDING_KIND,
    SEGMENT_KINDS,
)

TERMINAL = frozenset({SessionStatus.COMPLETED.value, SessionStatus.ABORTED.value, SessionStatus.FAILED.value})
# A part with no segment for this long, while a newer part of the same kind
# exists, is over (the page that recorded it is gone).
SUPERSEDED_IDLE = timedelta(minutes=2)
# Safety net for sessions that never reach a terminal status.
ABANDONED_IDLE = timedelta(minutes=30)


def _ms(value: Any, *, upper: int = MAX_MEDIA_MS) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= upper else None


def _epoch_ms(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 10**12 <= number <= 10**13 else None


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def to_epoch_ms(value: datetime) -> int:
    return int(round(value.timestamp() * 1000))


def from_epoch_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def part_lock_key(session_id: uuid.UUID | str, part_key: str) -> int:
    """Signed 64-bit key for ``pg_advisory_xact_lock`` (upload vs finalize of one part)."""
    digest = hashlib.sha256(f"recording:{session_id}:{part_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def lock_part(db: AsyncSession, session_id: uuid.UUID | str, part_key: str) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(part_lock_key(session_id, part_key))))


# ------------------------------------------------------------------ rows


@dataclass(slots=True)
class SegmentRef:
    id: uuid.UUID
    index: int
    kind: str
    storage_key: str
    size_bytes: int
    sha256: str | None
    content_type: str
    created_at: datetime
    retention_until: datetime | None
    details: dict[str, Any]

    @classmethod
    def from_row(cls, row: MediaAsset) -> SegmentRef:
        details = dict(row.details or {})
        return cls(
            id=row.id,
            index=int(details.get("segment", 0) or 0),
            kind="screen" if details.get("kind") == "screen" or row.kind == "screen_segment" else "camera",
            storage_key=row.storage_key,
            size_bytes=int(row.size_bytes or 0),
            sha256=row.sha256,
            content_type=row.content_type,
            created_at=row.created_at or datetime.now(UTC),
            retention_until=row.retention_until,
            details=details,
        )

    @property
    def part_key(self) -> str:
        part = self.details.get("part_id")
        return str(part) if part else f"{LEGACY_PART_PREFIX}{self.kind}"

    @property
    def received_ms(self) -> int:
        return _epoch_ms(self.details.get("received_at")) or to_epoch_ms(self.created_at)

    @property
    def received_at(self) -> datetime:
        return from_epoch_ms(self.received_ms)

    @property
    def client_sent_ms(self) -> int | None:
        return _epoch_ms(self.details.get("client_sent_at"))

    @property
    def media_start_ms(self) -> int | None:
        return _ms(self.details.get("media_start_ms"))

    @property
    def media_end_ms(self) -> int | None:
        return _ms(self.details.get("media_end_ms"))

    @property
    def session_ms(self) -> int | None:
        return _ms(self.details.get("session_ms"), upper=48 * 3600 * 1000)

    @property
    def part_started_at(self) -> datetime | None:
        return parse_time(self.details.get("part_started_at"))

    @property
    def audio_only(self) -> bool:
        return bool(self.details.get("audio_only")) or self.content_type.startswith("audio/")


@dataclass(slots=True)
class FinalizedRef:
    id: uuid.UUID
    storage_key: str
    size_bytes: int
    content_type: str
    created_at: datetime
    retention_until: datetime | None
    details: dict[str, Any]

    @classmethod
    def from_row(cls, row: MediaAsset) -> FinalizedRef:
        return cls(
            id=row.id,
            storage_key=row.storage_key,
            size_bytes=int(row.size_bytes or 0),
            content_type=row.content_type,
            created_at=row.created_at or datetime.now(UTC),
            retention_until=row.retention_until,
            details=dict(row.details or {}),
        )

    @property
    def part_key(self) -> str:
        return str(self.details.get("part_id") or "")

    @property
    def version(self) -> int:
        return int(self.details.get("version") or 1)

    @property
    def segment_max(self) -> int:
        value = self.details.get("segment_max")
        return int(value) if isinstance(value, int) else -1

    @property
    def closed(self) -> bool:
        return bool(self.details.get("closed"))

    @property
    def remuxed(self) -> bool:
        return bool(self.details.get("remuxed"))


# ----------------------------------------------------------------- timing


@dataclass(slots=True)
class PartTiming:
    """Where a part sits in time.

    ``started_at`` is media time 0 on the server clock (the browser's start time
    corrected by the smallest ``server receive - browser send`` difference seen
    for the part). ``anchors`` pair media time with the interview clock (the
    clock transcript ``start_ms`` uses, which pauses while the candidate is
    disconnected), so a transcript line maps to the right moment even when the
    recording kept running through a pause.
    """

    started_at: datetime | None
    clock_offset_ms: int | None
    duration_ms: int | None
    anchors: list[tuple[int, int]] = field(default_factory=list)


def compress_anchors(
    points: Iterable[tuple[int, int]], tolerance_ms: int = ANCHOR_TOLERANCE_MS
) -> list[tuple[int, int]]:
    """Keep the first and the last point of every run of (nearly) equal clock
    offsets: a run ends where the interview clock paused while the recording
    kept going (a disconnect) or was resynced, so both of its ends matter."""
    ordered = sorted({(m, s) for m, s in points if m is not None and s is not None})
    if len(ordered) <= 2:
        return ordered
    kept = [ordered[0]]
    run_offset = ordered[0][1] - ordered[0][0]
    previous = ordered[0]
    for media_ms, session_ms in ordered[1:]:
        offset = session_ms - media_ms
        if abs(offset - run_offset) > tolerance_ms:
            if kept[-1] != previous:
                kept.append(previous)  # where the previous run ended
            kept.append((media_ms, session_ms))
            run_offset = offset
        previous = (media_ms, session_ms)
    if kept[-1] != previous:
        kept.append(previous)
    return kept


def part_timing(segments: list[SegmentRef]) -> PartTiming:
    offsets = [seg.received_ms - seg.client_sent_ms for seg in segments if seg.client_sent_ms is not None]
    offset = min(offsets) if offsets else None
    started: datetime | None = None
    client_start = next((seg.part_started_at for seg in segments if seg.part_started_at is not None), None)
    if client_start is not None and offset is not None:
        started = client_start + timedelta(milliseconds=offset)
    elif segments:
        first = segments[0]
        started = first.received_at - timedelta(milliseconds=first.media_end_ms or 0)
    ends = [seg.media_end_ms for seg in segments if seg.media_end_ms is not None]
    anchors = compress_anchors(
        (seg.media_end_ms, seg.session_ms)
        for seg in segments
        if seg.media_end_ms is not None and seg.session_ms is not None
    )
    return PartTiming(
        started_at=started, clock_offset_ms=offset, duration_ms=max(ends) if ends else None, anchors=anchors
    )


# ------------------------------------------------------------------ parts


@dataclass(slots=True)
class Part:
    key: str
    kind: str
    segments: list[SegmentRef] = field(default_factory=list)
    finalized: FinalizedRef | None = None
    superseded: list[FinalizedRef] = field(default_factory=list)

    @property
    def part_id(self) -> uuid.UUID | None:
        try:
            return uuid.UUID(self.key)
        except ValueError:
            return None

    @property
    def max_index(self) -> int:
        return max((s.index for s in self.segments), default=-1)

    @property
    def gaps(self) -> list[int]:
        present = {s.index for s in self.segments}
        return [i for i in range(self.max_index + 1) if i not in present]

    @property
    def has_header(self) -> bool:
        return any(s.index == 0 for s in self.segments)

    @property
    def uncovered(self) -> list[SegmentRef]:
        limit = self.finalized.segment_max if self.finalized is not None else -1
        return [s for s in self.segments if s.index > limit]

    @property
    def covered(self) -> bool:
        return self.finalized is not None and not self.uncovered

    @property
    def last_received_at(self) -> datetime | None:
        if not self.segments:
            return None
        return max(s.received_at for s in self.segments)

    @property
    def audio_only(self) -> bool:
        if self.finalized is not None and "audio_only" in self.finalized.details:
            return bool(self.finalized.details.get("audio_only"))
        return bool(self.segments) and all(s.audio_only for s in self.segments)

    @property
    def failure(self) -> dict[str, Any] | None:
        """The last failed finalize attempt (kept on the newest segment row)."""
        if not self.segments:
            return None
        newest = max(self.segments, key=lambda s: s.index)
        value = newest.details.get("finalize_failure")
        return value if isinstance(value, dict) else None

    @property
    def gave_up(self) -> bool:
        failure = self.failure
        return failure is not None and int(failure.get("attempts") or 0) >= MAX_FINALIZE_ATTEMPTS

    def timing(self) -> PartTiming:
        return part_timing(self.segments)

    def started_at(self) -> datetime | None:
        if self.finalized is not None:
            value = parse_time(self.finalized.details.get("started_at"))
            if value is not None and self.covered:
                return value
        timing = self.timing()
        if timing.started_at is not None:
            return timing.started_at
        if self.finalized is not None:
            return parse_time(self.finalized.details.get("started_at")) or self.finalized.created_at
        return None


def group_parts(rows: Iterable[MediaAsset]) -> list[Part]:
    parts: dict[str, Part] = {}
    finalized: dict[str, list[FinalizedRef]] = {}
    for row in rows:
        if row.deleted_at is not None:
            continue
        if row.kind == RECORDING_KIND:
            ref = FinalizedRef.from_row(row)
            if ref.part_key:
                finalized.setdefault(ref.part_key, []).append(ref)
            continue
        if row.kind not in SEGMENT_KINDS:
            continue
        seg = SegmentRef.from_row(row)
        part = parts.setdefault(seg.part_key, Part(key=seg.part_key, kind=seg.kind))
        part.segments.append(seg)
    for key, refs in finalized.items():
        refs.sort(key=lambda r: (r.version, r.created_at))
        kind = "screen" if refs[-1].details.get("kind") == "screen" else "camera"
        part = parts.setdefault(key, Part(key=key, kind=kind))
        part.finalized = refs[-1]
        part.superseded = refs[:-1]
    for part in parts.values():
        part.segments.sort(key=lambda s: s.index)
    far_future = datetime.max.replace(tzinfo=UTC)
    return sorted(parts.values(), key=lambda p: (p.started_at() or far_future, p.kind, p.key))


async def load_parts(db: AsyncSession, session_id: uuid.UUID) -> list[Part]:
    rows = (
        await db.scalars(
            select(MediaAsset).where(
                MediaAsset.session_id == session_id,
                MediaAsset.kind.in_((*SEGMENT_KINDS, RECORDING_KIND)),
                MediaAsset.deleted_at.is_(None),
            )
        )
    ).all()
    return group_parts(rows)


# ------------------------------------------------------------- lifecycle


def session_settled(status: str | None, ended_at: datetime | None, now: datetime) -> bool:
    """The session is over and late uploads had time to arrive."""
    if status not in TERMINAL:
        return False
    return ended_at is None or now - ended_at >= FINALIZE_SETTLE


def part_closed(
    part: Part,
    *,
    parts: list[Part],
    status: str | None,
    ended_at: datetime | None,
    now: datetime,
    requested: bool = False,
) -> bool:
    """No more segments will arrive for ``part`` (its segments may be removed
    once a finalized file covers them)."""
    if requested or session_settled(status, ended_at, now):
        return True
    last = part.last_received_at
    if last is None:
        return part.finalized is not None and part.finalized.closed
    if now - last >= ABANDONED_IDLE:
        return True
    start = part.started_at()
    newer = any(
        other is not part
        and other.kind == part.kind
        and start is not None
        and (other.started_at() or start) > start
        for other in parts
    )
    return newer and now - last >= SUPERSEDED_IDLE


def part_status(part: Part, *, status: str | None, ended_at: datetime | None, now: datetime) -> str:
    """``ready`` | ``recording`` | ``processing`` | ``failed``."""
    if part.covered or (part.finalized is not None and not part.segments):
        return "ready"
    last = part.last_received_at
    if last is not None and now - last <= RECORDING_ACTIVE and status not in TERMINAL:
        return "recording"
    if part.gave_up:
        return "failed"
    if not part.has_header and last is not None and now - last >= PART_IDLE:
        return "failed"
    return "processing"


__all__ = [
    "FinalizedRef",
    "Part",
    "PartTiming",
    "SegmentRef",
    "compress_anchors",
    "from_epoch_ms",
    "group_parts",
    "load_parts",
    "lock_part",
    "parse_time",
    "part_closed",
    "part_lock_key",
    "part_status",
    "part_timing",
    "session_settled",
    "to_epoch_ms",
]
