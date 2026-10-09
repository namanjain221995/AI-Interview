"""Generic report/scoring paths never touch intake sessions (INTAKE_INTERVIEW.md 8.10, 10.2): the report worker and
the score finaliser skip them, regenerate hands over to ``intake.report``, recompute answers 409, and the sessions
list filters by round type."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.queue import JobContext
from app.db.models import BackgroundJob, IntakeDecision, Report, ReviewDecision, Score
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.intake.demo import create_demo_intake
from app.workers.reports import finalize_scores, generate_report
from tests.integration.reports.factories import create_completed_session

from .eval_helpers import make_demo_rows

pytestmark = pytest.mark.integration


def _ctx(job_type: str) -> JobContext:
    return JobContext(job_id=str(uuid.uuid4()), job_type=job_type, attempt=1, max_attempts=3, payload={})


async def test_report_worker_and_finaliser_skip_intake_sessions(org_factory, redis, storage) -> None:
    org = await org_factory()
    demo = await create_demo_intake(org)
    async with session_scope() as db:
        scores_before = len(
            (await db.scalars(select(Score).where(Score.session_id == demo.session_id))).all()
        )
    result = await finalize_scores({"session_id": str(demo.session_id)}, _ctx("scoring.finalize"))
    assert result == {"skipped": "intake", "intake_report_enqueued": False}
    ctx = _ctx("report.generate")
    result = await generate_report({"session_id": str(demo.session_id)}, ctx)
    assert result["skipped"] == "intake" and result["intake_report_enqueued"] is True
    async with session_scope() as db:
        job = await db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.type == "intake.report", BackgroundJob.status == "queued"
            )
        )
        reports = (await db.scalars(select(Report).where(Report.session_id == demo.session_id))).all()
        scores_after = len((await db.scalars(select(Score).where(Score.session_id == demo.session_id))).all())
    assert job is not None and job.payload["evaluation_id"] == str(demo.evaluation_id)
    assert [r.type for r in reports] == ["recruiter"]  # the generic worker created no candidate report
    assert scores_after == scores_before  # the generic finaliser did not recompute intake scores


async def test_report_worker_skips_intake_session_without_evaluation(org_factory, redis, storage) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    result = await generate_report({"session_id": str(demo.session_id)}, _ctx("report.generate"))
    assert result == {"skipped": "intake", "intake_report_enqueued": False}


async def test_regenerate_recompute_and_round_type_filter(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    pending = await make_demo_rows(org)
    response = await admin.post(f"/api/v1/sessions/{pending.session_id}/reports/regenerate")
    assert response.status_code == 409 and response.json()["error"]["code"] == "evaluation_not_ready"

    demo = await create_demo_intake(org)
    response = await admin.post(f"/api/v1/sessions/{demo.session_id}/reports/regenerate")
    assert response.status_code == 202, response.text
    async with session_scope() as db:
        job = await db.get(BackgroundJob, uuid.UUID(response.json()["job_id"]))
    assert job is not None and job.type == "intake.report"
    assert job.payload["evaluation_id"] == str(demo.evaluation_id)

    response = await admin.post(f"/api/v1/sessions/{demo.session_id}/recompute-scores")
    assert response.status_code == 409 and response.json()["error"]["code"] == "intake_scores_managed"

    listing = await admin.get("/api/v1/sessions", params={"round_type": "intake"})
    assert listing.status_code == 200
    ids = {s["id"] for s in listing.json()["items"]}
    assert {str(demo.session_id), str(pending.session_id)} <= ids
    other = await admin.get(
        "/api/v1/sessions", params=[("round_type", "behavioral"), ("round_type", "coding")]
    )
    assert other.status_code == 200 and str(demo.session_id) not in {s["id"] for s in other.json()["items"]}
    both = await admin.get("/api/v1/sessions", params={"round_type": "behavioral,intake"})
    assert str(demo.session_id) in {s["id"] for s in both.json()["items"]}
    invalid = await admin.get("/api/v1/sessions", params={"round_type": "nope"})
    assert invalid.status_code == 422
    summary = next(s for s in listing.json()["items"] if s["id"] == str(demo.session_id))
    assert summary["recommendation"] == "proceed"  # the mapped intake band shows in the generic list


async def test_generic_review_refuses_intake_sessions(auth_client) -> None:
    """``POST /sessions/{id}/reviews`` answers 409 ``intake_decision_required`` on intake sessions: a plain review
    would leave the intake awaiting review (no ``intake_decisions`` row). The intake decision API still records the
    decision and mirrors it into ``review_decisions``, which the generic history then shows."""
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    demo = await create_demo_intake(org)
    for decision in ("advance", "reject"):
        response = await admin.post(
            f"/api/v1/sessions/{demo.session_id}/reviews",
            json={"decision": decision, "notes": "Looks fine to me."},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "intake_decision_required"
    async with session_scope() as db:
        rows = (
            await db.scalars(select(ReviewDecision).where(ReviewDecision.session_id == demo.session_id))
        ).all()
        intake_rows = (
            await db.scalars(select(IntakeDecision).where(IntakeDecision.intake_id == demo.intake_id))
        ).all()
    assert rows == [] and intake_rows == []

    decided = await admin.post(
        f"/api/v1/intake/interviews/{demo.intake_id}/decisions",
        json={"outcome": "second_interview", "rationale": "Reading was strong; probe the technical answers."},
    )
    assert decided.status_code == 201, decided.text
    history = (await admin.get(f"/api/v1/sessions/{demo.session_id}/reviews")).json()
    assert history["total"] == 1 and history["items"][0]["decision"] == "hold"  # mirrored outcome

    # Other round types keep the generic review flow.
    generic = await create_completed_session(
        org, with_coding=False, with_audio=False, flagged_integrity=False
    )
    response = await admin.post(
        f"/api/v1/sessions/{generic.session_id}/reviews", json={"decision": "advance"}
    )
    assert response.status_code == 201, response.text


async def test_generic_session_views_work_for_intake_sessions(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    org = admin.user.organization_id
    pending = await make_demo_rows(org)  # not evaluated yet: live computation path
    demo = await create_demo_intake(org)
    for sid in (pending.session_id, demo.session_id):
        for path in ("", "/scores", "/questions", "/transcript", "/timeline"):
            response = await admin.get(f"/api/v1/sessions/{sid}{path}")
            assert response.status_code == 200, (path, response.text)
    scores = (await admin.get(f"/api/v1/sessions/{demo.session_id}/scores")).json()
    keys = {c["key"] for c in scores["competencies"]}
    assert {"communication", "technical_reading", "adaptability"} <= keys
    report = await admin.get(f"/api/v1/sessions/{demo.session_id}/reports/recruiter")
    assert report.status_code == 200 and report.json()["content"]["kind"] == "intake"
    assert (await admin.get(f"/api/v1/sessions/{demo.session_id}/reports/candidate")).status_code == 404
