"""``recording.sweep`` (every two minutes): queue ``recording.finalize`` for
parts that are over but not finalized, or finalized but not cleaned up.

The browser asks for finalize itself when a recorder stops normally
(``POST /portal/sessions/{id}/recording/complete``); the sweep covers
everything else: page reloads and crashes (a newer part exists), sessions that
ended while the page was gone, joined-only files once ffmpeg is available, and
segments of covered parts that are still waiting to be removed.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.core.logging import get_logger
from app.core.queue import enqueue
from app.db.models import InterviewSession, MediaAsset
from app.db.session import session_scope
from app.services.recordings import ffmpeg
from app.services.recordings.constants import (
    FINALIZE_JOB,
    RECORDING_KIND,
    SEGMENT_KINDS,
    SWEEP_LOOKBACK,
    SWEEP_MAX_SESSIONS,
)
from app.services.recordings.parts import TERMINAL, Part, load_parts, part_closed

log = get_logger(__name__)


@dataclass(slots=True)
class SweepSummary:
    sessions_checked: int = 0
    parts_checked: int = 0
    enqueued: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def finalize_dedupe_key(session_id: uuid.UUID | str, part_key: str) -> str:
    return f"{FINALIZE_JOB}:{session_id}:{part_key}"


def needs_finalize(
    part: Part, *, parts: list[Part], status: str | None, ended_at: datetime | None, now: datetime
) -> bool:
    closed = part_closed(part, parts=parts, status=status, ended_at=ended_at, now=now)
    if part.uncovered:
        return closed and not part.gave_up
    finalized = part.finalized
    if finalized is None:
        return False
    if not finalized.remuxed and not finalized.details.get("remux_attempted") and ffmpeg.tools().ffmpeg:
        return True
    return closed and bool(part.segments or part.superseded or not finalized.closed)


async def _candidate_sessions(now: datetime) -> list[tuple[uuid.UUID, str, datetime | None]]:
    since = now - SWEEP_LOOKBACK
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(InterviewSession.id, InterviewSession.status, InterviewSession.ended_at)
                .where(
                    or_(
                        InterviewSession.status.not_in(TERMINAL),
                        InterviewSession.ended_at >= since,
                        InterviewSession.updated_at >= since,
                    )
                )
                .order_by(InterviewSession.updated_at.desc())
                .limit(SWEEP_MAX_SESSIONS * 4)
            )
        ).all()
        if not rows:
            return []
        with_media = set(
            (
                await db.scalars(
                    select(MediaAsset.session_id)
                    .where(
                        MediaAsset.session_id.in_([r.id for r in rows]),
                        MediaAsset.kind.in_((*SEGMENT_KINDS, RECORDING_KIND)),
                        MediaAsset.deleted_at.is_(None),
                    )
                    .distinct()
                )
            ).all()
        )
    return [(r.id, r.status, r.ended_at) for r in rows if r.id in with_media][:SWEEP_MAX_SESSIONS]


async def sweep_recordings(*, now: datetime | None = None) -> SweepSummary:
    now = now or datetime.now(UTC)
    summary = SweepSummary()
    due: list[tuple[uuid.UUID, str]] = []
    for session_id, status, ended_at in await _candidate_sessions(now):
        summary.sessions_checked += 1
        async with session_scope() as db:
            parts = await load_parts(db, session_id)
        for part in parts:
            summary.parts_checked += 1
            if needs_finalize(part, parts=parts, status=status, ended_at=ended_at, now=now):
                due.append((session_id, part.key))
    for session_id, part_key in due:
        await enqueue(
            FINALIZE_JOB,
            {"session_id": str(session_id), "part_id": part_key, "reason": "sweep"},
            dedupe_key=finalize_dedupe_key(session_id, part_key),
        )
        summary.enqueued += 1
    if summary.enqueued:
        log.info("recording.sweep", **summary.to_dict())
    return summary


__all__ = ["SweepSummary", "finalize_dedupe_key", "needs_finalize", "sweep_recordings"]
