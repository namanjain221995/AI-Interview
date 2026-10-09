"""Fixtures for the candidate lifecycle tests (archive / restore / erasure)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest_asyncio

from app.db.session import session_scope
from tests.integration.reports.factories import SessionFixture, create_completed_session


@pytest_asyncio.fixture
async def seeded_catalog(_db_cleanup: None) -> Any:
    """Global catalog (default templates are needed to add rounds to a job)."""
    from app.seed.catalog import seed_catalog

    async with session_scope() as db:
        return await seed_catalog(db)


@pytest_asyncio.fixture
async def session_factory(
    org_factory: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[SessionFixture]]:
    """``fixture = await session_factory(organization_id=None, **scenario)`` (completed session + all rows)."""

    async def make(organization_id: Any = None, **kwargs: Any) -> SessionFixture:
        org = organization_id or await org_factory()
        return await create_completed_session(org, **kwargs)

    return make
