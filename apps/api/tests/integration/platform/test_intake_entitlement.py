"""Intake is a per-organisation plan feature, managed by the platform (G8)."""

from __future__ import annotations

from app.domain.enums import Role

API = "/api/v1"


async def test_intake_is_hidden_until_the_platform_grants_it(auth_client, org_factory) -> None:
    org = await org_factory()  # a new tenant: no intake
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    me = (await recruiter.get(f"{API}/auth/me")).json()
    assert me["organization"]["features"] == {}
    listing = await recruiter.get(f"{API}/intake/interviews")
    assert listing.status_code == 404 and listing.json()["error"]["code"] == "intake_disabled"
    types = {t["round_type"] for t in (await recruiter.get(f"{API}/round-types")).json()["items"]}
    assert "intake" not in types and "technical_screening" in types

    job = await recruiter.post(f"{API}/jobs", json={"title": "Data analyst trainee", "status": "open"})
    assert job.status_code == 201, job.text
    intake_round = await recruiter.post(f"{API}/jobs/{job.json()['id']}/rounds", json={"round_type": "intake"})
    assert intake_round.status_code == 404 and intake_round.json()["error"]["code"] == "intake_disabled"

    platform = await auth_client(Role.SUPER_ADMIN)
    granted = await platform.patch(f"{API}/organizations/{org}", json={"features": {"intake": True}})
    assert granted.status_code == 200, granted.text
    assert granted.json()["features"] == {"intake": True}

    assert (await recruiter.get(f"{API}/auth/me")).json()["organization"]["features"] == {"intake": True}
    assert (await recruiter.get(f"{API}/intake/interviews")).status_code == 200
    types = {t["round_type"] for t in (await recruiter.get(f"{API}/round-types")).json()["items"]}
    assert "intake" in types


async def test_organisations_cannot_grant_themselves_features(auth_client, org_factory) -> None:
    org = await org_factory()
    admin = await auth_client(Role.ADMIN, organization_id=org)
    response = await admin.patch(f"{API}/organizations/{org}", json={"features": {"intake": True}})
    assert response.status_code == 403
