"""Media registry, authorization, signed URLs and streaming."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.errors import Conflict
from app.db.models import AuditLog, MediaAsset
from app.domain.enums import MediaKind, Role
from app.services.storage.media import delete_media, store_media, wait_for_cleanup
from app.services.storage.signing import make_media_token

pytestmark = pytest.mark.integration


async def _store(db, org_id, kind: MediaKind, data: bytes, content_type: str, *, candidate_id=None, name=None):
    key = f"orgs/{org_id}/test/{kind.value}/{name or uuid.uuid4().hex}"
    asset = await store_media(
        db, organization_id=org_id, kind=kind, key=key, data=data, content_type=content_type, candidate_id=candidate_id
    )
    await db.commit()
    return asset


async def test_store_media_registers_file(db_session, storage, org_factory, settings):
    org_id = await org_factory()
    asset = await _store(db_session, org_id, MediaKind.CAMERA_FRAME, b"\xff\xd8jpeg", "image/jpeg")
    assert asset.size_bytes == 6 and len(asset.sha256) == 64
    expected = datetime.now(UTC) + timedelta(days=settings.frame_retention_days)
    assert abs((asset.retention_until - expected).total_seconds()) < 60
    assert await storage.read(asset.storage_key) == b"\xff\xd8jpeg"
    with pytest.raises(Conflict):
        await store_media(db_session, organization_id=org_id, kind=MediaKind.CAMERA_FRAME, key=asset.storage_key,
                          data=b"x", content_type="image/jpeg")
    await db_session.rollback()
    assert await delete_media(db_session, asset) is True
    await db_session.commit()
    assert not await storage.exists(asset.storage_key)
    assert asset.deleted_at is not None
    assert await delete_media(db_session, asset) is False
    # A deleted key can be reused; the row is resurrected.
    again = await store_media(db_session, organization_id=org_id, kind=MediaKind.CAMERA_FRAME, key=asset.storage_key,
                              data=b"new", content_type="image/jpeg", retention_days=1)
    await db_session.commit()
    assert again.id == asset.id and again.deleted_at is None and again.size_bytes == 3


async def test_rollback_removes_written_files(db_session, storage, org_factory):
    org_id = await org_factory()
    asset = await store_media(db_session, organization_id=org_id, kind=MediaKind.RESUME, key=f"orgs/{org_id}/r/cv.pdf",
                              data=b"%PDF-1.7", content_type="application/pdf")
    assert await storage.exists(asset.storage_key)
    await db_session.rollback()
    await wait_for_cleanup()
    assert not await storage.exists(f"orgs/{org_id}/r/cv.pdf")
    assert await db_session.scalar(select(MediaAsset).where(MediaAsset.storage_key == f"orgs/{org_id}/r/cv.pdf")) is None


async def test_failed_write_leaves_caller_transaction_usable(db_session, storage, org_factory):
    from app.core.errors import PayloadTooLarge
    from app.db.models import SystemNote

    org_id = await org_factory()

    async def too_big():
        for _ in range(3):
            yield b"x" * 1000

    db_session.add(SystemNote(kind="t", body="kept"))
    with pytest.raises(PayloadTooLarge):
        await store_media(db_session, organization_id=org_id, kind=MediaKind.VIDEO_SEGMENT,
                          key=f"orgs/{org_id}/v/big.webm", data=too_big(), content_type="video/webm", max_bytes=1500)
    await db_session.commit()  # the savepoint rolled back; the outer transaction is intact
    assert await db_session.scalar(select(MediaAsset).where(MediaAsset.storage_key == f"orgs/{org_id}/v/big.webm")) is None
    assert (await db_session.scalars(select(SystemNote.body))).all() == ["kept"]
    assert not await storage.exists(f"orgs/{org_id}/v/big.webm")


async def test_concurrent_writers_of_one_key_never_lose_committed_bytes(db_session, storage, org_factory):
    from sqlalchemy.exc import IntegrityError

    from app.db.session import get_sessionmaker

    org_id = await org_factory()
    key = f"orgs/{org_id}/shared/file.bin"
    first = await store_media(db_session, organization_id=org_id, kind=MediaKind.REPORT_ARTIFACT, key=key,
                              data=b"first", content_type="application/octet-stream")
    other = get_sessionmaker()()
    try:
        commit_first = asyncio.create_task(asyncio.sleep(0.2))
        loser = asyncio.create_task(
            store_media(other, organization_id=org_id, kind=MediaKind.REPORT_ARTIFACT, key=key, data=b"second",
                        content_type="application/octet-stream")
        )
        await commit_first
        await db_session.commit()  # the second writer was blocked on the unique key until now
        with pytest.raises(IntegrityError):
            await loser
        await other.rollback()
    finally:
        await other.close()
    await wait_for_cleanup()
    assert await storage.read(key) == b"first" and first.sha256 is not None


async def test_streaming_from_async_iterable(db_session, storage, org_factory):
    org_id = await org_factory()

    async def chunks():
        for _ in range(4):
            yield os.urandom(300_000)

    asset = await store_media(db_session, organization_id=org_id, kind=MediaKind.VIDEO_SEGMENT,
                              key=f"orgs/{org_id}/v/00001.webm", data=chunks(), content_type="video/webm",
                              max_bytes=2_000_000)
    await db_session.commit()
    assert asset.size_bytes == 1_200_000 and await storage.size(asset.storage_key) == 1_200_000


async def test_url_issuance_authorization_by_kind(auth_client, db_session):
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    resume = await _store(db_session, org_id, MediaKind.RESUME, b"%PDF-1.7 resume", "application/pdf", candidate_id=uuid.uuid4())
    frame = await _store(db_session, org_id, MediaKind.CAMERA_FRAME, b"\xff\xd8", "image/jpeg")
    report = await _store(db_session, org_id, MediaKind.REPORT_ARTIFACT, b"<html></html>", "text/html")

    for asset in (resume, frame, report):
        response = await recruiter.get(f"/api/v1/media/{asset.id}/url")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["url"].startswith("/api/v1/media/signed/") and body["media_id"] == str(asset.id)
        assert body["kind"] == asset.kind and body["size_bytes"] == asset.size_bytes

    views = (await db_session.scalars(select(AuditLog).where(AuditLog.action == "media.view"))).all()
    assert {v.resource_id for v in views} == {str(resume.id), str(frame.id), str(report.id)}
    assert all(v.organization_id == org_id for v in views)

    from app.api.routers.media import required_permission
    from app.core.rbac import Permission

    assert required_permission(MediaKind.RESUME) is Permission.CANDIDATES_READ
    assert required_permission(MediaKind.REPORT_ARTIFACT) is Permission.REPORTS_READ
    assert all(
        required_permission(kind) is Permission.SESSIONS_READ
        for kind in MediaKind
        if kind not in (MediaKind.RESUME, MediaKind.REPORT_ARTIFACT)
    )
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=org_id)
    assert (await interviewer.get(f"/api/v1/media/{frame.id}/url")).status_code == 200


async def test_cross_org_and_missing_media_are_not_found(auth_client, db_session, org_factory):
    admin = await auth_client(Role.ADMIN)
    foreign_org = await org_factory()
    foreign = await _store(db_session, foreign_org, MediaKind.ANSWER_AUDIO, b"RIFFxxxx", "audio/wav")
    response = await admin.get(f"/api/v1/media/{foreign.id}/url")
    assert response.status_code == 404
    assert (await admin.get(f"/api/v1/media/{uuid.uuid4()}/url")).status_code == 404
    root = await auth_client(Role.SUPER_ADMIN)
    assert (await root.get(f"/api/v1/media/{foreign.id}/url")).status_code == 200


async def test_candidate_only_reads_own_resume(candidate_auth_client, db_session):
    candidate = await candidate_auth_client()
    ctx = candidate.candidate
    own = await _store(db_session, ctx.organization_id, MediaKind.RESUME, b"%PDF own", "application/pdf",
                       candidate_id=ctx.candidate_id)
    someone_else = await _store(db_session, ctx.organization_id, MediaKind.RESUME, b"%PDF other", "application/pdf",
                                candidate_id=uuid.uuid4())
    frame = await _store(db_session, ctx.organization_id, MediaKind.CAMERA_FRAME, b"\xff\xd8", "image/jpeg",
                         candidate_id=ctx.candidate_id)
    ok = await candidate.get(f"/api/v1/media/{own.id}/url")
    assert ok.status_code == 200
    assert (await candidate.get(f"/api/v1/media/{someone_else.id}/url")).status_code == 404
    assert (await candidate.get(f"/api/v1/media/{frame.id}/url")).status_code == 404
    streamed = await candidate.get(ok.json()["url"])
    assert streamed.status_code == 200 and streamed.content == b"%PDF own"


async def test_signed_download_headers_and_ranges(client, db_session, org_factory):
    org_id = await org_factory()
    data = bytes(range(256)) * 8  # 2048 bytes
    pdf = await _store(db_session, org_id, MediaKind.RESUME, data, "application/pdf", name="cv.pdf")
    token = make_media_token(pdf.id, 60)
    full = await client.get(f"/api/v1/media/signed/{token}")
    assert full.status_code == 200 and full.content == data
    assert full.headers["content-type"] == "application/pdf"
    assert full.headers["content-length"] == "2048"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in full.headers["cache-control"]
    assert full.headers["content-disposition"].startswith('attachment; filename="cv.pdf"')

    part = await client.get(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == data[100:200]
    assert part.headers["content-range"] == "bytes 100-199/2048" and part.headers["content-length"] == "100"
    suffix = await client.get(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=-48"})
    assert suffix.status_code == 206 and suffix.content == data[-48:]
    open_ended = await client.get(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=2000-"})
    assert open_ended.content == data[2000:]
    clamp = await client.get(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=2040-999999"})
    assert clamp.status_code == 206 and clamp.content == data[2040:]
    unsatisfiable = await client.get(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=5000-6000"})
    assert unsatisfiable.status_code == 416 and unsatisfiable.headers["content-range"] == "bytes */2048"
    multi = await client.get(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=0-1,5-6"})
    assert multi.status_code == 200 and len(multi.content) == 2048  # unsupported multi-range ignored
    head = await client.head(f"/api/v1/media/signed/{token}")
    assert head.status_code == 200 and head.headers["content-length"] == "2048" and head.content == b""
    head_range = await client.head(f"/api/v1/media/signed/{token}", headers={"Range": "bytes=0-9"})
    assert head_range.status_code == 206 and head_range.headers["content-range"] == "bytes 0-9/2048"


async def test_inline_and_active_content_types(client, db_session, org_factory):
    org_id = await org_factory()
    image = await _store(db_session, org_id, MediaKind.CAMERA_FRAME, b"\xff\xd8\xff", "image/jpeg", name="f.jpg")
    html = await _store(db_session, org_id, MediaKind.REPORT_ARTIFACT, b"<script>alert(1)</script>", "text/html",
                        name="recruiter-v1.html")
    shown = await client.get(f"/api/v1/media/signed/{make_media_token(image.id, 60)}")
    assert shown.headers["content-type"] == "image/jpeg"
    assert shown.headers["content-disposition"].startswith("inline")
    downloaded = await client.get(f"/api/v1/media/signed/{make_media_token(html.id, 60)}")
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in downloaded.headers["content-security-policy"]


async def test_invalid_expired_and_deleted_links(client, db_session, org_factory):
    org_id = await org_factory()
    asset = await _store(db_session, org_id, MediaKind.ANSWER_AUDIO, b"RIFF", "audio/wav")
    expired = make_media_token(asset.id, 5, now=time.time() - 60)
    assert (await client.get(f"/api/v1/media/signed/{expired}")).status_code == 404
    assert (await client.get("/api/v1/media/signed/not-a-token")).status_code == 404
    assert (await client.get(f"/api/v1/media/signed/{make_media_token(uuid.uuid4(), 60)}")).status_code == 404
    token = make_media_token(asset.id, 60)
    await db_session.execute(
        update(MediaAsset).where(MediaAsset.id == asset.id).values(retention_until=datetime.now(UTC) - timedelta(days=1))
    )
    await db_session.commit()
    assert (await client.get(f"/api/v1/media/signed/{token}")).status_code == 404
    await db_session.execute(update(MediaAsset).where(MediaAsset.id == asset.id).values(retention_until=None))
    await db_session.commit()
    assert (await client.get(f"/api/v1/media/signed/{token}")).status_code == 200
    await delete_media(db_session, asset.id)
    await db_session.commit()
    assert (await client.get(f"/api/v1/media/signed/{token}")).status_code == 404


async def test_media_url_requires_authentication(client, app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as anonymous:
        assert (await anonymous.get(f"/api/v1/media/{uuid.uuid4()}/url")).status_code == 401
