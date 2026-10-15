"""Intake is a per-organisation plan feature, managed by the platform (G8)."""

from __future__ import annotations

import os
import secrets
import uuid

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.enums import Role
from tests.fixtures.core import TEST_DATABASE_URL, run_migrations

pytestmark = pytest.mark.integration

API = "/api/v1"


async def test_intake_is_hidden_until_the_platform_grants_it(auth_client, org_factory) -> None:
    org = await org_factory()  # a new tenant: no intake
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    me = (await recruiter.get(f"{API}/auth/me")).json()
    assert me["organization"]["features"] == {}
    listing = await recruiter.get(f"{API}/intake/interviews")
    assert listing.status_code == 404 and listing.json()["error"]["code"] == "intake_disabled"
    types = {t["round_type"] for t in (await recruiter.get(f"{API}/round-types")).json()["items"]}
    assert "intake" not in types and "technical_screening" in types

    job = await recruiter.post(f"{API}/jobs", json={"title": "Data analyst trainee", "status": "open"})
    assert job.status_code == 201, job.text
    intake_round = await recruiter.post(
        f"{API}/jobs/{job.json()['id']}/rounds", json={"round_type": "intake"}
    )
    assert intake_round.status_code == 404 and intake_round.json()["error"]["code"] == "intake_disabled"

    platform = await auth_client(Role.SUPER_ADMIN)
    granted = await platform.patch(f"{API}/organizations/{org}", json={"features": {"intake": True}})
    assert granted.status_code == 200, granted.text
    assert granted.json()["features"] == {"intake": True}

    assert (await recruiter.get(f"{API}/auth/me")).json()["organization"]["features"] == {"intake": True}
    assert (await recruiter.get(f"{API}/intake/interviews")).status_code == 200
    types = {t["round_type"] for t in (await recruiter.get(f"{API}/round-types")).json()["items"]}
    assert "intake" in types


async def test_organisations_cannot_grant_themselves_features(auth_client, org_factory) -> None:
    org = await org_factory()
    admin = await auth_client(Role.ADMIN, organization_id=org)
    response = await admin.patch(f"{API}/organizations/{org}", json={"features": {"intake": True}})
    assert response.status_code == 403


async def test_changing_a_round_into_an_intake_round_needs_the_feature(auth_client, org_factory) -> None:
    from app.db.models import Round
    from app.db.session import session_scope
    from tests.integration.resume.helpers import create_job

    org = await org_factory()
    recruiter = await auth_client(Role.RECRUITER, organization_id=org)
    job = await create_job(recruiter)
    async with session_scope() as db:
        row = Round(job_id=uuid.UUID(job["id"]), sequence=1, name="Screen", round_type="recruiter_screening")
        db.add(row)
        await db.flush()
        round_id = row.id
    changed = await recruiter.patch(f"{API}/rounds/{round_id}", json={"round_type": "intake"})
    assert changed.status_code == 404 and changed.json()["error"]["code"] == "intake_disabled"


async def test_the_migration_keeps_intake_for_organisations_that_only_set_up_rounds() -> None:
    # Review of G8: an intake round without any invitation yet (no intake rows, no saved settings) must keep intake.
    base = make_url(TEST_DATABASE_URL)
    name = f"interview_test_{os.getpid()}_{secrets.token_hex(4)}"
    url = base.set(database=name).render_as_string(hide_password=False)
    admin_dsn = base.set(database="postgres").render_as_string(hide_password=False).replace("+asyncpg", "")
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        await run_migrations(url, revision="0007")
        orgs = {"intake_round": uuid.uuid4(), "other": uuid.uuid4()}
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                for key, org_id in orgs.items():
                    job_id = uuid.uuid4()
                    await conn.exec_driver_sql(
                        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", (org_id, key, key)
                    )
                    await conn.exec_driver_sql(
                        "INSERT INTO jobs (id, organization_id, title, description) VALUES ($1, $2, 'Job', '')",
                        (job_id, org_id),
                    )
                    round_type = "intake" if key == "intake_round" else "technical_screening"
                    await conn.exec_driver_sql(
                        "INSERT INTO rounds (job_id, name, round_type) VALUES ($1, 'Round', $2)",
                        (job_id, round_type),
                    )
            await run_migrations(url)
            async with engine.connect() as conn:
                rows = (await conn.exec_driver_sql("SELECT id, features FROM organizations")).all()
        finally:
            await engine.dispose()
        features = {row[0]: row[1] for row in rows}
        assert features[orgs["intake_round"]] == {"intake": True}
        assert features[orgs["other"]] == {}
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
