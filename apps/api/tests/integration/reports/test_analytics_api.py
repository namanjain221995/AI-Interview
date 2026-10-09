"""/api/v1/analytics/* — org-scoped KPIs, funnel, distributions, rates, model usage."""

from __future__ import annotations

import pytest

from app.db.session import session_scope
from app.domain.enums import Role
from app.services.reports import generate_reports
from app.services.scoring import recompute_session_scores

from .test_generation import script_narratives

pytestmark = pytest.mark.integration


async def _setup(auth_client, session_factory, org_factory, mock_ai):  # noqa: ANN001, ANN202
    script_narratives(mock_ai)
    recruiter = await auth_client(Role.RECRUITER)
    org = recruiter.user.organization_id
    strong = await session_factory(org, flagged_integrity=True, integrity_reviewed=True)  # proceed
    flagged = await session_factory(org, flagged_integrity=True)  # human review
    live = await session_factory(org, status="live", with_coding=False)
    other = await session_factory(await org_factory())
    for fx in (strong, flagged, other):
        async with session_scope() as db:
            await recompute_session_scores(db, fx.session_id, final=True)
    await generate_reports(strong.session_id)
    review = await recruiter.post(
        f"/api/v1/sessions/{strong.session_id}/reviews", json={"decision": "reject"}
    )
    assert review.status_code == 201
    return recruiter, strong, flagged, live, other


async def test_overview_and_funnel(auth_client, session_factory, org_factory, mock_ai) -> None:
    recruiter, strong, flagged, live, other = await _setup(auth_client, session_factory, org_factory, mock_ai)
    response = await recruiter.get("/api/v1/analytics/overview")
    assert response.status_code == 200, response.text
    o = response.json()
    assert o["period_days"] == 30
    assert o["interviews_invited"] == 3 and o["interviews_completed"] == 2
    assert o["sessions_live"] == 1 and o["sessions_started"] == 3 and o["sessions_completed"] == 2
    assert o["completed_this_week"] == 2 and o["awaiting_review"] == 1
    assert o["completion_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert o["recommendation_distribution"] == {"proceed": 1, "human_review": 1}
    assert 60 < o["average_overall_score"] < 80 and o["average_confidence"] > 0.5
    assert o["integrity_unreviewed"] == 2  # flagged session + live session (other org excluded)
    assert o["integrity_events"] == 9  # per session: tab_hidden (count 2) + one phone signal
    assert o["review_decision_distribution"] == {"reject": 1} and o["ai_override_rate"] == 1.0
    assert o["average_duration_seconds"] == 1720.0
    assert sum(d["count"] for d in o["sessions_by_day"]) == 3

    funnel = (await recruiter.get("/api/v1/analytics/funnel")).json()
    stages = {s["stage"]: s["count"] for s in funnel["stages"]}
    assert stages == {
        "invited": 3,
        "opened": 3,
        "verified": 3,
        "started": 3,
        "completed": 2,
        "reviewed": 1,
        "report_released": 1,
    }
    completed = next(s for s in funnel["stages"] if s["stage"] == "completed")
    assert completed["rate_from_previous"] == pytest.approx(2 / 3, abs=1e-3)
    narrowed = (await recruiter.get(f"/api/v1/analytics/funnel?job_id={strong.job_id}")).json()
    assert narrowed["stages"][0]["count"] == 1


async def test_distributions_competencies_completion(
    auth_client, session_factory, org_factory, mock_ai
) -> None:
    recruiter, strong, flagged, live, other = await _setup(auth_client, session_factory, org_factory, mock_ai)
    scores = (await recruiter.get("/api/v1/analytics/scores")).json()
    assert scores["overall"]["count"] == 2 and sum(scores["overall"]["buckets"]) == 2
    assert len(scores["bucket_labels"]) == 10 and scores["bucket_labels"][-1] == "90-100"
    assert len(scores["by_job"]) == 2 and all(
        g["label"] == "Senior DevOps Engineer" for g in scores["by_job"]
    )
    assert all(g["label"] == "Combined AI Round" for g in scores["by_round"])

    comps = (await recruiter.get("/api/v1/analytics/competencies")).json()
    k8s = next(i for i in comps["items"] if i["competency_key"] == "kubernetes")
    assert k8s["sessions"] == 2 and k8s["evidence_count"] == 10 and k8s["sufficient_rate"] == 1.0
    assert 7 < k8s["average_score"] < 9

    completion = (await recruiter.get("/api/v1/analytics/completion")).json()
    assert completion["overall"]["started"] == 3 and completion["overall"]["completed"] == 2
    assert completion["overall"]["median_duration_seconds"] == 1720.0
    assert completion["status_counts"] == {"completed": 2, "live": 1}
    assert completion["end_reasons"] == {"sufficient_evidence": 2}


async def test_integrity_hints_model_usage(auth_client, session_factory, org_factory, mock_ai) -> None:
    recruiter, strong, flagged, live, other = await _setup(auth_client, session_factory, org_factory, mock_ai)
    integrity = (await recruiter.get("/api/v1/analytics/integrity")).json()
    assert integrity["note"].startswith("Integrity signals are automated observations")
    assert integrity["sessions"] == 3 and integrity["sessions_with_flags"] == 3
    assert integrity["by_type"] == {"tab_hidden": 6, "possible_phone_visible": 3}
    assert integrity["review_status"] == {"unreviewed": 5, "noted": 1}

    hints = (await recruiter.get("/api/v1/analytics/hints")).json()
    assert hints["coding_sessions"] == 2 and hints["sessions_with_hints"] == 2
    assert hints["average_hints"] == 1.0 and hints["distribution"] == {"0": 0, "1": 2, "2": 0, "3": 0}
    assert hints["by_level"] == {"1": 2} and hints["by_problem"][0]["title"] == "Noisiest Service in the Logs"

    usage = (await recruiter.get("/api/v1/analytics/model-usage")).json()
    by_task = {i["task"]: i for i in usage["items"]}
    assert by_task["turn_decision"]["calls"] == 6 and by_task["turn_decision"]["errors"] == 0
    assert by_task["turn_decision"]["p50_ms"] == pytest.approx(1100.0)
    assert by_task["answer_evaluation"]["errors"] == 3 and by_task["answer_evaluation"]["error_rate"] == 0.5
    assert usage["totals"]["calls"] == 12 and usage["totals"]["prompt_tokens"] == 12000


async def test_analytics_rbac_and_scoping(
    auth_client, session_factory, org_factory, candidate_auth_client
) -> None:
    other_org = await org_factory()
    await session_factory(other_org)
    interviewer = await auth_client(Role.INTERVIEWER)
    o = (await interviewer.get("/api/v1/analytics/overview?days=7")).json()
    assert o["period_days"] == 7 and o["sessions_completed"] == 0
    assert (
        await interviewer.get(f"/api/v1/analytics/overview?organization_id={other_org}")
    ).status_code == 403
    assert (await interviewer.get("/api/v1/analytics/overview?days=0")).status_code == 422
    admin = await auth_client(Role.SUPER_ADMIN)
    scoped = (await admin.get(f"/api/v1/analytics/overview?organization_id={other_org}")).json()
    assert scoped["sessions_completed"] == 1
    candidate = await candidate_auth_client()
    assert (await candidate.get("/api/v1/analytics/overview")).status_code == 403


async def test_every_endpoint_uses_the_same_period_rule(auth_client, session_factory, db_session) -> None:
    """A session is in the period when it started there (set up there, if never
    started): created 40 days ago but held 20 days ago counts everywhere; one
    held 40 days ago counts nowhere, even if a later write bumped updated_at."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.db.models import InterviewSession

    recruiter = await auth_client(Role.RECRUITER)
    org = recruiter.user.organization_id
    held = await session_factory(org, candidate_name="Held Recently")
    old = await session_factory(org, candidate_name="Held Long Ago")
    for fx in (held, old):
        async with session_scope() as db:
            await recompute_session_scores(db, fx.session_id, final=True)
    now = datetime.now(UTC)
    day20, day40 = now - timedelta(days=20), now - timedelta(days=40)
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == held.session_id)
        .values(created_at=day40, started_at=day20, ended_at=day20 + timedelta(minutes=30))
    )
    await db_session.execute(
        update(InterviewSession)
        .where(InterviewSession.id == old.session_id)
        .values(created_at=day40, started_at=day40, ended_at=None, updated_at=now)
    )
    await db_session.commit()

    async def get(name: str) -> dict:
        response = await recruiter.get(f"/api/v1/analytics/{name}", params={"days": 30})
        assert response.status_code == 200, response.text
        return response.json()

    o = await get("overview")
    assert o["sessions_started"] == 1 and o["sessions_completed"] == 1 and o["completion_rate"] == 1.0
    assert o["completed_this_week"] == 0
    assert [d["date"] for d in o["sessions_by_day"]] == [day20.date().isoformat()]
    assert o["integrity_events"] == 3  # the held session's signals only
    assert (await get("scores"))["overall"]["count"] == 1
    k8s = next(i for i in (await get("competencies"))["items"] if i["competency_key"] == "kubernetes")
    assert k8s["sessions"] == 1
    completion = await get("completion")
    assert completion["overall"]["started"] == 1 and completion["status_counts"] == {"completed": 1}
    integrity = await get("integrity")
    assert integrity["sessions"] == 1 and integrity["events_total"] == 3
    assert (await get("hints"))["coding_sessions"] == 1
    # Present-state KPIs ignore the period: both sessions still wait for a decision.
    assert o["awaiting_review"] == 2
