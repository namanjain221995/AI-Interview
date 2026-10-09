"""Candidates API: CRUD, search (name / e-mail / skills), aggregates, profile."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import CandidateProfile, CandidateResume
from app.domain.enums import Role
from tests.integration.resume.helpers import API, create_candidate

pytestmark = pytest.mark.integration


async def _profile(
    db_session, candidate_id: str, organization_id: uuid.UUID, skills: dict, version: int = 1
) -> None:
    resume = CandidateResume(
        candidate_id=uuid.UUID(candidate_id),
        organization_id=organization_id,
        storage_key=f"orgs/{organization_id}/resumes/{candidate_id}/{uuid.uuid4()}/cv.txt",
        original_filename="cv.txt",
        content_type="text/plain",
        size_bytes=10,
        sha256="0" * 64,
        status="ready",
    )
    db_session.add(resume)
    await db_session.flush()
    db_session.add(
        CandidateProfile(
            candidate_id=uuid.UUID(candidate_id),
            resume_id=resume.id,
            version=version,
            profile={"candidate_id": candidate_id, "headline": "Platform engineer", "skills": skills},
            experience_years=6.5,
        )
    )
    await db_session.commit()


async def test_candidate_crud_and_duplicates(auth_client, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    created = await create_candidate(
        client, email="Jane.Doe@Example.com", phone=" +1 555 0100 ", source="referral"
    )
    assert created["email"] == "jane.doe@example.com" and created["phone"] == "+1 555 0100"
    assert created["interviews_count"] == 0 and created["latest_resume_status"] is None
    duplicate = await client.post(
        f"{API}/candidates", json={"full_name": "Jane", "email": "JANE.DOE@example.com"}
    )
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "candidate_exists"
    other = await create_candidate(client, email="other@example.com")
    clash = await client.patch(f"{API}/candidates/{other['id']}", json={"email": "jane.doe@example.com"})
    assert clash.status_code == 409
    patched = await client.patch(
        f"{API}/candidates/{created['id']}", json={"location": "Austin, TX", "notes": "Strong referral"}
    )
    assert patched.status_code == 200 and patched.json()["location"] == "Austin, TX"
    invalid = await client.post(f"{API}/candidates", json={"full_name": "X", "email": "not-an-email"})
    assert invalid.status_code == 422
    fetched = (await client.get(f"{API}/candidates/{created['id']}")).json()
    assert fetched["notes"] == "Strong referral"
    missing = await client.get(f"{API}/candidates/{created['id']}/profile")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "profile_not_ready"


async def test_search_by_name_email_and_skill(auth_client, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    org = client.user.organization_id
    jane = await create_candidate(client, email="jane@example.com")
    marcus = (
        await client.post(
            f"{API}/candidates", json={"full_name": "Marcus Okafor", "email": "marcus@example.com"}
        )
    ).json()
    await _profile(db_session, jane["id"], org, {"devops": [{"name": "Kubernetes", "category": "devops"}]})
    await _profile(
        db_session, marcus["id"], org, {"framework": [{"name": "FastAPI", "category": "framework"}]}
    )

    async def search(**params) -> list[str]:
        body = (await client.get(f"{API}/candidates", params=params)).json()
        return sorted(item["full_name"] for item in body["items"])

    assert await search(q="okafor") == ["Marcus Okafor"]
    assert await search(q="jane@") == ["Jane Doe"]
    assert await search(q="kubernetes") == ["Jane Doe"]
    assert await search(skill="fastapi") == ["Marcus Okafor"]
    assert await search(q="100%_") == []  # LIKE wildcards are escaped
    assert await search(resume_status="ready") == ["Jane Doe", "Marcus Okafor"]
    listing = (await client.get(f"{API}/candidates", params={"q": "jane"})).json()
    item = listing["items"][0]
    assert item["latest_resume_status"] == "ready" and item["headline"] == "Platform engineer"
    assert item["experience_years"] == 6.5 and item["top_skills"] == ["Kubernetes"]
    profile = (await client.get(f"{API}/candidates/{jane['id']}/profile")).json()
    assert profile["version"] == 1 and profile["experience_years"] == 6.5
    page = (await client.get(f"{API}/candidates", params={"limit": 1, "offset": 1})).json()
    assert page["total"] == 2 and page["limit"] == 1 and page["offset"] == 1 and len(page["items"]) == 1
