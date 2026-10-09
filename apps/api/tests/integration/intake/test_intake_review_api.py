"""Intake staff review API (INTAKE_INTERVIEW.md 10.2): detail, timeline, evaluation, re-evaluation, decisions,
analytics — RBAC, organisation isolation, audit rows, conflict and validation codes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, BackgroundJob, InterviewSession, ReviewDecision
from app.db.models.intake import IntakeDecision, IntakeEvaluation
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.intake.demo import create_demo_intake

pytestmark = pytest.mark.integration


async def _demo(org_id: uuid.UUID, **kwargs):  # noqa: ANN202
    return await create_demo_intake(org_id, **kwargs)


async def _audit_actions(org_id: uuid.UUID) -> list[str]:
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(AuditLog.action).where(AuditLog.organization_id == org_id).order_by(AuditLog.id)
            )
        ).all()
    return list(rows)


async def test_detail_timeline_and_evaluation(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    demo = await _demo(org)

    response = await admin.get(f"/api/v1/intake/interviews/{demo.intake_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(demo.intake_id) and body["bucket"] == "awaiting_review"
    assert body["eligibility"] == {
        "enabled": True,
        "country": "the United States",
        "ask_sponsorship": True,
        "authorized": True,
        "needs_sponsorship": False,
        "answered_at": body["eligibility"]["answered_at"],
    }
    assert [s["key"] for s in body["stages"]] == [
        "greeting", "resume", "mandatory", "reading", "topic", "learning", "close",
    ]  # fmt: skip
    kinds = {a["kind"]: a for a in body["activities"]}
    assert set(kinds) == {"reading_aloud", "read_relay", "topic_card"}
    assert kinds["read_relay"]["material"]["title"] == "Overfitting"  # staff may read the passage
    assert kinds["topic_card"]["material"]["bullets"]
    evaluation = body["evaluation"]
    assert evaluation["band"] == "accept_with_notes" and evaluation["weighted_total"] == "4.1300"
    assert [c["key"] for c in evaluation["competencies"]] == [
        "communication", "technical_reading", "structured_answer", "technical", "trainability", "composure",
        "adaptability",
    ]  # fmt: skip
    reading = evaluation["competencies"][1]
    assert reading["score"] == "4.4" and reading["weight"] == "0.20" and reading["anchors"]["5"]
    assert all(item["resolution"] is None for item in body["review_items"])
    assert body["report"]["version"] == 1 and body["report"]["status"] == "ready"
    assert body["integrity"] == {"flags": 1, "unreviewed": 1}
    assert body["evaluation_ref"]["status"] == "ready"
    assert "intake.view" in await _audit_actions(org)

    timeline = await admin.get(f"/api/v1/intake/interviews/{demo.intake_id}/timeline")
    assert timeline.status_code == 200, timeline.text
    stages = timeline.json()["stages"]
    greeting = stages[0]
    assert greeting["key"] == "greeting" and greeting["items"][0]["item_key"] == "greeting.intro"
    exchange = greeting["items"][0]["exchanges"][0]
    assert exchange["answer"]["text"].startswith("Sure.") and exchange["answer"]["duration_ms"] > 0
    reading_items = {i["item_key"]: i for i in stages[3]["items"]}
    assert reading_items["reading.drill_b"]["exchanges"][0]["answer"]["text"].startswith("So the passage")
    assert "transcript.view" in await _audit_actions(org)

    detail = await admin.get(f"/api/v1/intake/interviews/{demo.intake_id}/evaluation")
    assert detail.status_code == 200 and detail.json()["version"] == 1
    assert detail.json()["summary"]["drill_a"]["metrics"]["completeness"] == 1.0
    assert detail.json()["summary"]["drill_a"]["events"]  # alignment events for highlighting
    missing = await admin.get(f"/api/v1/intake/interviews/{demo.intake_id}/evaluation?version=9")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "evaluation_not_found"


async def test_rbac_and_organisation_isolation(auth_client, org_factory, candidate_auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    demo = await _demo(admin.user.organization_id)
    for role in (Role.RECRUITER, Role.INTERVIEWER):
        staff = await auth_client(role)
        for path in ("", "/timeline", "/evaluation", "/decisions"):
            response = await staff.get(f"/api/v1/intake/interviews/{demo.intake_id}{path}")
            assert response.status_code == 200, (role, path, response.text)
        assert (await staff.get("/api/v1/intake/analytics")).status_code == 200
    other = await auth_client(Role.ADMIN, organization_id=await org_factory())
    response = await other.get(f"/api/v1/intake/interviews/{demo.intake_id}")
    assert response.status_code == 404 and response.json()["error"]["code"] == "intake_not_found"
    response = await other.post(
        f"/api/v1/intake/interviews/{demo.intake_id}/decisions",
        json={"outcome": "accept", "rationale": "Strong candidate overall."},
    )
    assert response.status_code == 404
    candidate = await candidate_auth_client(organization_id=admin.user.organization_id)
    assert (await candidate.get(f"/api/v1/intake/interviews/{demo.intake_id}")).status_code == 403
    assert (await candidate.get("/api/v1/intake/analytics")).status_code == 403


async def test_decisions_are_mirrored_audited_and_flag_overrides(auth_client) -> None:
    reviewer = await auth_client(Role.INTERVIEWER)
    org = reviewer.user.organization_id
    demo = await _demo(org)
    detail = (await reviewer.get(f"/api/v1/intake/interviews/{demo.intake_id}")).json()
    item_id = detail["review_items"][0]["id"]

    first = await reviewer.post(
        f"/api/v1/intake/interviews/{demo.intake_id}/decisions",
        json={
            "outcome": "accept_with_notes",
            "rationale": "Clear relay and solid pipeline work; plan extra statistics practice.",
            "review_items": [{"item_id": item_id, "resolution": "dismissed", "note": "checked"}],
            "competency_notes": {"technical": "Ask about modelling in week one."},
        },
    )
    assert first.status_code == 201, first.text
    decision = first.json()
    assert decision["overrides_ai"] is False and decision["ai_band_at_decision"] == "accept_with_notes"
    assert decision["ai_total_at_decision"] == "4.1300"
    assert decision["reviewer"]["id"] == str(reviewer.user.id)
    async with session_scope() as db:
        mirror = await db.get(ReviewDecision, uuid.UUID(decision["review_decision_id"]))
        assert mirror is not None and mirror.decision == "advance" and mirror.session_id == demo.session_id
    actions = await _audit_actions(org)
    assert "review.create" in actions and "intake.decision.create" in actions

    second = await reviewer.post(
        f"/api/v1/intake/interviews/{demo.intake_id}/decisions",
        json={
            "outcome": "reject",
            "rationale": "After a second look the commitment does not fit the cohort.",
        },
    )
    assert second.status_code == 201 and second.json()["overrides_ai"] is True
    listing = (await reviewer.get(f"/api/v1/intake/interviews/{demo.intake_id}/decisions")).json()
    assert listing["total"] == 2 and listing["items"][0]["outcome"] == "reject"
    detail = (await reviewer.get(f"/api/v1/intake/interviews/{demo.intake_id}")).json()
    assert detail["bucket"] == "rejected" and detail["decision"]["outcome"] == "reject"
    # resolutions come from the newest decision (it resolved nothing)
    assert all(item["resolution"] is None for item in detail["review_items"])
    queue = await reviewer.get("/api/v1/sessions", params={"needs_review": "true"})
    assert str(demo.session_id) not in {s["id"] for s in queue.json()["items"]}


async def test_decision_validation_codes(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    demo = await _demo(admin.user.organization_id)
    url = f"/api/v1/intake/interviews/{demo.intake_id}/decisions"
    cases = [
        ({"outcome": "accept", "rationale": "short"}, "rationale_required"),
        ({"outcome": "request_evidence", "rationale": "Need the measured baseline numbers."}, "requested_evidence_required"),
        (
            {"outcome": "accept", "rationale": "Looks fine to me overall.", "review_items": [
                {"item_id": "ri_0000000000", "resolution": "confirmed"}]},
            "unknown_review_item",
        ),
    ]  # fmt: skip
    for body, code in cases:
        response = await admin.post(url, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code
    response = await admin.post(
        url,
        json={"outcome": "accept", "rationale": "Looks fine to me overall.", "competency_notes": {"x": "y"}},
    )
    assert response.status_code == 422
    response = await admin.post(url, json={"outcome": "promote", "rationale": "Looks fine to me overall."})
    assert response.status_code == 422
    ok = await admin.post(
        url,
        json={
            "outcome": "request_evidence",
            "rationale": "Need the measured baseline numbers.",
            "requested_evidence": ["Before/after query timings for the 40% claim"],
        },
    )
    assert ok.status_code == 201 and ok.json()["requested_evidence"] == [
        "Before/after query timings for the 40% claim"
    ]
    async with session_scope() as db:
        assert await db.scalar(select(func.count()).select_from(IntakeDecision)) == 1


async def test_conflicts_while_live_or_evaluating(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    demo = await _demo(admin.user.organization_id)
    url = f"/api/v1/intake/interviews/{demo.intake_id}"
    body = {"outcome": "accept", "rationale": "Strong across the board."}
    async with session_scope() as db:
        evaluation = IntakeEvaluation(
            organization_id=demo.organization_id,
            intake_id=demo.intake_id,
            session_id=demo.session_id,
            version=9,
            status="running",
            reason="reevaluate",
            kit_version="v1",
            scoring_version="intake-scoring-1",
            started_at=datetime.now(UTC),
        )
        db.add(evaluation)
    for path, payload in (("/decisions", body), ("/evaluate", None)):
        response = await admin.post(url + path, json=payload)
        assert response.status_code == 409 and response.json()["error"]["code"] == "evaluation_running"
    async with session_scope() as db:
        row = await db.scalar(select(IntakeEvaluation).where(IntakeEvaluation.version == 9))
        assert row is not None
        row.started_at = datetime.now(UTC) - timedelta(minutes=20)  # stale: no longer blocks
    assert (await admin.post(url + "/decisions", json=body)).status_code == 201
    async with session_scope() as db:
        session = await db.get(InterviewSession, demo.session_id)
        assert session is not None
        session.status = "live"
    for path, payload in (("/decisions", body), ("/evaluate", None)):
        response = await admin.post(url + path, json=payload)
        assert response.status_code == 409 and response.json()["error"]["code"] == "session_not_finished"


async def test_reevaluate_queues_a_new_version(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    demo = await _demo(recruiter.user.organization_id)
    response = await recruiter.post(f"/api/v1/intake/interviews/{demo.intake_id}/evaluate")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["evaluation_version"] == 2 and body["status"] == "queued"
    async with session_scope() as db:
        job = await db.get(BackgroundJob, uuid.UUID(body["job_id"]))
    assert job is not None and job.type == "intake.evaluate"
    assert job.payload["reason"] == "reevaluate" and job.dedupe_key == f"intake.evaluate:{demo.session_id}:v2"
    assert "intake.evaluate" in await _audit_actions(recruiter.user.organization_id)
    repeat = await recruiter.post(f"/api/v1/intake/interviews/{demo.intake_id}/evaluate")
    assert (
        repeat.status_code == 202 and repeat.json()["job_id"] == body["job_id"]
    )  # deduplicated while queued


async def test_analytics(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    decided = await _demo(org)
    await _demo(org, variant="incomplete")
    await admin.post(
        f"/api/v1/intake/interviews/{decided.intake_id}/decisions",
        json={"outcome": "reject", "rationale": "Commitment does not fit this cohort's schedule."},
    )
    response = await admin.get("/api/v1/intake/analytics", params={"days": 30})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["funnel"]["invitations"] == 2 and body["funnel"]["evaluated"] == 2
    assert body["funnel"]["decided"] == 1 and body["funnel"]["completed"] == 2
    bands = {b["band"]: b["count"] for b in body["bands"]}
    assert bands["accept_with_notes"] == 1 and bands["insufficient_evidence"] == 1
    assert {d["outcome"]: d["count"] for d in body["decisions"]}["reject"] == 1
    assert body["overrides"] == {"count": 1, "rate": 1.0}
    averages = {c["key"]: c for c in body["competency_averages"]}
    assert averages["communication"]["average"] == "4.0000" and averages["communication"]["scored"] == 2
    assert averages["structured_answer"]["insufficient"] == 1
    weaknesses = {w["key"] for w in body["technical_weaknesses"]}
    assert "claim_team_level" in weaknesses
    assert body["reviewers"][0]["decisions"] == 1
    stage = {s["stage"]: s for s in body["stage_completion"]}
    assert stage["topic"]["incomplete"] == 1 and stage["greeting"]["completed"] == 2
    assert body["by_target_role"][0]["target_role"] == "ai_ml" and body["by_target_role"][0]["count"] == 2
    accepted = await admin.get("/api/v1/intake/analytics", params={"bucket": "rejected"})
    assert accepted.json()["funnel"]["invitations"] == 1


async def test_feature_flag_disables_review_routes(auth_client, settings) -> None:
    admin = await auth_client(Role.ADMIN)
    demo = await _demo(admin.user.organization_id)
    settings.intake_enabled = False
    response = await admin.get(f"/api/v1/intake/interviews/{demo.intake_id}")
    assert response.status_code == 404 and response.json()["error"]["code"] == "intake_disabled"
    assert (await admin.get("/api/v1/intake/analytics")).status_code == 404
