"""DELETE /sessions/{id}: admin-only, org-scoped, refuses live sessions, removes rows and files."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import AuditLog, Candidate, Interview, InterviewSession, ModelCall
from app.db.models.intake import IntakeInterview
from app.db.session import session_scope
from app.domain.enums import Role
from tests.integration.deletion.world import API, add_session_records, count, session_tables
from tests.integration.intake.eval_helpers import make_demo_rows
from tests.integration.reports.factories import create_completed_session

pytestmark = pytest.mark.integration


async def test_admin_deletes_a_session_with_its_rows_and_files(auth_client, mock_ai, redis, storage) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    fx = await create_completed_session(org, candidate_name="Sam Session")
    keys = await add_session_records(org, fx, reviewer_id=admin.user.id, storage=storage)
    other = await create_completed_session(org, candidate_name="Kept Person")
    other_keys = await add_session_records(org, other, reviewer_id=admin.user.id, storage=storage)
    async with session_scope() as db:
        db.add(
            ModelCall(
                organization_id=org,
                session_id=fx.session_id,
                task="answer_evaluation",
                endpoint="main",
                model="session-delete-model",
                status="ok",
                latency_ms=10.0,
                request_payload={"prompt": "Sam Session said"},
            )
        )
    tables = session_tables([fx.session_id], fx.coding_session_id)
    for model, condition in tables:
        if not model.__name__.startswith("Intake"):  # a regular (non-intake) session
            assert await count(model, condition) > 0, model.__name__
    await redis.set(f"session:{fx.session_id}:state", "{}")

    response = await admin.delete(f"{API}/sessions/{fx.session_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == str(fx.session_id) and body["sessions"] == 1
    assert body["candidate_id"] == str(fx.candidate_id) and body["interview_id"] == str(fx.interview_id)
    assert body["media_files_deleted"] == len(keys) and body["reports"] >= 1 and body["coding_sessions"] == 1

    for model, condition in tables:
        assert await count(model, condition) == 0, model.__name__
    for key in keys:
        assert not await storage.exists(key), key
    for key in other_keys:
        assert await storage.exists(key), key
    # The candidate and the (completed) invitation stay; the other session is untouched.
    assert await count(Candidate, Candidate.id == fx.candidate_id) == 1
    assert await count(Interview, Interview.id == fx.interview_id) == 1
    assert await count(InterviewSession, InterviewSession.id == other.session_id) == 1
    assert await redis.exists(f"session:{fx.session_id}:state") == 0
    assert (await admin.get(f"{API}/sessions/{fx.session_id}")).status_code == 404
    assert (await admin.delete(f"{API}/sessions/{fx.session_id}")).status_code == 404
    async with session_scope() as db:
        call = (await db.scalars(select(ModelCall).where(ModelCall.model == "session-delete-model"))).one()
        assert call.request_payload is None and call.latency_ms == 10.0
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "session.delete"))).one()
        assert entry.resource_id == str(fx.session_id) and entry.details["sessions"] == 1
        assert "Sam" not in json.dumps(entry.details)


async def test_session_delete_requires_admin_and_own_org(auth_client, org_factory, mock_ai) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    fx = await create_completed_session(org)
    url = f"{API}/sessions/{fx.session_id}"
    for role in (Role.RECRUITER, Role.INTERVIEWER):
        client = await auth_client(role, organization_id=org)
        assert (await client.delete(url)).status_code == 403
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.delete(url)).status_code == 404
    assert await count(InterviewSession, InterviewSession.id == fx.session_id) == 1


async def test_session_delete_refuses_a_live_session(auth_client, db_session, mock_ai, storage) -> None:
    admin = await auth_client(Role.ADMIN)
    fx = await create_completed_session(admin.user.organization_id, status="live")
    now = datetime.now(UTC)
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == fx.session_id)
        .values(status="live", last_heartbeat_at=now, updated_at=now)
    )
    await db_session.commit()
    busy = await admin.delete(f"{API}/sessions/{fx.session_id}")
    assert busy.status_code == 409, busy.text
    error = busy.json()["error"]
    assert error["code"] == "session_busy"
    assert error["details"]["blockers"] == [{"type": "session", "id": str(fx.session_id), "status": "live"}]
    assert await count(InterviewSession, InterviewSession.id == fx.session_id) == 1
    # A crashed runtime (nothing for 2 h) no longer blocks.
    stale = now - timedelta(hours=2)
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == fx.session_id)
        .values(last_heartbeat_at=stale, updated_at=stale)
    )
    await db_session.commit()
    assert (await admin.delete(f"{API}/sessions/{fx.session_id}")).status_code == 200


async def test_deleting_an_intake_session_removes_its_intake_rows(auth_client, storage) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    demo = await make_demo_rows(org)
    async with session_scope() as db:
        await db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == demo.session_id)
            .values(status="completed", updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
    response = await admin.delete(f"{API}/sessions/{demo.session_id}")
    assert response.status_code == 200, response.text
    for model, condition in session_tables([demo.session_id], None):
        assert await count(model, condition) == 0, model.__name__
    assert await count(IntakeInterview, IntakeInterview.id == demo.intake_id) == 1
