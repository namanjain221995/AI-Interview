"""Fixtures for the catalog seeding integration tests.

Each test module gets its own throwaway PostgreSQL database
(``interview_seedtest_<pid>_<hex>``) created through the server's ``postgres``
maintenance database, migrated with ``alembic upgrade head`` (falling back to
``Base.metadata.create_all`` with a warning if the migrations cannot run) and
dropped afterwards. Databases left by crashed runs are reaped on start.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import uuid
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Organization, User
from app.domain.enums import Role

API_ROOT = Path(__file__).resolve().parents[3]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: heavy seeding checks (deselect with -m 'not slow')")


DB_PREFIX = "interview_seedtest_"
_DB_PATTERN = re.compile(rf"^{DB_PREFIX}(\d+)_[0-9a-f]+$")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _urls() -> tuple[str, str, str]:
    """(admin asyncpg DSN, test SQLAlchemy URL, test database name)."""
    base = make_url(Settings().database_url)  # type: ignore[call-arg]
    name = f"{DB_PREFIX}{os.getpid()}_{secrets.token_hex(4)}"
    admin = base.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    return admin, base.set(database=name).render_as_string(hide_password=False), name


def _alembic_upgrade(sync_connection: Any) -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.attributes["connection"] = sync_connection
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")


async def _create_schema(url: str) -> str:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for extension in ("vector", "pg_trgm", "pgcrypto"):
                await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
        if (API_ROOT / "alembic.ini").exists() and any((API_ROOT / "alembic" / "versions").glob("*.py")):
            try:
                async with engine.connect() as conn:
                    await conn.run_sync(_alembic_upgrade)
                    await conn.commit()
                return "alembic"
            except Exception as exc:  # migrations are written concurrently by another module
                warnings.warn(
                    f"alembic upgrade head failed ({type(exc).__name__}: {exc}); using Base.metadata.create_all",
                    stacklevel=1,
                )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return "create_all"
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def seed_database() -> AsyncIterator[tuple[str, str]]:
    """Yields (database URL, schema method: 'alembic' | 'create_all')."""
    import asyncpg

    admin_dsn, url, name = _urls()
    try:
        admin = await asyncio.wait_for(asyncpg.connect(admin_dsn), timeout=10)
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not reachable ({type(exc).__name__})")
    try:
        for row in await admin.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE $1", f"{DB_PREFIX}%"
        ):
            match = _DB_PATTERN.match(row["datname"])
            if match and not _pid_alive(int(match.group(1))):
                with contextlib.suppress(Exception):
                    await admin.execute(f'DROP DATABASE IF EXISTS "{row["datname"]}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    try:
        method = await _create_schema(url)
        yield url, method
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def seed_engine(seed_database: tuple[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(seed_database[0], poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def seed_sessions(seed_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(seed_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(loop_scope="session")
async def make_org(
    seed_sessions: async_sessionmaker[AsyncSession],
) -> Callable[[], Awaitable[tuple[uuid.UUID, uuid.UUID]]]:
    """Factory creating a committed organization plus a recruiter; returns (org_id, user_id)."""

    async def factory() -> tuple[uuid.UUID, uuid.UUID]:
        suffix = secrets.token_hex(4)
        async with seed_sessions() as session:
            org = Organization(
                id=uuid.uuid4(), name=f"Seed Test {suffix}", slug=f"seed-{suffix}", settings={}
            )
            session.add(org)
            await session.flush()
            user = User(
                id=uuid.uuid4(),
                organization_id=org.id,
                email=f"recruiter-{suffix}@seed.test",
                full_name="Seed Recruiter",
                password_hash="not-a-real-hash",  # noqa: S106 - never used to log in
                role=Role.RECRUITER.value,
            )
            session.add(user)
            await session.commit()
            return org.id, user.id

    return factory
