"""DELETE /open-links/{id}: admin-only, org-scoped; refused once a registration became a candidate."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Candidate, InterviewLink, LinkRegistration
from app.db.session import session_scope
from app.domain.enums import Role
from tests.integration.deletion.world import count
from tests.integration.open_links.helpers import API, create_link, register, token_of

pytestmark = pytest.mark.integration


async def test_admin_deletes_an_unused_link(link_env, auth_client, org_factory) -> None:
    link = (await create_link(link_env))["link"]
    link_id = uuid.UUID(link["id"])
    url = f"{API}/open-links/{link_id}"
    assert (await link_env.recruiter.delete(url)).status_code == 403
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.delete(url)).status_code == 404
    async with session_scope() as db:  # an abandoned (blocked) attempt: no candidate behind it
        db.add(
            LinkRegistration(
                organization_id=link_env.organization_id,
                link_id=link_id,
                job_id=link_env.job_id,
                status="blocked",
                full_name="Blocked Person",
                email="blocked@example.com",
                email_key="blocked@example.com",
                answers={},
            )
        )
    admin = await auth_client(Role.ADMIN, organization_id=link_env.organization_id)
    response = await admin.delete(url)
    assert response.status_code == 200, response.text
    assert response.json()["registrations_deleted"] == 1
    assert response.json()["previous_status"] == "active"
    assert (await admin.get(url)).status_code == 404
    assert await count(LinkRegistration, LinkRegistration.link_id == link_id) == 0
    async with session_scope() as db:
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "link.delete"))).one()
        assert entry.resource_id == str(link_id)
        assert entry.details == {"previous_status": "active", "registrations_deleted": 1}


async def test_link_with_a_registered_candidate_is_refused(link_env, auth_client, public_client) -> None:
    link = (await create_link(link_env))["link"]
    registered = await register(public_client(), token_of(link))
    assert registered.status_code in (200, 201), registered.text
    admin = await auth_client(Role.ADMIN, organization_id=link_env.organization_id)
    response = await admin.delete(f"{API}/open-links/{link['id']}")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "link_has_registrations"
    assert response.json()["error"]["details"]["registrations"] == 1
    assert await count(InterviewLink, InterviewLink.id == uuid.UUID(link["id"])) == 1
    assert await count(Candidate, Candidate.organization_id == link_env.organization_id) == 1
