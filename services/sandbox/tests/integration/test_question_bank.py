"""Cross-check with the question bank (samples/coding): every reference solution
passes all its tests in every language, and every starter template compiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.integration

SAMPLES = Path(__file__).resolve().parents[4] / "samples" / "coding"
TEST_FIELDS = ("id", "args", "expected", "setup_sql", "expected_rows", "ordered", "hidden")


def load_problems() -> list[dict[str, Any]]:
    if not SAMPLES.is_dir():
        return []
    return [yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(SAMPLES.glob("*.yaml"))]


PROBLEMS = load_problems()
CASES = [(p["slug"], lang) for p in PROBLEMS for lang in p.get("languages", [])]
BY_SLUG = {p["slug"]: p for p in PROBLEMS}


def sandbox_tests(problem: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: t[k] for k in TEST_FIELDS if k in t} for t in problem["tests"]]


@pytest.mark.skipif(not CASES, reason="samples/coding not available")
@pytest.mark.parametrize(("slug", "language"), CASES)
def test_reference_solution_passes_and_starter_compiles(sandbox, slug, language):
    problem = BY_SLUG[slug]
    common: dict[str, Any] = {"language": language, "mode": "tests", "tests": sandbox_tests(problem)}
    if problem.get("signature"):
        common["signature"] = problem["signature"]
    if problem.get("sql_setup"):
        common["sql_setup"] = problem["sql_setup"]
    result = sandbox.execute(code=problem["reference_solutions"][language], **common)
    failures = [(t["id"], t["error"], t["actual"], t["expected"]) for t in result["tests"] if not t["passed"]]
    assert result["status"] == "passed", (result["status"], result["compile_output"], failures[:3])
    assert result["passed"] == len(problem["tests"])
    starter = problem.get("starter_code", {}).get(language)
    if starter:
        started = sandbox.execute(code=starter, **common)
        assert started["status"] != "compile_error", started["compile_output"]
