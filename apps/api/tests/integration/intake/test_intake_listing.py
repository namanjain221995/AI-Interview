"""Intake list buckets, badge precedence, filters, sorting, paging and stats (INTAKE_INTERVIEW.md 10.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import IntakeStageResult, IntegrityEvent, Interview
from app.db.session import session_scope
from app.domain.enums import Role
from tests.integration.intake.admin_helpers import (
    API,
    create_intake,
    insert_decision,
    insert_evaluation,
    insert_session,
    intake_row,
    make_intake_job,
)

pytestmark = pytest.mark.integration

PLAN = {
    "generated_by": "deterministic",
    "summary": "Focus on Python fundamentals.",
    "modules": [
        {
            "key": "python_fundamentals",
            "title": "Python fundamentals",
            "priority": 1,
            "why": "Verification answers stayed general.",
            "objectives": ["Write functions", "Use lists and dicts"],
            "milestones": [{"week": w, "milestone": "m", "measure": "x"} for w in (1, 2, 3)],
        }
    ],
}
ITEMS = [
    {
        "id": f"ri_{i:010x}",
        "kind": "stage_incomplete",
        "severity": "attention",
        "title": "t",
        "source": "validator",
    }
    for i in range(3)
]


async def _fixture(auth_client):  # noqa: ANN202 - test fixture builder
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    reviewer = await auth_client(Role.INTERVIEWER, organization_id=org_id)
    job_id, _ = await make_intake_job(org_id)
    other_job, _ = await make_intake_job(org_id, title="Second program")

    async def make(name: str, *, job=job_id, **kw) -> dict:
        created = await create_intake(
            recruiter,
            job,
            candidate={"full_name": name, "email": f"{name.lower().replace(' ', '.')}@x.test"},
            **kw,
        )
        return created["intake"]

    rows: dict[str, dict] = {}
    rows["draft"] = await make("Dana Draft", send=False)
    rows["active"] = await make("Ari Active", target_role="data_analyst")
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    rows["scheduled"] = await make("Sam Scheduled", settings={"scheduled_for": future})
    rows["live"] = await make("Lee Live")
    rows["undecided_good"] = await make("Uma Undecided")
    rows["undecided_borderline"] = await make("Bea Borderline", job=other_job)
    rows["accepted"] = await make("Ace Accepted", settings={"assigned_reviewer_id": str(reviewer.user.id)})
    rows["rejected"] = await make("Rex Rejected")
    rows["second"] = await make("Sid Second")
    rows["expired"] = await make("Eve Expired")
    rows["cancelled"] = await make("Cal Cancelled")

    async def interview_of(key: str) -> uuid.UUID:
        return (await intake_row(rows[key]["id"])).interview_id

    await insert_session(await interview_of("live"), status="live")
    sessions = {}
    for key in ("undecided_good", "undecided_borderline", "accepted", "rejected", "second"):
        sessions[key] = await insert_session(
            await interview_of(key), status="completed", elapsed_seconds=2400.0
        )
    good = uuid.UUID(rows["undecided_good"]["id"])
    await insert_evaluation(
        good, sessions["undecided_good"], band="accept_with_notes", total="3.7000", training_plan=PLAN
    )
    await insert_evaluation(
        uuid.UUID(rows["undecided_borderline"]["id"]),
        sessions["undecided_borderline"],
        band="borderline",
        total="2.9000",
    )
    accepted = uuid.UUID(rows["accepted"]["id"])
    await insert_evaluation(
        accepted,
        sessions["accepted"],
        band="strong_accept",
        total="4.5000",
        training_plan=PLAN,
        review_items=ITEMS,
    )
    await insert_decision(
        accepted,
        sessions["accepted"],
        reviewer.user.id,
        outcome="accept",
        resolutions=[{"item_id": ITEMS[0]["id"], "resolution": "dismissed", "note": None}],
    )
    rejected = uuid.UUID(rows["rejected"]["id"])
    await insert_evaluation(rejected, sessions["rejected"], band="insufficient_evidence", total=None)
    await insert_decision(rejected, sessions["rejected"], recruiter.user.id, outcome="reject")
    second = uuid.UUID(rows["second"]["id"])
    await insert_evaluation(second, sessions["second"], band="borderline", total="3.0000", status="running")
    await insert_decision(second, sessions["second"], reviewer.user.id, outcome="second_interview")
    async with session_scope() as db:
        expired = await db.get(Interview, await interview_of("expired"))
        expired.invite_expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.add(
            IntegrityEvent(
                session_id=sessions["accepted"],
                organization_id=org_id,
                type="tab_hidden",
                source="client",
                action="flag_for_human_review",
            )
        )
        db.add(
            IntegrityEvent(
                session_id=sessions["accepted"],
                organization_id=org_id,
                type="tab_hidden",
                source="client",
                action="log_only",
            )
        )
    assert (
        await recruiter.post(f"{API}/intake/interviews/{rows['cancelled']['id']}/cancel")
    ).status_code == 200
    return recruiter, reviewer, rows, sessions, job_id, other_job


async def _ids(client, **params) -> set[str]:
    response = await client.get(f"{API}/intake/interviews", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def test_buckets_badges_and_stats(auth_client) -> None:
    recruiter, _reviewer, rows, _sessions, _job, _other = await _fixture(auth_client)
    rid = {key: row["id"] for key, row in rows.items()}
    expected = {
        "drafts": {"draft"},
        "active": {"active"},
        "scheduled": {"scheduled"},
        "in_progress": {"live"},
        "completed": {"undecided_good", "undecided_borderline", "accepted", "rejected", "second"},
        "awaiting_review": {"undecided_good", "undecided_borderline"},
        "accepted": {"accepted"},
        "borderline": {"undecided_borderline", "second"},
        "rejected": {"rejected"},
        "training": {"undecided_good", "accepted"},
        "expired": {"expired"},
        "cancelled": {"cancelled"},
        "all": set(rows),
    }
    for bucket, keys in expected.items():
        assert await _ids(recruiter, bucket=bucket) == {rid[k] for k in keys}, bucket

    listing = (await recruiter.get(f"{API}/intake/interviews", params={"limit": 200})).json()
    badges = {item["id"]: item["bucket"] for item in listing["items"]}
    assert {key: badges[rid[key]] for key in rows} == {
        "draft": "drafts",
        "active": "active",
        "scheduled": "scheduled",
        "live": "in_progress",
        "undecided_good": "awaiting_review",
        "undecided_borderline": "awaiting_review",
        "accepted": "accepted",
        "rejected": "rejected",
        "second": "borderline",
        "expired": "expired",
        "cancelled": "cancelled",
    }
    by_id = {item["id"]: item for item in listing["items"]}
    accepted = by_id[rid["accepted"]]
    assert (
        accepted["evaluation"]["weighted_total"] == "4.5000"
        and accepted["evaluation"]["band"] == "strong_accept"
    )
    assert accepted["decision"]["outcome"] == "accept" and accepted["has_training_plan"] is True
    assert accepted["review_items_open"] == 2 and accepted["integrity_flags"] == 1
    assert accepted["assigned_reviewer"]["full_name"]
    second = by_id[rid["second"]]
    assert second["evaluation"]["status"] == "running" and second["evaluation"]["band"] == "borderline"
    assert by_id[rid["live"]]["session"]["status"] == "live" and by_id[rid["draft"]]["interview"] is None

    stats = (await recruiter.get(f"{API}/intake/stats")).json()
    assert stats["buckets"] == {key: len(keys) for key, keys in expected.items() if key != "all"}
    totals = stats["totals"]
    assert totals["invitations"] == 10 and totals["started"] == 6 and totals["completed"] == 5
    assert totals["awaiting_review"] == 2 and totals["decided"] == 3 and totals["incomplete"] == 0
    # Average over ready evaluations with a band: (3.7 + 2.9 + 4.5) / 3 (insufficient and running excluded).
    assert Decimal(totals["average_weighted_total"]) == Decimal("3.7000")
    assert totals["average_duration_s"] == 2400.0


async def test_filters_sorting_paging(auth_client, org_factory) -> None:
    recruiter, reviewer, rows, sessions, job_id, other_job = await _fixture(auth_client)
    rid = {key: row["id"] for key, row in rows.items()}
    assert await _ids(recruiter, q="bea") == {rid["undecided_borderline"]}
    assert await _ids(recruiter, q="ace.accepted@") == {rid["accepted"]}
    assert await _ids(recruiter, q="%") == set()  # LIKE wildcards are escaped
    assert await _ids(recruiter, job_id=str(other_job)) == {rid["undecided_borderline"]}
    assert await _ids(recruiter, target_role="data_analyst") == {rid["active"]}
    assert await _ids(recruiter, band="borderline") == {
        rid["undecided_borderline"]
    }  # newest *ready* evaluation
    assert await _ids(recruiter, outcome="reject") == {rid["rejected"]}
    assert await _ids(recruiter, reviewer_id=str(reviewer.user.id)) == {rid["accepted"], rid["second"]}
    # Exact session lookup (the staff web's /app/intake/session/:sessionId resolver).
    assert await _ids(recruiter, session_id=str(sessions["accepted"])) == {rid["accepted"]}
    assert await _ids(recruiter, session_id=str(uuid.uuid4())) == set()
    outsider = await auth_client(Role.RECRUITER, organization_id=await org_factory())
    assert await _ids(outsider, session_id=str(sessions["accepted"])) == set()
    bad_session = await recruiter.get(f"{API}/intake/interviews", params={"session_id": "nope"})
    assert bad_session.status_code == 422 and bad_session.json()["error"]["details"]["field"] == "session_id"
    assert await _ids(recruiter, bucket="completed", job_id=str(job_id)) == {
        rid[k] for k in ("undecided_good", "accepted", "rejected", "second")
    }
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert await _ids(recruiter, created_from=future) == set()
    assert len(await _ids(recruiter, created_to=future)) == len(rows)

    async def order(sort: str, **params) -> list[str]:
        data = (
            await recruiter.get(f"{API}/intake/interviews", params={"sort": sort, "limit": 200, **params})
        ).json()
        return [item["id"] for item in data["items"]]

    scored = await order("-score", bucket="completed")
    assert scored[:3] == [rid["accepted"], rid["undecided_good"], rid["undecided_borderline"]]
    assert (await order("score", bucket="completed"))[:3] == [
        rid["undecided_borderline"], rid["undecided_good"], rid["accepted"],
    ]  # fmt: skip
    names = (
        await recruiter.get(f"{API}/intake/interviews", params={"sort": "candidate", "limit": 200})
    ).json()
    assert [i["candidate"]["full_name"] for i in names["items"]][:2] == ["Ace Accepted", "Ari Active"]
    newest = await order("-created_at")
    assert newest[0] == rid["cancelled"] and await order("created_at") == list(reversed(newest))
    assert (await order("-completed_at"))[0] in {
        rid[k] for k in ("undecided_good", "undecided_borderline", "accepted", "rejected", "second")
    }

    page = (await recruiter.get(f"{API}/intake/interviews", params={"limit": 4, "offset": 8})).json()
    assert (
        page["total"] == len(rows) and len(page["items"]) == 3 and (page["limit"], page["offset"]) == (4, 8)
    )
    bad = await recruiter.get(f"{API}/intake/interviews", params={"bucket": "nope"})
    assert bad.status_code == 422 and bad.json()["error"]["details"]["field"] == "bucket"
    filtered = (await recruiter.get(f"{API}/intake/stats", params={"job_id": str(other_job)})).json()
    assert filtered["totals"]["invitations"] == 1 and filtered["buckets"]["borderline"] == 1


async def test_incomplete_totals(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    job_id, _ = await make_intake_job(recruiter.user.organization_id)
    a = (await create_intake(recruiter, job_id))["intake"]
    b = (await create_intake(recruiter, job_id))["intake"]
    c = (await create_intake(recruiter, job_id))["intake"]
    await insert_session(
        (await intake_row(a["id"])).interview_id, status="completed", end_reason="candidate_ended"
    )
    session_b = await insert_session((await intake_row(b["id"])).interview_id, status="completed")
    await insert_session((await intake_row(c["id"])).interview_id, status="completed")
    async with session_scope() as db:
        row_b = await intake_row(b["id"])
        db.add(
            IntakeStageResult(
                organization_id=row_b.organization_id,
                intake_id=row_b.id,
                session_id=session_b,
                stage_key="learning",
                stage_number=6,
                status="incomplete",
            )
        )
    totals = (await recruiter.get(f"{API}/intake/stats")).json()["totals"]
    assert totals["completed"] == 3 and totals["incomplete"] == 2 and totals["average_weighted_total"] is None
