"""Helpers for the coding unit tests (no database, no network)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.db.models import CodingQuestion
from app.domain.enums import CodeLanguage, Difficulty
from app.seed.loaders import CodingQuestionSeed, load_coding_questions
from app.services.coding.contracts import CodingContext

_SEEDS: dict[str, CodingQuestionSeed] | None = None


def seeds() -> dict[str, CodingQuestionSeed]:
    global _SEEDS
    if _SEEDS is None:
        _SEEDS = {s.slug: s for s in load_coding_questions()}
    return _SEEDS


def make_question(slug: str = "two_sum", **overrides: Any) -> CodingQuestion:
    row = seeds()[slug].to_row()
    row.update(overrides)
    return CodingQuestion(id=uuid.uuid4(), organization_id=None, **row)


def make_context(**overrides: Any) -> CodingContext:
    data: dict[str, Any] = {
        "coding_session_id": str(uuid.uuid4()),
        "question_title": "Two Sum",
        "prompt_markdown": "Given nums and target return indices.",
        "difficulty": Difficulty.EASY,
        "language": CodeLanguage.PYTHON,
        "current_code": "def two_sum(nums, target):\n    return []\n",
        "previous_code": None,
        "recent_diff": None,
        "snapshots_count": 1,
        "runs": [],
        "elapsed_s": 300.0,
        "time_limit_s": 1200,
        "seconds_since_last_edit": 30.0,
        "expected_concepts": ["hash map"],
        "optimal_complexity": {"time": "O(n)", "space": "O(n)"},
    }
    data.update(overrides)
    return CodingContext(**data)


@pytest.fixture
def two_sum() -> CodingQuestion:
    return make_question("two_sum")


@pytest.fixture
def sql_question() -> CodingQuestion:
    return make_question("second_highest_salary")
