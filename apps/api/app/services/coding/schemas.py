"""Coding-module structured outputs that are not part of the shared AI
contract (``app/schemas/ai.py``): the generated-problem schema.

Test arguments and expected values travel as JSON *strings* so the schema
stays strict for guided decoding (free-form ``Any`` values are not reliably
supported by every structured-output backend); they are parsed and
type-checked against the signature before the problem is used.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.ai import AIModel

SignatureTypeName = Literal[
    "int",
    "long",
    "float",
    "bool",
    "string",
    "int[]",
    "long[]",
    "float[]",
    "string[]",
    "bool[]",
    "int[][]",
    "string[][]",
]


class GeneratedParam(AIModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,39}$", description="snake_case parameter name")
    type: SignatureTypeName


class GeneratedTest(AIModel):
    args_json: str = Field(
        ..., max_length=4000, description="JSON array of positional arguments, e.g. [[2,7,11,15], 9]"
    )
    expected_json: str = Field(..., max_length=2000, description="JSON value of the expected return value")
    hidden: bool = False
    description: str = Field("", max_length=200)


class GeneratedCodingProblem(AIModel):
    title: str = Field(..., min_length=3, max_length=120)
    prompt_markdown: str = Field(
        ..., min_length=80, max_length=5000, description="Problem statement with examples and constraints"
    )
    function_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,59}$", description="snake_case function name")
    params: list[GeneratedParam] = Field(..., min_length=1, max_length=4)
    return_type: SignatureTypeName
    comparison: Literal["exact", "unordered", "float_tolerance"] = "exact"
    tests: list[GeneratedTest] = Field(..., min_length=7, max_length=16)
    reference_solution_python: str = Field(..., min_length=20, max_length=6000)
    hints: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Level 1 nudge, level 2 direction, level 3 near-solution (no code)",
    )
    expected_concepts: list[str] = Field(..., min_length=2, max_length=8)
    time_complexity: str = Field(..., max_length=40)
    space_complexity: str = Field(..., max_length=40)
    rubric_excellent: str = Field(..., max_length=400)
    rubric_good: str = Field(..., max_length=400)
    rubric_weak: str = Field(..., max_length=400)
    followups: list[str] = Field(default_factory=list, max_length=4)
    common_errors: list[str] = Field(default_factory=list, max_length=4)
