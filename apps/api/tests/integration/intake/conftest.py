"""Intake tests run as an organisation whose plan includes intake (G8)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest_asyncio


@pytest_asyncio.fixture
async def org_factory(org_factory: Callable[..., Awaitable[uuid.UUID]]) -> Callable[..., Awaitable[uuid.UUID]]:
    async def make(**kwargs: Any) -> uuid.UUID:
        kwargs.setdefault("features", {"intake": True})
        return await org_factory(**kwargs)

    return make
