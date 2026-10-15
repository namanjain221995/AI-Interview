"""A round type this build does not know (written by a newer release, e.g. before a rollback) must never
turn a listing into a 500: readers answer 409 ``unsupported_round_type`` (plan package G14)."""

from __future__ import annotations

import pytest

from app.db.models import InterviewTemplate
from app.domain.enums import Role
from tests.integration.resume.helpers import API, create_job

pytestmark = pytest.mark.integration


async def test_unknown_stored_round_type_is_a_409_not_a_500(
    auth_client, mock_ai, db_session, seeded_catalog
) -> None:
    client = await auth_client(Role.RECRUITER)
    template = InterviewTemplate(
        organization_id=client.user.organization_id,
        name="Hiring call (from a newer release)",
        round_type="hiring_call_v9",
        config={"round_type": "hiring_call_v9", "name": "Hiring call", "duration_minutes": 7},
    )
    db_session.add(template)
    await db_session.commit()

    listing = await client.get(f"{API}/templates")
    assert listing.status_code == 200, listing.text  # TemplateOut.round_type is a plain string
    assert any(
        t["id"] == str(template.id) and t["round_type"] == "hiring_call_v9" for t in listing.json()["items"]
    )

    job = await create_job(client)
    created = await client.post(f"{API}/jobs/{job['id']}/rounds", json={"template_id": str(template.id)})
    assert created.status_code == 409, created.text
    body = created.json()
    assert (
        body["error"]["code"] == "unsupported_round_type"
        if "error" in body
        else body["code"] == "unsupported_round_type"
    )

    renamed = await client.patch(f"{API}/templates/{template.id}", json={"name": "Renamed"})
    assert renamed.status_code == 409, renamed.text  # the stored type is re-read on update

    known = await client.post(f"{API}/jobs/{job['id']}/rounds", json={"round_type": "behavioral"})
    assert known.status_code == 201, known.text  # everything else keeps working
