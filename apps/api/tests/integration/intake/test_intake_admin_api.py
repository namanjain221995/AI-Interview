"""Intake staff admin API (INTAKE_INTERVIEW.md 10.1): single/draft/send/cancel/regenerate, batch with partial
failures, links shown once, settings, config, RBAC, cross-org 404s, rate limit, feature flag, audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, Candidate, IntakeInterview, IntakeOrgSettings, Interview
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.intake.kit import load_intake_kit
from tests.integration.intake.admin_helpers import (
    API,
    candidate_client,
    create_body,
    create_intake,
    intake_round_config,
    intake_row,
    make_intake_job,
)

pytestmark = pytest.mark.integration


async def audit_rows(action: str) -> list[AuditLog]:
    async with session_scope() as db:
        return list((await db.scalars(select(AuditLog).where(AuditLog.action == action))).all())


# ===================================================================================== single


async def test_single_create_sends_once_and_freezes_settings(auth_client, settings) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, round_id = await make_intake_job(org_id)
    response = await recruiter.post(
        f"{API}/intake/interviews",
        json=create_body(
            job_id,
            candidate={"full_name": "Asha Rao", "email": "Asha@Example.test", "phone": "+91 98765 43210"},
            target_role="data_analyst",
            settings={
                "duration_minutes": 40,
                "max_reconnects": 3,
                "presence": "none",
                "require_access_code": True,
            },
            notes="Referred by the campus team.",
        ),
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["invite_url"].startswith(f"{settings.public_base_url}/i/")
    assert body["access_code"] and body["expires_at"]
    intake = body["intake"]
    assert intake["status"] == "active" and intake["bucket"] == "active"
    assert intake["candidate"]["email"] == "asha@example.test" and intake["target_role"] == "data_analyst"
    assert intake["round"]["id"] == str(round_id) and intake["interview"]["status"] == "invited"
    assert intake["session"] is None and intake["evaluation"] is None and intake["decision"] is None
    assert intake["onboarding_submitted"] is False and intake["review_items_open"] == 0

    row = await intake_row(intake["id"])
    kit = load_intake_kit()
    assert row.settings["duration_minutes"] == 40 and row.settings["max_reconnects"] == 3
    assert row.settings["presence"] == "none" and row.settings["require_access_code"] is True
    assert row.settings["kit_version"] == kit.version and row.settings["kit_sha256"] == kit.source.sha256
    assert row.settings["program_name"] == "AI/ML Training Program"
    assert row.settings["work_authorization"]["enabled"] is False
    assert row.sent_at is not None and row.created_by_id == recruiter.user.id and row.notes
    async with session_scope() as db:
        interview = await db.get(Interview, row.interview_id)
        assert interview.policy_snapshot["integrity"]["max_reconnects"] == 3
        assert interview.round_id == round_id
        count = await db.scalar(
            select(func.count())
            .select_from(IntakeInterview)
            .where(IntakeInterview.interview_id == interview.id)
        )
        assert count == 1  # intake_managed: the generic hook did not insert a second row
        candidate = await db.get(Candidate, row.candidate_id)
        assert candidate.source == "intake" and candidate.phone == "+91 98765 43210"

    [entry] = await audit_rows("intake.create")
    assert entry.details["sent"] is True and entry.details["access_code_issued"] is True
    assert body["access_code"] not in str(entry.details) and "token" not in str(entry.details)

    # Links are shown once: list and update responses never carry them.
    listing = (await recruiter.get(f"{API}/intake/interviews")).json()
    assert (
        listing["total"] == 1 and "invite_url" not in str(listing) and body["access_code"] not in str(listing)
    )

    # The same candidate cannot hold two open invitations for the job.
    again = await recruiter.post(
        f"{API}/intake/interviews",
        json=create_body(job_id, candidate_id=intake["candidate"]["id"], candidate=None),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "interview_exists"


async def test_create_validation_and_job_errors(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, round_id = await make_intake_job(org_id, other_round=True)
    no_intake_job, _ = await make_intake_job(org_id, with_intake_round=False, other_round=True)
    closed_job, _ = await make_intake_job(org_id, status="closed")

    async def post(body: dict) -> tuple[int, dict]:
        response = await recruiter.post(f"{API}/intake/interviews", json=body)
        return response.status_code, response.json()

    code, data = await post(create_body(uuid.uuid4()))
    assert (code, data["error"]["code"]) == (404, "job_not_found")
    code, data = await post(create_body(no_intake_job))
    assert (code, data["error"]["code"]) == (409, "job_has_no_intake_round")
    async with session_scope() as db:
        from app.db.models import Round

        technical = await db.scalar(
            select(Round.id).where(Round.job_id == job_id, Round.round_type != "intake")
        )
    code, data = await post(create_body(job_id, round_id=str(technical)))
    assert (code, data["error"]["code"]) == (409, "job_has_no_intake_round")
    code, data = await post(create_body(closed_job))
    assert (code, data["error"]["code"]) == (409, "job_not_open")
    code, data = await post({"job_id": str(job_id), "settings": {}})
    assert (code, data["error"]["code"]) == (422, "candidate_or_candidate_id_required")
    assert data["error"]["details"]["field"] == "candidate"
    code, data = await post(create_body(job_id, settings={"work_authorization": {"enabled": True}}))
    assert (code, data["error"]["code"]) == (422, "work_authorization_country_required")
    assert data["error"]["details"]["field"] == "settings.work_authorization.country"
    code, data = await post(create_body(job_id, settings={"duration_minutes": 30}))
    assert (code, data["error"]["code"]) == (422, "validation_failed")
    assert data["error"]["details"]["field"] == "settings.duration_minutes"
    code, data = await post(create_body(job_id, settings={"assigned_reviewer_id": str(uuid.uuid4())}))
    assert (code, data["error"]["code"]) == (422, "invalid_reviewer")
    # The default round is the job's first active intake round (a technical round comes first here).
    created = await create_intake(recruiter, job_id)
    assert created["intake"]["round"]["id"] == str(round_id)

    async with session_scope() as db:
        archived = Candidate(
            organization_id=org_id,
            full_name="Old Applicant",
            email="old@example.test",
            archived_at=datetime.now(UTC),
        )
        db.add(archived)
    code, data = await post(create_body(job_id, candidate={"full_name": "Old", "email": "old@example.test"}))
    assert (code, data["error"]["code"]) == (409, "candidate_archived")


async def test_work_authorization_precedence(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    org_id = admin.user.organization_id
    response = await admin.put(
        f"{API}/intake/settings",
        json={
            "program_name": "Data Academy",
            "work_authorization": {"enabled": True, "country": "Canada", "ask_sponsorship": False},
            "defaults": {"duration_minutes": 50, "max_reconnects": 4, "expires_in_hours": 48},
        },
    )
    assert response.status_code == 200, response.text
    job_id, _ = await make_intake_job(org_id)
    inherited = await create_intake(admin, job_id)
    row = await intake_row(inherited["intake"]["id"])
    assert row.settings["work_authorization"] == {
        "enabled": True, "country": "Canada", "ask_sponsorship": False, "variant": "default",
    }  # fmt: skip
    assert row.settings["program_name"] == "Data Academy"
    assert (row.settings["duration_minutes"], row.settings["max_reconnects"], row.settings["expires_in_hours"]) == (
        50, 4, 48,
    )  # fmt: skip
    explicit = await create_intake(
        admin, job_id, settings={"work_authorization": {"enabled": False}, "duration_minutes": 35}
    )
    row = await intake_row(explicit["intake"]["id"])
    assert row.settings["work_authorization"]["enabled"] is False and row.settings["duration_minutes"] == 35
    round_job, _ = await make_intake_job(
        org_id,
        round_config={
            **intake_round_config(),
            "intake": {"work_authorization": {"enabled": True, "country": "Ireland"}},
        },
    )
    from_round = await create_intake(admin, round_job)
    row = await intake_row(from_round["intake"]["id"])
    assert row.settings["work_authorization"]["country"] == "Ireland"


# ====================================================================== drafts, send, update, cancel


async def test_draft_lifecycle(auth_client, app) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    reviewer = await auth_client(Role.INTERVIEWER, organization_id=org_id)
    job_id, _ = await make_intake_job(org_id)
    other_job, other_round = await make_intake_job(org_id, title="Second program")
    draft = await create_intake(recruiter, job_id, send=False, title="Spring cohort intake")
    assert draft["invite_url"] is None and draft["access_code"] is None and draft["expires_at"] is None
    intake = draft["intake"]
    assert intake["status"] == "draft" and intake["bucket"] == "drafts" and intake["interview"] is None
    intake_id = intake["id"]

    patched = await recruiter.patch(
        f"{API}/intake/interviews/{intake_id}",
        json={
            "title": "Autumn cohort intake",
            "job_id": str(other_job),
            "settings": {"duration_minutes": 50, "assigned_reviewer_id": str(reviewer.user.id)},
            "target_role": "ai_ml",
        },
    )
    assert patched.status_code == 200, patched.text
    patched_body = patched.json()
    assert patched_body["title"] == "Autumn cohort intake" and patched_body["job"]["id"] == str(other_job)
    assert patched_body["round"]["id"] == str(other_round)
    assert patched_body["assigned_reviewer"]["id"] == str(reviewer.user.id)
    assert (await intake_row(intake_id)).settings["duration_minutes"] == 50

    sent = await recruiter.post(f"{API}/intake/interviews/{intake_id}/send", json={"expires_in_hours": 24})
    assert sent.status_code == 200, sent.text
    sent_body = sent.json()
    assert sent_body["invite_url"] and sent_body["intake"]["status"] == "active"
    row = await intake_row(intake_id)
    assert row.interview_id is not None and row.settings["expires_in_hours"] == 24
    again = await recruiter.post(f"{API}/intake/interviews/{intake_id}/send")
    assert again.status_code == 409 and again.json()["error"]["code"] == "intake_not_draft"

    not_draft = await recruiter.patch(f"{API}/intake/interviews/{intake_id}", json={"title": "x"})
    assert not_draft.status_code == 409 and not_draft.json()["error"]["code"] == "intake_not_draft"
    allowed = await recruiter.patch(
        f"{API}/intake/interviews/{intake_id}",
        json={"notes": "Call after 5 pm", "notify_on_completion": False, "assigned_reviewer_id": None},
    )
    assert allowed.status_code == 200 and allowed.json()["assigned_reviewer"] is None
    row = await intake_row(intake_id)
    assert row.notes == "Call after 5 pm" and row.settings["notify_on_completion"] is False

    # The new link works for the candidate; the regenerated one replaces it.
    candidate = await candidate_client(
        app, sent_body["invite_url"], sent_body["intake"]["candidate"]["email"]
    )
    await candidate.aclose()
    rotated = await recruiter.post(f"{API}/intake/interviews/{intake_id}/regenerate-link", json={})
    assert rotated.status_code == 200 and rotated.json()["invite_url"] != sent_body["invite_url"]
    old_token = sent_body["invite_url"].rsplit("/i/", 1)[1]
    assert (await recruiter.get(f"{API}/candidate-access/{old_token}")).status_code == 404

    cancelled = await recruiter.post(
        f"{API}/intake/interviews/{intake_id}/cancel", json={"reason": "Withdrew"}
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["bucket"] == "cancelled"
    async with session_scope() as db:
        interview = await db.get(Interview, row.interview_id)
        assert interview.status == "cancelled"
    assert (await recruiter.post(f"{API}/intake/interviews/{intake_id}/cancel")).status_code == 200
    assert len(await audit_rows("intake.cancel")) == 1
    regen = await recruiter.post(f"{API}/intake/interviews/{intake_id}/regenerate-link")
    assert regen.status_code == 409 and regen.json()["error"]["code"] == "intake_cancelled"
    locked = await recruiter.patch(f"{API}/intake/interviews/{intake_id}", json={"target_role": "other"})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "intake_cancelled"
    for action in ("intake.update", "intake.send", "intake.regenerate_link"):
        assert await audit_rows(action), action

    draft2 = await create_intake(recruiter, job_id, send=False)
    unsent = await recruiter.post(f"{API}/intake/interviews/{draft2['intake']['id']}/regenerate-link")
    assert unsent.status_code == 409 and unsent.json()["error"]["code"] == "intake_not_sent"
    dropped = await recruiter.post(f"{API}/intake/interviews/{draft2['intake']['id']}/cancel")
    assert dropped.status_code == 200 and dropped.json()["status"] == "cancelled"


async def test_target_role_locks_after_onboarding(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    job_id, _ = await make_intake_job(recruiter.user.organization_id)
    created = await create_intake(recruiter, job_id)
    intake_id = created["intake"]["id"]
    ok = await recruiter.patch(f"{API}/intake/interviews/{intake_id}", json={"target_role": "ai_ml"})
    assert ok.status_code == 200 and ok.json()["target_role"] == "ai_ml"
    async with session_scope() as db:
        row = await db.get(IntakeInterview, uuid.UUID(intake_id))
        row.onboarding_submitted_at = datetime.now(UTC)
        row.onboarding = {"submitted": True}
    locked = await recruiter.patch(
        f"{API}/intake/interviews/{intake_id}", json={"target_role": "data_analyst"}
    )
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "target_role_locked"


# ========================================================================================= batch


async def test_batch_partial_failures_and_links_once(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, _ = await make_intake_job(org_id)
    existing = await create_intake(
        recruiter, job_id, candidate={"full_name": "Open Invite", "email": "open@x.test"}
    )
    async with session_scope() as db:
        db.add(Candidate(organization_id=org_id, full_name="Reuse Me", email="reuse@x.test"))
        db.add(
            Candidate(
                organization_id=org_id,
                full_name="Archived",
                email="archived@x.test",
                archived_at=datetime.now(UTC),
            )
        )
    rows = [
        {"full_name": "Row Zero", "email": "zero@x.test", "target_role": "ai_ml"},
        {"full_name": "Reuse Me", "email": "REUSE@x.test", "notes": "Second attempt"},
        {"full_name": "Row Two", "email": "zero@x.test"},  # duplicate of row 0
        {"full_name": "Archived", "email": "archived@x.test"},
        {"full_name": "Open Invite", "email": "open@x.test"},
        {"full_name": "Row Five", "email": "five@x.test"},
    ]
    response = await recruiter.post(
        f"{API}/intake/interviews/batch",
        json={"job_id": str(job_id), "settings": {"max_reconnects": 2}, "rows": rows},
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [c["row"] for c in body["created"]] == [0, 1, 5]
    assert all(c["invite_url"] and c["expires_at"] for c in body["created"])
    assert [c["candidate_reused"] for c in body["created"]] == [False, True, False]
    assert {(f["row"], f["code"]) for f in body["failed"]} == {
        (2, "duplicate_row"),
        (3, "candidate_archived"),
        (4, "interview_exists"),
    }
    batch_id = body["batch_id"]
    listing = (await recruiter.get(f"{API}/intake/interviews", params={"batch_id": batch_id})).json()
    assert listing["total"] == 3 and all(item["batch_id"] == batch_id for item in listing["items"])
    assert "invite_url" not in str(listing)
    row = await intake_row(body["created"][0]["intake_id"])
    assert row.target_role == "ai_ml" and row.settings["max_reconnects"] == 2
    assert (await intake_row(body["created"][1]["intake_id"])).notes == "Second attempt"
    [entry] = await audit_rows("intake.batch_create")
    assert entry.details["created"] == 3 and entry.details["failed"] == 3 and entry.details["sent"] == 3
    assert existing["invite_url"] not in str(entry.details)

    drafts = await recruiter.post(
        f"{API}/intake/interviews/batch",
        json={
            "job_id": str(job_id),
            "send": False,
            "rows": [{"full_name": "Draft", "email": "draft@x.test"}],
        },
    )
    assert drafts.status_code == 201 and drafts.json()["created"][0]["invite_url"] is None
    too_many = await recruiter.post(
        f"{API}/intake/interviews/batch",
        json={
            "job_id": str(job_id),
            "rows": [{"full_name": f"P{i}", "email": f"p{i}@x.test"} for i in range(201)],
        },
    )
    assert too_many.status_code == 422
    missing_job = await recruiter.post(
        f"{API}/intake/interviews/batch",
        json={"job_id": str(uuid.uuid4()), "rows": [{"full_name": "A", "email": "a@x.test"}]},
    )
    assert missing_job.status_code == 404 and missing_job.json()["error"]["code"] == "job_not_found"


async def test_batch_limit_setting_and_rate_limit(auth_client, settings) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    job_id, _ = await make_intake_job(recruiter.user.organization_id)
    settings.intake_batch_max_rows = 2
    over = await recruiter.post(
        f"{API}/intake/interviews/batch",
        json={
            "job_id": str(job_id),
            "send": False,
            "rows": [{"full_name": f"P{i}", "email": f"q{i}@x.test"} for i in range(3)],
        },
    )
    assert over.status_code == 422 and over.json()["error"]["code"] == "batch_too_large"
    assert over.json()["error"]["details"]["max_rows"] == 2
    statuses = []
    for index in range(10):
        response = await recruiter.post(
            f"{API}/intake/interviews/batch",
            json={
                "job_id": str(job_id),
                "send": False,
                "rows": [{"full_name": "R", "email": f"r{index}@x.test"}],
            },
        )
        statuses.append(response.status_code)
    assert statuses.count(201) == 9  # the over-limit request above counted too
    limited = statuses[-1]
    assert limited == 429


# ====================================================================================== settings


async def test_settings_get_put_and_fairness(auth_client) -> None:
    admin = await auth_client(Role.ADMIN)
    org_id = admin.user.organization_id
    defaults = (await admin.get(f"{API}/intake/settings")).json()
    assert defaults["program_name"] == "AI/ML Training Program" and defaults["updated_by"] is None
    assert defaults["approved_info"] == [] and defaults["defaults"]["duration_minutes"] == 45
    async with session_scope() as db:
        assert await db.get(IntakeOrgSettings, org_id) is None  # reads never persist
    body = {
        "program_name": "Data Academy",
        "work_authorization": {"enabled": True, "country": "the United Kingdom"},
        "approved_info": [
            {"topic": "Schedule", "answer": "Training runs Monday to Friday, 10:00-16:00, for 12 weeks."},
            {"topic": "Location", "answer": "Fully remote."},
        ],
        "defaults": {"duration_minutes": 40, "max_reconnects": 5, "expires_in_hours": 72, "presence": "none"},
    }
    saved = await admin.put(f"{API}/intake/settings", json=body)
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["program_name"] == "Data Academy" and out["updated_by"]["id"] == str(admin.user.id)
    assert out["defaults"]["presence"] == "none" and len(out["approved_info"]) == 2
    assert (await admin.get(f"{API}/intake/settings")).json()["approved_info"][0]["topic"] == "Schedule"
    [entry] = await audit_rows("intake.settings.update")
    assert set(entry.details["changed"]) == {
        "program_name",
        "work_authorization",
        "approved_info",
        "defaults",
    }
    assert entry.details["approved_info_count"] == 2

    protected = await admin.put(
        f"{API}/intake/settings",
        json={**body, "approved_info": [{"topic": "Visa", "answer": "We need your citizenship status."}]},
    )
    assert protected.status_code == 422 and protected.json()["error"]["code"] == "protected_topic"
    assert protected.json()["error"]["details"]["field"] == "approved_info[0]"
    country = await admin.put(
        f"{API}/intake/settings", json={**body, "work_authorization": {"enabled": True}}
    )
    assert (
        country.status_code == 422
        and country.json()["error"]["code"] == "work_authorization_country_required"
    )

    recruiter = await auth_client(Role.RECRUITER, organization_id=org_id)
    assert (await recruiter.get(f"{API}/intake/settings")).status_code == 200
    assert (await recruiter.put(f"{API}/intake/settings", json=body)).status_code == 403


async def test_config_lists_kit_index_and_intake_jobs(auth_client) -> None:
    interviewer = await auth_client(Role.INTERVIEWER)
    org_id = interviewer.user.organization_id
    job_id, round_id = await make_intake_job(org_id)
    await make_intake_job(org_id, status="closed")
    await make_intake_job(org_id, with_intake_round=False, other_round=True)
    response = await interviewer.get(f"{API}/intake/config")
    assert response.status_code == 200, response.text
    config = response.json()
    kit = config["kit"]
    assert kit["version"] == "v1" and len(kit["sha256"]) == 64
    assert kit["duration"] == {"min": 35, "max": 50, "target": 45}
    assert [s["key"] for s in kit["stages"]] == [
        "greeting",
        "resume",
        "mandatory",
        "reading",
        "topic",
        "learning",
        "close",
    ]
    assert kit["stages"][3]["candidate_title"] == "Reading exercise"
    assert [p["key"] for p in kit["passages"]] == list("ABCDEF") and "paragraphs" not in str(kit["passages"])
    assert len(kit["topic_cards"]) == 8 and "bullets" not in str(kit["topic_cards"])
    assert config["jobs"] == [
        {
            "job_id": str(job_id),
            "title": "AI/ML Training Program",
            "status": "open",
            "round_id": str(round_id),
            "round_name": "Intake Interview",
        }
    ]
    assert (
        config["defaults"]["max_reconnects"] == 10
        and config["work_authorization_default"]["enabled"] is False
    )


# ================================================================================ RBAC / cross-org


async def test_rbac_cross_org_and_unauthenticated(
    auth_client, org_factory, client, candidate_auth_client
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, _ = await make_intake_job(org_id)
    created = await create_intake(recruiter, job_id)
    intake_id = created["intake"]["id"]

    interviewer = await auth_client(Role.INTERVIEWER, organization_id=org_id)
    assert (await interviewer.get(f"{API}/intake/interviews")).json()["total"] == 1
    assert (await interviewer.get(f"{API}/intake/stats")).status_code == 200
    denied = await interviewer.post(f"{API}/intake/interviews", json=create_body(job_id))
    assert denied.status_code == 403
    assert (await interviewer.post(f"{API}/intake/interviews/{intake_id}/cancel")).status_code == 403
    assert (
        await interviewer.patch(f"{API}/intake/interviews/{intake_id}", json={"notes": "x"})
    ).status_code == 403

    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.get(f"{API}/intake/interviews")).json()["total"] == 0
    for method, path in (
        ("patch", f"/intake/interviews/{intake_id}"),
        ("post", f"/intake/interviews/{intake_id}/send"),
        ("post", f"/intake/interviews/{intake_id}/cancel"),
        ("post", f"/intake/interviews/{intake_id}/regenerate-link"),
    ):
        kwargs = {"json": {"notes": "x"}} if method == "patch" else {}
        response = await getattr(outsider, method)(f"{API}{path}", **kwargs)
        assert response.status_code == 404 and response.json()["error"]["code"] == "intake_not_found", path
    foreign_job = await outsider.post(f"{API}/intake/interviews", json=create_body(job_id))
    assert foreign_job.status_code == 404 and foreign_job.json()["error"]["code"] == "job_not_found"

    assert (await client.get(f"{API}/intake/interviews")).status_code == 401
    candidate = await candidate_auth_client(organization_id=org_id)
    assert (await candidate.get(f"{API}/intake/interviews")).status_code == 403


async def test_feature_flag_disables_every_route(auth_client, settings) -> None:
    admin = await auth_client(Role.ADMIN)
    settings.intake_enabled = False
    for method, path in (
        ("get", "/intake/config"),
        ("get", "/intake/settings"),
        ("get", "/intake/interviews"),
        ("get", "/intake/stats"),
        ("post", "/intake/interviews"),
        ("post", "/intake/interviews/batch"),
        ("get", f"/portal/intake/{uuid.uuid4()}"),
    ):
        response = await getattr(admin, method)(f"{API}{path}")
        assert response.status_code == 404 and response.json()["error"]["code"] == "intake_disabled", path


async def test_send_draft_whose_round_was_removed_uses_the_jobs_intake_round(auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    job_id, round_id = await make_intake_job(recruiter.user.organization_id, other_round=True)
    draft = await create_intake(recruiter, job_id, send=False)
    async with session_scope() as db:
        row = await db.get(IntakeInterview, uuid.UUID(draft["intake"]["id"]))
        row.round_id = None  # as after ON DELETE SET NULL
    sent = await recruiter.post(f"{API}/intake/interviews/{draft['intake']['id']}/send")
    assert sent.status_code == 200, sent.text
    row = await intake_row(draft["intake"]["id"])
    async with session_scope() as db:
        interview = await db.get(Interview, row.interview_id)
    assert row.round_id == round_id and interview.round_id == round_id


async def test_job_round_api_accepts_and_validates_intake_rounds(auth_client) -> None:
    from app.db.models import Competency

    recruiter = await auth_client(Role.RECRUITER)
    async with session_scope() as db:
        for key in (
            "communication", "technical_reading", "structured_answer", "technical", "trainability", "composure",
            "adaptability",
        ):  # fmt: skip
            db.add(Competency(organization_id=None, key=key, name=key, category="communication"))
    job_id, _ = await make_intake_job(recruiter.user.organization_id, with_intake_round=False)
    types = (await recruiter.get(f"{API}/round-types")).json()["items"]
    intake_type = next(t for t in types if t["round_type"] == "intake")
    assert intake_type["label"] == "Intake interview" and intake_type["config"]["intake"]["scenarios"] == 2
    created = await recruiter.post(f"{API}/jobs/{job_id}/rounds", json={"round_type": "intake"})
    assert created.status_code == 201, created.text
    round_ = created.json()
    assert round_["round_type"] == "intake" and round_["duration_minutes"] == 45
    assert round_["config"]["intake"]["claims"] == {"min_claims": 2, "max_claims": 3}
    assert round_["config"]["coding"]["enabled"] is False and round_["config"]["resume_validation"]["enabled"]
    for patch in (
        {"duration_minutes": 30},
        {"config": {"coding": {"enabled": True}}},
        {"candidate_selectable": True},
        {"config": {"intake": {"work_authorization": {"enabled": True}}}},
    ):
        response = await recruiter.patch(f"{API}/rounds/{round_['id']}", json=patch)
        assert response.status_code == 422, (patch, response.text)
    ok = await recruiter.patch(
        f"{API}/rounds/{round_['id']}",
        json={"duration_minutes": 40, "config": {"intake": {"passage_drill_a": "B", "topic_card": "3"}}},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["duration_minutes"] == 40 and ok.json()["max_duration_minutes"] == 45
    assert ok.json()["config"]["intake"]["passage_drill_a"] == "B"
    # The new round is offered by the intake config and used by create.
    config = (await recruiter.get(f"{API}/intake/config")).json()
    assert [j["round_id"] for j in config["jobs"]] == [round_["id"]]
    created_intake = await create_intake(recruiter, job_id)
    assert (await intake_row(created_intake["intake"]["id"])).settings[
        "duration_minutes"
    ] == 45  # org default
