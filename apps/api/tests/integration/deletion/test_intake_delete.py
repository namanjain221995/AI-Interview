"""DELETE /intake/interviews/{id}: admin-only, org-scoped, refuses a live session, removes
the intake row, invitation, sessions, evaluations, decisions, reports and files."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import (
    AuditLog,
    AuthSession,
    Candidate,
    Consent,
    Interview,
    InterviewSession,
    Report,
    User,
)
from app.db.models.intake import IntakeInterview
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.identity.sessions import start_candidate_session
from app.services.intake.demo import create_demo_intake
from app.services.storage.media import store_media
from tests.integration.deletion.world import API, count, session_tables

pytestmark = pytest.mark.integration


async def _demo(admin, storage):
    org = admin.user.organization_id
    async with session_scope() as db:
        reviewer = await db.get(User, admin.user.id)
        db.expunge(reviewer)
    demo = await create_demo_intake(org, decide=True, reviewer=reviewer, candidate_name="Ira Intake")
    async with session_scope() as db:
        capture = await store_media(
            db,
            organization_id=org,
            kind="answer_audio",
            key=f"orgs/{org}/sessions/{demo.session_id}/intake/relay-1.wav",
            data=b"RIFF relay",
            content_type="audio/wav",
            session_id=demo.session_id,
            candidate_id=demo.candidate_id,
        )
        db.add(
            Consent(
                interview_id=demo.interview_id,
                candidate_id=demo.candidate_id,
                policy_version="v1",
                policy_hash="0" * 64,
            )
        )
        await start_candidate_session(
            db, candidate_id=demo.candidate_id, interview_id=demo.interview_id, organization_id=org
        )
        await db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == demo.session_id)
            .values(status="completed", updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
        keys = [capture.storage_key] + [
            k
            for k in (
                await db.scalars(
                    select(Report.artifact_storage_key).where(Report.session_id == demo.session_id)
                )
            ).all()
            if k
        ]
    return demo, keys


async def test_admin_deletes_an_intake_interview(auth_client, storage) -> None:
    admin = await auth_client(Role.ADMIN)
    demo, keys = await _demo(admin, storage)
    assert len(keys) >= 2 and all([await storage.exists(k) for k in keys])
    tables = session_tables([demo.session_id], None)
    present = {model.__name__ for model, condition in tables if await count(model, condition) > 0}
    assert {
        "IntakeStageResult",
        "IntakeActivityResult",
        "IntakeEvaluation",
        "IntakeEvaluatorRun",
        "IntakeCompetencyScore",
        "IntakeDecision",
        "Report",
        "ReviewDecision",
        "Transcript",
        "IntegrityEvent",
        "MediaAsset",
    } <= present, present

    # G8: this organisation's plan does not include intake (it cannot list it), yet its data can always be removed
    assert (await admin.get(f"{API}/intake/interviews")).status_code == 404
    response = await admin.delete(f"{API}/intake/interviews/{demo.intake_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intake_id"] == str(demo.intake_id) and body["was_draft"] is False
    assert body["interviews"] == 1 and body["evaluations"] >= 1 and body["decisions"] == 1
    assert body["auth_sessions_revoked"] == 1
    assert body["sessions"]["sessions"] == 1 and body["sessions"]["media_files_deleted"] == len(keys)

    for model, condition in tables:
        assert await count(model, condition) == 0, model.__name__
    for key in keys:
        assert not await storage.exists(key), key
    assert await count(IntakeInterview, IntakeInterview.id == demo.intake_id) == 0
    assert await count(Interview, Interview.id == demo.interview_id) == 0
    assert await count(Consent, Consent.interview_id == demo.interview_id) == 0
    assert await count(AuthSession, AuthSession.interview_id == demo.interview_id) == 0
    assert await count(Candidate, Candidate.id == demo.candidate_id) == 1  # the candidate stays
    assert (await admin.delete(f"{API}/intake/interviews/{demo.intake_id}")).status_code == 404
    async with session_scope() as db:
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "intake.delete"))).one()
        assert entry.resource_id == str(demo.intake_id) and entry.details["decisions"] == 1
        assert "Ira" not in json.dumps(entry.details)


async def test_intake_delete_permissions_scope_and_busy(
    auth_client, org_factory, db_session, storage
) -> None:
    admin = await auth_client(Role.ADMIN)
    demo, _keys = await _demo(admin, storage)
    url = f"{API}/intake/interviews/{demo.intake_id}"
    recruiter = await auth_client(Role.RECRUITER, organization_id=admin.user.organization_id)
    assert (await recruiter.delete(url)).status_code == 403
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    outside = await outsider.delete(url)
    assert outside.status_code == 404 and outside.json()["error"]["code"] == "intake_not_found"
    now = datetime.now(UTC)
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == demo.session_id)
        .values(status="live", last_heartbeat_at=now, updated_at=now)
    )
    await db_session.commit()
    busy = await admin.delete(url)
    assert busy.status_code == 409, busy.text
    assert busy.json()["error"]["code"] == "intake_busy"
    assert await count(IntakeInterview, IntakeInterview.id == demo.intake_id) == 1
    assert await count(InterviewSession, InterviewSession.id == demo.session_id) == 1
