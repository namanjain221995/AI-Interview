"""Interview invitations: create (policy snapshot, token, access code), list,
regenerate-link, cancel, validation, RBAC and audit."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.security import hash_token, verify_access_code_async
from app.db.models import AuditLog, AuthSession, Interview, Organization
from app.db.session import session_scope
from app.domain.enums import Role
from app.schemas.policies import OrganizationSettings
from app.services.identity.sessions import start_candidate_session
from app.services.jobs.policy import build_policy_snapshot
from tests.integration.resume.helpers import API, create_candidate, create_job

pytestmark = pytest.mark.integration


async def _job_with_rounds(client, *, selectable: bool = False) -> dict:
    job = await create_job(client, integrity_settings={"screen_share_required": True})
    for round_type in ("technical_screening", "behavioral"):
        response = await client.post(
            f"{API}/jobs/{job['id']}/rounds",
            json={"round_type": round_type, "candidate_selectable": selectable},
        )
        assert response.status_code == 201, response.text
    return (await client.get(f"{API}/jobs/{job['id']}")).json()


async def test_invite_flow(auth_client, mock_ai, seeded_catalog, settings, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await _job_with_rounds(client)
    candidate = await create_candidate(client)
    response = await client.post(
        f"{API}/interviews",
        json={
            "candidate_id": candidate["id"],
            "job_id": job["id"],
            "expires_in_hours": 48,
            "require_access_code": True,
            "allow_round_selection": False,
        },
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    prefix = f"{settings.public_base_url}/i/"
    assert body["invite_url"].startswith(prefix)
    token = body["invite_url"][len(prefix) :]
    assert len(token) >= 43
    code = body["access_code"]
    assert code and len(code) >= 6
    interview = body["interview"]
    assert interview["status"] == "invited" and interview["requires_access_code"] is True
    assert interview["round_id"] == job["rounds"][0]["id"]  # first active round
    assert interview["candidate"]["full_name"] == "Jane Doe" and interview["job"]["title"] == job["title"]
    assert interview["round"]["round_type"] == "technical_screening"
    expires = datetime.fromisoformat(body["expires_at"])
    assert abs((expires - datetime.now(UTC)) - timedelta(hours=48)) < timedelta(minutes=1)

    row = await db_session.get(Interview, uuid.UUID(interview["id"]))
    assert row.invite_token_hash == hash_token(token)
    assert row.access_code_hash and await verify_access_code_async(code, row.access_code_hash)
    org = await db_session.get(Organization, row.organization_id)
    from app.db.models import Job

    expected = build_policy_snapshot(
        OrganizationSettings.model_validate(org.settings), await db_session.get(Job, row.job_id)
    )
    assert row.policy_snapshot == expected.model_dump(mode="json")
    assert row.policy_snapshot["integrity"]["screen_share_required"] is True

    listing = (await client.get(f"{API}/interviews")).json()
    detail = (await client.get(f"{API}/interviews/{interview['id']}")).json()
    for payload in (listing, detail):
        text = json.dumps(payload)
        assert token not in text and code not in text and row.invite_token_hash not in text
        assert "invite_url" not in text and "access_code_hash" not in text
    assert listing["total"] == 1 and detail["requires_access_code"] is True

    audit_rows = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "interview.create"))
    ).all()
    assert len(audit_rows) == 1
    assert token not in json.dumps(audit_rows[0].details) and code not in json.dumps(audit_rows[0].details)

    duplicate = await client.post(
        f"{API}/interviews", json={"candidate_id": candidate["id"], "job_id": job["id"]}
    )
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "interview_exists"

    per_candidate = (await client.get(f"{API}/candidates/{candidate['id']}/interviews")).json()
    assert per_candidate["total"] == 1 and per_candidate["items"][0]["id"] == interview["id"]
    filtered = (
        await client.get(f"{API}/candidates", params={"job_id": job["id"], "status": "invited"})
    ).json()
    assert [c["id"] for c in filtered["items"]] == [candidate["id"]]
    assert (
        filtered["items"][0]["interviews_count"] == 1
        and filtered["items"][0]["latest_interview_status"] == "invited"
    )
    jobs = (await client.get(f"{API}/jobs")).json()
    assert jobs["items"][0]["candidates_count"] == 1 and jobs["items"][0]["rounds_count"] == 2

    in_use = await client.delete(f"{API}/rounds/{job['rounds'][0]['id']}")
    assert in_use.status_code == 409 and in_use.json()["error"]["code"] == "round_in_use"


async def test_regenerate_and_cancel_revoke_candidate_sessions(
    auth_client, mock_ai, seeded_catalog, db_session
) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await _job_with_rounds(client)
    candidate = await create_candidate(client)
    created = (
        await client.post(
            f"{API}/interviews",
            json={"candidate_id": candidate["id"], "job_id": job["id"], "require_access_code": True},
        )
    ).json()
    interview_id = uuid.UUID(created["interview"]["id"])
    async with session_scope() as db:
        tokens = await start_candidate_session(
            db,
            candidate_id=uuid.UUID(candidate["id"]),
            interview_id=interview_id,
            organization_id=client.user.organization_id,
        )
    await db_session.execute(
        update(Interview)
        .where(Interview.id == interview_id)
        .values(
            status="expired",
            invite_expires_at=datetime.now(UTC) - timedelta(hours=1),
            failed_verification_count=4,
        )
    )
    await db_session.commit()

    rotated = await client.post(
        f"{API}/interviews/{interview_id}/regenerate-link", json={"expires_in_hours": 24}
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.headers["cache-control"] == "no-store"
    body = rotated.json()
    assert body["invite_url"] != created["invite_url"]
    assert body["access_code"] and body["access_code"] != created["access_code"]
    assert body["interview"]["status"] == "invited" and body["interview"]["is_expired"] is False
    db_session.expire_all()
    row = await db_session.get(Interview, interview_id)
    assert row.invite_token_hash == hash_token(body["invite_url"].rsplit("/", 1)[1])
    assert row.failed_verification_count == 0
    session_row = await db_session.get(AuthSession, tokens.auth_session_id)
    assert session_row.revoked_at is not None and session_row.revoked_reason == "invite_link_rotated"

    no_code = await client.post(
        f"{API}/interviews/{interview_id}/regenerate-link", json={"rotate_access_code": False}
    )
    assert no_code.json()["access_code"] is None

    cancelled = await client.post(
        f"{API}/interviews/{interview_id}/cancel", json={"reason": "Position filled"}
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert "Position filled" in cancelled.json()["notes"]
    again = await client.post(f"{API}/interviews/{interview_id}/cancel")
    assert again.status_code == 200
    assert (await client.post(f"{API}/interviews/{interview_id}/regenerate-link")).status_code == 409
    actions = (
        await db_session.scalars(select(AuditLog.action).where(AuditLog.resource_id == str(interview_id)))
    ).all()
    assert actions.count("interview.cancel") == 1 and actions.count("interview.regenerate_link") == 2


async def test_invite_validation(auth_client, org_factory, mock_ai, seeded_catalog, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await _job_with_rounds(client)
    candidate = await create_candidate(client)
    base = {"candidate_id": candidate["id"], "job_id": job["id"]}

    other_job = await _job_with_rounds(client)
    foreign_round = await client.post(
        f"{API}/interviews", json={**base, "round_id": other_job["rounds"][0]["id"]}
    )
    assert foreign_round.status_code == 422
    no_selectable = await client.post(f"{API}/interviews", json={**base, "allow_round_selection": True})
    assert (
        no_selectable.status_code == 422 and no_selectable.json()["error"]["code"] == "no_selectable_rounds"
    )
    empty_job = await create_job(client, title="No rounds yet", requirements=[])
    no_rounds = await client.post(f"{API}/interviews", json={**base, "job_id": empty_job["id"]})
    assert no_rounds.status_code == 422 and no_rounds.json()["error"]["code"] == "no_rounds"
    await client.patch(f"{API}/jobs/{other_job['id']}", json={"status": "closed"})
    closed = await client.post(f"{API}/interviews", json={**base, "job_id": other_job["id"]})
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "job_not_open"
    unknown = await client.post(f"{API}/interviews", json={**base, "candidate_id": str(uuid.uuid4())})
    assert unknown.status_code == 404
    too_long = await client.post(f"{API}/interviews", json={**base, "expires_in_hours": 10_000})
    assert too_long.status_code == 422

    org = await db_session.get(Organization, client.user.organization_id)
    org.settings = {**org.settings, "require_access_code": True}
    await db_session.commit()
    selectable_job = await _job_with_rounds(client, selectable=True)
    forced = await client.post(
        f"{API}/interviews",
        json={"candidate_id": candidate["id"], "job_id": selectable_job["id"], "allow_round_selection": True},
    )
    assert forced.status_code == 201, forced.text
    assert forced.json()["access_code"] and forced.json()["interview"]["round_id"] is None
    assert forced.json()["interview"]["allow_round_selection"] is True


async def test_invite_rbac_and_expiry_filter(
    auth_client, org_factory, mock_ai, seeded_catalog, db_session
) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await _job_with_rounds(client)
    candidate = await create_candidate(client)
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=client.user.organization_id)
    payload = {"candidate_id": candidate["id"], "job_id": job["id"]}
    assert (await interviewer.post(f"{API}/interviews", json=payload)).status_code == 403
    created = (await client.post(f"{API}/interviews", json=payload)).json()["interview"]
    assert (await interviewer.get(f"{API}/interviews/{created['id']}")).status_code == 200
    assert (await interviewer.post(f"{API}/interviews/{created['id']}/cancel")).status_code == 403
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.get(f"{API}/interviews/{created['id']}")).status_code == 404
    assert (await outsider.post(f"{API}/interviews/{created['id']}/regenerate-link")).status_code == 404
    assert (await outsider.post(f"{API}/interviews", json=payload)).status_code == 404

    await db_session.execute(
        update(Interview)
        .where(Interview.id == uuid.UUID(created["id"]))
        .values(invite_expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()
    expired = (await client.get(f"{API}/interviews", params={"status": "expired"})).json()
    assert [i["id"] for i in expired["items"]] == [created["id"]] and expired["items"][0][
        "is_expired"
    ] is True
    assert (await client.get(f"{API}/interviews", params={"status": "invited"})).json()["total"] == 0
    assert (await client.get(f"{API}/interviews", params={"job_id": job["id"]})).json()["total"] == 1
    # An expired invitation no longer blocks a new one.
    assert (await client.post(f"{API}/interviews", json=payload)).status_code == 201
