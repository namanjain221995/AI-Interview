"""Round progression: immediate and after_review, policy/technical endings,
portal progress + continue, worker hook, expiry, retake and cancel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import AuthSession, Interview, LinkRegistration, LinkRegistrationRound
from app.db.session import session_scope
from tests.integration.open_links.helpers import API, answers, complete, create_link, register, token_of

pytestmark = pytest.mark.integration


async def _registration() -> LinkRegistration:
    async with session_scope() as db:
        return (await db.scalars(select(LinkRegistration))).one()


async def test_immediate_two_rounds_end_to_end(link_env, public_client, db_session) -> None:
    link = (await create_link(link_env))["link"]
    browser = public_client()
    registered = await register(browser, token_of(link), answers(1))
    headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
    round1 = registered.json()["interview_id"]

    progress = (await browser.get(f"{API}/portal/link/progress")).json()
    assert progress["next_action"] == "continue_current" and progress["next_interview_id"] == round1
    assert [(r["name"], r["state"], r["is_current"]) for r in progress["rounds"]] == [
        ("Round 1", "invited", True),
        ("Round 2", "locked", False),
    ]
    assert progress["deadline"] is not None and progress["link_name"] == "Campus drive Pune"

    result = await complete(link_env.recruiter, round1)
    assert result["registration_status"] == "registered" and result["unlocked_interview_id"]
    round2 = result["unlocked_interview_id"]

    progress = (await browser.get(f"{API}/portal/link/progress")).json()
    assert progress["next_action"] == "start_next" and progress["next_interview_id"] == round2
    assert progress["next_round_name"] == "Round 2"
    assert [r["state"] for r in progress["rounds"]] == ["completed", "invited"]

    old_session = (
        await db_session.scalars(select(AuthSession).where(AuthSession.interview_id == uuid.UUID(round1)))
    ).one()
    switched = await browser.post(
        f"{API}/portal/link/continue", json={"interview_id": round2}, headers=headers
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["interview_id"] == round2 and switched.json()["next_step"] == "welcome"
    await db_session.refresh(old_session)
    assert old_session.revoked_reason == "link_round_switch"
    headers = {"X-CSRF-Token": switched.json()["csrf_token"]}
    me = (await browser.get(f"{API}/portal/me")).json()
    assert me["interview_id"] == round2
    portal = await browser.get(f"{API}/portal/interviews/{round2}")
    assert portal.status_code == 200

    interview2 = await db_session.get(Interview, uuid.UUID(round2))
    assert interview2.round_id == link_env.round_ids[1] and interview2.access_code_hash is None
    row = await db_session.scalar(
        select(LinkRegistrationRound).where(LinkRegistrationRound.interview_id == interview2.id)
    )
    assert row.unlocked_by == "auto" and row.sequence == 1

    await complete(link_env.recruiter, round2)
    progress = (await browser.get(f"{API}/portal/link/progress")).json()
    assert progress["next_action"] == "all_done"
    assert (await _registration()).status == "completed"
    regs = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()
    assert [r["state"] for r in regs["items"][0]["rounds"]] == ["completed", "completed"]
    assert regs["items"][0]["rounds"][0]["session_id"]


async def test_after_review_waits_and_bulk_advance(link_env, public_client) -> None:
    link = (await create_link(link_env, progression="after_review"))["link"]
    token = token_of(link)
    ids: list[str] = []
    for n in (1, 2, 3):
        response = await register(public_client(ip=f"10.0.0.{n}"), token, answers(n))
        ids.append(response.json()["interview_id"])
    for interview_id in ids[:2]:
        result = await complete(link_env.recruiter, interview_id)
        assert result["registration_status"] == "awaiting_review" and result["unlocked_interview_id"] is None
    base = f"{API}/open-links/{link['id']}/registrations"
    waiting = (await link_env.recruiter.get(base, params={"status": "awaiting_review"})).json()
    assert waiting["total"] == 2 and all(r["status_reason"] == "after_review" for r in waiting["items"])
    detail = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}")).json()
    assert detail["counts"]["awaiting_review"] == 2

    all_ids = [r["id"] for r in (await link_env.recruiter.get(base)).json()["items"]]
    advanced = await link_env.recruiter.post(
        f"{base}/advance", json={"registration_ids": [*all_ids, str(uuid.uuid4())], "note": "Shortlisted"}
    )
    assert advanced.status_code == 200, advanced.text
    body = advanced.json()
    assert len(body["advanced"]) == 2 and all(
        a["round_id"] == str(link_env.round_ids[1]) for a in body["advanced"]
    )
    assert sorted(s["code"] for s in body["skipped"]) == ["not_awaiting_review", "not_found"]
    again = (await link_env.recruiter.post(f"{base}/advance", json={"registration_ids": all_ids})).json()
    assert again["advanced"] == [] and len(again["skipped"]) == 3

    # After the last round there is nothing to advance to: completed, never "awaiting".
    final = await complete(link_env.recruiter, body["advanced"][0]["interview_id"])
    assert final["registration_status"] == "completed"


@pytest.mark.parametrize("end_reason", ["policy_termination", "technical_failure"])
async def test_immediate_routes_problem_endings_to_a_person(link_env, public_client, end_reason) -> None:
    link = (await create_link(link_env))["link"]
    browser = public_client()
    registered = await register(browser, token_of(link), answers(1))
    result = await complete(link_env.recruiter, registered.json()["interview_id"], end_reason)
    assert result["registration_status"] == "awaiting_review" and result["unlocked_interview_id"] is None
    registration = await _registration()
    assert registration.status_reason == end_reason
    progress = (await browser.get(f"{API}/portal/link/progress")).json()
    assert progress["next_action"] == "wait_for_review"
    # The recruiter decides: another attempt of the same round...
    base = f"{API}/open-links/{link['id']}/registrations/{registration.id}"
    retake = await link_env.recruiter.post(
        f"{base}/retake", json={"round_index": 0, "reason": "Network dropped"}
    )
    assert retake.status_code == 200, retake.text
    detail = retake.json()
    assert detail["status"] == "registered" and [a["attempt"] for a in detail["attempts"]] == [2, 1]
    assert detail["rounds"][0]["state"] == "invited" and detail["rounds"][0]["attempt"] == 2
    progress = (await browser.get(f"{API}/portal/link/progress")).json()
    assert (
        progress["next_action"] == "start_next"
        and progress["next_interview_id"] == detail["rounds"][0]["interview_id"]
    )


async def test_retake_rules(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    registered = await register(public_client(), token_of(link), answers(1))
    registration = await _registration()
    base = f"{API}/open-links/{link['id']}/registrations/{registration.id}"
    open_round = await link_env.recruiter.post(f"{base}/retake", json={"round_index": 0, "reason": "try"})
    assert open_round.status_code == 409 and open_round.json()["error"]["code"] == "retake_not_allowed"
    locked = await link_env.recruiter.post(f"{base}/retake", json={"round_index": 1, "reason": "try"})
    assert locked.status_code == 409
    short = await link_env.recruiter.post(f"{base}/retake", json={"round_index": 0, "reason": "x"})
    assert short.status_code == 422
    assert registered.status_code == 201


async def test_round_window_expiry_and_cancel(link_env, public_client) -> None:
    link = (await create_link(link_env, round_window_hours=1))["link"]
    browser = public_client()
    registered = await register(browser, token_of(link), answers(1))
    interview_id = uuid.UUID(registered.json()["interview_id"])
    async with session_scope() as db:
        await db.execute(
            update(Interview)
            .where(Interview.id == interview_id)
            .values(invite_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    regs = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()
    item = regs["items"][0]
    assert item["status"] == "expired" and item["rounds"][0]["state"] == "expired"  # lazy sync on read
    again = await register(public_client(), token_of(link), answers(1))
    assert again.status_code == 409 and again.json()["error"]["details"]["state"] == "expired"

    base = f"{API}/open-links/{link['id']}/registrations/{item['id']}"
    retake = await link_env.recruiter.post(
        f"{base}/retake", json={"round_index": 0, "reason": "Was travelling"}
    )
    assert retake.status_code == 200 and retake.json()["status"] == "registered"
    cancelled = await link_env.recruiter.post(f"{base}/cancel", json={"reason": "Withdrew"})
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled" and body["status_reason"] == "recruiter_cancelled"
    assert body["rounds"][0]["state"] == "cancelled"
    twice = await link_env.recruiter.post(f"{base}/cancel")
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "invalid_status"
    progress = await browser.get(f"{API}/portal/link/progress")
    assert progress.status_code == 401  # sessions of cancelled interviews are revoked


async def test_worker_hook_from_scoring_finalize(link_env, public_client, worker_factory) -> None:
    """``scoring.finalize`` enqueues ``open_link.round_finished``; its handler unlocks round 2."""
    import app.workers.open_links  # noqa: F401 - registers the handlers
    from app.core.queue import enqueue
    from app.domain.enums import InterviewStatus, SessionStatus
    from app.workers.open_links import round_finished

    link = (await create_link(link_env))["link"]
    registered = await register(public_client(), token_of(link), answers(1))
    interview_id = uuid.UUID(registered.json()["interview_id"])
    # Close the session the way the engine does (no sync yet).
    from app.db.models import InterviewSession, Round

    async with session_scope() as db:
        interview = await db.get(Interview, interview_id)
        round_ = await db.get(Round, interview.round_id)
        session = InterviewSession(
            interview_id=interview.id,
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=round_.id,
            status=SessionStatus.COMPLETING.value,
            end_reason="sufficient_evidence",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )
        db.add(session)
        interview.status = InterviewStatus.COMPLETED.value
        interview.completed_at = datetime.now(UTC)
        await db.flush()
        session_id = session.id
    assert (await _registration()).status == "registered"  # nothing synced yet

    job_id = await enqueue("open_link.round_finished", {"session_id": str(session_id)})
    worker = worker_factory(job_types=["open_link.round_finished"])
    await worker.drain()
    from app.core.queue import get_job

    job = await get_job(job_id)
    assert job is not None and job.status == "succeeded", job.status if job else None
    registration = await _registration()
    assert registration.current_round_index == 1 and registration.status == "registered"
    # Idempotent; a non-link session is a no-op.
    again = await round_finished({"session_id": str(session_id)}, None)  # type: ignore[arg-type]
    assert again["link"] is True and again["changed"] is False
    unknown = await round_finished({"session_id": str(uuid.uuid4())}, None)  # type: ignore[arg-type]
    assert unknown == {"session_id": unknown["session_id"], "link": False}


async def test_scoring_finalize_enqueues_round_finished(monkeypatch) -> None:
    from app.workers import reports

    calls: list[tuple[str, dict, str | None]] = []

    async def fake_enqueue(job_type, payload=None, *, dedupe_key=None, **kwargs):
        calls.append((job_type, dict(payload or {}), dedupe_key))
        return "job"

    async def stop(*args, **kwargs):
        raise RuntimeError("stop after the hook")

    monkeypatch.setattr(reports, "enqueue", fake_enqueue)
    monkeypatch.setattr(reports, "_maybe_defer", stop)
    sid = uuid.uuid4()
    with pytest.raises(RuntimeError):
        await reports.finalize_scores({"session_id": str(sid)}, None)  # type: ignore[arg-type]
    assert calls == [
        ("open_link.round_finished", {"session_id": str(sid)}, f"open_link.round_finished:{sid}")
    ]
