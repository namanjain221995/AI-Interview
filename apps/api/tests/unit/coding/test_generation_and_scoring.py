"""Generated-problem validation and the evaluation guardrails (pure parts)."""

from __future__ import annotations

import json

import pytest

from app.services.coding.evaluation import assistance_factor, fallback_evaluation, score_cap
from app.services.coding.generation import GeneratedProblemInvalid, slugify, validate_generated
from app.services.coding.schemas import GeneratedCodingProblem, GeneratedParam, GeneratedTest

REFERENCE = "def max_pair_sum(nums: list[int]) -> int:\n    a, b = sorted(nums)[-2:]\n    return a + b\n"


def problem(**overrides) -> GeneratedCodingProblem:
    tests = [
        GeneratedTest(args_json=json.dumps([[1, 2, 3]]), expected_json="5"),
        GeneratedTest(args_json=json.dumps([[5, 5]]), expected_json="10"),
        GeneratedTest(args_json=json.dumps([[0, -1, -2]]), expected_json="-1"),
        GeneratedTest(args_json=json.dumps([[9, 1]]), expected_json="10", hidden=True),
        GeneratedTest(args_json=json.dumps([[-5, -6, -7]]), expected_json="-11", hidden=True),
        GeneratedTest(args_json=json.dumps([[100, 200, 300]]), expected_json="500", hidden=True),
        GeneratedTest(
            args_json=json.dumps([[1, 1, 1, 1]]), expected_json="2", hidden=True, description="all equal"
        ),
    ]
    data = {
        "title": "Maximum Pair Sum",
        "prompt_markdown": "Return the largest sum of two distinct elements of `nums`.\n\n"
        + "Example: [1,2,3] -> 5. " * 4,
        "function_name": "max_pair_sum",
        "params": [GeneratedParam(name="nums", type="int[]")],
        "return_type": "int",
        "tests": tests,
        "reference_solution_python": REFERENCE,
        "hints": [
            "Think about which two values matter.",
            "You only need the two largest values.",
            "Track the largest and second largest in one pass.",
        ],
        "expected_concepts": ["single pass", "tracking two maxima"],
        "time_complexity": "O(n)",
        "space_complexity": "O(1)",
        "rubric_excellent": "One pass, O(1) space.",
        "rubric_good": "Sorting based.",
        "rubric_weak": "Nested loops.",
    }
    data.update(overrides)
    return GeneratedCodingProblem(**data)


def test_valid_problem_builds_signature_and_test_ids() -> None:
    validated = validate_generated(problem())
    assert validated.signature["function_name"] == "max_pair_sum"
    assert validated.signature["params"] == [{"name": "nums", "type": "int[]"}]
    assert [t["id"] for t in validated.tests] == [f"t{i}" for i in range(1, 8)]
    assert sum(t["hidden"] for t in validated.tests) == 4
    assert validated.tests[-1]["description"] == "all equal"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tests": [GeneratedTest(args_json="[[1,2]]", expected_json="3")] * 7}, "hidden"),
        ({"function_name": "class"}, "signature"),
        ({"reference_solution_python": "def other(nums):\n    return 0\n"}, "reference_solution_python"),
    ],
)
def test_invalid_problems_are_rejected(overrides, message) -> None:
    with pytest.raises(GeneratedProblemInvalid) as info:
        validate_generated(problem(**overrides))
    assert message in str(info.value)


def test_test_values_are_type_checked_against_the_signature() -> None:
    bad = problem().tests.copy()
    bad[0] = GeneratedTest(args_json='[["a", "b"]]', expected_json="5")
    with pytest.raises(GeneratedProblemInvalid, match="not a valid int"):
        validate_generated(problem(tests=bad))
    bad[0] = GeneratedTest(args_json="[[1, 2]]", expected_json='"five"')
    with pytest.raises(GeneratedProblemInvalid, match="expected value"):
        validate_generated(problem(tests=bad))
    bad[0] = GeneratedTest(args_json="not json", expected_json="1")
    with pytest.raises(GeneratedProblemInvalid, match="valid JSON"):
        validate_generated(problem(tests=bad))
    bad[0] = GeneratedTest(args_json="[[1], 2]", expected_json="1")
    with pytest.raises(GeneratedProblemInvalid, match="1 elements"):
        validate_generated(problem(tests=bad))


def test_slugify() -> None:
    assert slugify("Two Sum (variant #2)") == "two_sum_variant_2"
    assert slugify("123 go") == "p_123_go"


@pytest.mark.parametrize(
    ("hints", "factor"), [(-1, 1.0), (0, 1.0), (1, 0.93), (2, 0.86), (3, 0.79), (4, 0.79), (12, 0.79)]
)
def test_assistance_factor(hints: int, factor: float) -> None:
    assert assistance_factor(hints) == pytest.approx(factor)


def test_assistance_factor_matches_scoring_rule() -> None:
    from app.services.scoring.compute import assistance_factor as scoring_factor

    for hints in range(-1, 8):
        assert assistance_factor(hints) == scoring_factor(hints)
    assert assistance_factor(3, max_hints=2) == pytest.approx(0.86)


def test_score_cap_and_fallback() -> None:
    assert score_cap(0, 0) is None
    assert score_cap(0, 10) == 3.0
    assert score_cap(10, 10) == 10.0
    assert score_cap(5, 10) == 6.5
    fallback = fallback_evaluation(3, 4, hints_used=2)
    assert fallback.tests_passed == 3 and fallback.tests_total == 4
    assert fallback.confidence < 0.5
    assert 0 <= fallback.score_estimate <= 10
    assert "2 hint" in fallback.hints_impact
