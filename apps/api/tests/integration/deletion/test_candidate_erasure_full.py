"""Candidate erasure removes every record and file of the candidate (intake, open links,
recordings, frames, reports, coding, portal sessions ...), one-step archive + delete, preview."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, InterviewLink, InterviewSession, MediaAsset, ModelCall
from app.db.session import session_scope
from app.domain.enums import Role
from tests.integration.deletion.world import (
    API,
    add_session_records,
    build_world,
    candidate_tables,
    count,
    stored_files,
)
from tests.integration.reports.factories import create_completed_session

pytestmark = pytest.mark.integration


async def test_erasure_removes_every_record_and_file_in_one_step(
    auth_client, mock_ai, redis, storage
) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    world = await build_world(org=org, admin_user_id=admin.user.id, storage=storage, recruiter=recruiter)
    other = await create_completed_session(org, candidate_name="Kept Person")
    other_keys = await add_session_records(org, other, reviewer_id=admin.user.id, storage=storage)
    for key in [*world.file_keys, *other_keys]:
        assert await storage.exists(key), key
    for model, condition in candidate_tables(world):
        assert await count(model, condition) > 0, model.__name__  # the world really has every kind of row
    await redis.set(f"session:{world.session.session_id}:state", "{}")

    url = f"{API}/candidates/{world.candidate_id}"
    assert (await recruiter.get(f"{url}/deletion-preview")).status_code == 403
    assert (await recruiter.delete(url, params={"archive_first": "true"})).status_code == 403
    preview = await admin.get(f"{url}/deletion-preview")
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert (p["archived"], p["interviews"], p["sessions"], p["intake_interviews"]) == (False, 2, 2, 1)
    assert p["resumes"] == 1 and p["link_registrations"] == 1 and p["reports"] >= 2
    assert p["stored_files"] == len(world.file_keys) and p["stored_bytes"] > 0 and p["blockers"] == []

    not_archived = await admin.delete(url)
    assert not_archived.status_code == 409
    assert not_archived.json()["error"]["code"] == "candidate_not_archived"

    response = await admin.delete(url, params={"archive_first": "true", "request_reference": "DSR-7"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["archived_in_request"] is True
    assert (body["interviews"], body["sessions"], body["intake_interviews"], body["resumes"]) == (2, 2, 1, 1)
    assert body["media_files_deleted"] == len(world.file_keys)
    assert body["link_registrations"] == 1 and body["link_blocked_registrations_deleted"] == 1

    for model, condition in candidate_tables(world):
        assert await count(model, condition) == 0, model.__name__
    for key in world.file_keys:
        assert not await storage.exists(key), key
    remaining = stored_files(storage.root)
    assert remaining == set(other_keys), remaining - set(other_keys)
    assert not any(str(world.candidate_id) in k or str(world.intake_session_id) in k for k in remaining)
    # Everyone else is untouched; the link itself stays (only the registration went).
    assert await count(InterviewSession, InterviewSession.id == other.session_id) == 1
    assert await count(InterviewLink, InterviewLink.id == world.link_id) == 1
    assert await redis.exists(f"session:{world.session.session_id}:state") == 0

    async with session_scope() as db:
        for call in (await db.scalars(select(ModelCall).where(ModelCall.id.in_(world.model_call_ids)))).all():
            assert call.request_payload is None and call.response_payload is None, call.model
            assert call.latency_ms is not None
        media = (
            await db.scalars(select(MediaAsset).where(MediaAsset.candidate_id == world.candidate_id))
        ).all()
        assert media and all(m.storage_key == f"erased/{m.id}" and m.session_id is None for m in media)
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "candidate.delete"))).one()
        assert entry.details["archived_in_request"] is True and entry.details["intake_interviews"] == 1
        trail = json.dumps([e.details for e in (await db.scalars(select(AuditLog))).all()])
        assert "Erin" not in trail and world.email not in trail


async def test_preview_is_org_scoped(auth_client, org_factory) -> None:
    admin = await auth_client(Role.ADMIN)
    created = await admin.post(
        f"{API}/candidates", json={"full_name": "Pat Preview", "email": "pat@example.com"}
    )
    assert created.status_code == 201, created.text
    url = f"{API}/candidates/{created.json()['id']}"
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.get(f"{url}/deletion-preview")).status_code == 404
    assert (await outsider.delete(url, params={"archive_first": "true"})).status_code == 404
    preview = (await admin.get(f"{url}/deletion-preview")).json()
    assert preview["sessions"] == 0 and preview["archived"] is False
    deleted = await admin.delete(url, params={"archive_first": "true"})
    assert deleted.status_code == 200, deleted.text
    assert (await admin.get(url)).status_code == 404
