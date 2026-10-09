"""CSV export (escaping, columns, audit), registration listing/search, the
retention sweep (signal hashes, blocked rows, stale syncs) and candidate erasure."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db.models import AuditLog, Candidate, Interview, LinkRegistration
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.open_links.retention import sweep
from tests.integration.open_links.helpers import API, answers, complete, create_link, register, token_of

pytestmark = pytest.mark.integration

NO_NETWORK = {"scope": "job", "device": "flag", "network": "off"}


async def test_csv_export_escapes_formulas(link_env, public_client, db_session) -> None:
    schema = {
        "fields": [
            {
                "key": "full_name",
                "builtin": True,
                "type": "text",
                "label": "Full name",
                "enabled": True,
                "required": True,
            },
            {
                "key": "email",
                "builtin": True,
                "type": "email",
                "label": "Email",
                "enabled": True,
                "required": True,
            },
            {
                "key": "phone",
                "builtin": True,
                "type": "phone",
                "label": "Mobile number",
                "enabled": True,
                "required": True,
            },
            {"key": "c_note", "type": "text", "label": "=Note", "required": False},
            {"key": "c_shift", "type": "multiselect", "label": "Shifts", "options": ["Day", "Night"]},
        ]
    }
    link = (await create_link(link_env, form_schema=schema, duplicate_policy=NO_NETWORK))["link"]
    token = token_of(link)
    evil = await register(
        public_client(),
        token,
        answers(1, full_name='=HYPERLINK("http://x","y")', c_note="+SUM(A1:A2)", c_shift=["Night", "Day"]),
    )
    assert evil.status_code == 201, evil.text
    await complete(link_env.recruiter, evil.json()["interview_id"])
    assert (await register(public_client(), token, answers(2, c_note="@cmd"))).status_code == 201

    response = await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith(
        'attachment; filename="campus-drive-pune-registrations-'
    ) and disposition.endswith('.csv"')
    rows = list(csv.reader(io.StringIO(response.text)))
    header = rows[0]
    assert header[:7] == ["registered_at", "full_name", "email", "phone", "status", "status_reason", "flags"]
    assert header[7:15] == [
        "round_1_name", "round_1_state", "round_1_completed_at", "round_1_session_id",
        "round_2_name", "round_2_state", "round_2_completed_at", "round_2_session_id",
    ]  # fmt: skip
    assert "'=Note" in header and "Shifts" in header and "device_hash" not in header
    by_email = {r[2]: dict(zip(header, r, strict=True)) for r in rows[1:]}
    first = by_email["candidate1@example.com"]
    assert first["full_name"] == '\'=HYPERLINK("http://x","y")'
    assert first["'=Note"] == "'+SUM(A1:A2)" and first["Shifts"] == "Day; Night"
    assert first["phone"] == "'+91 98765 00001"  # "+" starts a formula too
    assert first["round_1_state"] == "completed" and first["round_1_session_id"]
    assert first["round_2_state"] == "invited"
    assert by_email["candidate2@example.com"]["'=Note"] == "'@cmd"

    audit = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "link.registrations_export"))
    ).all()
    assert len(audit) == 1 and audit[0].details["rows"] == 2
    filtered = await link_env.recruiter.get(
        f"{API}/open-links/{link['id']}/registrations/export", params={"status": "completed"}
    )
    assert len(list(csv.reader(io.StringIO(filtered.text)))) == 1  # header only


async def test_registration_list_search_detail_and_by_candidate(link_env, public_client) -> None:
    link = (await create_link(link_env, duplicate_policy=NO_NETWORK))["link"]
    token = token_of(link)
    first = await register(public_client(), token, answers(1, full_name="Asha Rao"))
    await register(public_client(), token, answers(2, full_name="Vikram 100%_Singh"))
    base = f"{API}/open-links/{link['id']}/registrations"
    assert [
        r["full_name"] for r in (await link_env.recruiter.get(base, params={"q": "asha"})).json()["items"]
    ] == ["Asha Rao"]
    assert (await link_env.recruiter.get(base, params={"q": "100%_"})).json()["total"] == 1
    assert (await link_env.recruiter.get(base, params={"round_index": 1})).json()["total"] == 0
    listing = (await link_env.recruiter.get(base)).json()
    assert listing["total"] == 2 and listing["items"][0]["full_name"] == "Vikram 100%_Singh"  # newest first
    reg = next(r for r in listing["items"] if r["full_name"] == "Asha Rao")
    detail = (await link_env.recruiter.get(f"{base}/{reg['id']}")).json()
    assert [a["key"] for a in detail["answers"]] == ["full_name", "email", "phone"]
    assert detail["personal_link_issued_at"] and detail["privacy_ack_at"] and len(detail["attempts"]) == 1
    assert "device_hash" not in detail and "ip_hash" not in detail

    other_link = (await create_link(link_env, name="Other"))["link"]
    assert (
        await link_env.recruiter.get(f"{API}/open-links/{other_link['id']}/registrations/{reg['id']}")
    ).status_code == 404

    async with session_scope() as db:
        interview = await db.get(Interview, uuid.UUID(first.json()["interview_id"]))
        candidate_id = interview.candidate_id
    by_candidate = (
        await link_env.recruiter.get(
            f"{API}/open-links/registrations", params={"candidate_id": str(candidate_id)}
        )
    ).json()
    assert by_candidate["total"] == 1
    assert by_candidate["items"][0]["link"] == {
        "id": link["id"],
        "name": "Campus drive Pune",
        "job_title": "Backend Engineer",
    }
    interviewer = link_env.recruiter  # all staff roles may read
    assert (
        await interviewer.get(f"{API}/open-links/registrations", params={"candidate_id": str(uuid.uuid4())})
    ).json()["total"] == 0


async def test_sweep_purges_signals_and_old_blocked_rows(link_env, public_client, settings) -> None:
    link = (
        await create_link(link_env, duplicate_policy={"scope": "job", "device": "block", "network": "off"})
    )["link"]
    token = token_of(link)
    shared = public_client()
    assert (await register(shared, token, answers(1))).status_code == 201
    assert (await register(shared, token, answers(2))).status_code == 403  # blocked row
    old = datetime.now(UTC) - timedelta(days=settings.open_link_signal_retention_days + 1)
    async with session_scope() as db:
        await db.execute(update(LinkRegistration).values(created_at=old))
    async with session_scope() as db:
        summary = await sweep(db)
    assert summary.signals_purged == 2 and summary.blocked_deleted == 1
    async with session_scope() as db:
        [row] = (await db.scalars(select(LinkRegistration))).all()
        assert row.device_hash is None and row.ip_hash is None and row.ip_ua_hash is None
        assert row.signals_purged_at is not None and row.status == "registered"
    async with session_scope() as db:
        again = await sweep(db)
    assert again.signals_purged == 0 and again.blocked_deleted == 0


async def test_sweep_purges_signals_of_closed_links_and_syncs(link_env, public_client) -> None:
    link = (await create_link(link_env))["link"]
    response = await register(public_client(), token_of(link), answers(1))
    await link_env.recruiter.post(f"{API}/open-links/{link['id']}/status", json={"status": "closed"})
    async with session_scope() as db:
        from app.db.models import InterviewLink

        await db.execute(update(InterviewLink).values(closed_at=datetime.now(UTC) - timedelta(days=31)))
        await db.execute(
            update(Interview)
            .where(Interview.id == uuid.UUID(response.json()["interview_id"]))
            .values(invite_expires_at=datetime.now(UTC) - timedelta(minutes=5))
        )
    async with session_scope() as db:
        summary = await sweep(db)
    assert summary.signals_purged == 1 and summary.changed == 1
    async with session_scope() as db:
        assert (await db.scalars(select(LinkRegistration))).one().status == "expired"


async def test_candidate_erasure_removes_registrations_and_blocked_rows(
    link_env, public_client, auth_client
) -> None:
    link = (
        await create_link(link_env, duplicate_policy={"scope": "job", "device": "block", "network": "off"})
    )["link"]
    token = token_of(link)
    shared = public_client()
    first = await register(shared, token, answers(1))
    await register(shared, token, answers(2))  # blocked (other person)
    # The same person blocked on another link of the org (same e-mail, no candidate row).
    other = (
        await create_link(
            link_env, name="Other", duplicate_policy={"scope": "link", "device": "block", "network": "off"}
        )
    )["link"]
    await register(shared, token_of(other), answers(3))
    assert (await register(shared, token_of(other), answers(1, phone="+91 90000 12345"))).status_code == 403
    async with session_scope() as db:
        statuses = sorted((await db.scalars(select(LinkRegistration.status))).all())
    assert statuses == ["blocked", "blocked", "registered", "registered"]

    async with session_scope() as db:
        interview = await db.get(Interview, uuid.UUID(first.json()["interview_id"]))
        candidate_id = interview.candidate_id
    admin = await auth_client(Role.ADMIN, organization_id=link_env.organization_id)
    assert (await admin.post(f"{API}/candidates/{candidate_id}/archive")).status_code == 200
    # Archiving cancelled the open interview; the registration follows on the next read.
    regs = (await link_env.recruiter.get(f"{API}/open-links/{link['id']}/registrations")).json()
    assert {r["full_name"]: r["status"] for r in regs["items"]}["Candidate 1"] == "cancelled"
    deleted = await admin.delete(f"{API}/candidates/{candidate_id}")
    assert deleted.status_code == 200, deleted.text
    async with session_scope() as db:
        remaining = (await db.scalars(select(LinkRegistration))).all()
        assert all(r.email != "candidate1@example.com" for r in remaining)
        assert sorted(r.status for r in remaining) == [
            "blocked",
            "registered",
        ]  # candidate 2 blocked, candidate 3
        assert (
            int(
                await db.scalar(
                    select(func.count()).select_from(Candidate).where(Candidate.id == candidate_id)
                )
            )
            == 0
        )
        audit = (await db.scalars(select(AuditLog).where(AuditLog.action == "candidate.delete"))).one()
    assert (
        audit.details["link_registrations"] == 1 and audit.details["link_blocked_registrations_deleted"] == 1
    )
    assert (
        deleted.json()["link_registrations"] == 1
        and deleted.json()["link_blocked_registrations_deleted"] == 1
    )
