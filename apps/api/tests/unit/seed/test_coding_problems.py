"""Curated coding problems: inventory, structure, signatures, and every
reference solution and starter executed against all tests.

Python and JavaScript run through the validator's batched runner (one
process per language, :mod:`app.seed.runner`); TypeScript, C++, Java and Go
run explicit (authored) code with the local toolchain when it is installed
(skipped otherwise; verify in the sandbox runtime images) and are marked
``slow``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.enums import CodeLanguage, Difficulty
from app.seed.checks import coding_structure_problems
from app.seed.loaders import (
    FUNCTION_LANGUAGES,
    REQUIRED_REFERENCE_LANGUAGES,
    SIGNATURE_TYPES,
    CodingQuestionSeed,
    SeedCatalog,
    compare_results,
    entry_point_name,
    load_coding_questions,
    value_matches_type,
)
from app.seed.runner import node_binary, run_sql_sqlite, sql_failures
from app.seed.starters import starters_for
from app.seed.validate import Report
from app.services.coding.contracts import SandboxTestCase

from . import coding_harness as harness

PROBLEMS = load_coding_questions()
FUNCTION_PROBLEMS = [p for p in PROBLEMS if not p.is_sql]
SQL_PROBLEMS = [p for p in PROBLEMS if p.is_sql]
COMPILED_LANGUAGES = [CodeLanguage.JAVA, CodeLanguage.GO, CodeLanguage.CPP]
LEGACY_SLUGS = {
    "two_sum", "valid_parentheses", "merge_intervals", "top_k_frequent", "longest_substring_without_repeating",
    "number_of_islands", "product_except_self", "group_anagrams", "min_window_substring", "course_schedule",
    "second_highest_salary", "customers_without_orders", "department_top_earners",
}  # fmt: skip
_POOL = max(2, min(8, (os.cpu_count() or 2) // 2))


def _explicit(problem: CodingQuestionSeed, language: CodeLanguage) -> list[tuple[str, str]]:
    """Authored code of ``problem`` in ``language`` as (kind, code)."""
    out = []
    if language in problem.reference_solutions:
        out.append(("reference", problem.reference_solutions[language]))
    if language in problem.explicit_starter_code:
        out.append(("starter", problem.starter_code[language]))
    return out


# ------------------------------------------------------------------ inventory


def test_problem_inventory(catalog: SeedCatalog) -> None:
    slugs = {p.slug for p in PROBLEMS}
    assert len(FUNCTION_PROBLEMS) >= 10 and len(SQL_PROBLEMS) >= 3
    assert LEGACY_SLUGS <= slugs
    assert {p.difficulty for p in FUNCTION_PROBLEMS} == set(Difficulty)
    assert len(slugs) == len(PROBLEMS)
    assert LEGACY_SLUGS <= set(catalog.coding_by_slug())


# ------------------------------------------------------------------ structure


def test_structure_rules_hold_for_every_problem() -> None:
    failures = [f"{p.slug}: {message}" for p in PROBLEMS for message in coding_structure_problems(p)]
    assert failures == []


def test_tests_are_valid_sandbox_cases() -> None:
    for problem in PROBLEMS:
        for test in problem.tests:
            SandboxTestCase.model_validate(test.to_sandbox_case())


def test_signatures_languages_and_starters() -> None:
    failures: list[str] = []
    for problem in FUNCTION_PROBLEMS:
        sig = problem.signature
        assert sig is not None
        if sig.return_type not in SIGNATURE_TYPES or any(p.type not in SIGNATURE_TYPES for p in sig.params):
            failures.append(f"{problem.slug}: unsupported signature type")
        if set(problem.starter_code) != set(FUNCTION_LANGUAGES):
            failures.append(
                f"{problem.slug}: starters missing for {set(FUNCTION_LANGUAGES) - set(problem.starter_code)}"
            )
        if (
            not set(REQUIRED_REFERENCE_LANGUAGES)
            <= set(problem.reference_solutions)
            <= set(FUNCTION_LANGUAGES)
        ):
            failures.append(f"{problem.slug}: reference languages {sorted(problem.reference_solutions)}")
        for test in problem.tests:
            assert test.args is not None and len(test.args) == len(sig.params)
            assert all(value_matches_type(a, p.type) for a, p in zip(test.args, sig.params, strict=True))
            assert value_matches_type(test.expected, sig.return_type)
        for language, code in problem.starter_code.items():
            if entry_point_name(sig.function_name, language) not in code:
                failures.append(f"{problem.slug}: {language.value} starter lacks the entry point")
        generated = starters_for(
            sig.model_dump(mode="json", exclude_none=True),
            sorted(problem.generated_starter_languages),
            description=problem.title,
        )
        for language in problem.generated_starter_languages:
            assert problem.starter_code[language] == generated[language.value]
    assert failures == []


def test_sql_problem_structure() -> None:
    for problem in SQL_PROBLEMS:
        assert problem.languages == [CodeLanguage.SQL]
        assert problem.sql_setup and "CREATE TABLE" in problem.sql_setup.upper()
        assert all(t.expected_rows is not None for t in problem.tests)


# ------------------------------------------- execution (batched validator runner)


def test_every_reference_and_starter_passes_in_python_javascript_and_sqlite(validation: Report) -> None:
    """References pass every test; starters load, never raise and do not solve
    the problem (Python, JavaScript); SQL references pass on SQLite."""
    failures = [f"{f.file}: {f.message}" for f in validation.findings if f.file.startswith("coding/")]
    assert failures == []
    assert validation.counts["run_jobs"] >= 2 * len(FUNCTION_PROBLEMS) + len(SQL_PROBLEMS)
    if node_binary() is not None:
        assert not [n for n in validation.notes if "javascript" in n]


# ------------------------------------------------- other languages (local, slow)


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_typescript_references_and_starters_pass() -> None:
    available, reason = harness.toolchain_status(CodeLanguage.TYPESCRIPT)
    if not available:
        pytest.skip(f"{reason} not installed (verify in the sandbox runtime image)")
    report = harness.run_typescript(FUNCTION_PROBLEMS)
    failures = [f"{slug}: {m}" for slug, messages in report.errors.items() for m in messages]
    assert failures == []


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_typescript_sources_typecheck_strictly() -> None:
    available, reason = harness.toolchain_status(CodeLanguage.TYPESCRIPT)
    if not available:
        pytest.skip(f"{reason} not installed")
    sources = {}
    generated_samples = 0
    for problem in FUNCTION_PROBLEMS:
        codes = _explicit(problem, CodeLanguage.TYPESCRIPT)
        if (
            not codes
            and CodeLanguage.TYPESCRIPT in problem.generated_starter_languages
            and generated_samples < 25
        ):
            codes = [("generated", problem.starter_code[CodeLanguage.TYPESCRIPT])]
            generated_samples += 1
        for kind, code in codes:
            program = harness.build_program(problem, CodeLanguage.TYPESCRIPT, code)
            sources[f"{problem.slug}_{kind}.ts"] = program.files["main.ts"]
    assert harness.typecheck_typescript(sources) == ""


@pytest.mark.slow
@pytest.mark.timeout(1800)
@pytest.mark.parametrize("language", COMPILED_LANGUAGES, ids=lambda lang: lang.value)
def test_compiled_language_references_and_starters(language: CodeLanguage) -> None:
    available, reason = harness.toolchain_status(language)
    if not available:
        pytest.skip(f"{reason} not installed (verify in the sandbox runtime image)")

    def check(item: tuple[CodingQuestionSeed, str, str]) -> list[str]:
        problem, kind, code = item
        try:
            outcomes = harness.run_local(problem, language, code)
        except AssertionError as exc:
            return [f"{problem.slug} [{kind}]: {str(exc)[:500]}"]
        if kind == "reference":
            return [f"{problem.slug} [reference]: {m}" for m in harness.mismatches(problem, outcomes)]
        return [f"{problem.slug}/{o.test_id} [starter]: {o.error}" for o in outcomes if not o.ok]

    work = [(p, kind, code) for p in FUNCTION_PROBLEMS for kind, code in _explicit(p, language)]
    with ThreadPoolExecutor(max_workers=_POOL) as pool:
        failures = [m for result in pool.map(check, work) for m in result]
    assert failures == []


# ------------------------------------------------------------------------- SQL


def test_not_in_pitfall_is_caught() -> None:
    problem = next(p for p in SQL_PROBLEMS if p.slug == "customers_without_orders")
    wrong = "SELECT name FROM customers WHERE id NOT IN (SELECT customer_id FROM orders);"
    failures = sql_failures(problem, run_sql_sqlite(problem, wrong), "[sqlite]")
    assert any(m.startswith("[sqlite] t7") for m in failures)


def test_sql_submissions_follow_the_sandbox_rules() -> None:
    problem = SQL_PROBLEMS[0]
    two_statements = "SELECT 1; SELECT 2;"
    assert all("exactly one" in (error or "") for _, _, error in run_sql_sqlite(problem, two_statements))
    writes = "DELETE FROM employee"
    results = run_sql_sqlite(next(p for p in SQL_PROBLEMS if p.slug == "second_highest_salary"), writes)
    assert all("read-only" in (error or "") for _, _, error in results)


# ------------------------------------------- hidden tests catch common mistakes

BUGGY_SOLUTIONS = {
    # stores the element before looking up its complement -> returns [i, i]
    "two_sum": """
def two_sum(nums, target):
    seen = {}
    for i, v in enumerate(nums):
        seen[v] = i
        if target - v in seen:
            return [seen[target - v], i]
    return []
""",
    # counts brackets per type, ignoring order
    "valid_parentheses": """
def is_valid_parentheses(s):
    return all(s.count(o) == s.count(c) for o, c in ("()", "[]", "{}"))
""",
    # best sum initialised to zero
    "maximum_subarray_sum": """
def max_subarray_sum(nums):
    best = cur = 0
    for v in nums:
        cur = max(0, cur + v)
        best = max(best, cur)
    return best
""",
    # forgets to sort first
    "merge_intervals": """
def merge_intervals(intervals):
    merged = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged
""",
    # window start may move backwards ("abba")
    "longest_substring_without_repeating": """
def length_of_longest_substring(s):
    last, start, best = {}, 0, 0
    for i, ch in enumerate(s):
        if ch in last:
            start = last[ch] + 1
        last[ch] = i
        best = max(best, i - start + 1)
    return best
""",
    # searches the whole line for ERROR
    "most_frequent_error_service": """
def most_frequent_error_service(logs):
    counts = {}
    for line in logs:
        parts = line.split(" ")
        if "ERROR" in line and len(parts) >= 3:
            counts[parts[2]] = counts.get(parts[2], 0) + 1
    return min(counts, key=lambda s: (-counts[s], s)) if counts else ""
""",
    # integer division of the best window sum
    "max_average_subarray": """
def find_max_average(nums, k):
    return max(sum(nums[i:i + k]) for i in range(len(nums) - k + 1)) // k
""",
    # treats t as a set, ignoring duplicates
    "min_window_substring": """
def min_window(s, t):
    need, best = set(t), ""
    for i in range(len(s)):
        for j in range(i, len(s)):
            if need <= set(s[i:j + 1]):
                if not best or j - i + 1 < len(best):
                    best = s[i:j + 1]
                break
    return best
""",
}


@pytest.mark.parametrize("slug", sorted(BUGGY_SOLUTIONS))
def test_hidden_tests_catch_common_mistake(slug: str) -> None:
    problem = next(p for p in FUNCTION_PROBLEMS if p.slug == slug)
    outcomes = harness.run_python(problem, BUGGY_SOLUTIONS[slug])
    failing = {f.split(":")[0] for f in harness.mismatches(problem, outcomes)}
    hidden = {t.id for t in problem.hidden_tests}
    assert failing & hidden, f"no hidden test of {slug} catches the buggy solution"


# ----------------------------------------------------- comparison semantics


def test_unordered_comparison_is_order_insensitive_at_every_level() -> None:
    expected = [["bat"], ["nat", "tan"], ["ate", "eat", "tea"]]
    actual = [["tea", "ate", "eat"], ["bat"], ["tan", "nat"]]
    assert compare_results(actual, expected, return_type="string[][]", comparison="unordered")
    assert not compare_results(
        [["bat"], ["nat", "tan"]], expected, return_type="string[][]", comparison="unordered"
    )
    assert compare_results([1, 0], [0, 1], return_type="int[]", comparison="unordered")


def test_exact_comparison_distinguishes_bools_and_orders() -> None:
    assert compare_results(True, True, return_type="bool")
    assert not compare_results(1, True, return_type="bool")
    assert not compare_results([1, 0], [0, 1], return_type="int[]")
    assert compare_results(3.0, 3, return_type="long")
    assert compare_results(None, [], return_type="int[][]")  # nil Go slice


def test_float_tolerance() -> None:
    assert compare_results(
        12.750001, 12.75, return_type="float", comparison="float_tolerance", tolerance=1e-5
    )
    assert not compare_results(
        12.76, 12.75, return_type="float", comparison="float_tolerance", tolerance=1e-5
    )
    assert compare_results(7 / 3, 2.3333333333333335, return_type="float", comparison="float_tolerance")
