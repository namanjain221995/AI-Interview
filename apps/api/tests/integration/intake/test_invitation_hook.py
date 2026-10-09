"""The generic invitation hook (INTAKE_INTERVIEW.md 10.1 "Invitation hook"): a job invite or an open-link round of
type intake gets its ``intake_interviews`` row; non-intake invites, the intake flows themselves
(``intake_managed=True``) and a disabled flag never create one, and a failing hook never breaks an invitation."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Candidate, IntakeInterview, Interview
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.intake.invitations import ensure_intake_for_interview
from tests.integration.intake.admin_helpers import API, intake_round_config, make_intake_job
from tests.integration.open_links.helpers import answers, register, token_of

pytestmark = pytest.mark.integration


async def _candidate(org_id: uuid.UUID, email: str = "hook@example.test") -> uuid.UUID:
    async with session_scope() as db:
        row = Candidate(organization_id=org_id, full_name="Hook Candidate", email=email)
        db.add(row)
        await db.flush()
        return row.id


async def _rows_for(interview_id: str | uuid.UUID) -> list[IntakeInterview]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(
                    select(IntakeInterview).where(
                        IntakeInterview.interview_id == uuid.UUID(str(interview_id))
                    )
                )
            ).all()
        )


async def test_generic_job_invite_creates_the_intake_row(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, round_id = await make_intake_job(org_id, round_config=intake_round_config(duration_minutes=40))
    candidate_id = await _candidate(org_id)
    response = await recruiter.post(
        f"{API}/interviews",
        json={"candidate_id": str(candidate_id), "job_id": str(job_id), "expires_in_hours": 72},
    )
    assert response.status_code == 201, response.text
    interview = response.json()["interview"]
    [row] = await _rows_for(interview["id"])
    assert row.status == "active" and row.round_id == round_id and row.candidate_id == candidate_id
    assert (
        row.created_by_id == recruiter.user.id and row.sent_at is not None and row.title == "Intake Interview"
    )
    assert row.settings["duration_minutes"] == 40  # from the intake round
    assert row.settings["max_reconnects"] == interview["policy_snapshot"]["integrity"]["max_reconnects"]
    assert row.settings["expires_in_hours"] == 72 and row.settings["kit_version"] == "v1"
    # The intake listing shows it like any other intake.
    listing = (await recruiter.get(f"{API}/intake/interviews")).json()
    assert [item["id"] for item in listing["items"]] == [str(row.id)]
    # Idempotent: a second ensure returns the same row.
    async with session_scope() as db:
        again = await ensure_intake_for_interview(db, await db.get(Interview, row.interview_id))
        assert again is not None and again.id == row.id


async def test_non_intake_invites_and_disabled_flag_create_nothing(auth_client, settings) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    technical_job, _ = await make_intake_job(org_id, with_intake_round=False, other_round=True)
    first = await recruiter.post(
        f"{API}/interviews",
        json={"candidate_id": str(await _candidate(org_id)), "job_id": str(technical_job)},
    )
    assert first.status_code == 201 and await _rows_for(first.json()["interview"]["id"]) == []
    settings.intake_enabled = False
    intake_job, _ = await make_intake_job(org_id)
    second = await recruiter.post(
        f"{API}/interviews",
        json={"candidate_id": str(await _candidate(org_id, "off@example.test")), "job_id": str(intake_job)},
    )
    assert second.status_code == 201 and await _rows_for(second.json()["interview"]["id"]) == []


async def test_failing_hook_never_breaks_the_invitation(auth_client, monkeypatch) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, _ = await make_intake_job(org_id)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.intake.invitations.ensure_intake_for_interview", broken)
    response = await recruiter.post(
        f"{API}/interviews", json={"candidate_id": str(await _candidate(org_id)), "job_id": str(job_id)}
    )
    assert response.status_code == 201, response.text
    assert await _rows_for(response.json()["interview"]["id"]) == []
    async with session_scope() as db:
        assert await db.scalar(select(func.count()).select_from(Interview)) == 1


async def test_open_link_intake_round_creates_the_row(auth_client, app) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, round_id = await make_intake_job(org_id)
    link = await recruiter.post(
        f"{API}/open-links",
        json={"job_id": str(job_id), "name": "Intake drive", "round_ids": [str(round_id)]},
    )
    assert link.status_code == 201, link.text
    transport = httpx.ASGITransport(app=app, client=("10.0.0.9", 50000))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"user-agent": "pytest-intake-browser"}
    ) as browser:
        registered = await register(browser, token_of(link.json()["link"]), answers(7))
    assert registered.status_code in (200, 201), registered.text
    async with session_scope() as db:
        interview = await db.scalar(select(Interview).where(Interview.job_id == job_id))
        assert interview is not None and interview.round_id == round_id
    [row] = await _rows_for(interview.id)
    assert row.status == "active" and row.settings["require_access_code"] is False
