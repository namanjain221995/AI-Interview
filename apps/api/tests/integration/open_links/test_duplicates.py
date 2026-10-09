"""One attempt per person: exact identity (e-mail/phone), resume on the same
device, job vs link scope, recruiter invitations, soft signals (device,
network, resume), device block policy and recruiter unblock."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, Candidate, Interview, LinkRegistration
from app.db.session import session_scope
from tests.integration.open_links.helpers import API, answers, complete, create_link, register, token_of

pytestmark = pytest.mark.integration

NO_NETWORK = {"scope": "job", "device": "flag", "network": "off"}


async def _registrations() -> list[LinkRegistration]:
    async with session_scope() as db:
        return list((await db.scalars(select(LinkRegistration).order_by(LinkRegistration.created_at))).all())


async def test_same_email_or_phone_never_gets_a_second_attempt(link_env, public_client, db_session) -> None:
    link = (await create_link(link_env, duplicate_policy=NO_NETWORK))["link"]
    token = token_of(link)
    first = await register(
        public_client(), token, answers(1, email="Jane.Doe@gmail.com", phone="+91 98765 43210")
    )
    assert first.status_code == 201
    cases = [
        answers(2, email="jane.doe@gmail.com", phone="+91 90000 00000"),  # same e-mail
        answers(3, email="janedoe+again@gmail.com", phone="+91 90000 00001"),  # gmail dots/+tag
        answers(4, email="someone.else@example.com", phone="098765-43210"),  # same phone, other e-mail
    ]
    for body in cases:
        response = await register(public_client(), token, body)
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "already_registered"
        assert error["details"] == {"state": "registered", "resume_with": "personal_link"}
        assert "jane" not in json.dumps(error).lower()  # no personal data in the refusal
    assert len(await _registrations()) == 1
    assert int(await db_session.scalar(select(func.count()).select_from(Interview))) == 1
    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "link.registration_duplicate"))
    ).all()
    assert sorted(a.details["match"] for a in audit) == ["email", "email", "phone"]


async def test_same_device_resumes_instead_of_registering_again(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    token = token_of(link)
    browser = public_client()
    first = await register(browser, token, answers(1))
    again = await register(browser, token, answers(1))
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["outcome"] == "resumed" and body["personal_url"] is None
    assert body["interview_id"] == first.json()["interview_id"] and body["next_step"] == "welcome"
    assert len(await _registrations()) == 1
    other_device = await register(public_client(), token, answers(1))
    assert other_device.status_code == 409


async def test_completed_person_cannot_retake(link_env, public_client) -> None:
    link = (await create_link(link_env, round_ids=[str(link_env.round_ids[0])]))["link"]
    token = token_of(link)
    browser = public_client()
    first = await register(browser, token, answers(1))
    await complete(link_env.recruiter, first.json()["interview_id"])
    for client in (browser, public_client()):
        response = await register(client, token, answers(1))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_completed"


async def test_job_scope_spans_links_and_link_scope_does_not(link_env, public_client) -> None:
    job_link = (await create_link(link_env, round_ids=[str(link_env.round_ids[0])]))["link"]
    first = await register(public_client(), token_of(job_link), answers(1))
    await complete(link_env.recruiter, first.json()["interview_id"])
    second_link = (await create_link(link_env, name="Second drive"))["link"]
    refused = await register(public_client(), token_of(second_link), answers(1))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "already_completed"
    repeat_drive = (await create_link(link_env, name="Repeat drive", duplicate_policy={"scope": "link"}))[
        "link"
    ]
    allowed = await register(public_client(), token_of(repeat_drive), answers(1))
    assert allowed.status_code == 201, allowed.text
    async with session_scope() as db:
        assert (
            int(await db.scalar(select(func.count()).select_from(Candidate))) == 1
        )  # same person, same record


async def test_recruiter_invitation_blocks_open_link(link_env, public_client) -> None:
    recruiter = link_env.recruiter
    candidate = (
        await recruiter.post(
            f"{API}/candidates", json={"full_name": "Invited Person", "email": "invited@example.com"}
        )
    ).json()
    invite = await recruiter.post(
        f"{API}/interviews", json={"candidate_id": candidate["id"], "job_id": str(link_env.job_id)}
    )
    assert invite.status_code == 201, invite.text
    link = (await create_link(link_env))["link"]
    response = await register(public_client(), token_of(link), answers(9, email="invited@example.com"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_invited"
    link_scope = (await create_link(link_env, name="Link scope", duplicate_policy={"scope": "link"}))["link"]
    response = await register(public_client(), token_of(link_scope), answers(9, email="invited@example.com"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_invited"
    assert await _registrations() == []


async def test_archived_candidate_gets_a_neutral_refusal(link_env, public_client) -> None:
    from datetime import UTC, datetime

    async with session_scope() as db:
        db.add(
            Candidate(
                organization_id=link_env.organization_id,
                full_name="Archived",
                email="archived@example.com",
                archived_at=datetime.now(UTC),
            )
        )
    link = (await create_link(link_env))["link"]
    response = await register(public_client(), token_of(link), answers(8, email="archived@example.com"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "registration_unavailable"


async def test_same_device_other_person_is_flagged(link_env, public_client) -> None:
    link = (await create_link(link_env, duplicate_policy=NO_NETWORK))["link"]
    token = token_of(link)
    shared_pc = public_client()
    first = await register(shared_pc, token, answers(1))
    second = await register(shared_pc, token, answers(2))
    assert second.status_code == 201, second.text
    rows = await _registrations()
    assert rows[0].flags == [] and rows[0].flag_review == "none"
    [flag] = rows[1].flags
    assert flag["code"] == "same_device" and flag["severity"] == "high"
    assert flag["related_registration_id"] == str(rows[0].id) and rows[1].flag_review == "open"
    assert rows[1].status == "registered"  # a flag never stops the candidate
    review = (
        await link_env.recruiter.get(
            f"{API}/open-links/{link['id']}/registrations", params={"needs_review": True}
        )
    ).json()
    assert [r["id"] for r in review["items"]] == [str(rows[1].id)]
    assert first.status_code == 201


async def test_device_block_policy(link_env, public_client, db_session) -> None:
    link = (
        await create_link(link_env, duplicate_policy={"scope": "job", "device": "block", "network": "off"})
    )["link"]
    token = token_of(link)
    shared_pc = public_client()
    assert (await register(shared_pc, token, answers(1))).status_code == 201
    blocked = await register(shared_pc, token, answers(2))
    assert blocked.status_code == 403
    assert blocked.json()["error"] == {
        **blocked.json()["error"],
        "code": "registration_blocked",
        "message": "We couldn't register you. Please contact the hiring team.",
    }
    rows = await _registrations()
    assert [r.status for r in rows] == ["registered", "blocked"]
    assert rows[1].candidate_id is None and rows[1].status_reason == "device_policy"
    assert int(await db_session.scalar(select(func.count()).select_from(Candidate))) == 1
    counts = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}")).json()["counts"]
    assert counts["registrations"] == 1 and counts["blocked"] == 1

    # A recruiter can allow the blocked person (with a reason); a personal link is issued once.
    unblock = await link_env.recruiter.post(
        f"{API}/open-links/{link['id']}/registrations/{rows[1].id}/unblock", json={"reason": "Shared lab PC"}
    )
    assert unblock.status_code == 200, unblock.text
    assert unblock.headers["cache-control"] == "no-store"
    body = unblock.json()
    assert body["personal_url"] and body["registration"]["status"] == "registered"
    assert body["registration"]["rounds"][0]["state"] == "invited"
    again = await link_env.recruiter.post(
        f"{API}/open-links/{link['id']}/registrations/{rows[1].id}/unblock", json={"reason": "again"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_blocked"


async def test_network_flag_is_low_and_can_be_switched_off(link_env, public_client) -> None:
    ua = "Mozilla/5.0 same-browser"
    flagged = (await create_link(link_env, name="flag", duplicate_policy={"scope": "link"}))["link"]
    off = (await create_link(link_env, name="off", duplicate_policy={"scope": "link", "network": "off"}))[
        "link"
    ]
    for link, people in ((flagged, (1, 2)), (off, (3, 4))):
        for n in people:
            response = await register(public_client(ip="10.3.3.3", user_agent=ua), token_of(link), answers(n))
            assert response.status_code == 201, response.text
    rows = {r.link_id: r for r in await _registrations() if r.full_name in ("Candidate 2", "Candidate 4")}

    assert [f["code"] for f in rows[uuid.UUID(flagged["id"])].flags] == ["same_network"]
    assert rows[uuid.UUID(flagged["id"])].flag_review == "none"
    assert rows[uuid.UUID(off["id"])].flags == []
    # Signals are keyed hashes: the raw IP and user agent are never stored.
    for row in rows.values():
        assert "10.3.3.3" not in json.dumps([row.ip_hash, row.ip_ua_hash, row.device_hash])


async def test_flag_review(link_env, public_client) -> None:
    link = (await create_link(link_env, duplicate_policy=NO_NETWORK))["link"]
    token = token_of(link)
    shared_pc = public_client()
    await register(shared_pc, token, answers(1))
    await register(shared_pc, token, answers(2))
    rows = await _registrations()
    base = f"{API}/open-links/{link['id']}/registrations"
    no_flags = await link_env.recruiter.post(f"{base}/{rows[0].id}/flag-review", json={"decision": "cleared"})
    assert no_flags.status_code == 409 and no_flags.json()["error"]["code"] == "no_open_flags"
    cleared = await link_env.recruiter.post(
        f"{base}/{rows[1].id}/flag-review", json={"decision": "cleared", "note": "Siblings, shared laptop"}
    )
    assert cleared.status_code == 200, cleared.text
    detail = cleared.json()
    assert detail["flag_review"] == "cleared" and detail["flag_review_note"] == "Siblings, shared laptop"
    assert detail["flags"][0]["code"] == "same_device"  # the flag itself is kept


async def test_parallel_submissions_of_one_person_create_one_registration(link_env, public_client) -> None:
    import asyncio

    link = (await create_link(link_env, duplicate_policy=NO_NETWORK))["link"]
    token = token_of(link)
    results = await asyncio.gather(
        *(register(public_client(ip=f"10.4.0.{n}"), token, answers(1)) for n in range(4)),
        register(public_client(ip="10.4.1.1"), token, answers(5, phone="+91 98765 00001")),  # same phone
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409], [r.text for r in results]
    assert len(await _registrations()) == 1
    async with session_scope() as db:
        assert int(await db.scalar(select(func.count()).select_from(Interview))) == 1
