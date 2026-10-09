"""``recording.finalize``: turn a part's segments into one seekable file.

For each part of a session (or one part):

1. **Join** the segments in index order into a scratch file. MediaRecorder
   timeslices of one recorder run are one continuous WebM (or fragmented MP4)
   stream, so the joined bytes are already playable.
2. **Remux** with ffmpeg (``-c copy``: no transcode, no quality loss) so the
   file gets an index (WebM cues + duration, MP4 ``faststart``) and browsers
   can seek. Without ffmpeg, or if the remux fails, the joined file is stored
   instead (``remuxed = false``: playable, seeking limited) and a later run
   remuxes it once ffmpeg is available.
3. **Store** the result as a ``session_recording`` media row (versioned key,
   same retention deadline as its segments) under the part's advisory lock,
   which also tells whether new segments arrived meanwhile.
4. **Clean up** only after that commit: older versions, and the segments once
   the part is *closed* (no more uploads can come). Until then the segments
   stay, so a failed or partial finalize never loses anything.

Failures are recorded on the part's newest segment row
(``details.finalize_failure``); the sweep stops retrying a part after
``MAX_FINALIZE_ATTEMPTS`` until new segments arrive or staff ask again.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import ERRORS
from app.core.queue import publish_session_event
from app.db.models import Interview, InterviewSession, MediaAsset
from app.db.session import session_scope
from app.domain.enums import MediaKind
from app.realtime.policy import parse_policy_snapshot
from app.services.recordings import ffmpeg
from app.services.recordings.parts import (
    FinalizedRef,
    Part,
    SegmentRef,
    load_parts,
    lock_part,
    part_closed,
    part_timing,
)
from app.services.storage import get_storage
from app.services.storage.base import ObjectNotFound
from app.services.storage.media import delete_media, store_media

log = get_logger(__name__)

_IO_CHUNK = 1024 * 1024


@dataclass(slots=True)
class FinalizeResult:
    part_id: str
    kind: str
    status: str  # finalized | cleaned | unchanged | failed | skipped
    media_id: str | None = None
    version: int | None = None
    remuxed: bool | None = None
    duration_ms: int | None = None
    segments: int = 0
    size_bytes: int = 0
    removed_segments: int = 0
    closed: bool = False
    error: str | None = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _SessionInfo:
    id: uuid.UUID
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    status: str
    ended_at: datetime | None
    video_days: int


@dataclass(slots=True)
class _Built:
    path: Path
    content_type: str
    remuxed: bool
    remux_error: str | None
    probe: ffmpeg.Probe | None
    source_bytes: int


class FinalizeFailed(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


# ---------------------------------------------------------------- helpers


def _container(content_type: str) -> ffmpeg.Container:
    return "webm" if content_type.endswith("/webm") else "mp4"


def _source_type(part: Part) -> str:
    types = {s.content_type for s in part.segments} or {
        part.finalized.content_type if part.finalized else "video/webm"
    }
    if len({_container(t) for t in types}) > 1:
        raise FinalizeFailed("mixed_containers", "segments of one part use different containers")
    first = sorted(types)[0]
    container = _container(first)
    if any(t.startswith("video/") for t in types) and not part.audio_only:
        return f"video/{container}"
    return f"audio/{container}"


def _work_dir() -> str:
    configured = get_settings().recording_work_dir
    if configured:
        Path(configured).mkdir(parents=True, exist_ok=True)
        return configured
    return tempfile.gettempdir()


async def _join(segments: list[SegmentRef], target: Path) -> int:
    """Concatenate the segment files (index order) into ``target``."""
    storage = get_storage()
    total = 0
    handle = await asyncio.to_thread(open, target, "wb")
    try:
        buffer = bytearray()
        for segment in segments:
            try:
                async for chunk in storage.open(segment.storage_key):
                    buffer += chunk
                    total += len(chunk)
                    if len(buffer) >= _IO_CHUNK:
                        data, buffer = bytes(buffer), bytearray()
                        await asyncio.to_thread(handle.write, data)
            except ObjectNotFound:
                raise FinalizeFailed("segment_missing", f"segment {segment.index} is gone") from None
        if buffer:
            await asyncio.to_thread(handle.write, bytes(buffer))
    finally:
        await asyncio.to_thread(handle.close)
    return total


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    handle = await asyncio.to_thread(open, path, "rb")
    try:
        while True:
            chunk = await asyncio.to_thread(handle.read, _IO_CHUNK)
            if not chunk:
                return
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


async def _copy_object(key: str, target: Path) -> int:
    storage = get_storage()
    total = 0
    handle = await asyncio.to_thread(open, target, "wb")
    try:
        async for chunk in storage.open(key):
            total += len(chunk)
            await asyncio.to_thread(handle.write, chunk)
    except ObjectNotFound:
        raise FinalizeFailed("recording_missing", "the finalized file is gone") from None
    finally:
        await asyncio.to_thread(handle.close)
    return total


async def _build(source: Path, workdir: Path, content_type: str, source_bytes: int) -> _Built:
    """Remux ``source`` when ffmpeg is available (fallback: ``source`` as is), then probe."""
    container = _container(content_type)
    output = source
    remuxed = False
    error: str | None = None
    if ffmpeg.tools().ffmpeg is not None:
        target = workdir / f"remuxed.{container}"
        try:
            await ffmpeg.remux(
                source, target, container=container, limit_s=get_settings().recording_finalize_timeout_seconds
            )
            output, remuxed = target, True
        except ffmpeg.ToolFailed as exc:
            error = exc.stderr_tail or str(exc)
            ERRORS.labels(component="recording").inc()
            log.warning("recording.remux_failed", error=error[-300:])
    probe = await ffmpeg.probe(output)
    if probe is not None and not probe.has_video and probe.has_audio:
        content_type = f"audio/{container}"
    return _Built(
        path=output,
        content_type=content_type,
        remuxed=remuxed,
        remux_error=error,
        probe=probe,
        source_bytes=source_bytes,
    )


def _retention(
    segments: list[SegmentRef], previous: FinalizedRef | None, info: _SessionInfo, now: datetime
) -> datetime:
    """The finalized file never outlives what the candidate was told."""
    deadlines = [s.retention_until for s in segments if s.retention_until is not None]
    if previous is not None and previous.retention_until is not None:
        deadlines.append(previous.retention_until)
    return min(deadlines) if deadlines else now + timedelta(days=info.video_days)


def _probe_details(built: _Built, timing_duration: int | None) -> dict[str, Any]:
    probe = built.probe
    out: dict[str, Any] = {
        "remuxed": built.remuxed,
        "duration_ms": (probe.duration_ms if probe and probe.duration_ms else None) or timing_duration,
        "probed": probe is not None,
        "source_bytes": built.source_bytes,
    }
    if built.remux_error:
        out["remux_error"] = built.remux_error[-300:]
    if probe is not None:
        out.update(
            has_video=probe.has_video,
            has_audio=probe.has_audio,
            video_codec=probe.video_codec,
            audio_codec=probe.audio_codec,
            width=probe.width,
            height=probe.height,
        )
    return out


async def _record_failure(session_id: uuid.UUID, part: Part, code: str, message: str) -> int:
    """Count a failed attempt on the part's newest segment row; returns the count."""
    if not part.segments:
        return 0
    newest = max(part.segments, key=lambda s: s.index)
    async with session_scope() as db:
        row = await db.get(MediaAsset, newest.id)
        if row is None or row.deleted_at is not None:
            return 0
        details = dict(row.details or {})
        raw = details.get("finalize_failure")
        previous: dict[str, Any] = raw if isinstance(raw, dict) else {}
        attempts = int(previous.get("attempts") or 0) + 1
        details["finalize_failure"] = {
            "error": code,
            "message": message[:300],
            "attempts": attempts,
            "at": datetime.now(UTC).isoformat(),
        }
        row.details = details
    log.warning(
        "recording.finalize_failed", session_id=str(session_id), part=part.key, error=code, attempts=attempts
    )
    return attempts


# ------------------------------------------------------------------ store


async def _store(
    info: _SessionInfo,
    part: Part,
    included: list[SegmentRef],
    built: _Built,
    details: dict[str, Any],
    *,
    segment_max: int,
    closing: bool,
    now: datetime,
) -> tuple[MediaAsset | None, bool, list[uuid.UUID]]:
    """Store the built file under the part lock. Returns the new row (None if
    the session is gone), whether the part ended up closed (no segment newer
    than ``segment_max`` arrived meanwhile), and the older versions to remove."""
    included_ids = {s.id for s in included}
    async with session_scope() as db:
        await lock_part(db, info.id, part.key)
        if await db.get(InterviewSession, info.id) is None:
            return None, False, []
        current = next((p for p in await load_parts(db, info.id) if p.key == part.key), None)
        if current is None:
            raise FinalizeFailed("part_gone", "the part disappeared while it was processed")
        present = {s.id for s in current.segments}
        if included_ids - present:
            raise FinalizeFailed("segments_changed", "segments were removed while the part was processed")
        newer = [s for s in current.segments if s.index > segment_max]
        closed = closing and not newer
        previous = current.finalized
        version = (previous.version + 1) if previous is not None else 1
        retention_until = _retention(included, previous, info, now)
        days = max(1, math.ceil((retention_until - now).total_seconds() / 86400))
        container = _container(built.content_type)
        key = f"orgs/{info.organization_id}/sessions/{info.id}/recordings/{part.kind}-{part.key}-v{version}.{container}"
        media = await store_media(
            db,
            organization_id=info.organization_id,
            kind=MediaKind.SESSION_RECORDING,
            key=key,
            data=_file_chunks(built.path),
            content_type=built.content_type,
            session_id=info.id,
            candidate_id=info.candidate_id,
            retention_days=days,
            details={**details, "version": version, "closed": closed},
        )
        media.retention_until = retention_until
        await db.flush()
        stale = [ref.id for ref in current.superseded]
        if previous is not None:
            stale.append(previous.id)
        return media, closed, stale


async def _cleanup(stale: list[uuid.UUID], segments: list[uuid.UUID]) -> int:
    removed = 0
    async with session_scope() as db:
        for media_id in stale:
            await delete_media(db, media_id, reason="recording_superseded")
        for media_id in segments:
            if await delete_media(db, media_id, reason="recording_finalized"):
                removed += 1
    return removed


async def _mark_closed(info: _SessionInfo, part: Part) -> tuple[int, bool]:
    """Covered part, no more uploads: remove its segments and older versions."""
    assert part.finalized is not None
    async with session_scope() as db:
        await lock_part(db, info.id, part.key)
        current = next((p for p in await load_parts(db, info.id) if p.key == part.key), None)
        if current is None or current.finalized is None or not current.covered:
            return 0, False
        row = await db.get(MediaAsset, current.finalized.id)
        if row is not None and not (row.details or {}).get("closed"):
            row.details = {**(row.details or {}), "closed": True}
        segment_ids = [s.id for s in current.segments]
        stale = [ref.id for ref in current.superseded]
    return await _cleanup(stale, segment_ids), True


# --------------------------------------------------------------- finalize


async def finalize_part(
    info: _SessionInfo, part: Part, parts: list[Part], *, requested_close: bool = False, force: bool = False
) -> FinalizeResult:
    started = time.perf_counter()
    now = datetime.now(UTC)
    result = FinalizeResult(part_id=part.key, kind=part.kind, status="unchanged")
    closing = part_closed(
        part, parts=parts, status=info.status, ended_at=info.ended_at, now=now, requested=requested_close
    )
    try:
        if part.finalized is not None and not part.uncovered:
            finalized = part.finalized
            needs_remux = (
                not finalized.remuxed
                and ffmpeg.tools().ffmpeg is not None
                and (force or not finalized.details.get("remux_attempted"))
            )
            if needs_remux:
                return await _remux_existing(info, part, closing, result, now, started)
            if closing and (part.segments or part.superseded or not finalized.closed):
                result.removed_segments, result.closed = await _mark_closed(info, part)
                result.status = "cleaned"
            result.media_id = str(finalized.id)
            return result
        if not part.segments:
            result.status = "skipped"
            return result
        if part.gave_up and not force:
            result.status = "failed"
            result.error = str((part.failure or {}).get("error") or "gave_up")
            return result
        if not part.has_header:
            raise FinalizeFailed(
                "missing_start", "the first segment (with the container header) never arrived"
            )
        included = list(part.segments)
        content_type = _source_type(part)
        timing = part_timing(included)
        container = _container(content_type)
        with tempfile.TemporaryDirectory(prefix="recording-", dir=_work_dir()) as tmp:
            workdir = Path(tmp)
            joined = workdir / f"joined.{container}"
            source_bytes = await _join(included, joined)
            built = await _build(joined, workdir, content_type, source_bytes)
            details: dict[str, Any] = {
                "part_id": part.key,
                "kind": part.kind,
                "audio_only": part.audio_only or not (built.probe.has_video if built.probe else True),
                "segments": len(included),
                "segment_max": max(s.index for s in included),
                "gaps": part.gaps[:200],
                "started_at": timing.started_at.isoformat() if timing.started_at else None,
                "clock_offset_ms": timing.clock_offset_ms,
                "anchors": [[m, s] for m, s in timing.anchors],
                "finalized_at": now.isoformat(),
                "remux_attempted": ffmpeg.tools().ffmpeg is not None,
                **_probe_details(built, timing.duration_ms),
            }
            media, closed, stale = await _store(
                info,
                part,
                included,
                built,
                details,
                segment_max=details["segment_max"],
                closing=closing,
                now=now,
            )
        if media is None:
            result.status = "skipped"
            result.error = "session_gone"
            return result
        result.status = "finalized"
        result.media_id = str(media.id)
        result.version = int((media.details or {}).get("version") or 1)
        result.remuxed = built.remuxed
        result.duration_ms = details.get("duration_ms")
        result.segments = len(included)
        result.size_bytes = int(media.size_bytes or 0)
        result.closed = closed
        result.removed_segments = await _cleanup(stale, [s.id for s in included] if closed else [])
        await _announce(info.id, result)
        return result
    except FinalizeFailed as exc:
        result.status = "failed"
        result.error = exc.code
        await _record_failure(info.id, part, exc.code, str(exc))
        return result
    except Exception as exc:
        # Counted too (the sweep stops after MAX_FINALIZE_ATTEMPTS), then retried by the queue.
        result.status = "failed"
        result.error = type(exc).__name__
        ERRORS.labels(component="recording").inc()
        try:
            await _record_failure(info.id, part, "error", f"{type(exc).__name__}: {exc}")
        except Exception:
            log.warning("recording.failure_not_recorded", part=part.key)
        raise
    finally:
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "recording.finalize_part",
            session_id=str(info.id),
            part=part.key,
            kind=part.kind,
            status=result.status,
            remuxed=result.remuxed,
            segments=result.segments,
            size_bytes=result.size_bytes,
            duration_ms=result.duration_ms,
            closed=result.closed,
            removed_segments=result.removed_segments,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )


async def _remux_existing(
    info: _SessionInfo, part: Part, closing: bool, result: FinalizeResult, now: datetime, started: float
) -> FinalizeResult:
    """A joined-only file stored without ffmpeg: remux it now."""
    finalized = part.finalized
    assert finalized is not None
    container = _container(finalized.content_type)
    with tempfile.TemporaryDirectory(prefix="recording-", dir=_work_dir()) as tmp:
        workdir = Path(tmp)
        source = workdir / f"joined.{container}"
        size = await _copy_object(finalized.storage_key, source)
        built = await _build(source, workdir, finalized.content_type, size)
        if not built.remuxed:
            async with session_scope() as db:
                row = await db.get(MediaAsset, finalized.id)
                if row is not None:
                    row.details = {
                        **(row.details or {}),
                        "remux_attempted": True,
                        "remux_error": (built.remux_error or "")[-300:],
                    }
            result.status = "unchanged"
            result.media_id = str(finalized.id)
            result.remuxed = False
            result.error = "remux_failed"
            return result
        details = {
            **{k: v for k, v in finalized.details.items() if k not in ("version", "closed", "remux_error")},
            **_probe_details(built, finalized.details.get("duration_ms")),
            "remux_attempted": True,
            "finalized_at": now.isoformat(),
        }
        media, closed, stale = await _store(
            info, part, [], built, details, segment_max=finalized.segment_max, closing=closing, now=now
        )
    if media is None:
        result.status = "skipped"
        return result
    result.status = "finalized"
    result.media_id = str(media.id)
    result.version = int((media.details or {}).get("version") or 1)
    result.remuxed = True
    result.duration_ms = details.get("duration_ms")
    result.size_bytes = int(media.size_bytes or 0)
    result.closed = closed
    result.removed_segments = await _cleanup(stale, [s.id for s in part.segments] if closed else [])
    await _announce(info.id, result)
    return result


async def _announce(session_id: uuid.UUID, result: FinalizeResult) -> None:
    try:
        await publish_session_event(
            session_id,
            {
                "type": "recording.ready",
                "part_id": result.part_id,
                "kind": result.kind,
                "media_id": result.media_id,
                "remuxed": result.remuxed,
            },
        )
    except Exception as exc:  # the staff UI polls; the bus is only a hint
        log.debug("recording.announce_failed", error=type(exc).__name__)


async def _session_info(session_id: uuid.UUID) -> tuple[_SessionInfo, list[Part]] | None:
    async with session_scope() as db:
        row = await db.get(InterviewSession, session_id)
        if row is None:
            return None
        interview = await db.get(Interview, row.interview_id)
        snapshot = parse_policy_snapshot(interview.policy_snapshot if interview is not None else None)
        info = _SessionInfo(
            id=row.id,
            organization_id=row.organization_id,
            candidate_id=row.candidate_id,
            status=row.status,
            ended_at=row.ended_at,
            video_days=snapshot.retention.video_days,
        )
        parts = await load_parts(db, row.id)
    return info, parts


async def finalize_session(
    session_id: uuid.UUID | str,
    *,
    part_id: str | None = None,
    requested_close: bool = False,
    force: bool = False,
) -> list[FinalizeResult]:
    """Finalize every part of the session (or just ``part_id``)."""
    sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    loaded = await _session_info(sid)
    if loaded is None:
        return []
    info, parts = loaded
    results: list[FinalizeResult] = []
    for part in parts:
        if part_id is not None and part.key != part_id:
            continue
        results.append(await finalize_part(info, part, parts, requested_close=requested_close, force=force))
    return results


__all__ = ["FinalizeFailed", "FinalizeResult", "finalize_part", "finalize_session"]
