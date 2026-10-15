"""Intake candidate portal (INTAKE_INTERVIEW.md 10.4): overview, details form (MQ1 only when enabled), résumé
review confirm/correct/skip timing, locks, start-guard codes, journey step keys, ``PortalInterview.intake``,
``InviteInfo.round_type``, scoping and rate limits."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.db.models import (
    AuditLog,
    CandidateProfile,
    CandidateResume,
    IntakeInterview,
    Interview,
    InterviewSession,
)
from app.db.session import session_scope
from app.domain.enums import Role
from app.schemas.intake import ResumeReviewRecord
from app.services.intake.resume_review import effective_profile
from tests.integration.intake.admin_helpers import (
    API,
    PROFILE_DOC,
    add_resume,
    candidate_client,
    create_intake,
    fake_sessions,
    give_consent,
    intake_row,
    make_intake_job,
    onboarding_body,
    token_of,
)

pytestmark = pytest.mark.integration


async def _setup(auth_client, app, **create_overrides):  # noqa: ANN202
    recruiter = await auth_client(Role.RECRUITER)
    job_id, round_id = await make_intake_job(recruiter.user.organization_id)
    created = await create_intake(recruiter, job_id, **create_overrides)
    intake = created["intake"]
    http = await candidate_client(app, created["invite_url"], intake["candidate"]["email"])
    return recruiter, http, created, intake["interview"]["id"], intake["id"]


async def test_overview_details_form_and_mq1(auth_client, app) -> None:
    recruiter, http, created, iid, intake_id = await _setup(
        auth_client,
        app,
        settings={"work_authorization": {"enabled": True, "country": "Canada", "ask_sponsorship": True}},
    )
    try:
        info = (await http.get(f"{API}/candidate-access/{token_of(created['invite_url'])}")).json()
        assert info["round_type"] == "intake" and info["duration_minutes"] == 45
        overview = (await http.get(f"{API}/portal/intake/{iid}")).json()
        assert overview["intake_id"] == intake_id and overview["interview_id"] == iid
        assert overview["program_name"] == "AI/ML Training Program" and overview["estimated_minutes"] == 45
        assert overview["duration_range"] == {"min": 35, "max": 50}
        assert [p["title"] for p in overview["parts"]] == [
            "Introduction", "Your experience", "Standard questions", "Reading exercise", "Speaking exercise",
            "Learning and growth", "Your questions",
        ]  # fmt: skip
        assert overview["eligibility"] == {
            "enabled": True, "country": "Canada", "ask_sponsorship": True, "answered": False, "authorized": None,
            "needs_sponsorship": None,
        }  # fmt: skip
        assert (
            overview["missing"] == ["consent", "resume", "onboarding"] and overview["ready_to_start"] is False
        )
        assert overview["onboarding"] is None and overview["resume_review"] == {
            "required": False, "confirmed": False, "skipped": False,
        }  # fmt: skip
        for forbidden in ("score", "band", "claims_to_verify", "evaluation", "notes"):
            assert forbidden not in overview

        early = await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())
        assert early.status_code == 409 and early.json()["error"]["code"] == "consent_required"
        await give_consent(http, iid)
        missing_mq1 = await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())
        assert (
            missing_mq1.status_code == 422 and missing_mq1.json()["error"]["code"] == "eligibility_required"
        )
        assert missing_mq1.json()["error"]["details"]["field"] == "eligibility"
        other = await http.put(
            f"{API}/portal/intake/{iid}/onboarding",
            json=onboarding_body(target_role="other", eligibility={"authorized": True}),
        )
        assert other.status_code == 422 and other.json()["error"]["code"] == "target_role_other_required"
        assert other.json()["error"]["details"]["field"] == "target_role_other"
        bad_field = await http.put(
            f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body(self_rating=0, eligibility={})
        )
        assert bad_field.status_code == 422 and bad_field.json()["error"]["details"]["field"] == "self_rating"

        saved = await http.put(
            f"{API}/portal/intake/{iid}/onboarding",
            json=onboarding_body(eligibility={"authorized": None, "needs_sponsorship": True}),
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["onboarding_submitted"] is True and body["missing"] == ["resume"]
        assert body["onboarding"]["self_rating"] == 6 and "eligibility" not in body["onboarding"]
        assert body["eligibility"]["answered"] is True and body["eligibility"]["needs_sponsorship"] is True
        row = await intake_row(intake_id)
        assert row.eligibility["country"] == "Canada" and row.eligibility["authorized"] is None
        assert "eligibility" not in row.onboarding and row.onboarding_submitted_at is not None
        async with session_scope() as db:
            entry = await db.scalar(select(AuditLog).where(AuditLog.action == "candidate.intake_onboarding"))
        assert entry is not None and entry.details == {
            "resubmitted": False, "eligibility_enabled": True, "eligibility_answered": True,
        }  # fmt: skip

        portal = (await http.get(f"{API}/portal/interviews/{iid}")).json()
        assert "details" in portal["steps_completed"] and "resume-review" not in portal["steps_completed"]
        assert portal["intake"] == {
            "intake_id": intake_id, "title": "Intake Interview", "estimated_minutes": 45, "presence": "orb",
        }  # fmt: skip
        assert portal["requires_resume"] is True
        with fake_sessions() as sessions:
            guard = await http.post(f"{API}/portal/interviews/{iid}/session")
            assert guard.status_code == 409 and guard.json()["error"]["code"] == "intake_resume_required"
            assert sessions.created == []
    finally:
        await http.aclose()


async def test_resume_review_confirm_corrections_and_start(auth_client, app) -> None:
    _recruiter, http, _created, iid, intake_id = await _setup(auth_client, app)
    try:
        await give_consent(http, iid)
        assert (
            await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())
        ).status_code == 200
        interview_id = uuid.UUID(iid)
        resume_id, _ = await add_resume(interview_id, status="extracting", profile_status=None)
        waiting = (await http.get(f"{API}/portal/intake/{iid}/resume-review")).json()
        assert waiting["status"] == "waiting" and waiting["document"] is None and waiting["can_skip"] is False
        assert 170 <= waiting["skip_available_in_s"] <= 180
        not_ready = await http.put(
            f"{API}/portal/intake/{iid}/resume-review", json={"action": "confirm", "document": {"roles": []}}
        )
        assert not_ready.status_code == 409 and not_ready.json()["error"]["code"] == "resume_not_ready"
        early_skip = await http.put(f"{API}/portal/intake/{iid}/resume-review", json={"action": "skip"})
        assert early_skip.status_code == 409 and early_skip.json()["error"]["code"] == "skip_not_allowed"
        assert early_skip.json()["error"]["details"]["skip_available_in_s"] > 0
        no_doc = await http.put(f"{API}/portal/intake/{iid}/resume-review", json={"action": "confirm"})
        assert no_doc.status_code == 422 and no_doc.json()["error"]["code"] == "document_required"
        with fake_sessions() as sessions:
            guard = await http.post(f"{API}/portal/interviews/{iid}/session")
            assert (
                guard.status_code == 409 and guard.json()["error"]["code"] == "intake_resume_review_required"
            )
            assert sessions.created == []

        # The candidate reads the quick profile; the full profile arrives while they edit.
        async with session_scope() as db:
            await db.execute(delete(CandidateResume))
        resume_id, _ = await add_resume(interview_id, status="ready", profile_status=None, quick=True)
        quick_view = (await http.get(f"{API}/portal/intake/{iid}/resume-review")).json()
        assert quick_view["status"] == "ready" and quick_view["profile_stage"] == "quick"
        document = quick_view["document"]
        assert [r["ref"] for r in document["roles"]] == ["r1"] and document["projects"] == []
        assert "claims_to_verify" not in str(quick_view) and "overall_fit" not in str(quick_view)
        async with session_scope() as db:
            interview = await db.get(Interview, interview_id)
            db.add(
                CandidateProfile(
                    candidate_id=interview.candidate_id,
                    resume_id=resume_id,
                    version=5,
                    status="ready",
                    profile=PROFILE_DOC,
                )
            )
        document["roles"][0]["title"] = "Senior Data Analyst"
        document["roles"].append({"ref": "new", "title": "Volunteer tutor", "company": "Code Club"})
        document["skills"] = [*document["skills"], "PyTorch"]
        confirmed = await http.put(
            f"{API}/portal/intake/{iid}/resume-review", json={"action": "confirm", "document": document}
        )
        assert confirmed.status_code == 200, confirmed.text
        view = confirmed.json()
        assert view["confirmed"] is True and view["skipped"] is False and view["confirmed_at"]
        assert [r["ref"] for r in view["document"]["roles"]] == ["r1", "n1"]
        record = ResumeReviewRecord.model_validate((await intake_row(intake_id)).resume_review)
        assert record.source.profile_status == "quick" and record.source.resume_id == resume_id
        assert {(c.path, c.op) for c in record.corrections} == {
            ("roles[r1].title", "changed"),
            ("roles[n1]", "added"),
            ("skills", "added"),
        }
        effective = effective_profile(PROFILE_DOC, record)
        assert [(r["title"], r["source"]) for r in effective["roles"]] == [
            ("Senior Data Analyst", "resume"),
            ("Volunteer tutor", "candidate_correction"),
        ]
        bad_ref = dict(document, roles=[{"ref": "r7", "title": "Ghost", "company": "Nowhere"}])
        unknown = await http.put(
            f"{API}/portal/intake/{iid}/resume-review", json={"action": "confirm", "document": bad_ref}
        )
        assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_ref"

        overview = (await http.get(f"{API}/portal/intake/{iid}")).json()
        assert overview["missing"] == [] and overview["ready_to_start"] is True
        assert overview["resume_review"] == {"required": True, "confirmed": True, "skipped": False}
        steps = (await http.get(f"{API}/portal/interviews/{iid}")).json()["steps_completed"]
        assert {"details", "resume-review", "resume", "consent"} <= set(steps)
        with fake_sessions() as sessions:
            started = await http.post(f"{API}/portal/interviews/{iid}/session")
            assert started.status_code == 200, started.text
            assert len(sessions.created) == 1
        async with session_scope() as db:
            row = await db.get(InterviewSession, sessions.created[0])
            row.status = "live"
        locked = await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())
        assert locked.status_code == 409 and locked.json()["error"]["code"] == "onboarding_locked"
        locked = await http.put(f"{API}/portal/intake/{iid}/resume-review", json={"action": "skip"})
        assert locked.status_code == 409 and locked.json()["error"]["code"] == "onboarding_locked"
        assert (await http.get(f"{API}/portal/intake/{iid}")).json()["onboarding_locked"] is True
        async with session_scope() as db:
            actions = set((await db.scalars(select(AuditLog.action))).all())
        assert "candidate.intake_resume_review" in actions
    finally:
        await http.aclose()


async def test_skip_after_three_minutes_and_reupload(auth_client, app) -> None:
    _recruiter, http, _created, iid, intake_id = await _setup(auth_client, app)
    try:
        await give_consent(http, iid)
        assert (
            await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())
        ).status_code == 200
        old = datetime.now(UTC) - timedelta(seconds=200)
        first_resume, _ = await add_resume(
            uuid.UUID(iid), status="analyzing", profile_status=None, created_at=old
        )
        view = (await http.get(f"{API}/portal/intake/{iid}/resume-review")).json()
        assert view["can_skip"] is True and view["skip_available_in_s"] == 0
        skipped = await http.put(f"{API}/portal/intake/{iid}/resume-review", json={"action": "skip"})
        assert skipped.status_code == 200 and skipped.json()["skipped"] is True
        record = ResumeReviewRecord.model_validate((await intake_row(intake_id)).resume_review)
        assert (
            record.action == "skipped" and record.original is None and record.source.resume_id == first_resume
        )
        assert (await http.get(f"{API}/portal/intake/{iid}")).json()["ready_to_start"] is True
        # A new upload needs a new review.
        await add_resume(uuid.UUID(iid), status="ready", profile_status="ready")
        overview = (await http.get(f"{API}/portal/intake/{iid}")).json()
        assert overview["missing"] == ["resume_review"] and overview["resume_review"]["skipped"] is False
        with fake_sessions():
            guard = await http.post(f"{API}/portal/interviews/{iid}/session")
            assert (
                guard.status_code == 409 and guard.json()["error"]["code"] == "intake_resume_review_required"
            )
    finally:
        await http.aclose()


async def test_resume_not_required_and_preset_role(auth_client, app) -> None:
    _recruiter, http, _created, iid, _intake_id = await _setup(
        auth_client, app, settings={"resume_required": False}, target_role="product_manager_growth"
    )
    try:
        overview = (await http.get(f"{API}/portal/intake/{iid}")).json()
        assert (
            overview["resume_required"] is False
            and overview["target_role_preset"] == "product_manager_growth"
        )
        assert overview["missing"] == ["consent", "onboarding"]
        await give_consent(http, iid)
        saved = (
            await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body(target_role="ai_ml"))
        ).json()
        assert (
            saved["onboarding"]["target_role"] == "product_manager_growth" and saved["ready_to_start"] is True
        )
        portal = (await http.get(f"{API}/portal/interviews/{iid}")).json()
        assert portal["requires_resume"] is False and "resume-review" in portal["steps_completed"]
        with fake_sessions() as sessions:
            assert (await http.post(f"{API}/portal/interviews/{iid}/session")).status_code == 200
            assert len(sessions.created) == 1
    finally:
        await http.aclose()


async def test_disabled_flag_blocks_new_sessions_only(auth_client, app, settings) -> None:
    _recruiter, http, _created, iid, _intake_id = await _setup(
        auth_client, app, settings={"resume_required": False}
    )
    try:
        await give_consent(http, iid)
        assert (
            await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())
        ).status_code == 200
        settings.intake_enabled = False
        with fake_sessions() as sessions:
            blocked = await http.post(f"{API}/portal/interviews/{iid}/session")
            assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "intake_disabled"
            settings.intake_enabled = True
            assert (await http.post(f"{API}/portal/interviews/{iid}/session")).status_code == 200
            settings.intake_enabled = False  # an open session resumes normally while disabled
            resumed = await http.post(f"{API}/portal/interviews/{iid}/session")
            assert resumed.status_code == 200 and len(sessions.created) == 1
        assert (await http.get(f"{API}/portal/intake/{iid}")).json()["error"]["code"] == "intake_disabled"
    finally:
        await http.aclose()


async def test_scoping_not_applicable_lazy_rows_and_rate_limit(
    auth_client, app, candidate_auth_client
) -> None:
    recruiter, http, _created, iid, intake_id = await _setup(auth_client, app)
    try:
        other = await create_intake(recruiter, (await intake_row(intake_id)).job_id)
        foreign = await http.get(f"{API}/portal/intake/{other['intake']['interview']['id']}")
        assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "interview_not_found"
        # G2: a staff session is not a candidate session on candidate routes.
        assert (await recruiter.get(f"{API}/portal/intake/{iid}")).status_code == 401

        # A missing row (legacy invitation) is created lazily.
        async with session_scope() as db:
            await db.execute(delete(IntakeInterview).where(IntakeInterview.id == uuid.UUID(intake_id)))
        lazily = (await http.get(f"{API}/portal/intake/{iid}")).json()
        assert lazily["intake_id"] != intake_id
        async with session_scope() as db:
            rows = (
                await db.scalars(
                    select(IntakeInterview).where(IntakeInterview.interview_id == uuid.UUID(iid))
                )
            ).all()
        assert len(rows) == 1 and rows[0].status == "active"

        await give_consent(http, iid)
        statuses = [
            (await http.put(f"{API}/portal/intake/{iid}/onboarding", json=onboarding_body())).status_code
            for _ in range(31)
        ]
        assert statuses[:30] == [200] * 30 and statuses[30] == 429
    finally:
        await http.aclose()
    plain = await candidate_auth_client()
    not_intake = await plain.get(f"{API}/portal/intake/{plain.candidate.interview_id}")
    assert not_intake.status_code == 409 and not_intake.json()["error"]["code"] == "intake_not_applicable"
    assert (await plain.get(f"{API}/portal/interviews/{plain.candidate.interview_id}")).json()[
        "intake"
    ] is None
