"""retention.sweep: expired media deleted (file + deleted_at), others kept,
transient frames purged, summary note written."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import BackgroundJob, MediaAsset, SystemNote
from app.db.session import session_scope
from app.services.integrity.retention import sweep_expired_media
from app.services.storage.media import store_media

pytestmark = pytest.mark.integration


async def _store(
    org_id: uuid.UUID, name: str, kind: str = "camera_frame", data: bytes = b"\xff\xd8\xff" + b"x" * 100
) -> uuid.UUID:
    async with session_scope() as db:
        asset = await store_media(
            db,
            organization_id=org_id,
            kind=kind,
            key=f"orgs/{org_id}/test/{name}",
            data=data,
            content_type="image/jpeg",
        )
        return asset.id


async def _expire(media_id: uuid.UUID, *, days_ago: float = 1) -> None:
    async with session_scope() as db:
        await db.execute(
            update(MediaAsset)
            .where(MediaAsset.id == media_id)
            .values(retention_until=datetime.now(UTC) - timedelta(days=days_ago))
        )


async def test_sweep_deletes_expired_and_keeps_current(db_session, redis, storage, org_factory) -> None:
    org = await org_factory()
    expired = [await _store(org, f"old-{i}.jpg") for i in range(3)]
    audio = await _store(org, "old.wav", kind="answer_audio", data=b"RIFF" + b"a" * 50)
    current = await _store(org, "new.jpg")
    for mid in [*expired, audio]:
        await _expire(mid)
    async with session_scope() as db:  # one file already gone from disk
        missing = await db.get(MediaAsset, expired[0])
        await storage.delete(missing.storage_key)
    # a transient frame that lost its TTL, and a normal one
    await redis.hset("integrity:frame:stale", mapping={"data": "x", "meta": "{}"})
    await redis.hset("integrity:frame:fresh", mapping={"data": "x", "meta": "{}"})
    await redis.expire("integrity:frame:fresh", 300)

    summary = await sweep_expired_media(batch_size=2)

    assert summary.deleted == 4 and summary.files_removed == 3 and summary.files_missing == 1
    assert summary.by_kind == {"answer_audio": 1, "camera_frame": 3}
    assert summary.transient_frames_purged == 1 and summary.errors == 0
    assert summary.bytes_freed > 0
    async with session_scope() as db:
        rows = {a.id: a for a in (await db.scalars(select(MediaAsset))).all()}
        notes = (await db.scalars(select(SystemNote).where(SystemNote.kind == "retention.sweep"))).all()
    for mid in [*expired, audio]:
        assert rows[mid].deleted_at is not None and rows[mid].details["deleted_reason"] == "retention_expired"
        assert not await storage.exists(rows[mid].storage_key)
    assert rows[current].deleted_at is None and await storage.exists(rows[current].storage_key)
    assert (
        await redis.exists("integrity:frame:stale") == 0 and await redis.exists("integrity:frame:fresh") == 1
    )
    [note] = notes
    assert "removed 4 expired media file(s)" in note.body and note.details["by_kind"]["camera_frame"] == 3

    again = await sweep_expired_media()
    assert again.deleted == 0 and not again.changed
    async with session_scope() as db:
        assert len((await db.scalars(select(SystemNote))).all()) == 1  # no note when nothing changed


async def test_sweep_survives_storage_errors(db_session, redis, storage, org_factory, monkeypatch) -> None:
    org = await org_factory()
    good, bad = await _store(org, "good.jpg"), await _store(org, "bad.jpg")
    await _expire(good)
    await _expire(bad)
    async with session_scope() as db:
        bad_key = (await db.get(MediaAsset, bad)).storage_key
    original = storage.delete

    async def flaky(key: str) -> bool:
        if key == bad_key:
            raise OSError("disk unavailable")
        return await original(key)

    monkeypatch.setattr(storage, "delete", flaky)
    summary = await sweep_expired_media()
    assert summary.deleted == 1 and summary.errors == 1
    async with session_scope() as db:
        assert (await db.get(MediaAsset, bad)).deleted_at is None  # retried next run
        assert (await db.get(MediaAsset, good)).deleted_at is not None


async def test_sweep_respects_max_items(db_session, redis, storage, org_factory) -> None:
    org = await org_factory()
    ids = [await _store(org, f"cap-{i}.jpg") for i in range(5)]
    for mid in ids:
        await _expire(mid)
    summary = await sweep_expired_media(batch_size=2, max_items=3)
    assert summary.deleted == 3 and summary.capped


async def test_retention_job_through_worker(db_session, redis, storage, org_factory, worker_factory) -> None:
    from app.core.queue import enqueue

    org = await org_factory()
    mid = await _store(org, "job.jpg")
    await _expire(mid)
    job_id = await enqueue("retention.sweep", {})
    worker = worker_factory(job_types=["retention.sweep"])
    assert await worker.drain(timeout=20) == 1
    async with session_scope() as db:
        job = await db.get(BackgroundJob, uuid.UUID(job_id))
        assert job.status == "succeeded" and job.result["deleted"] == 1
        assert (await db.get(MediaAsset, mid)).deleted_at is not None
