"""Batched reference runner: every failure mode is reported per problem and
per test, crashes and hangs only cost the job that caused them."""

from __future__ import annotations

import time

import pytest

from app.domain.enums import CodeLanguage
from app.seed.loaders import CodingQuestionSeed, load_coding_questions
from app.seed.runner import (
    node_binary,
    postgres_refusal,
    run_function_problems,
    run_sql_problems_sqlite,
    single_statement,
    split_sql_statements,
)

# Non-raising load: other problems may be mid-authoring.
PROBLEMS = {p.slug: p for p in load_coding_questions(problems=[]) if p.slug in {"two_sum"} or p.is_sql}
needs_node = pytest.mark.skipif(node_binary() is None, reason="node is not installed")


def variant(
    slug: str, *, python: str | None = None, javascript: str | None = None, starter: str | None = None
) -> CodingQuestionSeed:
    base = PROBLEMS["two_sum"]
    references = dict(base.reference_solutions)
    if python is not None:
        references[CodeLanguage.PYTHON] = python
    if javascript is not None:
        references[CodeLanguage.JAVASCRIPT] = javascript
    starters = dict(base.starter_code)
    if starter is not None:
        starters[CodeLanguage.PYTHON] = starter
    return base.model_copy(update={"slug": slug, "reference_solutions": references, "starter_code": starters})


GOOD_PY = PROBLEMS["two_sum"].reference_solutions[CodeLanguage.PYTHON]


def test_wrong_answers_show_expected_and_actual_per_test() -> None:
    report = run_function_problems(
        [variant("wrong", python="def two_sum(nums, target):\n    return [0, 0]\n")],
        languages=[CodeLanguage.PYTHON],
    )
    messages = report.errors["wrong"]
    assert messages[0] == "[python reference] t1: expected [0, 1], got [0, 0] (Example 1)"
    assert messages[-1].startswith("[python reference] ... and ") and len(messages) == 6


def test_exceptions_are_grouped_by_error() -> None:
    report = run_function_problems(
        [variant("raises", python="def two_sum(nums, target):\n    raise ValueError('boom')\n")],
        languages=[CodeLanguage.PYTHON],
    )
    assert report.errors["raises"] == [
        "[python reference] t1, t2, t3 (+7 more): raised ValueError: boom (line 2)"
    ]


def test_timeouts_crashes_and_syntax_errors_are_isolated() -> None:
    problems = [
        variant("loop", python="def two_sum(nums, target):\n    while True:\n        pass\n"),
        variant("crash", python="import os\ndef two_sum(nums, target):\n    os._exit(3)\n"),
        variant("syntax", python="def two_sum(nums, target)\n    return []\n"),
        variant("missing", python="def other(nums, target):\n    return []\n"),
        variant("fine", python=GOOD_PY),
    ]
    started = time.perf_counter()
    report = run_function_problems(problems, languages=[CodeLanguage.PYTHON], timeout=1.0, workers=1)
    assert time.perf_counter() - started < 30
    assert report.errors["loop"] == ["[python reference] timed out: exceeded 1.0 s"]
    assert report.errors["crash"][0].startswith("[python reference] crashed the runner: runner exited (3)")
    assert report.errors["syntax"] == [
        "[python reference] does not compile: SyntaxError: expected ':' (line 1)"
    ]
    assert report.errors["missing"] == [
        "[python reference] fails to load: entry point 'two_sum' is not defined"
    ]
    assert "fine" not in report.errors


def test_starters_must_load_and_must_not_solve_the_problem() -> None:
    problems = [
        variant("solved", starter=GOOD_PY),
        variant("broken_starter", starter="def two_sum(nums, target):\n    return nums[99]\n"),
    ]
    report = run_function_problems(problems, languages=[CodeLanguage.PYTHON])
    assert report.errors["solved"] == [
        "[python starter] the starter already passes every test (add discriminating tests)"
    ]
    # nums[99] only exists in the 1000-element hidden test: 9 of 10 tests raise.
    assert report.errors["broken_starter"] == [
        "[python starter] t1, t2, t3 (+6 more): raised IndexError: list index out of range (line 2)"
    ]


def test_python_values_are_normalised_like_the_sandbox() -> None:
    as_tuple = (
        "def two_sum(nums, target):\n    seen = {}\n    for i, v in enumerate(nums):\n"
        "        if target - v in seen:\n            return (seen[target - v], i)\n        seen[v] = i\n"
    )
    as_class = (
        "class Solution:\n    def two_sum(self, nums, target):\n        seen = {}\n"
        "        for i, v in enumerate(nums):\n            if target - v in seen:\n"
        "                return [seen[target - v], i]\n            seen[v] = i\n        return []\n"
    )
    report = run_function_problems(
        [variant("tuple", python=as_tuple), variant("cls", python=as_class)],
        languages=[CodeLanguage.PYTHON],
        starters=False,
    )
    assert report.errors == {}
    assert report.jobs == 2


@needs_node
def test_javascript_failures_timeouts_and_entry_points() -> None:
    problems = [
        variant("js_loop", javascript="function twoSum(nums, target) { while (true) {} }"),
        variant("js_syntax", javascript="function twoSum(nums, target) { return [; }"),
        variant("js_throw", javascript="function twoSum(n, t) { throw new Error('bad'); }"),
        variant(
            "js_class",
            javascript="class Solution { twoSum(nums, target) { const m = new Map(); for (let i = 0; i < nums.length; i++) { if (m.has(target - nums[i])) return [m.get(target - nums[i]), i]; m.set(nums[i], i); } return []; } }",
        ),
        variant(
            "js_exports",
            javascript="module.exports = { twoSum(nums, target) { for (let i = 0; i < nums.length; i++) for (let j = i + 1; j < nums.length; j++) if (nums[i] + nums[j] === target) return [i, j]; return []; } };",
        ),
    ]
    report = run_function_problems(problems, languages=[CodeLanguage.JAVASCRIPT], timeout=1.0, starters=False)
    assert report.errors["js_loop"] == ["[javascript reference] timed out: exceeded 1.0 s"]
    assert report.errors["js_syntax"][0].startswith("[javascript reference] does not compile: SyntaxError")
    assert report.errors["js_throw"][0].startswith(
        "[javascript reference] t1, t2, t3 (+7 more): raised Error: bad"
    )
    assert "js_class" not in report.errors and "js_exports" not in report.errors


def test_sql_rules_and_postgres_guard() -> None:
    assert split_sql_statements("SELECT 1; -- note; ignored\nSELECT ';';") == ["SELECT 1;", "SELECT ';';"]
    assert single_statement("SELECT 1;  ") == "SELECT 1"
    with pytest.raises(ValueError, match="exactly one"):
        single_statement("SELECT 1; SELECT 2")
    sql_problems = [p for p in PROBLEMS.values() if p.is_sql]
    report = run_sql_problems_sqlite(sql_problems)
    assert report.errors == {} and report.jobs == 2 * len(sql_problems)
    assert postgres_refusal("CREATE TABLE t (id int);", "SELECT * FROM t") is None
    for bad in (
        "COPY t TO PROGRAM 'id'",
        "SELECT * FROM public.users",
        "RESET ROLE",
        "SELECT pg_read_file('x')",
    ):
        assert postgres_refusal(bad) is not None, bad
