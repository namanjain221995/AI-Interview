"""Staff link API: create/validate/update/status/token/access code, form guard, RBAC and IDOR."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.core.security import verify_access_code_async
from app.db.models import AuditLog, InterviewLink, Round
from app.db.session import session_scope
from app.domain.enums import Role
from tests.integration.open_links.helpers import (
    API,
    answers,
    complete,
    create_link,
    link_body,
    make_job,
    register,
    token_of,
)

pytestmark = pytest.mark.integration


async def test_catalog_and_validate(link_env) -> None:
    client = link_env.recruiter
    catalog = (await client.get(f"{API}/open-links/form/catalog")).json()
    assert [f["key"] for f in catalog["builtins"]][:3] == ["full_name", "email", "phone"]
    assert catalog["limits"] == {"max_fields": 30, "max_options": 50}
    assert "date" in catalog["types"] and catalog["default_schema"]["version"] == 1

    schema = catalog["default_schema"]
    schema["fields"] += [
        {"key": "c_age", "type": "number", "label": "Age"},
        {"key": "c_pay", "type": "text", "label": "Expected CTC"},
    ]
    result = (
        await client.post(
            f"{API}/open-links/form/validate", json={"form_schema": schema, "acknowledged_warnings": []}
        )
    ).json()
    assert result["ok"] is False
    assert result["errors"] == [
        {
            "key": "c_age",
            "label": "Age",
            "topic": "age",
            "message": "This asks for age or date of birth, which we never collect. Remove the field or reword it.",
        }
    ]
    assert result["warnings"][0]["code"] == "expected_pay" and result["warnings"][0]["acknowledged"] is False
    malformed = await client.post(
        f"{API}/open-links/form/validate", json={"form_schema": {"fields": [{"key": "x"}]}}
    )
    assert malformed.status_code == 422


async def test_create_get_list_and_audit(link_env, settings, db_session) -> None:
    client = link_env.recruiter
    created = await client.post(
        f"{API}/open-links", json=link_body(link_env, require_access_code=True, max_registrations=100)
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    link, code = body["link"], body["access_code"]
    assert code and link["requires_access_code"] is True
    assert link["url"].startswith(f"{settings.public_base_url}/j/") and len(token_of(link)) == 32
    assert [r["id"] for r in link["rounds"]] == [str(r) for r in link_env.round_ids]
    assert link["state"] == "open" and link["progression"] == "immediate"
    assert link["duplicate_policy"] == {"scope": "job", "device": "flag", "network": "flag"}
    assert link["policy_summary"]["video_recording"] is True and link["warnings"] == []
    assert link["counts"]["registrations"] == 0 and link["created_by"]["id"] == str(client.user.id)

    row = await db_session.get(InterviewLink, uuid.UUID(link["id"]))
    assert row.access_code_hash and await verify_access_code_async(code, row.access_code_hash)

    detail = (await client.get(f"{API}/open-links/{link['id']}")).json()
    assert code not in json.dumps(detail) and detail["requires_access_code"] is True
    listing = (await client.get(f"{API}/open-links", params={"job_id": str(link_env.job_id)})).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == link["id"]
    assert (await client.get(f"{API}/open-links", params={"status": "closed"})).json()["total"] == 0

    audit = (await db_session.scalars(select(AuditLog).where(AuditLog.action == "link.create"))).all()
    assert len(audit) == 1 and code not in json.dumps(audit[0].details)


async def test_rounds_are_stored_in_job_order(link_env) -> None:
    reversed_ids = [str(r) for r in reversed(link_env.round_ids)]
    link = (await create_link(link_env, round_ids=reversed_ids))["link"]
    assert [r["id"] for r in link["rounds"]] == [str(r) for r in link_env.round_ids]


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"round_ids": []}, 422, "no_rounds"),
        ({"round_ids": [str(uuid.uuid4())]}, 422, "invalid_rounds"),
        ({"opens_at": "2026-10-10T10:00:00Z", "closes_at": "2026-10-09T10:00:00Z"}, 422, "validation_failed"),
        ({"max_registrations": 0}, 422, "validation_failed"),
        ({"per_ip_hourly_limit": 2}, 422, "validation_failed"),
        (
            {"form_schema": {"fields": [{"key": "c_g", "type": "text", "label": "Gender"}]}},
            422,
            "protected_field",
        ),
        (
            {"form_schema": {"fields": [{"key": "c_a", "type": "text", "label": "Postal address"}]}},
            422,
            "form_warnings_unacknowledged",
        ),
    ],
)
async def test_create_validation(link_env, overrides, status, code) -> None:
    response = await link_env.recruiter.post(f"{API}/open-links", json=link_body(link_env, **overrides))
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


async def test_acknowledged_warning_is_stored(link_env, db_session) -> None:
    schema = {"fields": [{"key": "c_a", "type": "text", "label": "Postal address"}]}
    link = (await create_link(link_env, form_schema=schema, acknowledged_warnings=["c_a:address"]))["link"]
    assert link["acknowledged_warnings"] == ["c_a:address"]
    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "link.form_warnings_acknowledged"))
    ).all()
    assert len(audit) == 1


async def test_job_checks(link_env) -> None:
    job_id, rounds = await make_job(link_env.organization_id, status="closed")
    response = await link_env.recruiter.post(
        f"{API}/open-links", json={"job_id": str(job_id), "name": "x", "round_ids": [str(rounds[0])]}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "job_not_open"
    other_job, other_rounds = await make_job(link_env.organization_id)
    mixed = await link_env.recruiter.post(
        f"{API}/open-links",
        json=link_body(link_env, round_ids=[str(link_env.round_ids[0]), str(other_rounds[0])]),
    )
    assert mixed.status_code == 422 and mixed.json()["error"]["details"]["round_ids"] == [
        str(other_rounds[0])
    ]


async def test_update_form_version_and_round_in_use(link_env, public_client, client) -> None:
    recruiter = link_env.recruiter
    link = (await create_link(link_env))["link"]
    schema = link["form_schema"]
    assert schema["version"] == 1
    schema["fields"].append(
        {"key": "c_shift", "type": "select", "label": "Shift", "options": ["Day", "Night"]}
    )
    updated = await recruiter.patch(
        f"{API}/open-links/{link['id']}", json={"form_schema": schema, "name": "Drive 2"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["form_schema"]["version"] == 2 and updated.json()["name"] == "Drive 2"
    same = await recruiter.patch(
        f"{API}/open-links/{link['id']}", json={"form_schema": updated.json()["form_schema"]}
    )
    assert same.json()["form_schema"]["version"] == 2  # unchanged form keeps its version

    # Reordering/removing rounds is fine until someone has an interview for them.
    one = await recruiter.patch(
        f"{API}/open-links/{link['id']}", json={"round_ids": [str(link_env.round_ids[0])]}
    )
    assert one.status_code == 200 and len(one.json()["rounds"]) == 1
    both = await recruiter.patch(
        f"{API}/open-links/{link['id']}", json={"round_ids": [str(r) for r in link_env.round_ids]}
    )
    assert len(both.json()["rounds"]) == 2
    assert (await register(public_client(), token_of(link), answers(1))).status_code == 201
    removed = await recruiter.patch(
        f"{API}/open-links/{link['id']}", json={"round_ids": [str(link_env.round_ids[1])]}
    )
    assert removed.status_code == 409 and removed.json()["error"]["code"] == "round_in_use"
    assert removed.json()["error"]["details"]["round_ids"] == [str(link_env.round_ids[0])]
    extended = await recruiter.patch(
        f"{API}/open-links/{link['id']}", json={"round_ids": [str(link_env.round_ids[0])]}
    )
    assert extended.status_code == 200  # dropping a round nobody reached is allowed


async def test_status_token_access_code(link_env, public_client) -> None:
    recruiter = link_env.recruiter
    link = (await create_link(link_env))["link"]
    old_token = token_of(link)
    paused = (await recruiter.post(f"{API}/open-links/{link['id']}/status", json={"status": "paused"})).json()
    assert paused["status"] == "paused" and paused["state"] == "paused"
    info = (await public_client().get(f"{API}/link-access/{old_token}")).json()
    assert info["state"] == "paused" and info["form"] is None

    rotated = (await recruiter.post(f"{API}/open-links/{link['id']}/regenerate-token")).json()
    assert token_of(rotated) != old_token
    assert (await public_client().get(f"{API}/link-access/{old_token}")).status_code == 404

    code_resp = await recruiter.post(f"{API}/open-links/{link['id']}/access-code", json={"enabled": True})
    assert code_resp.headers["cache-control"] == "no-store"
    assert code_resp.json()["access_code"] and code_resp.json()["link"]["requires_access_code"] is True
    off = (await recruiter.post(f"{API}/open-links/{link['id']}/access-code", json={"enabled": False})).json()
    assert off["access_code"] is None and off["link"]["requires_access_code"] is False

    closed = (await recruiter.post(f"{API}/open-links/{link['id']}/status", json={"status": "closed"})).json()
    assert closed["state"] == "closed"
    for call in (
        recruiter.post(f"{API}/open-links/{link['id']}/status", json={"status": "active"}),
        recruiter.patch(f"{API}/open-links/{link['id']}", json={"name": "x"}),
        recruiter.post(f"{API}/open-links/{link['id']}/regenerate-token"),
    ):
        response = await call
        assert response.status_code == 409 and response.json()["error"]["code"] == "link_closed"


async def test_rbac_and_idor(link_env, auth_client, org_factory, candidate_auth_client, client) -> None:
    link = (await create_link(link_env))["link"]
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=link_env.organization_id)
    assert (await interviewer.get(f"{API}/open-links/{link['id']}")).status_code == 200
    assert (await interviewer.get(f"{API}/open-links/form/catalog")).status_code == 200
    for response in (
        await interviewer.post(f"{API}/open-links", json=link_body(link_env)),
        await interviewer.patch(f"{API}/open-links/{link['id']}", json={"name": "x"}),
        await interviewer.post(f"{API}/open-links/{link['id']}/status", json={"status": "paused"}),
        await interviewer.get(f"{API}/open-links/{link['id']}/registrations/export"),
    ):
        assert response.status_code == 403, response.text

    other_org = await org_factory()
    outsider = await auth_client(Role.ADMIN, organization_id=other_org)
    assert (await outsider.get(f"{API}/open-links/{link['id']}")).status_code == 404
    assert (await outsider.get(f"{API}/open-links/{link['id']}/registrations")).status_code == 404
    assert (
        await outsider.post(f"{API}/open-links/{link['id']}/status", json={"status": "paused"})
    ).status_code == 404
    assert (await outsider.get(f"{API}/open-links")).json()["total"] == 0
    cross = await outsider.post(f"{API}/open-links", json=link_body(link_env))
    assert cross.status_code == 404  # another org's job

    candidate = await candidate_auth_client()
    assert (await candidate.get(f"{API}/open-links")).status_code == 403
    assert (await client.get(f"{API}/open-links")).status_code == 401


async def test_missing_round_is_reported_and_skipped(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    async with session_scope() as db:
        row = await db.get(Round, link_env.round_ids[0])
        row.is_active = False
    detail = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}")).json()
    assert detail["rounds"][0]["missing"] is True and "round_missing" in detail["warnings"]
    info = (await public_client().get(f"{API}/link-access/{token_of(link)}")).json()
    assert [r["sequence"] for r in info["rounds"]] == [1]
    response = await register(public_client(), token_of(link), answers(7))
    assert response.status_code == 201, response.text
    regs = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()
    assert regs["items"][0]["current_round_index"] == 1
    assert [r["state"] for r in regs["items"][0]["rounds"]] == ["locked", "invited"]
    done = await complete(link_env.recruiter, response.json()["interview_id"])
    assert done["registration_status"] == "completed"
