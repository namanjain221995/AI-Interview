"""Validator at production scale on a synthetic tree (10k questions, 1k problems).

Marked ``slow`` (about 30 s): ``pytest tests/unit/seed -m slow -k scale``.
Time limits are generous (the machine is shared); typical numbers are in
samples/README.md.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.seed.loaders import load_catalog
from app.seed.validate import validate

from .synthetic import build_tree

pytestmark = [pytest.mark.slow, pytest.mark.timeout(900)]


@pytest.fixture(scope="module")
def big_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_tree(tmp_path_factory.mktemp("scale") / "samples", questions=10_000, problems=1_000)


def test_synthetic_tree_loads(big_tree: Path) -> None:
    catalog = load_catalog(big_tree, cache=False)
    assert len(catalog.questions) >= 10_000 and len(catalog.coding_questions) >= 1_000
    assert len(catalog.roles) >= 28
    assert sum(1 for p in catalog.coding_questions if p.generated_starter_languages) >= 900


def test_whole_tree_and_single_file_validation_scale(
    big_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEED_VALIDATE_CACHE_DIR", str(tmp_path / "cache"))
    started = time.perf_counter()
    whole = validate(big_tree)
    cold = time.perf_counter() - started
    errors = [f"{f.file}: {f.message}" for f in whole.findings if f.severity == "error"]
    assert errors == []
    assert whole.counts["run_jobs"] >= 2_000
    assert cold < 300, f"whole tree took {cold:.1f} s"

    started = time.perf_counter()
    warm = validate(big_tree)
    assert warm.counts["parsed_files"] == 0 and warm.counts["run_jobs"] == 0
    assert time.perf_counter() - started < cold

    problem = next(big_tree.glob("coding/graphs/*.yaml"))
    problem.write_text(problem.read_text(encoding="utf-8").replace("\ntitle: ", "\ntitle: Revised ", 1))
    started = time.perf_counter()
    single = validate(big_tree, [str(problem)])
    elapsed = time.perf_counter() - started
    assert single.errors == 0 and single.counts["run_jobs"] >= 1
    assert elapsed < 15, f"single file took {elapsed:.1f} s"
