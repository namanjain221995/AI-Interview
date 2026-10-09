"""Fixtures for the catalog integration tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import pytest_asyncio

from app.db.session import session_scope


@pytest_asyncio.fixture
async def seeded_catalog(_db_cleanup: None) -> Any:
    from app.seed.catalog import seed_catalog

    async with session_scope() as db:
        return await seed_catalog(db)


@pytest.fixture
def catalog_worker(worker_factory: Callable[..., Any]) -> Any:
    import app.workers.catalog  # noqa: F401 - registers question_bank.embed / catalog.backfill_embeddings

    return worker_factory(job_types=["question_bank.embed", "catalog.backfill_embeddings"])
