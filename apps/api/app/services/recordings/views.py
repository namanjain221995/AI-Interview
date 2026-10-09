"""Staff view of a session's recordings (``GET /sessions/{id}/recordings``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Principal
from app.db.models import Interview, InterviewSession
from app.domain.enums import Role
from app.realtime.policy import latest_consent, session_policy
from app.schemas.api.recordings import (
    RecordingAnchorOut,
    RecordingPartOut,
    RecordingPolicyOut,
    SessionRecordingsOut,
)
from app.services.recordings.parts import Part, load_parts, parse_time, part_status

DOWNLOAD_ROLES = frozenset({Role.ADMIN, Role.SUPER_ADMIN})


def can_download(principal: Principal) -> bool:
    return principal.is_staff and principal.role in DOWNLOAD_ROLES


def _anchors(raw: Any) -> list[RecordingAnchorOut]:
    out: list[RecordingAnchorOut] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, list | tuple) and len(item) == 2:
            media_ms, session_ms = item
        elif isinstance(item, dict):
            media_ms, session_ms = item.get("media_ms"), item.get("session_ms")
        else:
            continue
        if isinstance(media_ms, int) and isinstance(session_ms, int):
            out.append(RecordingAnchorOut(media_ms=media_ms, session_ms=session_ms))
    return out


def part_out(part: Part, ordinal: int, *, status: str) -> RecordingPartOut:
    finalized = part.finalized
    details: dict[str, Any] = dict(finalized.details) if finalized is not None else {}
    use_final = finalized is not None and (part.covered or not part.segments)
    timing = part.timing() if part.segments else None
    if use_final:
        started = parse_time(details.get("started_at")) or part.started_at()
        duration = details.get("duration_ms") if isinstance(details.get("duration_ms"), int) else None
        anchors = _anchors(details.get("anchors"))
        segments = int(details.get("segments") or len(part.segments))
        gaps = [g for g in details.get("gaps") or [] if isinstance(g, int)]
    else:
        started = timing.started_at if timing else None
        duration = timing.duration_ms if timing else None
        anchors = [
            RecordingAnchorOut(media_ms=m, session_ms=s) for m, s in (timing.anchors if timing else [])
        ]
        segments = len(part.segments)
        gaps = part.gaps
    error: str | None = None
    failure = part.failure
    if status == "failed":
        error = str((failure or {}).get("error") or ("missing_start" if not part.has_header else "failed"))
    elif finalized is not None and details.get("remux_error") and not details.get("remuxed"):
        error = "not_seekable"
    ended = started + timedelta(milliseconds=duration) if started is not None and duration else None
    has_video = bool(details.get("has_video", not part.audio_only)) if use_final else not part.audio_only
    return RecordingPartOut(
        part_id=part.key,
        kind="screen" if part.kind == "screen" else "camera",
        ordinal=ordinal,
        status=status,  # type: ignore[arg-type]
        started_at=started,
        ended_at=ended,
        duration_ms=duration,
        media_id=finalized.id if finalized is not None else None,
        content_type=finalized.content_type if finalized is not None else None,
        size_bytes=finalized.size_bytes
        if finalized is not None
        else sum(s.size_bytes for s in part.segments),
        has_video=has_video,
        has_audio=bool(details.get("has_audio", True)) if use_final else True,
        audio_only=part.audio_only or not has_video,
        seekable=bool(details.get("remuxed")) if finalized is not None else False,
        version=finalized.version if finalized is not None else None,
        segments=segments,
        pending_segments=len(part.uncovered),
        gaps=gaps[:200],
        anchors=anchors,
        retention_until=finalized.retention_until
        if finalized is not None
        else min((s.retention_until for s in part.segments if s.retention_until), default=None),
        error=error,
    )


async def recordings_view(
    db: AsyncSession, session: InterviewSession, principal: Principal, *, now: datetime | None = None
) -> SessionRecordingsOut:
    now = now or datetime.now(UTC)
    interview = await db.get(Interview, session.interview_id)
    consent = await latest_consent(db, interview.id) if interview is not None else None
    policy = session_policy(interview.policy_snapshot if interview is not None else None, consent)
    parts = await load_parts(db, session.id)
    ordinals: dict[str, int] = {}
    items: list[RecordingPartOut] = []
    for part in parts:
        ordinals[part.kind] = ordinals.get(part.kind, 0) + 1
        status = part_status(part, status=session.status, ended_at=session.ended_at, now=now)
        items.append(part_out(part, ordinals[part.kind], status=status))
    return SessionRecordingsOut(
        session_id=session.id,
        status=session.status,
        session_started_at=session.started_at,
        session_ended_at=session.ended_at,
        policy=RecordingPolicyOut(
            record_video=policy.snapshot.recording.record_video,
            record_screen=policy.snapshot.recording.record_screen,
            video_days=policy.snapshot.retention.video_days,
            consented=policy.video_recording_allowed,
        ),
        can_download=can_download(principal),
        processing=any(item.status in ("recording", "processing") for item in items),
        parts=items,
    )


__all__ = ["DOWNLOAD_ROLES", "can_download", "part_out", "recordings_view"]
