"""Shared fixtures for the catalog content tests.

Heavy checks are marked ``slow`` (multi-language compilation, synthetic
scale runs): ``pytest tests/unit/seed -m "not slow"`` skips them.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.seed.fairness import fairness_hits
from app.seed.loaders import SeedCatalog, load_catalog
from app.seed.validate import Report, validate


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: heavy catalog checks (deselect with -m 'not slow')")


def protected_hits(texts: Iterable[str], *, evaluation: bool = False) -> list[str]:
    """Fairness-lint findings for ``texts`` (see :mod:`app.seed.fairness`)."""
    return [hit.describe() for text in texts for hit in fairness_hits(text, evaluation=evaluation)]


@pytest.fixture(scope="session")
def catalog() -> SeedCatalog:
    return load_catalog()


@pytest.fixture(scope="session")
def validation() -> Report:
    """The validator over the whole samples tree, once per session (no cache,
    references and starters executed in Python and JavaScript, SQL on SQLite)."""
    return validate(use_cache=False)
