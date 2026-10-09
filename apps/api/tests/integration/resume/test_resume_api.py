"""Resume API: attack corpus over HTTP, RBAC and organization isolation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, CandidateResume, MediaAsset
from app.domain.enums import Role
from tests.integration.resume.helpers import API, create_candidate, upload
from tests.unit.resume import corpus

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("filename", "builder", "code"),
    [
        ("cv.pdf", corpus.pdf_with_javascript, "active_content"),
        ("cv.docx", corpus.docm_with_macro, "active_content"),
        ("cv.pdf", corpus.windows_exe, "unsupported_file_type"),
        ("cv.pdf", corpus.pdf_with_trailing_zip, "invalid_file_structure"),
        ("cv.docx", corpus.zip_bomb_docx, "invalid_file_structure"),
        ("cv.pdf", lambda: corpus.pdf_bytes(encrypt_with="pw"), "encrypted_file"),
        ("cv.png", corpus.png_declaring_huge_dimensions, "image_too_large"),
    ],
)
async def test_attack_uploads_are_rejected_and_nothing_is_stored(
    auth_client, mock_ai, db_session, storage, filename, builder, code
) -> None:
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    response = await upload(client, candidate["id"], filename, builder(), content_type="application/pdf")
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["details"]["reason"] == code
    assert "Traceback" not in error["message"]
    assert await db_session.scalar(select(func.count()).select_from(CandidateResume)) == 0
    assert await db_session.scalar(select(func.count()).select_from(MediaAsset)) == 0
    assert not [path for path in storage.root.rglob("*") if path.is_file()]
    trail = (await db_session.scalars(select(AuditLog).where(AuditLog.action == "resume.rejected"))).all()
    assert len(trail) == 1 and trail[0].details["reason"] == code


async def test_oversized_upload(auth_client, mock_ai, settings) -> None:
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    settings.max_resume_bytes = 50_000
    response = await upload(client, candidate["id"], "cv.pdf", b"%PDF-1.7\n" + b"0" * 60_000)
    assert response.status_code == 422 and response.json()["error"]["code"] == "file_too_large"


async def test_rbac_and_organization_isolation(auth_client, org_factory, mock_ai) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(recruiter)
    uploaded = await upload(recruiter, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"))
    assert uploaded.status_code == 201
    resume_id = uploaded.json()["id"]

    interviewer = await auth_client(Role.INTERVIEWER, organization_id=recruiter.user.organization_id)
    assert (await interviewer.get(f"{API}/resumes/{resume_id}")).status_code == 200
    assert (await interviewer.get(f"{API}/candidates/{candidate['id']}")).status_code == 200
    denied = await upload(interviewer, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"))
    assert denied.status_code == 403
    assert (await interviewer.post(f"{API}/resumes/{resume_id}/reprocess", json={})).status_code == 403
    assert (
        await interviewer.post(f"{API}/candidates", json={"full_name": "X", "email": "x@example.com"})
    ).status_code == 403

    other_org = await org_factory()
    outsider = await auth_client(Role.ADMIN, organization_id=other_org)
    assert (await outsider.get(f"{API}/resumes/{resume_id}")).status_code == 404
    assert (await outsider.get(f"{API}/candidates/{candidate['id']}")).status_code == 404
    assert (await outsider.get(f"{API}/candidates/{candidate['id']}/profile")).status_code == 404
    assert (
        await upload(outsider, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"))
    ).status_code == 404
    assert (await outsider.get(f"{API}/candidates")).json()["total"] == 0

    superadmin = await auth_client(Role.SUPER_ADMIN, organization_id=other_org)
    scoped = await superadmin.get(
        f"{API}/resumes/{resume_id}", params={"organization_id": str(recruiter.user.organization_id)}
    )
    assert scoped.status_code == 200


async def test_unauthenticated_and_candidate_principals(client, candidate_auth_client) -> None:
    assert (await client.get(f"{API}/candidates")).status_code == 401
    candidate = await candidate_auth_client()
    assert (await candidate.get(f"{API}/candidates")).status_code == 403
    assert (await candidate.get(f"{API}/jobs")).status_code == 403
    assert (await candidate.post(f"{API}/interviews", json={})).status_code in (403, 422)


async def test_upload_with_foreign_job_or_interview_is_404(auth_client, org_factory, mock_ai) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(recruiter)
    response = await upload(
        recruiter, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"), job_id=str(uuid.uuid4())
    )
    assert response.status_code == 404
    assert (await recruiter.get(f"{API}/resumes/{uuid.uuid4()}")).status_code == 404


async def test_resume_view_audit_is_throttled_for_polling(auth_client, mock_ai, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    resume_id = (
        await upload(client, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"))
    ).json()["id"]
    for _ in range(5):
        body = (await client.get(f"{API}/resumes/{resume_id}")).json()
        assert body["media_url"]
    quiet = (await client.get(f"{API}/resumes/{resume_id}", params={"include_media_url": "false"})).json()
    assert quiet["media_url"] is None
    views = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "media.view")
    )
    assert views == 1
