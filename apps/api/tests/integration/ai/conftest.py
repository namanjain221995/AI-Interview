"""Database fixtures for AI-layer integration tests.

The platform plugin (``tests/fixtures/core.py``) points ``DATABASE_URL`` at a
per-process, migrated test database (``test_database`` fixture). The AI
retrieval tests create their own throwaway schema inside it so they never
touch application tables; when the platform plugin is absent they fall back
to the configured database.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

if importlib.util.find_spec("tests.fixtures.core") is None:

    @pytest.fixture(scope="session")
    def test_database() -> str:
        return get_settings().database_url


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def pg_schema(test_database: str) -> AsyncIterator[tuple[AsyncEngine, str]]:
    url = test_database or get_settings().database_url
    schema = f"ai_it_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001
        await admin.dispose()
        pytest.skip(f"postgres not usable: {type(exc).__name__}: {exc}"[:300])
    engine = create_async_engine(
        url, poolclass=NullPool, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    try:
        yield engine, schema
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def pg_session(pg_schema: tuple[AsyncEngine, str]) -> AsyncIterator[AsyncSession]:
    engine, _ = pg_schema
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        yield session
        await session.rollback()
