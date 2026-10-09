"""Seed data and Alembic migrations (round trip on a scratch database, no drift)."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Organization, User
from app.db.session import session_scope
from app.seed.core import DEMO_ORG_SLUG, call_seed_fn, registered_steps, seed_core, seed_step
from tests.fixtures.core import TEST_DATABASE_URL, run_migrations

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------- seed


async def test_seed_core_is_idempotent(test_database, _db_cleanup):
    async with session_scope() as db:
        first = await seed_core(db)
    async with session_scope() as db:
        second = await seed_core(db)
    assert first["organization_id"] == second["organization_id"]
    assert first["users"] == second["users"]
    assert first["created"]["organization"] is True and len(first["created"]["users"]) == 4
    assert second["created"] == {"organization": False, "users": []}
    assert set(first["users"]) == {"SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"}
    async with session_scope() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        assert org is not None and org.name == "Demo Corp"
        assert org.settings["candidate_report_mode"] == "after_review"
        roles = {u.role for u in (await db.scalars(select(User).where(User.organization_id == org.id))).all()}
        assert roles == {"SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"}


async def test_seeded_accounts_can_sign_in(client):
    settings = get_settings()
    async with session_scope() as db:
        context = await seed_core(db)
    password = settings.seed_password.get_secret_value()
    for role, email in context["user_emails"].items():
        response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, (role, response.text)
        assert response.json()["principal"]["role"] == role


async def test_seed_steps_receive_context(test_database, _db_cleanup):
    seen: dict[str, Any] = {}

    @seed_step("test-step", order=99)
    async def step(db: Any, context: dict[str, Any]) -> dict[str, Any]:
        seen.update(context)
        return {"extra": 1}

    async def db_only(db: Any) -> str:
        return "ok"

    try:
        assert any(s.name == "test-step" for s in registered_steps())
        async with session_scope() as db:
            context = await seed_core(db)
            assert await call_seed_fn(step, db, context) == {"extra": 1}
            assert await call_seed_fn(db_only, db, context) == "ok"
        assert seen["organization_id"] == context["organization_id"]
    finally:
        from app.seed import core

        core._STEPS.pop("test-step", None)


async def test_seed_cli_core_only_hides_credentials(test_database, _db_cleanup):
    env = dict(os.environ)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.seed", "--core-only"],
        cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    password = get_settings().seed_password.get_secret_value()
    assert "Demo Corp" in result.stdout and password not in result.stdout + result.stderr
    shown = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.seed", "--core-only", "--print-credentials"],
        cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert shown.returncode == 0 and password in shown.stdout


# ---------------------------------------------------------------- migrations


def _compare(sync_connection: Any) -> list[Any]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    context = MigrationContext.configure(sync_connection, opts={"compare_type": True})
    return compare_metadata(context, Base.metadata)


def _downgrade_base(sync_connection: Any) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.attributes["connection"] = sync_connection
    cfg.attributes["skip_logging_config"] = True
    command.downgrade(cfg, "base")


async def test_test_database_matches_models(test_database):
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            assert await conn.run_sync(_compare) == []
    finally:
        await engine.dispose()


async def test_migration_round_trip_on_scratch_database():
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
                assert await conn.run_sync(_compare) == []
                indexes = await conn.exec_driver_sql(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE indexname LIKE '%hnsw%' ORDER BY indexname"
                )
                rows = indexes.all()
                assert [r[0] for r in rows] == ["ix_question_bank_embedding_hnsw", "ix_resume_chunks_embedding_hnsw"]
                assert all("USING hnsw" in r[1] and "vector_cosine_ops" in r[1] for r in rows)
                extensions = {r[0] for r in (await conn.exec_driver_sql("SELECT extname FROM pg_extension")).all()}
                assert {"vector", "pg_trgm", "pgcrypto"} <= extensions
                default = (
                    await conn.exec_driver_sql(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_name='users' AND column_name='id'"
                    )
                ).scalar_one()
                assert default == "gen_random_uuid()"
            async with engine.connect() as conn:
                await conn.run_sync(_downgrade_base)
                await conn.commit()
                tables = (
                    await conn.exec_driver_sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars().all()
                assert set(tables) <= {"alembic_version"}
        finally:
            await engine.dispose()
        await run_migrations(url)  # re-applying after a full downgrade works
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                count = (
                    await conn.exec_driver_sql("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
                ).scalar_one()
                assert count == len(Base.metadata.tables) + 1
        finally:
            await engine.dispose()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

