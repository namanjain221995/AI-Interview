"""Public registration: landing info, happy path, validation, honeypot, privacy
ack, access code + lockout, schedule/pause/max, origin check and rate limits."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AuditLog,
    Candidate,
    CandidateResume,
    Interview,
    LinkRegistration,
    LinkRegistrationRound,
)
from app.db.session import session_scope
from tests.integration.open_links.helpers import (
    API,
    answers,
    create_link,
    make_job,
    portal_headers,
    register,
    solved_pow,
    token_of,
)

pytestmark = pytest.mark.integration


async def _count(model) -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_landing_info_has_no_internal_ids(link_env, public_client) -> None:
    link = (await create_link(link_env, description="Bring a laptop."))["link"]
    response = await public_client().get(f"{API}/link-access/{token_of(link)}")
    assert response.status_code == 200
    info = response.json()
    text = json.dumps(info)
    for secret in (
        link["id"],
        str(link_env.job_id),
        str(link_env.organization_id),
        *map(str, link_env.round_ids),
    ):
        assert secret not in text
    assert "counts" not in info and info["state"] == "open"
    assert info["job_title"] == "Backend Engineer" and info["job_location"] == "Pune"
    assert [r["name"] for r in info["rounds"]] == ["Round 1", "Round 2"]
    assert [f["key"] for f in info["form"]["fields"]] == ["full_name", "email", "phone"]
    assert "builtin" not in info["form"]["fields"][0] and "enabled" not in info["form"]["fields"][0]
    use = info["data_use"]
    assert use["ai_assessment"] is True and use["human_decision"] is True and use["video_recording"] is True
    assert use["notice_markdown"] and use["retention_days"]["video"] == 30
    assert info["honeypot_field"] == "website" and info["interviewer_name"] == "Maya"

    for token in ("short", "x" * 40, "bad token with spaces!!"):
        missing = await public_client().get(f"{API}/link-access/{token}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "link_not_found"


async def test_register_creates_candidate_interview_and_session(
    link_env, public_client, settings, db_session
) -> None:
    link = (await create_link(link_env))["link"]
    browser = public_client()
    response = await register(browser, token_of(link), answers(1, email=" Candidate1@Example.com "))
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["outcome"] == "registered" and body["next_step"] == "welcome"
    assert body["personal_url"].startswith(f"{settings.public_base_url}/r/")
    set_cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith("cand_access_token=") for c in set_cookies)  # candidate cookie set (G2)
    device = next(c for c in set_cookies if c.startswith("ol_device="))
    assert "HttpOnly" in device and "Path=/api/v1/link-access" in device and "samesite=lax" in device.lower()

    interview = await db_session.get(Interview, uuid.UUID(body["interview_id"]))
    assert interview.round_id == link_env.round_ids[0] and interview.access_code_hash is None
    assert interview.allow_round_selection is False and interview.verified_at is not None
    window = interview.invite_expires_at - datetime.now(UTC)
    assert timedelta(hours=71) < window <= timedelta(hours=72)
    candidate = await db_session.get(Candidate, interview.candidate_id)
    assert candidate.email == "candidate1@example.com" and candidate.source == "open_link"
    assert candidate.phone == "+91 98765 00001"
    registration = await db_session.scalar(select(LinkRegistration))
    assert registration.status == "registered" and registration.candidate_id == candidate.id
    assert registration.personal_token_hash and registration.device_hash and registration.ip_ua_hash
    assert body["personal_url"].rsplit("/", 1)[1] not in json.dumps(registration.answers)
    assert registration.privacy_ack_at and registration.disclosure_hash
    round_row = await db_session.scalar(select(LinkRegistrationRound))
    assert round_row.unlocked_by == "registration" and round_row.sequence == 0 and round_row.attempt == 1

    # The cookies give access to the existing portal for exactly this interview.
    headers = {"X-CSRF-Token": body["csrf_token"]}
    portal = await browser.get(f"{API}/portal/interviews/{body['interview_id']}", headers=headers)
    assert portal.status_code == 200 and portal.json()["job"]["title"] == "Backend Engineer"

    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "link.registration_created"))
    ).all()
    assert len(audit) == 1 and "candidate1@example.com" not in json.dumps(audit[0].details)

    listing = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}")).json()
    assert listing["counts"]["registrations"] == 1 and listing["counts"]["in_progress"] == 1


async def test_existing_candidate_is_reused_not_overwritten(link_env, public_client, db_session) -> None:
    async with session_scope() as db:
        db.add(
            Candidate(
                organization_id=link_env.organization_id,
                full_name="Original Name",
                email="candidate2@example.com",
            )
        )
    link = (await create_link(link_env, duplicate_policy={"scope": "link"}))["link"]
    response = await register(public_client(), token_of(link), answers(2, full_name="Changed Name"))
    assert response.status_code == 201, response.text
    candidate = await db_session.scalar(select(Candidate).where(Candidate.email == "candidate2@example.com"))
    assert candidate.full_name == "Original Name" and candidate.phone  # empty phone filled
    assert await _count(Candidate) == 1


async def test_validation_errors(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    token = token_of(link)
    bad = await register(
        public_client(), token, {"full_name": "", "email": "nope", "phone": "12", "c_extra": "x"}
    )
    assert bad.status_code == 422
    fields = bad.json()["error"]["details"]["fields"]
    assert set(fields) == {"full_name", "email", "phone", "c_extra"}
    browser = public_client()
    no_ack = await browser.post(
        f"{API}/link-access/{token}/register",
        json={"answers": answers(3), "privacy_ack": False, "pow": await solved_pow(browser, token)},
    )
    assert no_ack.status_code == 422 and no_ack.json()["error"]["code"] == "privacy_ack_required"
    assert await _count(LinkRegistration) == 0 and await _count(Candidate) == 0


async def test_honeypot_rejects_without_creating_anything(link_env, public_client, db_session) -> None:
    link = (await create_link(link_env))["link"]
    response = await register(public_client(), token_of(link), answers(4), website="http://spam.example")
    assert response.status_code == 400 and response.json()["error"]["code"] == "registration_rejected"
    assert await _count(LinkRegistration) == 0 and await _count(Candidate) == 0
    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "link.registration_rejected"))
    ).all()
    assert len(audit) == 1


async def test_access_code_required_wrong_and_locked(link_env, public_client, settings) -> None:
    settings.login_lockout_seconds = 120
    created = await create_link(link_env, require_access_code=True)
    token, code = token_of(created["link"]), created["access_code"]
    browser = public_client()
    missing = await register(browser, token, answers(5))
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "access_code_required"
    for attempt in range(9):
        wrong = await register(browser, token, answers(5), access_code="WRONG-CODE")
        assert wrong.status_code == 401, attempt
        assert wrong.json()["error"]["code"] == "invalid_access_code"
    locked = await register(browser, token, answers(5), access_code="WRONG-CODE")
    assert locked.status_code == 423 and locked.json()["error"]["code"] == "access_code_locked"
    assert locked.headers["retry-after"] == "120"
    still = await register(browser, token, answers(5), access_code=code)
    assert still.status_code == 423  # locked even with the right code
    other_ip = await register(public_client(ip="10.9.8.7"), token, answers(5), access_code=code.lower())
    assert other_ip.status_code == 201, other_ip.text  # per (link, IP); codes are case-insensitive


@pytest.mark.parametrize(
    ("overrides", "state"),
    [
        ({"opens_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}, "scheduled"),
        (
            {
                "opens_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
                "closes_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
            "expired",
        ),
    ],
)
async def test_schedule_window(link_env, public_client, overrides, state) -> None:
    link = (await create_link(link_env, **overrides))["link"]
    assert link["state"] == state
    info = (await public_client().get(f"{API}/link-access/{token_of(link)}")).json()
    assert info["state"] == state and info["form"] is None
    response = await register(public_client(), token_of(link), answers(6))
    assert response.status_code == 409
    assert response.json()["error"] == {
        **response.json()["error"],
        "code": "link_unavailable",
        "details": {"state": state},
    }


async def test_max_registrations_and_paused(link_env, public_client) -> None:
    link = (
        await create_link(
            link_env,
            max_registrations=2,
            duplicate_policy={"scope": "job", "device": "flag", "network": "off"},
        )
    )["link"]
    token = token_of(link)
    assert (await register(public_client(), token, answers(1))).status_code == 201
    assert (await register(public_client(), token, answers(2))).status_code == 201
    full = await register(public_client(), token, answers(3))
    assert full.status_code == 409 and full.json()["error"]["details"]["state"] == "full"
    assert (await public_client().get(f"{API}/link-access/{token}")).json()["state"] == "full"
    await link_env.recruiter.post(f"{API}/open-links/{link['id']}/status", json={"status": "paused"})
    paused = await register(public_client(), token, answers(4))
    assert paused.json()["error"]["details"]["state"] == "paused"


async def test_closed_job_closes_the_link(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    from app.db.models import Job

    async with session_scope() as db:
        (await db.get(Job, link_env.job_id)).status = "closed"
    response = await register(public_client(), token_of(link), answers(1))
    assert response.status_code == 409 and response.json()["error"]["details"]["state"] == "closed"


async def test_origin_check(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    token = token_of(link)
    evil = await public_client().post(
        f"{API}/link-access/{token}/register",
        json={"answers": answers(1), "privacy_ack": True},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403 and evil.json()["error"]["code"] == "origin_not_allowed"
    referer = await public_client().post(
        f"{API}/link-access/{token}/register",
        json={"answers": answers(1), "privacy_ack": True},
        headers={"Referer": "https://evil.example/page"},
    )
    assert referer.status_code == 403
    browser = public_client()
    same_host = await browser.post(
        f"{API}/link-access/{token}/register",
        json={"answers": answers(1), "privacy_ack": True, "pow": await solved_pow(browser, token)},
        headers={"Origin": "http://testserver"},
    )
    assert same_host.status_code == 201, same_host.text


async def test_per_link_ip_rate_limit(link_env, public_client) -> None:
    link = (
        await create_link(
            link_env,
            per_ip_hourly_limit=5,
            duplicate_policy={"scope": "job", "device": "flag", "network": "off"},
        )
    )["link"]
    token = token_of(link)
    for n in range(5):
        assert (await register(public_client(ip="10.1.1.1"), token, answers(n + 10))).status_code == 201
    limited = await register(public_client(ip="10.1.1.1"), token, answers(20))
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) > 0
    assert (await register(public_client(ip="10.1.1.2"), token, answers(21))).status_code == 201


async def test_campus_preset_allows_many_from_one_network(link_env, public_client) -> None:
    link = (await create_link(link_env, per_ip_hourly_limit=1000))["link"]
    token = token_of(link)
    ua = "Mozilla/5.0 campus-lab"
    for n in range(30):
        response = await register(public_client(ip="10.2.2.2", user_agent=ua), token, answers(n + 100))
        assert response.status_code == 201, response.text
    regs = (
        await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations", params={"limit": 200})
    ).json()
    assert regs["total"] == 30
    # Same network + browser type -> low-severity flags only; nobody is held back.
    assert all(r["status"] == "registered" for r in regs["items"])
    assert sum(1 for r in regs["items"] if r["flags"]) == 29
    assert all(f["severity"] == "low" for r in regs["items"] for f in r["flags"])
    review = (
        await link_env.recruiter.get(
            f"{API}/open-links/{link['id']}/registrations", params={"needs_review": True}
        )
    ).json()
    assert review["total"] == 0  # low flags never fill the "Needs review" queue


# ------------------------------------------------------------------ identity stop-gap (S4, audit D1)


async def _two_links(link_env) -> tuple[dict, dict]:
    """Open links for two different jobs of the organisation (two applications by the same person)."""
    first = (await create_link(link_env))["link"]
    job_id, round_ids = await make_job(link_env.organization_id)
    second = (
        await create_link(
            link_env, job_id=str(job_id), round_ids=[str(r) for r in round_ids], name="Second job"
        )
    )["link"]
    return first, second


async def test_an_open_link_interview_only_sees_its_own_registrations_resumes(link_env, public_client) -> None:
    """Review of S4: the interview's profile (claims, greeting name) and the résumé wait come only from résumés
    uploaded within the open-link registration, never from the same person's other applications."""
    from app.db.models import Interview
    from app.services.interview.sessions import latest_profile, profile_scope, resume_pending
    from tests.integration.intake.admin_helpers import add_resume

    first, second = await _two_links(link_env)
    browser = public_client()
    one = await register(browser, token_of(first), answers(8))
    assert one.status_code == 201, one.text
    first_interview = uuid.UUID(one.json()["interview_id"])
    await add_resume(first_interview)  # a ready profile from the first application
    two = await register(browser, token_of(second), answers(8))  # same browser: the record is reused (S4)
    assert two.status_code == 201, two.text
    second_interview = uuid.UUID(two.json()["interview_id"])
    async with session_scope() as db:
        interview = await db.get(Interview, second_interview)
        assert interview is not None
        scope = await profile_scope(db, second_interview)
        assert scope is not None and first_interview not in scope
        assert await latest_profile(db, interview.candidate_id, interview.job_id, interview_ids=scope) is None
        assert await resume_pending(db, interview.candidate_id, interview_ids=scope) is False
        # A recruiter invitation identifies the person: every résumé counts there (unchanged).
        assert await latest_profile(db, interview.candidate_id, interview.job_id) is not None
    await add_resume(second_interview)
    async with session_scope() as db:
        mine = await latest_profile(db, interview.candidate_id, interview.job_id, interview_ids=scope)
        assert mine is not None and mine.job_id == interview.job_id


async def test_existing_candidate_with_history_is_refused_from_another_browser(
    link_env, public_client, db_session
) -> None:
    """Someone else's email must not sign the registrant in as that person (their résumé, their history)."""
    first, second = await _two_links(link_env)
    owner = public_client()
    assert (await register(owner, token_of(first), answers(7))).status_code == 201
    stranger = public_client(ip="10.9.9.9")
    response = await register(stranger, token_of(second), answers(7, full_name="Someone Else"))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "registration_unavailable"
    assert "candidate7@example.com" not in response.text
    blocked = await db_session.scalar(
        select(LinkRegistration).where(LinkRegistration.status_reason == "identity_unverified")
    )
    assert blocked is not None and blocked.status == "blocked" and blocked.candidate_id is None
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "link.registration_identity_refused")
    )
    assert audit is not None and "candidate7@example.com" not in json.dumps(audit.details)
    assert await _count(Candidate) == 1  # nothing attached to the existing record

    # The hiring team can allow it after confirming it is the same person: the record is reused.
    allowed = await link_env.recruiter.post(
        f"{API}/open-links/{second['id']}/registrations/{blocked.id}/unblock",
        json={"reason": "Confirmed by phone"},
    )
    assert allowed.status_code == 200, allowed.text
    await db_session.refresh(blocked)
    candidate = await db_session.scalar(select(Candidate).where(Candidate.email == "candidate7@example.com"))
    assert blocked.status == "registered" and blocked.candidate_id == candidate.id


async def test_returning_candidate_on_the_same_browser_is_reused_without_old_resumes(
    link_env, public_client, db_session
) -> None:
    first, second = await _two_links(link_env)
    browser = public_client()
    one = await register(browser, token_of(first), answers(8))
    assert one.status_code == 201
    first_interview = uuid.UUID(one.json()["interview_id"])
    candidate = await db_session.scalar(select(Candidate).where(Candidate.email == "candidate8@example.com"))
    async with session_scope() as db:
        db.add(
            CandidateResume(
                candidate_id=candidate.id,
                organization_id=link_env.organization_id,
                interview_id=first_interview,
                storage_key="resumes/test/first.pdf",
                original_filename="first.pdf",
                content_type="application/pdf",
                size_bytes=1000,
                sha256="a" * 64,
                status="ready",
            )
        )
    two = await register(browser, token_of(second), answers(8))
    assert two.status_code == 201, two.text  # the same browser: the returning applicant
    second_interview = uuid.UUID(two.json()["interview_id"])
    assert (await db_session.get(Interview, second_interview)).candidate_id == candidate.id
    assert await _count(Candidate) == 1

    # The résumé from the other application is never shown for (or counted towards) this one.
    headers = portal_headers(two)
    resume = await browser.get(f"{API}/portal/interviews/{second_interview}/resume", headers=headers)
    assert resume.status_code == 200 and resume.json()["resume"] is None
    portal = (await browser.get(f"{API}/portal/interviews/{second_interview}", headers=headers)).json()
    assert "resume" not in portal["steps_completed"]


# ------------------------------------------------------------------ proof-of-work (S15)


async def test_registration_needs_a_fresh_proof_of_work_for_this_link(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    other = (await create_link(link_env, name="Other drive"))["link"]
    token = token_of(link)
    challenge = await public_client().get(f"{API}/link-access/{token}/challenge")
    assert challenge.status_code == 200 and challenge.headers["cache-control"] == "no-store"
    assert set(challenge.json()) == {"algorithm", "challenge", "salt", "signature", "maxnumber"}
    unknown = await public_client().get(f"{API}/link-access/{'x' * 32}/challenge")
    assert unknown.status_code == 404

    missing = await register(public_client(), token, answers(41), pow=None)
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "pow_required"
    browser = public_client()
    for_other_link = await solved_pow(browser, token_of(other))
    wrong_link = await register(browser, token, answers(42), pow=for_other_link)
    assert wrong_link.status_code == 422 and wrong_link.json()["error"]["code"] == "pow_invalid"

    solution = await solved_pow(browser, token)
    assert (await register(browser, token, answers(43), pow=solution)).status_code == 201
    replay = await register(public_client(), token, answers(44), pow=solution)
    assert replay.status_code == 422 and replay.json()["error"]["code"] == "pow_invalid"
    assert await _count(LinkRegistration) == 1


async def test_proof_of_work_can_be_switched_off(link_env, public_client, settings) -> None:
    settings.open_link_pow_enabled = False
    link = (await create_link(link_env))["link"]
    response = await register(public_client(), token_of(link), answers(45), pow=None)
    assert response.status_code == 201, response.text
