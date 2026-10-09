"""Migration 0005 (INTAKE_INTERVIEW.md 3.4): upgrade / downgrade to 0004 / re-upgrade on a scratch database,
metadata parity (``alembic check``), the partial unique index, and cascades (candidate erasure, organisation)."""

from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models import (
    Candidate,
    IntakeActivityResult,
    IntakeInterview,
    IntakeOrgSettings,
    IntakeStageResult,
    Interview,
    InterviewSession,
    Job,
    Organization,
    Round,
)
from app.db.session import session_scope
from tests.fixtures.core import TEST_DATABASE_URL, run_migrations

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[3]
INTAKE_TABLES = {
    "intake_interviews",
    "intake_org_settings",
    "intake_stage_results",
    "intake_activity_results",
    "intake_evaluations",
    "intake_evaluator_runs",
    "intake_competency_scores",
    "intake_decisions",
}


def _alembic(sync_connection: Any, action: str, target: str) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.attributes["connection"] = sync_connection
    cfg.attributes["skip_logging_config"] = True
    getattr(command, action)(cfg, target)


def _compare(sync_connection: Any) -> list[Any]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    context = MigrationContext.configure(sync_connection, opts={"compare_type": True})
    return compare_metadata(context, Base.metadata)


async def _tables(conn: Any) -> set[str]:
    rows = (
        (
            await conn.exec_driver_sql(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'intake_%'"
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def test_upgrade_downgrade_and_metadata_parity() -> None:
    base = make_url(TEST_DATABASE_URL)
    name = f"interview_test_{os.getpid()}_{secrets.token_hex(4)}"
    url = base.set(database=name).render_as_string(hide_password=False)
    admin_dsn = base.set(database="postgres").render_as_string(hide_password=False).replace("+asyncpg", "")
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        await run_migrations(url)
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                assert await _tables(conn) == INTAKE_TABLES
                assert await conn.run_sync(_compare) == []
                version = (await conn.exec_driver_sql("SELECT version_num FROM alembic_version")).scalar_one()
                assert version == "0006"  # 0006: nullable scores.score (no evidence = NULL, never a midpoint)
                nullable = (
                    await conn.exec_driver_sql(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'scores' AND column_name = 'score'"
                    )
                ).scalar_one()
                assert nullable == "YES"
                index = (
                    await conn.exec_driver_sql(
                        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_intake_interviews_interview'"
                    )
                ).scalar_one()
                assert "UNIQUE" in index and "WHERE (interview_id IS NOT NULL)" in index
                default = (
                    await conn.exec_driver_sql(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_name = 'intake_org_settings' AND column_name = 'work_authorization'"
                    )
                ).scalar_one()
                assert '"enabled": false' in default and '"ask_sponsorship": true' in default
            async with engine.connect() as conn:
                await conn.run_sync(_alembic, "downgrade", "0004")
                await conn.commit()
                assert await _tables(conn) == set()
                version = (await conn.exec_driver_sql("SELECT version_num FROM alembic_version")).scalar_one()
                assert version == "0004"
            async with engine.connect() as conn:
                await conn.run_sync(_alembic, "upgrade", "head")
                await conn.commit()
                assert await _tables(conn) == INTAKE_TABLES
                assert await conn.run_sync(_compare) == []
        finally:
            await engine.dispose()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _graph(org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Candidate -> interview -> session -> intake row (+ stage, activity rows) and org settings."""
    async with session_scope() as db:
        job = Job(organization_id=org_id, title="Program", description="d", status="open")
        candidate = Candidate(
            organization_id=org_id, full_name="Erase Me", email=f"erase-{secrets.token_hex(3)}@x.test"
        )
        db.add_all([job, candidate])
        await db.flush()
        round_ = Round(job_id=job.id, name="Intake", round_type="intake", config={})
        db.add(round_)
        await db.flush()
        interview = Interview(
            organization_id=org_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_.id,
            invite_token_hash=secrets.token_hex(32),
            invite_expires_at=func.now(),
        )
        db.add(interview)
        await db.flush()
        session = InterviewSession(
            interview_id=interview.id,
            organization_id=org_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_.id,
            status="completed",
        )
        db.add(session)
        await db.flush()
        intake = IntakeInterview(
            organization_id=org_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_.id,
            interview_id=interview.id,
            status="active",
        )
        db.add(intake)
        await db.flush()
        db.add(
            IntakeStageResult(
                organization_id=org_id,
                intake_id=intake.id,
                session_id=session.id,
                stage_key="greeting",
                stage_number=1,
            )
        )
        db.add(
            IntakeActivityResult(
                organization_id=org_id,
                intake_id=intake.id,
                session_id=session.id,
                kind="reading_aloud",
                stage_key="reading",
                material_key="A",
                kit_version="v1",
            )
        )
        db.add(IntakeOrgSettings(organization_id=org_id, program_name="Program"))
        return {"candidate": candidate.id, "intake": intake.id, "round": round_.id, "job": job.id}


async def _count(model: Any, *where: Any) -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_cascades_and_constraints(org_factory) -> None:
    org_id = await org_factory()
    ids = await _graph(org_id)
    assert await _count(IntakeInterview) == 1 and await _count(IntakeStageResult) == 1
    # Deleting a round keeps the intake (round_id SET NULL); here a draft on a round without sessions.
    async with session_scope() as db:
        spare = Round(job_id=ids["job"], name="Spare intake", round_type="intake", sequence=2, config={})
        db.add(spare)
        await db.flush()
        draft = IntakeInterview(
            organization_id=org_id, candidate_id=ids["candidate"], job_id=ids["job"], round_id=spare.id
        )
        db.add(draft)
        await db.flush()
        spare_id, draft_id = spare.id, draft.id
    async with session_scope() as db:
        await db.delete(await db.get(Round, spare_id))
    async with session_scope() as db:
        row = await db.get(IntakeInterview, draft_id)
        assert row is not None and row.round_id is None and row.status == "draft"
    # Candidate erasure removes every intake row with it.
    async with session_scope() as db:
        await db.delete(await db.get(Candidate, ids["candidate"]))
    assert await _count(IntakeInterview) == 0
    assert await _count(IntakeStageResult) == 0 and await _count(IntakeActivityResult) == 0
    assert await _count(IntakeOrgSettings, IntakeOrgSettings.organization_id == org_id) == 1
    async with session_scope() as db:
        await db.delete(await db.get(Organization, org_id))
    assert await _count(IntakeOrgSettings, IntakeOrgSettings.organization_id == org_id) == 0


async def test_interview_unique_and_jsonb_nulls(org_factory) -> None:
    org_id = await org_factory()
    ids = await _graph(org_id)
    async with session_scope() as db:
        first = await db.get(IntakeInterview, ids["intake"])
        first.eligibility = None  # SQL NULL, not the JSON 'null'
        drafts = [
            IntakeInterview(organization_id=org_id, candidate_id=first.candidate_id, job_id=first.job_id)
            for _ in range(2)
        ]
        db.add_all(drafts)  # several drafts without an interview are fine
    async with session_scope() as db:
        nulls = await db.scalar(
            select(func.count()).select_from(IntakeInterview).where(IntakeInterview.eligibility.is_(None))
        )
        assert nulls == 3
        row = await db.scalar(select(IntakeInterview).where(IntakeInterview.interview_id.isnot(None)))
        assert row.status == "active" and row.title == "Intake Interview" and row.settings == {}
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with session_scope() as db:
            source = await db.get(IntakeInterview, ids["intake"])
            db.add(
                IntakeInterview(
                    organization_id=org_id,
                    candidate_id=source.candidate_id,
                    job_id=source.job_id,
                    interview_id=source.interview_id,
                )
            )
