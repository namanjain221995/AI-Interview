"""``recording.finalize``: join + remux, versions, cleanup, failures, worker job."""

from __future__ import annotations

import importlib
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.models import MediaAsset
from app.db.session import session_scope
from app.services.recordings import ffmpeg
from app.services.recordings.constants import MAX_FINALIZE_ATTEMPTS
from app.services.recordings.finalize import finalize_session
from app.services.recordings.parts import load_parts

from .conftest import (
    WEBM_HEADER,
    candidate_client,
    make_live_webm,
    make_session,
    set_session,
    split_bytes,
    upload,
)

CUES_ID = b"\x1c\x53\xbb\x6b"


async def _rows(
    session_id: uuid.UUID, *, kind: str | None = None, include_deleted: bool = False
) -> list[MediaAsset]:
    async with session_scope() as db:
        stmt = select(MediaAsset).where(MediaAsset.session_id == session_id)
        if kind:
            stmt = stmt.where(MediaAsset.kind == kind)
        if not include_deleted:
            stmt = stmt.where(MediaAsset.deleted_at.is_(None))
        return list((await db.scalars(stmt.order_by(MediaAsset.created_at))).all())


async def _upload_all(app, rec, chunks: list[bytes], part: uuid.UUID, **fields) -> None:
    http = await candidate_client(app, rec)
    for index, chunk in enumerate(chunks):
        r = await upload(
            http, rec, segment=index, data=chunk, part_id=part, media_end_ms=(index + 1) * 10_000, **fields
        )
        assert r.status_code == 201, r.text
    await http.aclose()


async def test_join_without_ffmpeg_then_close_and_clean_up(app, org, storage, no_ffmpeg) -> None:
    rec = await make_session(org)
    part = uuid.uuid4()
    chunks = [WEBM_HEADER, b"cluster-1" * 10, b"cluster-2" * 10]
    await _upload_all(app, rec, chunks, part)

    # Processed on demand while the interview still runs: segments stay.
    [result] = await finalize_session(rec.session_id)
    assert result.status == "finalized" and result.remuxed is False and result.closed is False
    [final] = await _rows(rec.session_id, kind="session_recording")
    assert await storage.read(final.storage_key) == b"".join(chunks)
    assert final.content_type == "video/webm" and final.details["segments"] == 3
    assert final.details["remuxed"] is False and final.details["version"] == 1
    assert final.storage_key.endswith(f"/recordings/camera-{part}-v1.webm")
    segments = await _rows(rec.session_id, kind="video_segment")
    assert len(segments) == 3
    assert final.retention_until == min(s.retention_until for s in segments)

    # The session ends; once late uploads had time to arrive the part is closed and cleaned up.
    await set_session(rec.session_id, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=3))
    [result] = await finalize_session(rec.session_id)
    assert result.status == "cleaned" and result.removed_segments == 3 and result.closed
    assert await _rows(rec.session_id, kind="video_segment") == []
    for seg in segments:
        assert not await storage.exists(seg.storage_key)
    [final] = await _rows(rec.session_id, kind="session_recording")
    assert final.details["closed"] is True and await storage.exists(final.storage_key)


async def test_new_segments_make_a_new_version(app, org, storage, no_ffmpeg) -> None:
    rec = await make_session(org)
    part = uuid.uuid4()
    await _upload_all(app, rec, [WEBM_HEADER, b"one"], part)
    [first] = await finalize_session(rec.session_id)
    assert first.version == 1
    http = await candidate_client(app, rec)
    assert (await upload(http, rec, segment=2, data=b"two", part_id=part)).status_code == 201
    async with session_scope() as db:
        [p] = await load_parts(db, rec.session_id)
    assert [s.index for s in p.uncovered] == [2]
    [second] = await finalize_session(rec.session_id, requested_close=True)
    assert second.status == "finalized" and second.version == 2 and second.closed
    rows = await _rows(rec.session_id, kind="session_recording", include_deleted=True)
    v1 = next(r for r in rows if r.details.get("version") == 1)
    v2 = next(r for r in rows if r.details.get("version") == 2)
    assert v1.deleted_at is not None and v1.details["deleted_reason"] == "recording_superseded"
    assert await storage.read(v2.storage_key) == WEBM_HEADER + b"one" + b"two"
    # Closed: a straggler for that part is refused instead of being lost silently.
    late = await upload(http, rec, segment=3, data=b"three", part_id=part)
    assert late.status_code == 409 and late.json()["error"]["code"] == "recording_part_closed"
    await http.aclose()


async def test_missing_start_fails_and_is_not_retried_forever(app, org, storage, no_ffmpeg) -> None:
    rec = await make_session(org, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=5))
    part = uuid.uuid4()
    http = await candidate_client(app, rec)
    assert (await upload(http, rec, segment=1, data=b"no header here", part_id=part)).status_code == 201
    await http.aclose()
    for attempt in range(1, MAX_FINALIZE_ATTEMPTS + 1):
        [result] = await finalize_session(rec.session_id)
        assert result.status == "failed" and result.error == "missing_start"
        [seg] = await _rows(rec.session_id, kind="video_segment")
        assert seg.details["finalize_failure"]["attempts"] == attempt
    [result] = await finalize_session(rec.session_id)  # gave up: no new attempt counted
    assert result.status == "failed"
    [seg] = await _rows(rec.session_id, kind="video_segment")
    assert seg.details["finalize_failure"]["attempts"] == MAX_FINALIZE_ATTEMPTS
    assert await _rows(rec.session_id, kind="session_recording") == []


async def test_unknown_session_is_a_no_op(app, storage) -> None:
    assert await finalize_session(uuid.uuid4()) == []


async def test_remux_makes_a_seekable_file(app, org, storage, real_ffmpeg, tmp_path: Path) -> None:
    rec = await make_session(org)
    part = uuid.uuid4()
    data = make_live_webm(real_ffmpeg, tmp_path / "live.webm", seconds=6)
    assert CUES_ID not in data  # like MediaRecorder output: no index
    await _upload_all(app, rec, split_bytes(data, 4), part)
    [result] = await finalize_session(rec.session_id, requested_close=True)
    assert result.status == "finalized" and result.remuxed is True and result.closed
    assert result.duration_ms is not None and abs(result.duration_ms - 6_000) <= 300
    [final] = await _rows(rec.session_id, kind="session_recording")
    stored = await storage.read(final.storage_key)
    assert CUES_ID in stored  # seekable: the remux wrote cues
    assert final.details["has_video"] is True and final.details["has_audio"] is True
    assert final.details["video_codec"] == "vp8" and final.details["audio_codec"] == "opus"
    assert final.content_type == "video/webm"
    assert await _rows(rec.session_id, kind="video_segment") == []
    out = tmp_path / "out.webm"
    out.write_bytes(stored)
    probe = await ffmpeg.probe(out)
    assert probe is not None and probe.has_video and probe.has_audio


async def test_joined_file_is_remuxed_once_ffmpeg_is_available(
    app, org, storage, real_ffmpeg, settings, tmp_path
) -> None:
    rec = await make_session(org)
    part = uuid.uuid4()
    data = make_live_webm(real_ffmpeg, tmp_path / "live.webm", seconds=3)
    await _upload_all(app, rec, split_bytes(data, 2), part)
    real = (settings.recording_ffmpeg_path, settings.recording_ffprobe_path)
    settings.recording_ffmpeg_path = settings.recording_ffprobe_path = "/nonexistent/tool"
    ffmpeg.reset_tools_cache()
    [joined] = await finalize_session(rec.session_id, requested_close=True)
    assert joined.status == "finalized" and joined.remuxed is False and joined.closed
    settings.recording_ffmpeg_path, settings.recording_ffprobe_path = real
    ffmpeg.reset_tools_cache()
    [remuxed] = await finalize_session(rec.session_id)
    assert remuxed.status == "finalized" and remuxed.remuxed is True and remuxed.version == 2
    rows = await _rows(rec.session_id, kind="session_recording")
    assert [r.details["version"] for r in rows] == [2] and rows[0].details["closed"] is True
    assert CUES_ID in await storage.read(rows[0].storage_key)


async def test_audio_only_part(app, org, storage, real_ffmpeg, tmp_path) -> None:
    rec = await make_session(org)
    part = uuid.uuid4()
    data = make_live_webm(real_ffmpeg, tmp_path / "audio.webm", seconds=3, video=False)
    await _upload_all(
        app, rec, split_bytes(data, 2), part, content_type="audio/webm;codecs=opus", audio_only=True
    )
    [result] = await finalize_session(rec.session_id, requested_close=True)
    assert result.status == "finalized" and result.remuxed is True
    [final] = await _rows(rec.session_id, kind="session_recording")
    assert final.content_type == "audio/webm"
    assert final.details["audio_only"] is True and final.details["has_video"] is False


async def test_finalize_job_runs_in_the_worker(app, org, storage, no_ffmpeg, worker_factory) -> None:
    from app.core.queue import enqueue

    importlib.import_module("app.workers.recordings")  # registers the handlers

    rec = await make_session(org)
    part = uuid.uuid4()
    await _upload_all(app, rec, [WEBM_HEADER, b"x"], part)
    await enqueue(
        "recording.finalize", {"session_id": str(rec.session_id), "part_id": str(part), "close": True}
    )
    worker = worker_factory(job_types=["recording.finalize"])
    await worker.drain(timeout=30)
    [final] = await _rows(rec.session_id, kind="session_recording")
    assert final.details["closed"] is True
    assert await _rows(rec.session_id, kind="video_segment") == []


def test_wrapper_ffmpeg_sanity(real_ffmpeg) -> None:
    """The configured binaries answer (guards against a broken wrapper silently skipping work)."""
    assert real_ffmpeg.ffmpeg is not None
    out = subprocess.run(
        [real_ffmpeg.ffmpeg, "-hide_banner", "-version"], capture_output=True, timeout=60, check=True
    )
    assert b"ffmpeg version" in out.stdout
