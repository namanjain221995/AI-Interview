"""Fixtures for the jobs / rounds / invitation integration tests."""

from __future__ import annotations

from typing import Any

import pytest_asyncio

from app.db.session import session_scope


@pytest_asyncio.fixture
async def seeded_catalog(_db_cleanup: None) -> Any:
    """The global catalog (competencies, default templates, questions, coding
    questions) seeded into this test's database (embeddings left NULL)."""
    from app.seed.catalog import seed_catalog

    async with session_scope() as db:
        return await seed_catalog(db)
