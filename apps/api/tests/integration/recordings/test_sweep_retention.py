"""Background sweep, retention deadlines and data-subject erasure of recordings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import BackgroundJob, Candidate, Interview, MediaAsset
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.integrity.retention import sweep_expired_media
from app.services.recordings.finalize import finalize_session
from app.services.recordings.sweep import sweep_recordings

from .conftest import WEBM_HEADER, candidate_client, make_session, set_session, upload

pytestmark = pytest.mark.integration


async def _upload_part(app, rec, chunks: list[bytes]) -> uuid.UUID:
    part = uuid.uuid4()
    http = await candidate_client(app, rec)
    for index, chunk in enumerate(chunks):
        assert (await upload(http, rec, segment=index, data=chunk, part_id=part)).status_code == 201
    await http.aclose()
    return part


async def _queued_parts() -> set[str]:
    async with session_scope() as db:
        jobs = (
            await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "recording.finalize"))
        ).all()
    return {job.payload["part_id"] for job in jobs}


async def test_sweep_queues_parts_that_are_over(app, org, storage, no_ffmpeg) -> None:
    live = await make_session(org)
    recording_now = await _upload_part(app, live, [WEBM_HEADER])
    ended = await make_session(org)
    finished = await _upload_part(app, ended, [WEBM_HEADER, b"1"])
    await set_session(ended.session_id, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=3))
    # A reload: the old part went quiet minutes ago and a newer part exists.
    reloaded = await make_session(org)
    old = await _upload_part(app, reloaded, [WEBM_HEADER])
    async with session_scope() as db:
        await db.execute(
            update(MediaAsset)
            .where(MediaAsset.session_id == reloaded.session_id)
            .values(
                details=MediaAsset.details.op("||")(
                    {"received_at": int((datetime.now(UTC) - timedelta(minutes=4)).timestamp() * 1000)}
                )
            )
        )
    await _upload_part(app, reloaded, [WEBM_HEADER])

    summary = await sweep_recordings()
    assert summary.enqueued == 2
    assert await _queued_parts() == {str(finished), str(old)}
    assert str(recording_now) not in await _queued_parts()
    again = await sweep_recordings()  # deduplicated while queued
    assert again.enqueued == 2 and len(await _queued_parts()) == 2


async def test_sweep_ignores_finished_and_cleaned_parts(app, org, storage, no_ffmpeg) -> None:
    rec = await make_session(org, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=5))
    await _upload_part(app, rec, [WEBM_HEADER, b"x"])
    [result] = await finalize_session(rec.session_id)
    assert result.closed and result.removed_segments == 2
    summary = await sweep_recordings()
    assert summary.sessions_checked == 1 and summary.enqueued == 0


async def test_retention_sweep_deletes_segments_and_finalized_files(app, org, storage, no_ffmpeg) -> None:
    rec = await make_session(org)
    await _upload_part(app, rec, [WEBM_HEADER, b"a"])
    [result] = await finalize_session(rec.session_id)  # live: segments kept
    assert result.status == "finalized" and not result.closed
    async with session_scope() as db:
        rows = list(
            (await db.scalars(select(MediaAsset).where(MediaAsset.session_id == rec.session_id))).all()
        )
        keys = [row.storage_key for row in rows]
        await db.execute(
            update(MediaAsset)
            .where(MediaAsset.session_id == rec.session_id)
            .values(retention_until=datetime.now(UTC) - timedelta(minutes=1))
        )
    assert len(rows) == 3 and all([await storage.exists(k) for k in keys])
    summary = await sweep_expired_media(write_note=False)
    assert summary.by_kind == {"session_recording": 1, "video_segment": 2}
    for key in keys:
        assert not await storage.exists(key)
    async with session_scope() as db:
        rows = list(
            (await db.scalars(select(MediaAsset).where(MediaAsset.session_id == rec.session_id))).all()
        )
    assert all(
        row.deleted_at is not None and row.details["deleted_reason"] == "retention_expired" for row in rows
    )


async def test_candidate_erasure_removes_every_recording_file(
    app, org, storage, no_ffmpeg, auth_client
) -> None:
    rec = await make_session(org)
    first = await _upload_part(app, rec, [WEBM_HEADER, b"a"])
    second = await _upload_part(app, rec, [WEBM_HEADER, b"b"])  # stays unprocessed
    await set_session(rec.session_id, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=5))
    [done] = await finalize_session(rec.session_id, part_id=str(first))
    assert done.closed and done.removed_segments == 2  # its segments are already gone
    async with session_scope() as db:
        rows = list(
            (await db.scalars(select(MediaAsset).where(MediaAsset.candidate_id == rec.candidate_id))).all()
        )
        keys = [row.storage_key for row in rows]
    kinds = sorted(row.kind for row in rows)
    assert kinds == ["session_recording", "video_segment", "video_segment", "video_segment", "video_segment"]
    assert str(second) in "".join(keys)
    async with session_scope() as db:
        await db.execute(update(Interview).where(Interview.id == rec.interview_id).values(status="completed"))
    admin = await auth_client(Role.ADMIN, organization_id=org)
    url = f"/api/v1/candidates/{rec.candidate_id}"
    archived = await admin.post(f"{url}/archive")
    assert archived.status_code == 200, archived.text
    deleted = await admin.delete(url, params={"request_reference": "DSR-2026-0099"})
    assert deleted.status_code in (200, 204), deleted.text
    for key in keys:
        assert not await storage.exists(key)
    async with session_scope() as db:
        assert await db.get(Candidate, rec.candidate_id) is None
        rows = list(
            (await db.scalars(select(MediaAsset).where(MediaAsset.id.in_([r.id for r in rows])))).all()
        )
    assert rows and all(row.storage_key == f"erased/{row.id}" and row.deleted_at is not None for row in rows)
