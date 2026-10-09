"""Jobs, requirements, rounds, templates and round-type defaults over HTTP."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, InterviewTemplate, Job, JobRequirement
from app.domain.enums import Role, RoundType
from tests.integration.resume.helpers import API, create_job

pytestmark = pytest.mark.integration


async def test_job_crud_requirements_and_embeddings(auth_client, mock_ai, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(
        client,
        integrity_settings={"camera_required": False},
        recording_settings={"record_video": True, "retention": {"video_days": 14}},
        coding_requirements={"enabled": True, "languages": ["python", "go"], "difficulty": "hard"},
    )
    assert job["status"] == "open" and job["has_embedding"] is True
    assert job["candidate_report_mode"] == "never"  # organization default
    assert [r["name"] for r in job["requirements"]][:2] == ["Kubernetes", "Terraform"]
    assert all(r["has_embedding"] for r in job["requirements"])
    assert job["requirements_count"] == 5 and job["rounds_count"] == 0
    assert job["effective_policy"]["integrity"]["camera_required"] is False
    assert job["effective_policy"]["retention"]["video_days"] == 14
    assert job["coding_requirements"] == {
        "enabled": True,
        "languages": ["python", "go"],
        "difficulty": "hard",
    }

    job_row = await db_session.get(Job, uuid.UUID(job["id"]))
    old_vector = list(job_row.embedding)
    patched = await client.patch(
        f"{API}/jobs/{job['id']}", json={"description": "Now with Kafka streaming.", "status": "draft"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "draft" and patched.json()["has_embedding"] is True
    await db_session.refresh(job_row)
    assert list(job_row.embedding) != old_vector  # re-embedded after a text change

    added = await client.post(
        f"{API}/jobs/{job['id']}/requirements", json={"kind": "qualification", "name": "On-call experience"}
    )
    assert added.status_code == 201 and added.json()["sort_order"] == 5 and added.json()["has_embedding"]
    requirement_id = added.json()["id"]
    updated = await client.patch(
        f"{API}/requirements/{requirement_id}", json={"weight": 2.5, "description": "Led incident response"}
    )
    assert updated.status_code == 200 and updated.json()["weight"] == 2.5
    listing = (await client.get(f"{API}/jobs/{job['id']}/requirements")).json()
    assert listing["total"] == 6
    assert (await client.delete(f"{API}/requirements/{requirement_id}")).status_code == 204
    assert await db_session.get(JobRequirement, uuid.UUID(requirement_id)) is None

    jobs = (await client.get(f"{API}/jobs", params={"q": "devops"})).json()
    assert jobs["total"] == 1 and jobs["items"][0]["requirements_count"] == 5
    archived = await client.post(f"{API}/jobs/{job['id']}/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert (await client.get(f"{API}/jobs")).json()["total"] == 0  # archived hidden by default
    assert (await client.get(f"{API}/jobs", params={"status": "archived"})).json()["total"] == 1

    actions = set((await db_session.scalars(select(AuditLog.action))).all())
    assert {
        "job.create",
        "job.update",
        "job.archive",
        "job.requirement.create",
        "job.requirement.update",
        "job.requirement.delete",
    } <= actions


async def test_job_validation_and_permissions(auth_client, org_factory, mock_ai) -> None:
    client = await auth_client(Role.RECRUITER)
    bad_policy = await client.post(
        f"{API}/jobs", json={"title": "X", "integrity_settings": {"no_such_field": True}}
    )
    assert bad_policy.status_code == 422 and bad_policy.json()["error"]["details"]["errors"]
    bad_range = await client.post(
        f"{API}/jobs", json={"title": "X", "experience_min_years": 8, "experience_max_years": 2}
    )
    assert bad_range.status_code == 422
    job = await create_job(client)

    interviewer = await auth_client(Role.INTERVIEWER, organization_id=client.user.organization_id)
    assert (await interviewer.get(f"{API}/jobs/{job['id']}")).status_code == 200
    assert (await interviewer.patch(f"{API}/jobs/{job['id']}", json={"title": "Y"})).status_code == 403
    assert (await interviewer.post(f"{API}/jobs", json={"title": "Y"})).status_code == 403

    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.get(f"{API}/jobs/{job['id']}")).status_code == 404
    assert (await outsider.patch(f"{API}/jobs/{job['id']}", json={"title": "Y"})).status_code == 404
    requirement_id = job["requirements"][0]["id"]
    assert (
        await outsider.patch(f"{API}/requirements/{requirement_id}", json={"weight": 1})
    ).status_code == 404
    assert (await outsider.get(f"{API}/jobs/{job['id']}/rounds")).status_code == 404


async def test_round_types_and_rounds(auth_client, mock_ai, seeded_catalog) -> None:
    client = await auth_client(Role.RECRUITER)
    types = (await client.get(f"{API}/round-types")).json()
    assert types["total"] == len(RoundType)  # one default template per round type (incl. intake)
    by_type = {item["round_type"]: item for item in types["items"]}
    assert by_type["combined"]["source"] == "database" and by_type["combined"]["template_id"]
    assert by_type["coding"]["config"]["coding"]["enabled"] is True

    job = await create_job(client)
    first = await client.post(f"{API}/jobs/{job['id']}/rounds", json={"round_type": "technical_screening"})
    assert first.status_code == 201, first.text
    first = first.json()
    assert first["sequence"] == 1 and first["config"]["round_type"] == "technical_screening"
    assert first["name"] == first["config"]["name"]
    assert first["duration_minutes"] == first["config"]["duration_minutes"]

    custom = await client.post(
        f"{API}/jobs/{job['id']}/rounds",
        json={
            "template_id": by_type["combined"]["template_id"],
            "name": "DevOps combined round",
            "duration_minutes": 45,
            "candidate_selectable": True,
            "config": {"coding": {"enabled": False}, "stopping": {"min_questions": 6}},
        },
    )
    assert custom.status_code == 201, custom.text
    custom = custom.json()
    assert custom["round_type"] == "combined" and custom["sequence"] == 2
    assert custom["name"] == "DevOps combined round" == custom["config"]["name"]
    assert custom["duration_minutes"] == 45 and custom["max_duration_minutes"] >= 45
    assert custom["candidate_selectable"] is True and custom["config"]["candidate_selectable"] is True
    assert (
        custom["config"]["coding"]["enabled"] is False and custom["config"]["stopping"]["min_questions"] == 6
    )
    assert custom["config"]["competencies"]  # inherited from the template
    assert custom["template_id"] == by_type["combined"]["template_id"]

    patched = await client.patch(
        f"{API}/rounds/{custom['id']}",
        json={"expected_difficulty": "hard", "config": {"confidence_threshold": 0.8}},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["expected_difficulty"] == "hard" == body["config"]["expected_difficulty"]
    assert body["config"]["confidence_threshold"] == 0.8 and body["config"]["coding"]["enabled"] is False

    unknown = await client.patch(
        f"{API}/rounds/{custom['id']}", json={"config": {"competencies": [{"key": "basket_weaving"}]}}
    )
    assert unknown.status_code == 422 and unknown.json()["error"]["details"]["unknown_competency_keys"] == [
        "basket_weaving"
    ]
    bad_rubric = await client.patch(
        f"{API}/rounds/{custom['id']}",
        json={"config": {"rubric": [{"key": "a", "label": "A", "weight": 60}]}},
    )
    assert bad_rubric.status_code == 422
    bad_pin = await client.patch(
        f"{API}/rounds/{custom['id']}", json={"config": {"coding": {"question_ids": [str(uuid.uuid4())]}}}
    )
    assert bad_pin.status_code == 422 and "unknown_coding_question_ids" in bad_pin.json()["error"]["details"]

    wrong = await client.post(f"{API}/jobs/{job['id']}/rounds/reorder", json={"round_ids": [first["id"]]})
    assert wrong.status_code == 422
    reordered = await client.post(
        f"{API}/jobs/{job['id']}/rounds/reorder", json={"round_ids": [custom["id"], first["id"]]}
    )
    assert [(r["id"], r["sequence"]) for r in reordered.json()["items"]] == [
        (custom["id"], 1),
        (first["id"], 2),
    ]
    listing = (await client.get(f"{API}/jobs/{job['id']}/rounds")).json()
    assert [r["id"] for r in listing["items"]] == [custom["id"], first["id"]]
    assert (await client.delete(f"{API}/rounds/{custom['id']}")).status_code == 204
    remaining = (await client.get(f"{API}/jobs/{job['id']}/rounds")).json()["items"]
    assert [(r["id"], r["sequence"]) for r in remaining] == [(first["id"], 1)]
    missing_type = await client.post(f"{API}/jobs/{job['id']}/rounds", json={"name": "No type"})
    assert missing_type.status_code == 422


async def test_round_types_fall_back_to_samples(auth_client) -> None:
    client = await auth_client(Role.RECRUITER)
    types = (await client.get(f"{API}/round-types")).json()
    assert types["total"] == len(RoundType) and {item["source"] for item in types["items"]} == {"samples"}


async def test_templates(auth_client, org_factory, mock_ai, seeded_catalog, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    templates = (await client.get(f"{API}/templates")).json()
    assert templates["total"] == len(RoundType) and all(t["is_global"] for t in templates["items"])
    global_id = templates["items"][0]["id"]
    assert (await client.patch(f"{API}/templates/{global_id}", json={"name": "Mine"})).status_code == 403
    assert (await client.delete(f"{API}/templates/{global_id}")).status_code == 403

    created = await client.post(
        f"{API}/templates",
        json={
            "name": "Platform deep dive",
            "round_type": "deep_technical",
            "config": {"duration_minutes": 50},
            "is_default": True,
        },
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert template["is_global"] is False and template["config"]["name"] == "Platform deep dive"
    assert (
        template["config"]["duration_minutes"] == 50 and template["config"]["round_type"] == "deep_technical"
    )
    second = (
        await client.post(
            f"{API}/templates",
            json={"name": "Second", "round_type": "deep_technical", "config": {}, "is_default": True},
        )
    ).json()
    first_row = await db_session.get(InterviewTemplate, uuid.UUID(template["id"]))
    await db_session.refresh(first_row)
    assert first_row.is_default is False and second["is_default"] is True  # one default per round type
    patched = await client.patch(
        f"{API}/templates/{template['id']}", json={"config": {"stopping": {"max_questions": 20}}}
    )
    assert patched.status_code == 200 and patched.json()["config"]["stopping"]["max_questions"] == 20
    assert patched.json()["config"]["duration_minutes"] == 50
    own = (await client.get(f"{API}/templates", params={"scope": "organization"})).json()
    assert own["total"] == 2

    job = await create_job(client)
    from_template = await client.post(f"{API}/jobs/{job['id']}/rounds", json={"template_id": template["id"]})
    assert from_template.status_code == 201 and from_template.json()["duration_minutes"] == 50

    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.get(f"{API}/templates/{template['id']}")).status_code == 404
    assert (await outsider.get(f"{API}/templates")).json()["total"] == len(RoundType)
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=client.user.organization_id)
    assert (
        await interviewer.post(f"{API}/templates", json={"name": "x", "round_type": "coding"})
    ).status_code == 403
    assert (await client.delete(f"{API}/templates/{template['id']}")).status_code == 204
