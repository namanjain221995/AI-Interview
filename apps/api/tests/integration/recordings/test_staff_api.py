"""Staff side: list, signed playback with HTTP Range, admin-only download, audit, RBAC."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, BackgroundJob
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.recordings.finalize import finalize_session

from .conftest import WEBM_HEADER, candidate_client, make_session, policy_snapshot, upload

pytestmark = pytest.mark.integration

API = "/api/v1"


async def _recorded_session(app, org, *, close: bool = True):
    rec = await make_session(org, status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=1))
    http = await candidate_client(app, rec)
    part = uuid.uuid4()
    started = datetime.now(UTC) - timedelta(minutes=2)
    body = [WEBM_HEADER + bytes(200), bytes(range(256)) * 4, b"tail" * 50]
    for index, chunk in enumerate(body):
        r = await upload(
            http,
            rec,
            segment=index,
            data=chunk,
            part_id=part,
            part_started_at=started.isoformat(),
            media_end_ms=(index + 1) * 10_000,
            session_ms=(index + 1) * 10_000 + 2_000,
            client_sent_at=int(datetime.now(UTC).timestamp() * 1000),
        )
        assert r.status_code == 201, r.text
    await http.aclose()
    if close:
        [result] = await finalize_session(rec.session_id, requested_close=True)
        assert result.status == "finalized"
    return rec, part, b"".join(body)


async def _audit(action: str) -> list[AuditLog]:
    async with session_scope() as db:
        return list((await db.scalars(select(AuditLog).where(AuditLog.action == action))).all())


async def test_list_parts_with_timing_and_roles(app, org, storage, no_ffmpeg, auth_client) -> None:
    rec, part, _ = await _recorded_session(app, org)
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=org)
    r = await interviewer.get(f"{API}/sessions/{rec.session_id}/recordings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["policy"] == {
        "record_video": True,
        "record_screen": False,
        "video_days": 30,
        "consented": True,
    }
    assert body["can_download"] is False and body["processing"] is False
    [item] = body["parts"]
    assert item["part_id"] == str(part) and item["kind"] == "camera" and item["ordinal"] == 1
    assert item["status"] == "ready" and item["media_id"] and item["seekable"] is False
    assert item["segments"] == 3 and item["pending_segments"] == 0 and item["gaps"] == []
    assert item["anchors"] == [
        {"media_ms": 10_000, "session_ms": 12_000},
        {"media_ms": 30_000, "session_ms": 32_000},
    ]
    assert item["duration_ms"] == 30_000 and item["started_at"] and item["ended_at"]
    admin = await auth_client(Role.ADMIN, organization_id=org)
    assert (await admin.get(f"{API}/sessions/{rec.session_id}/recordings")).json()["can_download"] is True


async def test_unprocessed_parts_are_listed_as_processing(app, org, storage, no_ffmpeg, auth_client) -> None:
    rec, part, _ = await _recorded_session(app, org, close=False)
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    [item] = (await recruiter.get(f"{API}/sessions/{rec.session_id}/recordings")).json()["parts"]
    assert item["status"] == "processing" and item["media_id"] is None and item["pending_segments"] == 3
    r = await recruiter.get(f"{API}/sessions/{rec.session_id}/recordings/{part}/url")
    assert r.status_code == 409 and r.json()["error"]["code"] == "recording_processing"
    # "Process now" queues the part and is audited.
    r = await recruiter.post(f"{API}/sessions/{rec.session_id}/recordings/finalize")
    assert r.status_code == 202 and r.json() == {"queued": [str(part)]}
    async with session_scope() as db:
        [job] = (
            await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "recording.finalize"))
        ).all()
    assert job.payload["force"] is True and job.payload["part_id"] == str(part)
    [entry] = await _audit("recording.finalize_requested")
    assert entry.details["parts"] == [str(part)]


async def test_signed_playback_supports_range_and_is_audited(
    app, org, storage, no_ffmpeg, auth_client
) -> None:
    rec, part, data = await _recorded_session(app, org)
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=org)
    r = await interviewer.get(f"{API}/sessions/{rec.session_id}/recordings/{part}/url")
    assert r.status_code == 200, r.text
    signed = r.json()
    assert signed["url"].startswith("/api/v1/media/signed/") and signed["download"] is False
    assert signed["content_type"] == "video/webm"
    expires = datetime.fromisoformat(signed["expires_at"])
    assert 800 <= (expires - datetime.now(UTC)).total_seconds() <= 901
    [entry] = await _audit("recording.view")
    assert entry.details["sensitive"] is True and entry.details["part_id"] == str(part)
    assert entry.resource_id == str(rec.session_id)

    full = await interviewer.get(signed["url"])
    assert full.status_code == 200 and full.content == data
    assert full.headers["accept-ranges"] == "bytes" and full.headers["content-disposition"].startswith(
        "inline"
    )
    part_resp = await interviewer.get(signed["url"], headers={"Range": "bytes=100-299"})
    assert part_resp.status_code == 206 and part_resp.content == data[100:300]
    assert part_resp.headers["content-range"] == f"bytes 100-299/{len(data)}"
    assert part_resp.headers["content-length"] == "200"
    tail = await interviewer.get(signed["url"], headers={"Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == data[-50:]
    open_ended = await interviewer.get(signed["url"], headers={"Range": f"bytes={len(data) - 10}-"})
    assert open_ended.status_code == 206 and open_ended.content == data[-10:]
    head = await interviewer.head(signed["url"], headers={"Range": "bytes=0-0"})
    assert head.status_code == 206 and head.headers["content-range"] == f"bytes 0-0/{len(data)}"
    bad = await interviewer.get(signed["url"], headers={"Range": f"bytes={len(data) + 5}-"})
    assert bad.status_code == 416 and bad.headers["content-range"] == f"bytes */{len(data)}"


async def test_download_is_admin_only_and_audited(app, org, storage, no_ffmpeg, auth_client) -> None:
    rec, part, data = await _recorded_session(app, org)
    url = f"{API}/sessions/{rec.session_id}/recordings/{part}/download"
    for role in (Role.INTERVIEWER, Role.RECRUITER):
        denied = await (await auth_client(role, organization_id=org)).get(url)
        assert denied.status_code == 403
    assert await _audit("recording.download") == []
    admin = await auth_client(Role.ADMIN, organization_id=org)
    r = await admin.get(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["download"] is True and body["url"].endswith("?download=1")
    assert body["filename"].startswith(f"camera-{part}-v1")
    file = await admin.get(body["url"])
    assert file.status_code == 200 and file.content == data
    assert file.headers["content-disposition"].startswith("attachment")
    [entry] = await _audit("recording.download")
    assert entry.details["sensitive"] is True and entry.details["size_bytes"] == len(data)


async def test_other_organizations_and_candidates_see_nothing(
    app, org, org_factory, storage, no_ffmpeg, auth_client
) -> None:
    rec, part, _ = await _recorded_session(app, org)
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    for path in ("recordings", f"recordings/{part}/url", f"recordings/{part}/download"):
        assert (await outsider.get(f"{API}/sessions/{rec.session_id}/{path}")).status_code == 404
    assert (await outsider.post(f"{API}/sessions/{rec.session_id}/recordings/finalize")).status_code == 404
    candidate = await candidate_client(app, rec)
    # G2: a candidate session is not a staff session on staff routes (separate cookie sets).
    assert (await candidate.get(f"{API}/sessions/{rec.session_id}/recordings")).status_code == 401
    await candidate.aclose()
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=org)
    missing = await interviewer.get(f"{API}/sessions/{rec.session_id}/recordings/{uuid.uuid4()}/url")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "recording_not_found"


async def test_policy_without_recording(app, org, storage, auth_client) -> None:
    rec = await make_session(org, policy=policy_snapshot(record_video=False))
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    body = (await recruiter.get(f"{API}/sessions/{rec.session_id}/recordings")).json()
    assert (
        body["parts"] == []
        and body["policy"]["record_video"] is False
        and body["policy"]["consented"] is False
    )
