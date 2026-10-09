"""/sessions/{id}/reports* and /sessions/{id}/reviews — release policy, audit, RBAC, org isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, BackgroundJob, Report
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.reports import generate_reports, get_released_candidate_report
from app.services.scoring import recompute_session_scores

from .test_generation import script_narratives

pytestmark = pytest.mark.integration


async def _audit_actions() -> list[str]:
    async with session_scope() as db:
        return [a.action for a in (await db.scalars(select(AuditLog).order_by(AuditLog.id))).all()]


async def test_get_report_versions_and_artifact(auth_client, session_factory, mock_ai) -> None:
    script_narratives(mock_ai)
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    missing = await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports/recruiter")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "report_not_ready"
    await generate_reports(fx.session_id, job_id="a")
    await generate_reports(fx.session_id, job_id="b")

    response = await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports/recruiter")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2 and body["status"] == "ready" and body["recommendation"] == "human_review"
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert body["candidate_report_mode"] == "immediate"
    assert body["content"]["narrative"]["executive_summary"]
    assert body["artifact_media_id"]
    v1 = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports/recruiter?version=1")).json()
    assert v1["version"] == 1
    candidate_report = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports/candidate")).json()
    assert candidate_report["recommendation"] is None and "integrity" not in candidate_report["content"]
    listing = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports")).json()
    assert listing["total"] == 4
    # The artifact is served through the media API (reports:read), downloaded as an attachment.
    url = await recruiter.get(f"/api/v1/media/{body['artifact_media_id']}/url")
    assert url.status_code == 200, url.text
    download = await recruiter.get(url.json()["url"])
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert b"Assistive recommendation" in download.content
    actions = await _audit_actions()
    assert actions.count("report.view") == 3
    assert (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports/bogus")).status_code == 422


async def test_regenerate(auth_client, session_factory, candidate_auth_client) -> None:
    interviewer = await auth_client(Role.INTERVIEWER)
    fx = await session_factory(interviewer.user.organization_id)
    response = await interviewer.post(f"/api/v1/sessions/{fx.session_id}/reports/regenerate")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    again = await interviewer.post(f"/api/v1/sessions/{fx.session_id}/reports/regenerate")
    assert again.json()["job_id"] == job_id  # deduplicated while the first is unfinished
    async with session_scope() as db:
        jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "report.generate"))).all()
    assert len(jobs) == 1 and jobs[0].payload["reason"] == "regenerate"
    assert jobs[0].payload["requested_by"] == str(interviewer.user.id)
    assert "report.regenerate" in await _audit_actions()
    live = await session_factory(interviewer.user.organization_id, status="live")
    conflict = await interviewer.post(f"/api/v1/sessions/{live.session_id}/reports/regenerate")
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "session_not_finished"
    candidate = await candidate_auth_client(organization_id=fx.organization_id)
    assert (await candidate.post(f"/api/v1/sessions/{fx.session_id}/reports/regenerate")).status_code == 403


async def test_release_policy_modes(auth_client, session_factory, mock_ai) -> None:
    script_narratives(mock_ai)
    recruiter = await auth_client(Role.RECRUITER)
    org = recruiter.user.organization_id

    immediate = await session_factory(org, candidate_report_mode="immediate")
    await generate_reports(immediate.session_id)
    response = await recruiter.post(f"/api/v1/sessions/{immediate.session_id}/reports/candidate/release")
    assert response.status_code == 409 and response.json()["error"]["code"] == "release_not_applicable"

    after_review = await session_factory(org, candidate_report_mode="after_review")
    not_ready = await recruiter.post(f"/api/v1/sessions/{after_review.session_id}/reports/candidate/release")
    assert not_ready.status_code == 409 and not_ready.json()["error"]["code"] == "review_required"
    await generate_reports(after_review.session_id)
    blocked = await recruiter.post(f"/api/v1/sessions/{after_review.session_id}/reports/candidate/release")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "review_required"
    async with session_scope() as db:
        assert await get_released_candidate_report(db, after_review.session_id) is None
    review = await recruiter.post(
        f"/api/v1/sessions/{after_review.session_id}/reviews",
        json={"decision": "advance", "notes": "Strong platform depth."},
    )
    assert review.status_code == 201, review.text
    released = await recruiter.post(
        f"/api/v1/sessions/{after_review.session_id}/reports/candidate/release",
        json={"note": "Shared after panel"},
    )
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["released_to_candidate"] is True and body["released_by_id"] == str(recruiter.user.id)
    async with session_scope() as db:
        portal = await get_released_candidate_report(db, after_review.session_id)
    assert portal is not None and portal.id.hex == body["id"].replace("-", "")
    assert "report.release" in await _audit_actions()
    # Idempotent second release.
    assert (
        await recruiter.post(f"/api/v1/sessions/{after_review.session_id}/reports/candidate/release")
    ).status_code == 200

    never = await session_factory(org, candidate_report_mode="never")
    await generate_reports(never.session_id)
    refused = await recruiter.post(f"/api/v1/sessions/{never.session_id}/reports/candidate/release")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "policy_override_required"
    override = await recruiter.post(
        f"/api/v1/sessions/{never.session_id}/reports/candidate/release",
        json={"acknowledge_policy_override": True},
    )
    assert override.status_code == 200 and override.json()["released_to_candidate"] is True
    missing_version = await recruiter.post(
        f"/api/v1/sessions/{never.session_id}/reports/candidate/release",
        json={"acknowledge_policy_override": True, "version": 7},
    )
    assert missing_version.status_code == 404


async def test_reports_org_isolation_and_roles(
    auth_client, session_factory, org_factory, mock_ai, candidate_auth_client
) -> None:
    script_narratives(mock_ai)
    other_org = await org_factory()
    fx = await session_factory(other_org, candidate_report_mode="after_review")
    await generate_reports(fx.session_id)
    recruiter = await auth_client(Role.RECRUITER)
    assert (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reports/recruiter")).status_code == 404
    assert (
        await recruiter.post(f"/api/v1/sessions/{fx.session_id}/reports/candidate/release")
    ).status_code == 404
    assert (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/reviews")).status_code == 404
    assert (
        await recruiter.post(f"/api/v1/sessions/{fx.session_id}/reviews", json={"decision": "hold"})
    ).status_code == 404
    candidate = await candidate_auth_client(organization_id=other_org)
    assert (await candidate.get(f"/api/v1/sessions/{fx.session_id}/reports/candidate")).status_code == 403
    admin = await auth_client(Role.SUPER_ADMIN)
    assert (await admin.get(f"/api/v1/sessions/{fx.session_id}/reports/recruiter")).status_code == 200


async def test_reviews_record_ai_recommendation_and_override(auth_client, session_factory, mock_ai) -> None:
    script_narratives(mock_ai)
    interviewer = await auth_client(Role.INTERVIEWER)
    org = interviewer.user.organization_id
    fx = await session_factory(
        org, flagged_integrity=True, integrity_reviewed=True, candidate_report_mode="after_review"
    )
    async with session_scope() as db:
        await recompute_session_scores(db, fx.session_id, final=True)  # proceed
    empty = (await interviewer.get(f"/api/v1/sessions/{fx.session_id}/reviews")).json()
    assert empty["total"] == 0 and empty["current_ai_recommendation"] == "proceed"
    reject = await interviewer.post(
        f"/api/v1/sessions/{fx.session_id}/reviews",
        json={"decision": "reject", "notes": "Terraform depth insufficient."},
    )
    assert reject.status_code == 201, reject.text
    body = reject.json()
    assert body["ai_recommendation_at_review"] == "proceed" and body["overrides_ai"] is True
    assert body["reviewer"]["full_name"] == interviewer.user.full_name
    await generate_reports(fx.session_id)
    advance = await interviewer.post(
        f"/api/v1/sessions/{fx.session_id}/reviews",
        json={"decision": "advance", "release_candidate_report": True},
    )
    assert advance.status_code == 201
    assert advance.json()["overrides_ai"] is False and advance.json()["candidate_report_released"] is True
    async with session_scope() as db:
        released = (
            await db.scalars(
                select(Report).where(
                    Report.session_id == fx.session_id, Report.released_to_candidate.is_(True)
                )
            )
        ).all()
    assert len(released) == 1
    history = (await interviewer.get(f"/api/v1/sessions/{fx.session_id}/reviews")).json()
    assert [r["decision"] for r in history["items"]] == ["advance", "reject"] and history["total"] == 2
    assert (await _audit_actions()).count("review.create") == 2
    invalid = await interviewer.post(f"/api/v1/sessions/{fx.session_id}/reviews", json={"decision": "hire"})
    assert invalid.status_code == 422
    live = await session_factory(org, status="live")
    conflict = await interviewer.post(
        f"/api/v1/sessions/{live.session_id}/reviews", json={"decision": "hold"}
    )
    assert conflict.status_code == 409


async def test_review_without_scores_has_no_ai_recommendation(
    auth_client, session_factory, candidate_auth_client
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    response = await recruiter.post(
        f"/api/v1/sessions/{fx.session_id}/reviews", json={"decision": "needs_more_information"}
    )
    assert response.status_code == 201
    assert response.json()["ai_recommendation_at_review"] is None and response.json()["overrides_ai"] is False
    candidate = await candidate_auth_client(organization_id=fx.organization_id)
    assert (
        await candidate.post(f"/api/v1/sessions/{fx.session_id}/reviews", json={"decision": "advance"})
    ).status_code == 403


async def test_regenerate_a_failed_session_records_the_reason(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    failed = await session_factory(
        recruiter.user.organization_id, status="failed", end_reason="technical_failure"
    )
    response = await recruiter.post(f"/api/v1/sessions/{failed.session_id}/reports/regenerate")
    assert response.status_code == 202, response.text
    async with session_scope() as db:
        job = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "report.generate"))).one()
        entry = (await db.scalars(select(AuditLog).where(AuditLog.action == "report.regenerate"))).one()
    assert job.payload["reason"] == "regenerate_after_failure"
    assert (
        entry.details["reason"] == "regenerate_after_failure" and entry.details["session_status"] == "failed"
    )
