"""Personal link (verify, lockout, reissue, cancelled), portal link routes
(IDOR, non-link interviews) and the portal hooks (resume required, resume hash)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.db.models import CandidateResume, Consent, Interview, LinkRegistration
from app.db.session import session_scope
from app.services.open_links.hooks import ensure_resume_for_session, on_resume_uploaded
from tests.integration.open_links.helpers import API, answers, complete, create_link, register, token_of

pytestmark = pytest.mark.integration


def _personal_token(response) -> str:
    return response.json()["personal_url"].rsplit("/r/", 1)[1]


async def test_personal_link_verify_and_lockout(link_env, public_client, redis, settings) -> None:
    settings.login_lockout_seconds = 300
    link = (await create_link(link_env))["link"]
    registered = await register(public_client(), token_of(link), answers(1))
    token = _personal_token(registered)

    info = await public_client().get(f"{API}/link-return/{token}")
    assert info.status_code == 200
    assert info.json() == {
        "organization_name": info.json()["organization_name"],
        "job_title": "Backend Engineer",
        "link_name": "Campus drive Pune",
        "state": "active",
    }
    assert (await public_client().get(f"{API}/link-return/{'x' * 43}")).json()["error"][
        "code"
    ] == "personal_link_not_found"
    assert (await public_client().get(f"{API}/link-return/short")).status_code == 404

    phone = public_client()
    evil = await phone.post(
        f"{API}/link-return/{token}/verify",
        json={"email": "candidate1@example.com"},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "origin_not_allowed"
    for _ in range(4):
        wrong = await phone.post(f"{API}/link-return/{token}/verify", json={"email": "other@example.com"})
        assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_credentials"
    locked = await phone.post(f"{API}/link-return/{token}/verify", json={"email": "other@example.com"})
    assert locked.status_code == 423 and locked.json()["error"]["details"]["retry_after_seconds"] == 300
    blocked = await phone.post(f"{API}/link-return/{token}/verify", json={"email": "candidate1@example.com"})
    assert blocked.status_code == 423
    await redis.flushdb()

    ok = await phone.post(f"{API}/link-return/{token}/verify", json={"email": "Candidate1@Example.com"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["interview_id"] == registered.json()["interview_id"] and body["next_step"] == "welcome"
    assert body["progress"]["next_action"] == "continue_current"
    assert any(c.startswith("cand_access_token=") for c in ok.headers.get_list("set-cookie"))  # G2
    assert (await phone.get(f"{API}/portal/me")).json()["interview_id"] == body["interview_id"]
    async with session_scope() as db:
        assert (await db.scalars(select(LinkRegistration))).one().personal_failed_count == 0


async def test_personal_link_after_round_one_and_hard_lock(link_env, public_client) -> None:
    link = (await create_link(link_env, progression="after_review"))["link"]
    registered = await register(public_client(), token_of(link), answers(1))
    token = _personal_token(registered)
    await complete(link_env.recruiter, registered.json()["interview_id"])
    ok = await public_client().post(
        f"{API}/link-return/{token}/verify", json={"email": "candidate1@example.com"}
    )
    assert ok.json()["next_step"] == "complete" and ok.json()["progress"]["next_action"] == "wait_for_review"

    async with session_scope() as db:
        await db.execute(update(LinkRegistration).values(personal_failed_count=20))
    locked = await public_client().post(
        f"{API}/link-return/{token}/verify", json={"email": "candidate1@example.com"}
    )
    assert locked.status_code == 423 and locked.json()["error"]["details"] == {"permanent": True}

    reg_id = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()["items"][
        0
    ]["id"]
    reissued = await link_env.recruiter.post(
        f"{API}/open-links/{link['id']}/registrations/{reg_id}/personal-link"
    )
    assert reissued.status_code == 200 and reissued.headers["cache-control"] == "no-store"
    new_token = reissued.json()["personal_url"].rsplit("/r/", 1)[1]
    assert new_token != token
    assert (await public_client().get(f"{API}/link-return/{token}")).status_code == 404
    fresh = await public_client().post(
        f"{API}/link-return/{new_token}/verify", json={"email": "candidate1@example.com"}
    )
    assert fresh.status_code == 200, fresh.text


async def test_cancelled_registration_personal_link(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    registered = await register(public_client(), token_of(link), answers(1))
    token = _personal_token(registered)
    reg_id = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()["items"][
        0
    ]["id"]
    await link_env.recruiter.post(f"{API}/open-links/{link['id']}/registrations/{reg_id}/cancel")
    assert (await public_client().get(f"{API}/link-return/{token}")).json()["state"] == "cancelled"
    gone = await public_client().post(
        f"{API}/link-return/{token}/verify", json={"email": "candidate1@example.com"}
    )
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "registration_cancelled"


async def test_portal_link_routes_are_scoped(link_env, public_client, candidate_auth_client) -> None:
    normal = await candidate_auth_client()
    assert (await normal.get(f"{API}/portal/link/progress")).json()["error"][
        "code"
    ] == "not_link_registration"

    link = (
        await create_link(link_env, duplicate_policy={"scope": "job", "device": "flag", "network": "off"})
    )["link"]
    alice_browser, bob_browser = public_client(), public_client()
    alice = await register(alice_browser, token_of(link), answers(1))
    bob = await register(bob_browser, token_of(link), answers(2))
    headers = {"X-CSRF-Token": alice.json()["csrf_token"]}
    stolen = await alice_browser.post(
        f"{API}/portal/link/continue", json={"interview_id": bob.json()["interview_id"]}, headers=headers
    )
    assert stolen.status_code == 404 and stolen.json()["error"]["code"] == "interview_not_found"
    random = await alice_browser.post(
        f"{API}/portal/link/continue", json={"interview_id": str(uuid.uuid4())}, headers=headers
    )
    assert random.status_code == 404
    no_csrf = await alice_browser.post(
        f"{API}/portal/link/continue", json={"interview_id": alice.json()["interview_id"]}
    )
    assert no_csrf.status_code == 403

    await complete(link_env.recruiter, alice.json()["interview_id"])
    done = await alice_browser.post(
        f"{API}/portal/link/continue", json={"interview_id": alice.json()["interview_id"]}, headers=headers
    )
    assert done.status_code == 409 and done.json()["error"]["code"] == "interview_not_available"
    assert done.json()["error"]["details"]["status"] == "completed"

    staff = await link_env.recruiter.get(f"{API}/portal/link/progress")
    assert staff.status_code == 401  # G2: a staff session is not a candidate session on candidate routes


async def test_resume_required_guard_and_resume_flag(link_env, public_client) -> None:
    link = (
        await create_link(link_env, duplicate_policy={"scope": "job", "device": "flag", "network": "off"})
    )["link"]
    browser = public_client()
    first = await register(browser, token_of(link), answers(1))
    second = await register(public_client(), token_of(link), answers(2))
    interview_id = uuid.UUID(first.json()["interview_id"])
    async with session_scope() as db:
        interview = await db.get(Interview, interview_id)
        db.add(
            Consent(
                interview_id=interview.id,
                candidate_id=interview.candidate_id,
                policy_version="2026-10-01",
                policy_hash="0" * 64,
                items={},
            )
        )
    response = await browser.post(
        f"{API}/portal/interviews/{interview_id}/session",
        headers={"X-CSRF-Token": first.json()["csrf_token"]},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "resume_required"

    sha = "a" * 64
    async with session_scope() as db:
        for reg_response in (first, second):
            interview = await db.get(Interview, uuid.UUID(reg_response.json()["interview_id"]))
            resume = CandidateResume(
                candidate_id=interview.candidate_id,
                organization_id=interview.organization_id,
                interview_id=interview.id,
                storage_key=f"resumes/{uuid.uuid4()}",
                original_filename="cv.txt",
                content_type="text/plain",
                size_bytes=10,
                sha256=sha,
                status="ready",
            )
            db.add(resume)
            await db.flush()
            assert await on_resume_uploaded(db, interview, resume) is (reg_response is second)
        await ensure_resume_for_session(db, await db.get(Interview, interview_id))  # no longer raises
    regs = {
        r["full_name"]: r
        for r in (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()[
            "items"
        ]
    }
    assert regs["Candidate 1"]["flags"] == []
    assert [f["code"] for f in regs["Candidate 2"]["flags"]] == ["same_resume"]
    assert regs["Candidate 2"]["flag_review"] == "open"
    async with session_scope() as db:
        rows = (await db.scalars(select(LinkRegistration))).all()
        assert {r.resume_sha256 for r in rows} == {sha}
        # Repeated uploads do not duplicate the flag.
        interview = await db.get(Interview, uuid.UUID(second.json()["interview_id"]))
        assert await on_resume_uploaded(db, interview, SimpleNamespace(sha256=sha)) is False

    # A link without the resume requirement does not block (and normal invites are untouched).
    no_resume = (
        await create_link(link_env, name="No CV", require_resume=False, duplicate_policy={"scope": "link"})
    )["link"]
    third = await register(public_client(), token_of(no_resume), answers(3))
    async with session_scope() as db:
        await ensure_resume_for_session(db, await db.get(Interview, uuid.UUID(third.json()["interview_id"])))
