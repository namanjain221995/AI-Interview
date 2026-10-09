"""Small writable sample trees for loader and validator tests.

Copying the whole ``samples/`` tree per test does not scale (10k questions,
1k problems), and content workflows may move legacy files into group
directories, so files are located through the loader's ``sources``. The real
tree is read without raising (``collect_catalog``): behaviour tests must not
fail because unrelated content is being authored.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from app.seed.loaders import SeedCatalog, collect_catalog, default_samples_dir

QUESTION_KEYS = ("devops.kubernetes.crashloop_backoff", "devops.kubernetes.az_failure")
CODING_SLUGS = ("two_sum", "valid_parentheses", "second_highest_salary")


@cache
def _real() -> SeedCatalog:
    return collect_catalog(default_samples_dir().resolve())[0]


def source(record_id: str) -> Path:
    """Path of the file defining ``record_id``, relative to the samples root."""
    catalog = _real()
    return catalog.sources[record_id].relative_to(catalog.root)


def minimal_tree(
    target: Path, *, questions: Iterable[str] = QUESTION_KEYS, coding: Iterable[str] = CODING_SLUGS
) -> Path:
    """A valid tree with the catalog files, the demo jobs, the coding problems
    they pin plus ``coding`` and the question files defining ``questions``."""
    root = default_samples_dir()
    catalog = _real()
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "catalog", target / "catalog")
    shutil.copytree(root / "jobs", target / "jobs")
    (target / "question_bank").mkdir(exist_ok=True)
    (target / "coding").mkdir(exist_ok=True)
    pinned = {slug for job in catalog.jobs for r in job.rounds for slug in r.pinned_coding_slugs}
    for record_id in {*questions, *coding, *pinned}:
        relative = source(record_id)
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target / relative)
    return target


def edit_yaml(path: Path, mutate: Callable[[Any], Any]) -> None:
    """Load ``path``, mutate the document in place (return values are ignored) and write it back."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def write_yaml(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
