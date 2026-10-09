from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest_asyncio

from .factories import SessionFixture, create_completed_session


@pytest_asyncio.fixture
async def session_factory(
    org_factory: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[SessionFixture]]:
    """``fixture = await session_factory(organization_id=None, **scenario)``."""

    async def make(organization_id: Any = None, **kwargs: Any) -> SessionFixture:
        org = organization_id or await org_factory()
        return await create_completed_session(org, **kwargs)

    return make
