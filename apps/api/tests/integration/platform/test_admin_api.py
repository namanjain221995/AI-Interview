"""Users, organizations, settings and audit-log endpoints: RBAC, org isolation,
privilege escalation guards and auditing."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Organization, User
from app.domain.enums import Role

pytestmark = pytest.mark.integration

STRONG = "sapphire-Lantern-4410"


def _new_user(role: str = "RECRUITER", email: str = "new.user@example.test", **extra):
    return {"email": email, "full_name": "New User", "role": role, "password": STRONG, **extra}


# --------------------------------------------------------------------- users


@pytest.mark.parametrize("role", [Role.RECRUITER, Role.INTERVIEWER])
async def test_non_admins_cannot_manage_users(auth_client, role):
    staff = await auth_client(role)
    assert (await staff.get("/api/v1/users")).status_code == 403
    response = await staff.post("/api/v1/users", json=_new_user())
    assert response.status_code == 403
    assert response.json()["error"]["details"]["missing_permissions"] == ["users:manage"]


async def test_admin_crud_within_own_org(auth_client, db_session):
    admin = await auth_client(Role.ADMIN)
    created = await admin.post("/api/v1/users", json=_new_user(email="Mixed.Case@Example.TEST"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["email"] == "mixed.case@example.test"
    assert body["organization_id"] == str(admin.user.organization_id)
    assert "password_hash" not in body and "password" not in body and "hash" not in created.text
    listing = await admin.get("/api/v1/users", params={"q": "mixed", "limit": 10})
    assert listing.status_code == 200 and listing.json()["total"] == 1
    page = (await admin.get("/api/v1/users", params={"limit": 1, "offset": 0})).json()
    assert page["limit"] == 1 and page["total"] == 2 and len(page["items"]) == 1
    user_id = body["id"]
    assert (await admin.get(f"/api/v1/users/{user_id}")).json()["full_name"] == "New User"
    patched = await admin.patch(f"/api/v1/users/{user_id}", json={"full_name": "Renamed User", "role": "INTERVIEWER"})
    assert patched.status_code == 200 and patched.json()["role"] == "INTERVIEWER"
    deactivated = await admin.post(f"/api/v1/users/{user_id}/deactivate")
    assert deactivated.json()["is_active"] is False
    actions = (await db_session.scalars(select(AuditLog.action).where(AuditLog.resource_id == user_id))).all()
    assert {"user.create", "user.update", "user.deactivate"} <= set(actions)
    update_entry = await db_session.scalar(select(AuditLog).where(AuditLog.action == "user.update"))
    assert update_entry.details["changes"]["role"] == {"from": "RECRUITER", "to": "INTERVIEWER"}
    assert update_entry.actor_id == admin.user.id and update_entry.request_id


async def test_user_validation_errors(auth_client):
    admin = await auth_client(Role.ADMIN)
    weak = await admin.post("/api/v1/users", json={**_new_user(), "password": "password1234"})
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
    bad_email = await admin.post("/api/v1/users", json=_new_user(email="not-an-email"))
    assert bad_email.status_code == 422
    candidate_role = await admin.post("/api/v1/users", json=_new_user(role="CANDIDATE"))
    assert candidate_role.status_code == 403
    extra = await admin.post("/api/v1/users", json={**_new_user(), "is_superuser": True})
    assert extra.status_code == 422
    assert (await admin.post("/api/v1/users", json=_new_user())).status_code == 201
    duplicate = await admin.post("/api/v1/users", json=_new_user())
    assert duplicate.status_code == 409


async def test_admin_cannot_escalate(auth_client, user_factory):
    admin = await auth_client(Role.ADMIN)
    assert (await admin.post("/api/v1/users", json=_new_user(role="SUPER_ADMIN"))).status_code == 403
    peer = await user_factory(Role.RECRUITER, organization_id=admin.user.organization_id)
    assert (await admin.patch(f"/api/v1/users/{peer.id}", json={"role": "SUPER_ADMIN"})).status_code == 403
    root = await user_factory(Role.SUPER_ADMIN, organization_id=admin.user.organization_id)
    assert (await admin.patch(f"/api/v1/users/{root.id}", json={"full_name": "Hijack"})).status_code == 403
    assert (await admin.post(f"/api/v1/users/{root.id}/deactivate")).status_code == 403
    assert (await admin.patch(f"/api/v1/users/{admin.user.id}", json={"role": "RECRUITER"})).status_code == 403
    assert (await admin.post(f"/api/v1/users/{admin.user.id}/deactivate")).status_code == 403


async def test_last_admin_is_protected(auth_client, user_factory):
    root = await auth_client(Role.SUPER_ADMIN)
    org = await root.post("/api/v1/organizations", json={"name": "Solo Inc", "slug": "solo-inc"})
    org_id = org.json()["id"]
    only_admin = await root.post("/api/v1/users", json=_new_user(role="ADMIN", email="solo@example.test", organization_id=org_id))
    admin_id = only_admin.json()["id"]
    demote = await root.patch(f"/api/v1/users/{admin_id}", json={"role": "RECRUITER"})
    assert demote.status_code == 409
    assert (await root.post(f"/api/v1/users/{admin_id}/deactivate")).status_code == 409
    await root.post("/api/v1/users", json=_new_user(role="ADMIN", email="second@example.test", organization_id=org_id))
    assert (await root.patch(f"/api/v1/users/{admin_id}", json={"role": "RECRUITER"})).status_code == 200


async def test_concurrent_demotions_keep_one_admin(auth_client, user_factory, org_factory):
    org_id = await org_factory()
    first = await user_factory(Role.ADMIN, organization_id=org_id)
    second = await user_factory(Role.ADMIN, organization_id=org_id)
    root = await auth_client(Role.SUPER_ADMIN)
    results = await asyncio.gather(
        root.patch(f"/api/v1/users/{first.id}", json={"role": "RECRUITER"}),
        root.patch(f"/api/v1/users/{second.id}", json={"role": "RECRUITER"}),
    )
    assert sorted(r.status_code for r in results) == [200, 409]


async def test_org_isolation_for_admins(auth_client, user_factory, org_factory):
    admin = await auth_client(Role.ADMIN)
    other_org = await org_factory()
    stranger = await user_factory(Role.RECRUITER, organization_id=other_org)
    listing = (await admin.get("/api/v1/users")).json()
    assert str(stranger.id) not in {u["id"] for u in listing["items"]}
    assert (await admin.get(f"/api/v1/users/{stranger.id}")).status_code == 404
    assert (await admin.patch(f"/api/v1/users/{stranger.id}", json={"full_name": "x"})).status_code == 404
    assert (await admin.get("/api/v1/users", params={"organization_id": str(other_org)})).status_code == 403
    cross = await admin.post("/api/v1/users", json=_new_user(organization_id=str(other_org)))
    assert cross.status_code == 403


async def test_super_admin_spans_organizations(auth_client, user_factory, org_factory):
    root = await auth_client(Role.SUPER_ADMIN)
    other_org = await org_factory()
    stranger = await user_factory(Role.RECRUITER, organization_id=other_org)
    filtered = (await root.get("/api/v1/users", params={"organization_id": str(other_org)})).json()
    assert [u["id"] for u in filtered["items"]] == [str(stranger.id)]
    everyone = (await root.get("/api/v1/users", params={"limit": 200})).json()
    assert everyone["total"] >= 2
    made = await root.post("/api/v1/users", json=_new_user(role="ADMIN", organization_id=str(other_org)))
    assert made.status_code == 201 and made.json()["organization_id"] == str(other_org)
    assert (await root.get(f"/api/v1/users/{stranger.id}")).status_code == 200


# ------------------------------------------------------------- organizations


async def test_only_super_admin_manages_organizations(auth_client):
    admin = await auth_client(Role.ADMIN)
    assert (await admin.get("/api/v1/organizations")).status_code == 403
    assert (await admin.post("/api/v1/organizations", json={"name": "X", "slug": "x"})).status_code == 403


async def test_organization_crud(auth_client, db_session):
    root = await auth_client(Role.SUPER_ADMIN)
    created = await root.post("/api/v1/organizations", json={"name": "Acme Corp", "slug": "acme"})
    assert created.status_code == 201, created.text
    org_id = created.json()["id"]
    org = await db_session.get(Organization, org_id)
    assert org.settings["candidate_report_mode"] == "after_review"
    assert (await root.post("/api/v1/organizations", json={"name": "Dup", "slug": "acme"})).status_code == 409
    assert (await root.post("/api/v1/organizations", json={"name": "Bad", "slug": "Bad Slug"})).status_code == 422
    listing = (await root.get("/api/v1/organizations", params={"q": "acm"})).json()
    assert [o["slug"] for o in listing["items"]] == ["acme"]
    renamed = await root.patch(f"/api/v1/organizations/{org_id}", json={"name": "Acme Corporation", "slug": "acme-corp"})
    assert renamed.status_code == 200 and renamed.json()["slug"] == "acme-corp"
    assert (await root.get(f"/api/v1/organizations/{org_id}")).json()["name"] == "Acme Corporation"
    assert (await root.get("/api/v1/organizations/00000000-0000-0000-0000-000000000000")).status_code == 404
    actions = (await db_session.scalars(select(AuditLog.action).where(AuditLog.organization_id == org.id))).all()
    assert {"organization.create", "organization.update"} <= set(actions)


# ------------------------------------------------------------------ settings


async def test_settings_read_and_update(auth_client, db_session):
    recruiter = await auth_client(Role.RECRUITER)
    current = await recruiter.get("/api/v1/settings")
    assert current.status_code == 200
    body = current.json()
    assert body["candidate_report_mode"] == "after_review" and body["integrity"]["camera_required"] is True
    body["integrity"]["camera_required"] = False
    body["candidate_report_mode"] = "immediate"
    assert (await recruiter.put("/api/v1/settings", json=body)).status_code == 403

    admin = await auth_client(Role.ADMIN, organization_id=recruiter.user.organization_id)
    updated = await admin.put("/api/v1/settings", json=body)
    assert updated.status_code == 200, updated.text
    assert (await recruiter.get("/api/v1/settings")).json()["integrity"]["camera_required"] is False
    entry = await db_session.scalar(select(AuditLog).where(AuditLog.action == "settings.update"))
    assert entry.details["changed_sections"] == ["candidate_report_mode", "integrity"]

    invalid = dict(body, unknown_section={"x": 1})
    assert (await admin.put("/api/v1/settings", json=invalid)).status_code == 422
    out_of_range = dict(body)
    out_of_range["integrity"] = dict(body["integrity"], camera_frame_interval_seconds=1)
    assert (await admin.put("/api/v1/settings", json=out_of_range)).status_code == 422


async def test_settings_org_scoping(auth_client, org_factory):
    admin = await auth_client(Role.ADMIN)
    other = await org_factory()
    assert (await admin.get("/api/v1/settings", params={"organization_id": str(other)})).status_code == 403
    root = await auth_client(Role.SUPER_ADMIN)
    assert (await root.get("/api/v1/settings", params={"organization_id": str(other)})).status_code == 200


# ---------------------------------------------------------------- audit logs


async def test_audit_log_listing_and_filters(auth_client, org_factory, db_session):
    admin = await auth_client(Role.ADMIN)
    await admin.post("/api/v1/users", json=_new_user(email="audited@example.test"))
    logs = await admin.get("/api/v1/audit-logs", params={"action": "user.*"})
    assert logs.status_code == 200
    items = logs.json()["items"]
    assert items and all(i["action"].startswith("user.") for i in items)
    assert all(i["organization_id"] == str(admin.user.organization_id) for i in items)
    login_logs = (await admin.get("/api/v1/audit-logs", params={"action": "auth.login"})).json()
    assert login_logs["total"] >= 1 and login_logs["items"][0]["ip_address"] == "127.0.0.1"
    by_actor = (await admin.get("/api/v1/audit-logs", params={"actor_id": str(admin.user.id)})).json()
    assert by_actor["total"] >= 2
    recruiter = await auth_client(Role.RECRUITER, organization_id=admin.user.organization_id)
    assert (await recruiter.get("/api/v1/audit-logs")).status_code == 403
    other = await org_factory()
    assert (await admin.get("/api/v1/audit-logs", params={"organization_id": str(other)})).status_code == 403
    newest_first = [i["created_at"] for i in (await admin.get("/api/v1/audit-logs")).json()["items"]]
    assert newest_first == sorted(newest_first, reverse=True)
    stored = await db_session.scalar(select(User).where(User.email == "audited@example.test"))
    assert stored is not None
