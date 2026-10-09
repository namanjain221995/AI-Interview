"""Candidate archive (soft delete), restore and permanent data-subject deletion."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db.models import (
    Answer,
    AnswerEvaluation,
    AuditLog,
    AuthSession,
    Candidate,
    CandidateProfile,
    CandidateResume,
    CodeRun,
    CodingSession,
    CompetencyEvidence,
    IntegrityEvent,
    Interview,
    InterviewEvent,
    InterviewSession,
    MediaAsset,
    ModelCall,
    Question,
    Report,
    Score,
    Transcript,
)
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.identity.sessions import start_candidate_session
from app.services.reports import generate_reports
from tests.integration.resume.helpers import API, create_candidate, create_job, upload

pytestmark = pytest.mark.integration


async def _audit_actions(resource_id: Any) -> list[str]:
    async with session_scope() as db:
        rows = await db.scalars(
            select(AuditLog.action).where(AuditLog.resource_id == str(resource_id)).order_by(AuditLog.id)
        )
        return list(rows.all())


async def _job_with_rounds(client: httpx.AsyncClient) -> dict[str, Any]:
    job = await create_job(client)
    response = await client.post(f"{API}/jobs/{job['id']}/rounds", json={"round_type": "technical_screening"})
    assert response.status_code == 201, response.text
    return job


async def _candidate_client(
    app: Any, *, candidate_id: uuid.UUID, interview_id: uuid.UUID, org: uuid.UUID
) -> httpx.AsyncClient:
    async with session_scope() as db:
        tokens = await start_candidate_session(
            db, candidate_id=candidate_id, interview_id=interview_id, organization_id=org
        )
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50001)),
        base_url="http://testserver",
        cookies={"access_token": tokens.access_token, "csrf_token": tokens.csrf_token},
    )
    http.headers["X-CSRF-Token"] = tokens.csrf_token
    return http


async def _count(model: Any, *where: Any) -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# ------------------------------------------------------------------ archive


async def test_archive_hides_candidate_and_restore_brings_it_back(auth_client, org_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    kept = await create_candidate(recruiter, email="kept@example.com")
    gone = await create_candidate(recruiter, email="gone@example.com")
    assert kept["archived_at"] is None

    archived = await recruiter.post(f"{API}/candidates/{gone['id']}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None

    async def listed(**params: Any) -> list[str]:
        body = (await recruiter.get(f"{API}/candidates", params=params)).json()
        assert body["total"] == len(body["items"])
        return sorted(item["email"] for item in body["items"])

    assert await listed() == ["kept@example.com"]
    assert await listed(include_archived="true") == ["gone@example.com", "kept@example.com"]
    assert await listed(archived_only="true") == ["gone@example.com"]
    assert await listed(q="gone") == []
    assert await listed(q="gone", include_archived="true") == ["gone@example.com"]
    # Still readable by id (the UI shows the archived banner and the restore action).
    detail = (await recruiter.get(f"{API}/candidates/{gone['id']}")).json()
    assert detail["archived_at"] == archived.json()["archived_at"]

    again = await recruiter.post(f"{API}/candidates/{gone['id']}/archive")
    assert again.status_code == 200 and again.json()["archived_at"] == archived.json()["archived_at"]
    restored = await recruiter.post(f"{API}/candidates/{gone['id']}/restore")
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    assert (await recruiter.post(f"{API}/candidates/{gone['id']}/restore")).status_code == 200
    assert await listed() == ["gone@example.com", "kept@example.com"]
    # Archived once, restored once: repeated calls are no-ops and are not audited.
    actions = await _audit_actions(gone["id"])
    assert actions.count("candidate.archive") == 1 and actions.count("candidate.restore") == 1

    interviewer = await auth_client(Role.INTERVIEWER, organization_id=recruiter.user.organization_id)
    assert (await interviewer.post(f"{API}/candidates/{kept['id']}/archive")).status_code == 403
    outsider = await auth_client(Role.RECRUITER, organization_id=await org_factory())
    assert (await outsider.post(f"{API}/candidates/{kept['id']}/archive")).status_code == 404


async def test_archive_cancels_open_invites_and_blocks_new_activity(
    app, auth_client, mock_ai, seeded_catalog, db_session
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org = recruiter.user.organization_id
    job = await _job_with_rounds(recruiter)
    candidate = await create_candidate(recruiter, email="arch@example.com")
    cid = uuid.UUID(candidate["id"])

    first = await recruiter.post(
        f"{API}/interviews", json={"candidate_id": candidate["id"], "job_id": job["id"]}
    )
    assert first.status_code == 201, first.text
    expired_id = uuid.UUID(first.json()["interview"]["id"])
    await db_session.execute(
        update(Interview)
        .where(Interview.id == expired_id)
        .values(invite_expires_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await db_session.commit()
    second = await recruiter.post(
        f"{API}/interviews", json={"candidate_id": candidate["id"], "job_id": job["id"]}
    )
    assert second.status_code == 201, second.text
    open_id = uuid.UUID(second.json()["interview"]["id"])

    portal = await _candidate_client(app, candidate_id=cid, interview_id=open_id, org=org)
    try:
        assert (await portal.get("/api/v1/portal/me")).status_code == 200
        archived = await recruiter.post(f"{API}/candidates/{candidate['id']}/archive")
        assert archived.status_code == 200, archived.text
        # The open invitation is cancelled and the candidate's portal session revoked at once.
        assert (await portal.get("/api/v1/portal/me")).status_code == 401
    finally:
        await portal.aclose()
    db_session.expire_all()
    assert (await db_session.get(Interview, open_id)).status == "cancelled"
    assert (await db_session.get(Interview, expired_id)).status == "invited"  # already unusable: untouched
    sessions = (
        await db_session.scalars(select(AuthSession).where(AuthSession.interview_id == open_id))
    ).all()
    assert sessions and all(s.revoked_at is not None for s in sessions)
    cancel_audit = await _audit_actions(open_id)
    assert "interview.cancel" in cancel_audit
    archive_entry = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "candidate.archive"))
    ).one()
    assert archive_entry.details == {"cancelled_interviews": [str(open_id)]}

    blocked = await recruiter.post(
        f"{API}/interviews", json={"candidate_id": candidate["id"], "job_id": job["id"]}
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "candidate_archived"
    relink = await recruiter.post(f"{API}/interviews/{expired_id}/regenerate-link")
    assert relink.status_code == 409 and relink.json()["error"]["code"] == "candidate_archived"
    resume = await upload(
        recruiter, candidate["id"], "cv.txt", b"Jane Doe\nPlatform engineer\n", content_type="text/plain"
    )
    assert resume.status_code == 409 and resume.json()["error"]["code"] == "candidate_archived"
    duplicate = await recruiter.post(
        f"{API}/candidates", json={"full_name": "Jane", "email": "ARCH@example.com"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["archived"] is True
    assert duplicate.json()["error"]["details"]["candidate_id"] == candidate["id"]
    # Editing an archived record stays possible.
    assert (
        await recruiter.patch(f"{API}/candidates/{candidate['id']}", json={"location": "Remote"})
    ).status_code == 200

    assert (await recruiter.post(f"{API}/candidates/{candidate['id']}/restore")).status_code == 200
    again = await recruiter.post(
        f"{API}/interviews", json={"candidate_id": candidate["id"], "job_id": job["id"]}
    )
    assert again.status_code == 201, again.text


async def test_archive_refuses_an_interview_in_progress(auth_client, session_factory, db_session) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id, status="live")
    response = await recruiter.post(f"{API}/candidates/{fx.candidate_id}/archive")
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "candidate_interview_in_progress"
    assert error["details"]["interview_ids"] == [str(fx.interview_id)]
    assert (await db_session.get(Candidate, fx.candidate_id)).archived_at is None


# ------------------------------------------------------------------ erasure


async def test_erasure_requires_admin_and_an_archived_candidate(auth_client, org_factory) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    candidate = await create_candidate(admin, email="dsr@example.com")
    url = f"{API}/candidates/{candidate['id']}"
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=org)
    for client in (recruiter, interviewer):
        denied = await client.delete(url)
        assert denied.status_code == 403, denied.text
    not_archived = await admin.delete(url)
    assert (
        not_archived.status_code == 409 and not_archived.json()["error"]["code"] == "candidate_not_archived"
    )
    assert (await admin.post(f"{url}/archive")).status_code == 200
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.delete(url)).status_code == 404
    bad_reference = await admin.delete(url, params={"request_reference": "Jane Doe asked"})
    assert bad_reference.status_code == 422  # free text could carry personal data into the audit log
    deleted = await admin.delete(url, params={"request_reference": "DSR-2026-0042"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["interviews"] == 0 and deleted.json()["sessions"] == 0
    assert (await admin.get(url)).status_code == 404
    assert (await admin.delete(url)).status_code == 404


async def test_erasure_removes_every_record_and_file(
    app, auth_client, session_factory, mock_ai, redis, storage, db_session
) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    fx = await session_factory(org, candidate_name="Erin Erasure")
    other = await session_factory(org, candidate_name="Kept Person")
    async with session_scope() as db:
        email = (await db.get(Candidate, fx.candidate_id)).email

    # Real bytes behind the registered answer audio, a recruiter-uploaded resume
    # (store_media) and both HTML report artifacts (report pipeline).
    audio_key = f"orgs/{org}/sessions/{fx.session_id}/audio/1.wav"
    await storage.put(audio_key, b"RIFF0000WAVEfmt ", "audio/wav")
    uploaded = await upload(
        admin,
        str(fx.candidate_id),
        "erin_cv.txt",
        b"Erin Erasure\nKubernetes engineer, 8 years\n",
        content_type="text/plain",
    )
    assert uploaded.status_code == 201, uploaded.text
    resume_id = uuid.UUID(uploaded.json()["id"])
    await generate_reports(fx.session_id, use_model=False)
    async with session_scope() as db:
        resume_row = await db.get(CandidateResume, resume_id)
        resume_key = resume_row.storage_key
        resume_row.interview_id = fx.interview_id  # portal-style upload: SET NULL and CASCADE paths meet
        profile_call = ModelCall(
            organization_id=org,
            task="profile_analysis",
            endpoint="main",
            model="test-model-profile",
            status="ok",
            latency_ms=95000.0,
            request_payload={"resume": "Erin Erasure, 8 years"},
        )
        db.add(profile_call)
        await db.flush()
        profile = CandidateProfile(
            candidate_id=fx.candidate_id,
            resume_id=resume_id,
            profile={"headline": "Kubernetes engineer"},
            model_call_ids=[str(profile_call.id)],
        )
        db.add(profile)
        await db.flush()
        await db.execute(
            update(InterviewSession).where(InterviewSession.id == fx.session_id).values(profile_id=profile.id)
        )
        artifact_keys = [
            k
            for k in (
                await db.scalars(
                    select(Report.artifact_storage_key).where(Report.session_id == fx.session_id)
                )
            ).all()
            if k
        ]
        db.add(
            ModelCall(
                organization_id=org,
                session_id=fx.session_id,
                task="answer_evaluation",
                endpoint="main",
                model="test-model",
                status="ok",
                latency_ms=812.0,
                prompt_tokens=900,
                request_payload={"messages": [{"role": "user", "content": "Erin Erasure said ..."}]},
                response_payload={"text": "Erin ..."},
                error="ValueError: Erin Erasure",
            )
        )
        db.add(
            AuditLog(
                organization_id=org,
                actor_type="candidate",
                actor_id=fx.candidate_id,
                action="consent.accept",
                resource_type="interview",
                resource_id=str(fx.interview_id),
                ip_address="10.1.2.3",
                user_agent="Mozilla/5.0 (Erin's laptop)",
            )
        )
    assert artifact_keys and all([await storage.exists(k) for k in [audio_key, resume_key, *artifact_keys]])
    await redis.set(f"session:{fx.session_id}:state", json.dumps({"version": 3}))
    await redis.rpush(f"session:{fx.session_id}:outbox", "transcript")
    await redis.set(f"session:{other.session_id}:state", "{}")

    portal = await _candidate_client(app, candidate_id=fx.candidate_id, interview_id=fx.interview_id, org=org)
    try:
        assert (await portal.get("/api/v1/portal/me")).status_code == 200  # auth state now cached

        url = f"{API}/candidates/{fx.candidate_id}"
        assert (await admin.post(f"{url}/archive")).status_code == 200
        busy = await admin.delete(url)
        assert busy.status_code == 409, busy.text
        assert busy.json()["error"]["code"] == "candidate_busy"
        assert busy.json()["error"]["details"]["blockers"] == [
            {"type": "resume", "id": str(resume_id), "status": "uploaded"}
        ]
        assert await storage.exists(resume_key)  # a refused request deletes nothing
        await db_session.execute(
            update(CandidateResume).where(CandidateResume.id == resume_id).values(status="ready")
        )
        await db_session.commit()

        response = await admin.delete(url, params={"request_reference": "DSR-2026-0042"})
        assert response.status_code == 200, response.text
        assert (await portal.get("/api/v1/portal/me")).status_code == 401
    finally:
        await portal.aclose()
    body = response.json()
    assert body["candidate_id"] == str(fx.candidate_id)
    assert (body["interviews"], body["sessions"], body["resumes"], body["profiles"]) == (1, 1, 1, 1)
    assert body["media_files_deleted"] == 2 + len(artifact_keys)  # audio, resume, report artifacts
    assert body["media_by_kind"] == {"answer_audio": 1, "report_artifact": len(artifact_keys), "resume": 1}
    assert body["model_calls_scrubbed"] >= 1 and body["audit_entries_scrubbed"] == 1
    assert body["auth_sessions_revoked"] >= 1

    for key in (audio_key, resume_key, *artifact_keys):
        assert not await storage.exists(key), key
    sid, cid = fx.session_id, fx.candidate_id
    for model, condition in (
        (Candidate, Candidate.id == cid),
        (CandidateResume, CandidateResume.candidate_id == cid),
        (Interview, Interview.candidate_id == cid),
        (InterviewSession, InterviewSession.id == sid),
        (InterviewEvent, InterviewEvent.session_id == sid),
        (Question, Question.session_id == sid),
        (Answer, Answer.session_id == sid),
        (Transcript, Transcript.session_id == sid),
        (AnswerEvaluation, AnswerEvaluation.session_id == sid),
        (CompetencyEvidence, CompetencyEvidence.session_id == sid),
        (Score, Score.session_id == sid),
        (Report, Report.session_id == sid),
        (CodingSession, CodingSession.session_id == sid),
        (CodeRun, CodeRun.coding_session_id == fx.coding_session_id),
        (IntegrityEvent, IntegrityEvent.session_id == sid),
        (AuthSession, AuthSession.candidate_id == cid),
    ):
        assert await _count(model, condition) == 0, model.__name__
    # The other candidate of the organization is untouched.
    assert await _count(InterviewSession, InterviewSession.id == other.session_id) == 1
    assert await _count(Question, Question.session_id == other.session_id) > 0

    async with session_scope() as db:
        media = (await db.scalars(select(MediaAsset).where(MediaAsset.candidate_id == cid))).all()
        assert len(media) == body["media_files_deleted"]
        for asset in media:  # kept as an anonymous deletion record
            assert asset.deleted_at is not None and asset.session_id is None
            assert asset.storage_key == f"erased/{asset.id}" and asset.sha256 is None
            assert asset.details == {"deleted_reason": "data_subject_deletion", "erased": True}
        call = (await db.scalars(select(ModelCall).where(ModelCall.model == "test-model"))).one()
        assert call.request_payload is None and call.response_payload is None and call.error is None
        assert call.latency_ms == 812.0 and call.prompt_tokens == 900  # usage metrics survive
        profile_call = (
            await db.scalars(select(ModelCall).where(ModelCall.model == "test-model-profile"))
        ).one()
        assert (
            profile_call.request_payload is None and profile_call.latency_ms == 95000.0
        )  # found via profile ids
        assert (
            await db.scalar(
                select(func.count()).select_from(CandidateProfile).where(CandidateProfile.candidate_id == cid)
            )
            == 0
        )
        actor_entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "consent.accept"))).one()
        assert actor_entry.ip_address is None and actor_entry.user_agent is None
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "candidate.delete"))).one()
        assert entry.resource_id == str(cid) and entry.details["request_reference"] == "DSR-2026-0042"
        assert entry.details["sessions"] == 1 and entry.details["reason"] == "data_subject_deletion"
        trail = json.dumps([e.details for e in (await db.scalars(select(AuditLog))).all()])
        assert "Erin" not in trail and email not in trail and "erin_cv" not in trail

    assert await redis.exists(f"session:{sid}:state", f"session:{sid}:outbox") == 0
    assert await redis.exists(f"session:{other.session_id}:state") == 1


async def test_erasure_waits_for_a_running_session_but_not_a_stale_one(
    auth_client, session_factory, db_session
) -> None:
    admin = await auth_client(Role.ADMIN)
    fx = await session_factory(admin.user.organization_id, status="completed")
    url = f"{API}/candidates/{fx.candidate_id}"
    assert (await admin.post(f"{url}/archive")).status_code == 200
    # A runtime is still attached to a session of the (archived) candidate.
    now = datetime.now(UTC)
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == fx.session_id)
        .values(status="live", last_heartbeat_at=now, updated_at=now)
    )
    await db_session.commit()
    busy = await admin.delete(url)
    assert busy.status_code == 409, busy.text
    assert busy.json()["error"]["details"]["blockers"] == [
        {"type": "session", "id": str(fx.session_id), "status": "live"}
    ]
    assert busy.json()["error"]["details"]["retry_after_seconds"] == 900
    # Abandoned long ago (crashed runtime): it no longer blocks the deletion.
    stale = now - timedelta(hours=2)
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == fx.session_id)
        .values(last_heartbeat_at=stale, updated_at=stale)
    )
    await db_session.commit()
    deleted = await admin.delete(url)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["sessions"] == 1


async def test_super_admin_can_erase_in_any_organization(auth_client, org_factory) -> None:
    other_org = await org_factory()
    owner = await auth_client(Role.ADMIN, organization_id=other_org)
    candidate = await create_candidate(owner, email="far@example.com")
    assert (await owner.post(f"{API}/candidates/{candidate['id']}/archive")).status_code == 200
    root = await auth_client(Role.SUPER_ADMIN)
    url = f"{API}/candidates/{candidate['id']}"
    assert (await root.delete(url)).status_code == 404  # own organization by default
    deleted = await root.delete(url, params={"organization_id": str(other_org)})
    assert deleted.status_code == 200, deleted.text
    async with session_scope() as db:
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "candidate.delete"))).one()
        assert entry.organization_id == other_org
