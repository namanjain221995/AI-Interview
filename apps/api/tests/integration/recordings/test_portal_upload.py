"""Candidate side: segment uploads per recording part, completion, policy gates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import BackgroundJob, MediaAsset
from app.db.session import session_scope
from app.services.recordings.parts import load_parts

from .conftest import (
    FULL_CONSENT,
    WEBM_HEADER,
    candidate_client,
    make_session,
    policy_snapshot,
    set_session,
    upload,
)


async def _segments(session_id: uuid.UUID) -> list[MediaAsset]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(
                    select(MediaAsset)
                    .where(MediaAsset.session_id == session_id, MediaAsset.deleted_at.is_(None))
                    .order_by(MediaAsset.storage_key)
                )
            ).all()
        )


async def test_parts_are_stored_separately_with_timing(app, org, storage) -> None:
    rec = await make_session(org)
    http = await candidate_client(app, rec)
    first, second = uuid.uuid4(), uuid.uuid4()
    sent = int(datetime.now(UTC).timestamp() * 1000) - 2_000  # browser clock 2 s behind
    r = await upload(
        http,
        rec,
        segment=0,
        data=WEBM_HEADER,
        part_id=first,
        part_started_at=(datetime.now(UTC) - timedelta(seconds=12)).isoformat(),
        media_start_ms=0,
        media_end_ms=10_000,
        session_ms=8_000,
        client_sent_at=sent,
    )
    assert r.status_code == 201, r.text
    assert r.json()["part_id"] == str(first) and r.json()["duplicate"] is False
    # Out of order: segment 2 before segment 1; a second part (page reload) starts at 0 again.
    assert (await upload(http, rec, segment=2, data=b"cluster-2", part_id=first)).status_code == 201
    assert (await upload(http, rec, segment=1, data=b"cluster-1", part_id=first)).status_code == 201
    assert (await upload(http, rec, segment=0, data=WEBM_HEADER + b"b", part_id=second)).status_code == 201
    rows = await _segments(rec.session_id)
    keys = [row.storage_key.split("/sessions/")[1].split("/", 1)[1] for row in rows]
    assert sorted(keys) == sorted(
        [
            f"video/{first}/00000.webm",
            f"video/{first}/00001.webm",
            f"video/{first}/00002.webm",
            f"video/{second}/00000.webm",
        ]
    )
    head = next(row for row in rows if row.storage_key.endswith(f"{first}/00000.webm"))
    assert head.details["part_id"] == str(first) and head.details["session_ms"] == 8_000
    assert head.details["media_end_ms"] == 10_000 and head.details["client_sent_at"] == sent
    assert head.retention_until is not None and (head.retention_until - datetime.now(UTC)).days in (29, 30)
    async with session_scope() as db:
        parts = {p.key: p for p in await load_parts(db, rec.session_id)}
    assert [s.index for s in parts[str(first)].segments] == [0, 1, 2]
    timing = parts[str(first)].timing()
    assert timing.clock_offset_ms is not None and 1_900 <= timing.clock_offset_ms <= 4_000
    assert timing.anchors == [(10_000, 8_000)]
    await http.aclose()


async def test_retries_are_idempotent_and_conflicts_rejected(app, org, storage) -> None:
    rec = await make_session(org)
    http = await candidate_client(app, rec)
    part = uuid.uuid4()
    assert (await upload(http, rec, segment=0, data=WEBM_HEADER, part_id=part)).status_code == 201
    again = await upload(http, rec, segment=0, data=WEBM_HEADER, part_id=part)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    clash = await upload(http, rec, segment=0, data=WEBM_HEADER + b"x", part_id=part)
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "segment_exists"
    bad = await upload(http, rec, segment=0, data=b"not a container", part_id=uuid.uuid4())
    assert bad.status_code == 415 and bad.json()["error"]["code"] == "invalid_recording"
    assert len(await _segments(rec.session_id)) == 1
    await http.aclose()


async def test_audio_only_parts_and_types(app, org, storage) -> None:
    rec = await make_session(org, policy=policy_snapshot(record_screen=True))
    http = await candidate_client(app, rec)
    part = uuid.uuid4()
    r = await upload(
        http, rec, segment=0, data=WEBM_HEADER, part_id=part, content_type="audio/webm;codecs=opus"
    )
    assert r.status_code == 201, r.text
    rows = await _segments(rec.session_id)
    assert rows[0].content_type == "audio/webm" and rows[0].details["audio_only"] is True
    assert rows[0].details["mime"] == "audio/webm;codecs=opus"
    screen_audio = await upload(
        http, rec, segment=0, data=WEBM_HEADER, part_id=uuid.uuid4(), kind="screen", content_type="audio/webm"
    )
    assert screen_audio.status_code == 415
    ogg = await upload(http, rec, segment=0, data=WEBM_HEADER, part_id=uuid.uuid4(), content_type="audio/ogg")
    assert ogg.status_code == 415 and ogg.json()["error"]["code"] == "unsupported_recording_type"
    await http.aclose()


async def test_policy_and_consent_gate_every_upload(app, org, storage) -> None:
    off = await make_session(org, policy=policy_snapshot(record_video=False))
    http = await candidate_client(app, off)
    r = await upload(http, off, segment=0, data=WEBM_HEADER, part_id=uuid.uuid4())
    assert r.status_code == 403 and r.json()["error"]["code"] == "recording_not_enabled"
    r = await http.post(
        f"/api/v1/portal/sessions/{off.session_id}/recording/complete", json={"part_id": str(uuid.uuid4())}
    )
    assert r.status_code == 403
    await http.aclose()

    declined = await make_session(org, consent={**FULL_CONSENT, "video_recording": False})
    http = await candidate_client(app, declined)
    r = await upload(http, declined, segment=0, data=WEBM_HEADER, part_id=uuid.uuid4())
    assert r.status_code == 403
    # Another candidate's session looks like it does not exist.
    other = await make_session(org)
    r = await upload(http, other, segment=0, data=WEBM_HEADER, part_id=uuid.uuid4())
    assert r.status_code == 404
    await http.aclose()


async def test_late_segments_within_grace_then_rejected(app, org, storage) -> None:
    rec = await make_session(org)
    http = await candidate_client(app, rec)
    part = uuid.uuid4()
    assert (await upload(http, rec, segment=0, data=WEBM_HEADER, part_id=part)).status_code == 201
    await set_session(rec.session_id, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=1))
    # The final timeslice is uploaded as the interview closes.
    assert (await upload(http, rec, segment=1, data=b"tail", part_id=part)).status_code == 201
    await set_session(rec.session_id, ended_at=datetime.now(UTC) - timedelta(minutes=11))
    late = await upload(http, rec, segment=2, data=b"too late", part_id=part)
    assert late.status_code == 409 and late.json()["error"]["code"] == "session_ended"
    await http.aclose()


async def test_complete_queues_finalize_once(app, org, storage) -> None:
    rec = await make_session(org)
    http = await candidate_client(app, rec)
    part = uuid.uuid4()
    assert (await upload(http, rec, segment=0, data=WEBM_HEADER, part_id=part)).status_code == 201
    url = f"/api/v1/portal/sessions/{rec.session_id}/recording/complete"
    for _ in range(2):
        r = await http.post(url, json={"part_id": str(part), "kind": "camera", "segments": 1})
        assert r.status_code == 202, r.text
        assert r.json() == {"part_id": str(part), "queued": True}
    async with session_scope() as db:
        jobs = (
            await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "recording.finalize"))
        ).all()
    assert len(jobs) == 1  # deduplicated per part
    assert jobs[0].payload["part_id"] == str(part) and jobs[0].payload["close"] is True
    assert jobs[0].dedupe_key == f"recording.finalize:{rec.session_id}:{part}"
    await http.aclose()


async def test_legacy_uploads_without_part_keep_the_flat_layout(app, org, storage) -> None:
    rec = await make_session(org)
    http = await candidate_client(app, rec)
    assert (await upload(http, rec, segment=0, data=WEBM_HEADER)).status_code == 201
    rows = await _segments(rec.session_id)
    assert rows[0].storage_key.endswith(f"/sessions/{rec.session_id}/video/00000.webm")
    async with session_scope() as db:
        parts = await load_parts(db, rec.session_id)
    assert [p.key for p in parts] == ["legacy-camera"]
    await http.aclose()
