"""Seeding a production-size catalog (10k questions, 1k coding problems).

Marked ``slow``: ``pytest tests/integration/seed -m slow``. Idempotency at
scale: unchanged rows untouched, changed rows updated in place, nothing
deleted; the 90 s budget per seed is generous (measured: about 2 s for the
first seed, 1.5 s for re-seeds, plus 4-6 s to parse the YAML).
"""

from __future__ import annotations

import dataclasses
import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BankQuestion, CodingQuestion
from app.seed.catalog import seed_catalog
from app.seed.loaders import SeedCatalog, load_catalog
from tests.unit.seed.synthetic import build_tree

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.timeout(900),
    pytest.mark.asyncio(loop_scope="session"),
]

BUDGET_S = 90


@pytest.fixture(scope="module")
def big_catalog(tmp_path_factory: pytest.TempPathFactory) -> SeedCatalog:
    root = build_tree(tmp_path_factory.mktemp("seed-scale") / "samples", questions=10_000, problems=1_000)
    return load_catalog(root, cache=False)


async def _seed(sessions: async_sessionmaker[AsyncSession], catalog: SeedCatalog) -> tuple[float, object]:
    started = time.perf_counter()
    async with sessions() as session:
        result = await seed_catalog(session, catalog=catalog)
        await session.commit()
    return time.perf_counter() - started, result


async def test_large_catalog_seeds_idempotently(
    seed_sessions: async_sessionmaker[AsyncSession], big_catalog: SeedCatalog
) -> None:
    questions, problems = len(big_catalog.questions), len(big_catalog.coding_questions)
    first_s, first = await _seed(seed_sessions, big_catalog)
    assert (first.questions.created, first.coding_questions.created) == (questions, problems)
    assert first_s < BUDGET_S

    again_s, again = await _seed(seed_sessions, big_catalog)
    assert (again.questions.unchanged, again.coding_questions.unchanged) == (questions, problems)
    assert again.questions.created == again.questions.updated == 0
    assert again_s < BUDGET_S

    edited = dataclasses.replace(
        big_catalog,
        questions=[
            q.model_copy(update={"text": q.text + " Be specific."}) if i % 20 == 0 else q
            for i, q in enumerate(big_catalog.questions)
        ],
        coding_questions=big_catalog.coding_questions[:-10],  # removed from YAML: kept in the DB
    )
    changed_s, changed = await _seed(seed_sessions, edited)
    assert changed.questions.updated == len(range(0, questions, 20))
    assert changed.coding_questions.unchanged == problems - 10
    assert changed_s < BUDGET_S
    async with seed_sessions() as session:
        stored_questions = await session.scalar(select(func.count()).select_from(BankQuestion))
        stored_problems = await session.scalar(select(func.count()).select_from(CodingQuestion))
    assert (stored_questions, stored_problems) == (questions, problems)
    print(
        f"\nseed {questions} questions + {problems} problems: first {first_s:.1f} s, unchanged {again_s:.1f} s, 5% edited {changed_s:.1f} s"
    )
