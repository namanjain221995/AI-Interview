"""Catalog and demo-content loaders.

Parses the curated YAML under ``samples/`` and validates every record against
the platform contracts before anything touches the database:

* competencies            -> ``competencies`` columns + rubric shape
* round templates         -> :class:`app.schemas.rounds.RoundConfig`
* question bank           -> ``question_bank`` columns + enums
* coding problems         -> ``coding_questions`` columns, the language-neutral
                             signature convention and the sandbox test format
                             (:class:`app.services.coding.contracts.SandboxTestCase`)
* demo jobs               -> ``jobs`` / ``job_requirements`` / ``rounds`` columns,
                             :class:`IntegrityPolicy`, :class:`RecordingPolicy`
* roles (optional)        -> ``catalog/roles.yaml``: the role vocabulary for
                             ``role_tags`` (validation only, not stored)

Layout: ``question_bank/**/*.yaml`` (any depth) and ``coding/<group>/<slug>.yaml``
(top-level ``coding/<slug>.yaml`` files stay valid); file names are sorted by
their path relative to the samples root, dot-files are ignored and ``.yml``
files are reported (use ``.yaml``). Keys (``external_key``, ``slug``) are
unique across the whole tree.

Cross-references (competency keys, template round types, pinned coding slugs,
role tags) are checked by :func:`validate_catalog`. The module has no database
dependency, so the seeders in :mod:`app.seed.catalog`, the validator CLI
(:mod:`app.seed.validate`), tests and tooling all share one validation path.

Signature convention (implemented by the sandbox harnesses)
-----------------------------------------------------------
``{"function_name": snake_case, "params": [{"name", "type"}], "return_type",
"comparison": "exact" | "unordered" | "float_tolerance", "tolerance"?}``

Types: ``int, long, float, bool, string, int[], long[], float[], string[],
bool[], int[][], string[][]``. Entry points: Python snake_case top-level
function; JavaScript/TypeScript top-level lowerCamelCase function; Java and C++
public lowerCamelCase method of ``class Solution``; Go top-level lowerCamelCase
func in ``package main`` (see :func:`entry_point_name`). ``long`` values stay
within +/-(2**53 - 1) so JavaScript numbers represent them exactly.
:func:`compare_results` is the reference implementation of the comparison
modes. ``starter_code`` may omit any function language: the missing starters
are generated deterministically from the signature
(:func:`app.seed.starters.starters_for` with the title as description);
explicit starters win and are validated.
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from app.domain.enums import (
    BankStatus,
    CandidateReportMode,
    CodeLanguage,
    CompetencyCategory,
    Difficulty,
    JobStatus,
    QuestionKind,
    RequirementKind,
    RoundType,
)
from app.schemas.ai import CodingEvaluation, DimensionScores
from app.schemas.policies import IntegrityPolicy, RecordingPolicy
from app.schemas.rounds import RoundConfig
from app.seed.starters import FUNCTION_LANGUAGES, entry_point_name, starters_for, to_lower_camel

SAMPLES_DIR_ENV = "SAMPLES_DIR"
SCHEMA_VERSION = 1

COMPETENCIES_FILE = Path("catalog/competencies.yaml")
ROUND_TEMPLATES_FILE = Path("catalog/round_templates.yaml")
ROLES_FILE = Path("catalog/roles.yaml")
QUESTION_BANK_DIR = Path("question_bank")
CODING_DIR = Path("coding")
JOBS_DIR = Path("jobs")

#: Reference solutions that must exist for every function-style problem.
REQUIRED_REFERENCE_LANGUAGES: tuple[CodeLanguage, ...] = (CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT)

SignatureType = Literal[
    "int", "long", "float", "bool", "string",
    "int[]", "long[]", "float[]", "string[]", "bool[]",
    "int[][]", "string[][]",
]  # fmt: skip
SIGNATURE_TYPES: tuple[str, ...] = (
    "int", "long", "float", "bool", "string",
    "int[]", "long[]", "float[]", "string[]", "bool[]",
    "int[][]", "string[][]",
)  # fmt: skip
Comparison = Literal["exact", "unordered", "float_tolerance"]
DEFAULT_FLOAT_TOLERANCE = 1e-6

#: Legacy vocabulary for ``question_bank.role_tags`` / ``coding_questions.role_tags``
#: (every tag used by the original samples). Always allowed; when
#: ``catalog/roles.yaml`` exists its role keys are allowed as well
#: (:meth:`SeedCatalog.allowed_role_tags`).
KNOWN_ROLE_TAGS: frozenset[str] = frozenset(
    {
        "general",
        "devops",
        "sre",
        "platform",
        "cloud",
        "security",
        "backend",
        "frontend",
        "fullstack",
        "data",
        "ml",
        "llm",
        "engineering_manager",
        "tech_lead",
    }
)
LEGACY_ROLE_TAGS = KNOWN_ROLE_TAGS

#: Answer-evaluation dimensions a rubric category may roll up
#: (``DimensionScores`` plus the numeric ``CodingEvaluation`` criteria).
ALLOWED_RUBRIC_DIMENSIONS: frozenset[str] = frozenset(DimensionScores.model_fields) | frozenset(
    {"code_quality", "problem_solving", "edge_case_handling", "testing_approach"}
    & set(CodingEvaluation.model_fields)
)

_INT32 = (-(2**31), 2**31 - 1)
_SAFE_INT = 2**53 - 1

# Identifiers that would not compile as parameter / function names in at
# least one supported language (after the per-language case conversion).
_RESERVED_WORDS: frozenset[str] = frozenset(
    """
    and as assert async await break case catch char class const continue def default defer del delete do
    double elif else enum except export extends false final finally float for from func function go goto
    if implements import in instanceof int interface is lambda let long map new nil none nonlocal not null
    or package pass private protected public raise range return select short static string struct super
    switch template this throw true try type typeof typename union unsigned var void volatile while with
    yield chan fallthrough auto register signed sizeof bool boolean byte native operator friend
    """.split()
)

SnakeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=2, max_length=80)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=1, max_length=60)]
ExternalKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$", max_length=120)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=2, max_length=120)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
Sentence = Annotated[str, StringConstraints(min_length=8)]


class SeedDataNotFound(FileNotFoundError):
    """The samples directory (or one of its files) does not exist."""


class SeedValidationError(ValueError):
    """One or more seed records failed validation. ``problems`` lists every
    failure as ``"<file>: <record>: <message>"`` so authors can fix them in one
    pass."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        preview = "\n  - ".join(self.problems[:40])
        more = f"\n  ... and {len(self.problems) - 40} more" if len(self.problems) > 40 else ""
        super().__init__(f"{len(self.problems)} seed validation problem(s):\n  - {preview}{more}")


# =============================================================================
# Type helpers (shared with harness generators; naming helpers live in
# app.seed.starters and are re-exported here)
# =============================================================================


def is_array_type(type_name: str) -> bool:
    return type_name.endswith("[]")


def element_type(type_name: str) -> str:
    if not is_array_type(type_name):
        raise ValueError(f"{type_name} is not an array type")
    return type_name[:-2]


def value_matches_type(value: Any, type_name: str) -> bool:
    """True when the JSON value ``value`` is a legal instance of ``type_name``."""
    if is_array_type(type_name):
        return isinstance(value, list) and all(value_matches_type(v, element_type(type_name)) for v in value)
    if type_name == "bool":
        return isinstance(value, bool)
    if type_name == "int":
        return isinstance(value, int) and not isinstance(value, bool) and _INT32[0] <= value <= _INT32[1]
    if type_name == "long":
        return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= _SAFE_INT
    if type_name == "float":
        return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    if type_name == "string":
        return isinstance(value, str)
    raise ValueError(f"unknown signature type {type_name!r}")


# =============================================================================
# Result comparison (reference semantics for the sandbox harnesses)
# =============================================================================


def _order_key(value: Any) -> tuple[Any, ...]:
    if value is None:
        return (0,)
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, int | float):
        return (2, float(value))
    if isinstance(value, str):
        return (3, value)
    if isinstance(value, list):
        return (4, tuple(_order_key(v) for v in value))
    return (5, repr(value))


def canonicalize_unordered(value: Any) -> Any:
    """Order-insensitive canonical form: every array is sorted at every
    nesting level (inner arrays first, then the outer array)."""
    if isinstance(value, list):
        return sorted((canonicalize_unordered(v) for v in value), key=_order_key)
    return value


def _float_close(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_float_close(a, e, tolerance) for a, e in zip(actual, expected, strict=True))
        )
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(float(actual) - float(expected)) <= tolerance * max(1.0, abs(float(expected)))
    return bool(actual == expected)


def _strict_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual is expected
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_strict_equal(a, e) for a, e in zip(actual, expected, strict=True))
        )
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return float(actual) == float(expected)
    return bool(actual == expected)


def compare_results(
    actual: Any,
    expected: Any,
    *,
    return_type: str,
    comparison: str = "exact",
    tolerance: float | None = None,
) -> bool:
    """Compare a decoded JSON result with the expected value.

    * ``exact``: deep equality; booleans never equal numbers; ``1`` equals ``1.0``.
    * ``unordered``: deep equality after :func:`canonicalize_unordered` on both sides.
    * ``float_tolerance``: ``|a - e| <= tolerance * max(1, |e|)`` element-wise
      (``tolerance`` defaults to 1e-6).

    For array return types a ``null`` result (e.g. a nil Go slice) is treated
    as an empty array.
    """
    if actual is None and is_array_type(return_type):
        actual = []
    if comparison == "unordered":
        return _strict_equal(canonicalize_unordered(actual), canonicalize_unordered(expected))
    if comparison == "float_tolerance":
        return _float_close(actual, expected, tolerance or DEFAULT_FLOAT_TOLERANCE)
    if comparison == "exact":
        return _strict_equal(actual, expected)
    raise ValueError(f"unknown comparison {comparison!r}")


def compare_sql_rows(
    actual: Sequence[Sequence[Any]], expected: Sequence[Sequence[Any]], *, ordered: bool
) -> bool:
    """Result-set comparison for SQL problems (rows are compared as tuples;
    numeric values compare by value, ``ordered`` keeps row order significant)."""
    norm_actual = [list(r) for r in actual]
    norm_expected = [list(r) for r in expected]
    if not ordered:
        norm_actual = sorted(norm_actual, key=_order_key)
        norm_expected = sorted(norm_expected, key=_order_key)
    return _strict_equal(norm_actual, norm_expected)


# =============================================================================
# Seed record schemas
# =============================================================================


class _Seed(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class CompetencyRubricSeed(_Seed):
    levels: dict[str, Sentence]
    dimensions: list[SnakeKey] = Field(..., min_length=2, max_length=12)

    @field_validator("levels", mode="before")
    @classmethod
    def _stringify_keys(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): v for k, v in value.items()}
        return value

    @field_validator("levels")
    @classmethod
    def _anchor_levels(cls, value: dict[str, str]) -> dict[str, str]:
        missing = {"1", "3", "5"} - set(value)
        unknown = set(value) - {"1", "2", "3", "4", "5"}
        if missing:
            raise ValueError(f"rubric levels must include anchors 1, 3 and 5 (missing {sorted(missing)})")
        if unknown:
            raise ValueError(f"rubric levels must be 1-5 (got {sorted(unknown)})")
        return dict(sorted(value.items()))

    @field_validator("dimensions")
    @classmethod
    def _unique_dimensions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("rubric dimensions must be unique")
        return value


class CompetencySeed(_Seed):
    key: SnakeKey
    name: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    category: CompetencyCategory
    description: Sentence
    rubric: CompetencyRubricSeed
    is_active: bool = True

    def to_row(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "rubric": self.rubric.model_dump(mode="json"),
            "is_active": self.is_active,
        }


class RoundTemplateSeed(_Seed):
    round_type: RoundType
    name: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    description: Sentence
    is_default: bool = True
    config: RoundConfig

    @model_validator(mode="before")
    @classmethod
    def _inject_identity(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and isinstance(data.get("config"), Mapping):
            config = dict(data["config"])
            config.setdefault("round_type", data.get("round_type"))
            config.setdefault("name", data.get("name"))
            data = {**data, "config": config}
        return data

    @model_validator(mode="after")
    def _identity_matches(self) -> RoundTemplateSeed:
        if self.config.round_type != self.round_type:
            raise ValueError(
                f"config.round_type {self.config.round_type} != template round_type {self.round_type}"
            )
        return self

    def to_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "round_type": self.round_type.value,
            "config": self.config.model_dump(mode="json"),
            "is_default": self.is_default,
        }


class QuestionRubricSeed(_Seed):
    excellent: Sentence
    good: Sentence
    weak: Sentence
    dimensions: dict[SnakeKey, Sentence] | None = None


class BankQuestionSeed(_Seed):
    external_key: ExternalKey
    role_tags: list[SnakeKey] = Field(..., min_length=1, max_length=8)
    round_types: list[RoundType] = Field(..., min_length=1, max_length=12)
    competency_key: SnakeKey
    topic: Annotated[str, StringConstraints(min_length=3, max_length=120)]
    difficulty: Difficulty
    kind: QuestionKind
    text: Annotated[str, StringConstraints(min_length=20, max_length=600)]
    expected_concepts: list[NonEmpty] = Field(..., min_length=2, max_length=12)
    rubric: QuestionRubricSeed
    followups: list[Sentence] = Field(..., min_length=1, max_length=6)
    common_errors: list[Sentence] = Field(default_factory=list, max_length=6)
    status: BankStatus = BankStatus.ACTIVE

    @field_validator("role_tags")
    @classmethod
    def _unique_role_tags(cls, value: list[str]) -> list[str]:
        # The vocabulary (legacy tags + roles.yaml keys) is a cross-reference
        # check in validate_catalog().
        if len(set(value)) != len(value):
            raise ValueError("role_tags must be unique")
        return value

    @field_validator("round_types")
    @classmethod
    def _unique_round_types(cls, value: list[RoundType]) -> list[RoundType]:
        if len(set(value)) != len(value):
            raise ValueError("round_types must be unique")
        return value

    def to_row(self) -> dict[str, Any]:
        return {
            "external_key": self.external_key,
            "role_tags": list(self.role_tags),
            "round_types": [r.value for r in self.round_types],
            "competency_key": self.competency_key,
            "topic": self.topic,
            "difficulty": self.difficulty.value,
            "kind": self.kind.value,
            "text": self.text,
            "expected_concepts": list(self.expected_concepts),
            "rubric": self.rubric.model_dump(mode="json", exclude_none=True),
            "followups": list(self.followups),
            "common_errors": list(self.common_errors),
            "source": "curated",
            "status": self.status.value,
        }

    def embedding_text(self) -> str:
        """Text the embedding job should embed (stable across seeds)."""
        return f"{self.topic}. {self.text} Concepts: {', '.join(self.expected_concepts)}"


class SignatureParam(_Seed):
    name: Identifier
    type: SignatureType


class CodingSignature(_Seed):
    function_name: Identifier
    params: list[SignatureParam] = Field(..., min_length=1, max_length=6)
    return_type: SignatureType
    comparison: Comparison = "exact"
    tolerance: float | None = Field(None, gt=0, lt=1)

    @model_validator(mode="after")
    def _consistent(self) -> CodingSignature:
        names = [p.name for p in self.params]
        if len(set(names)) != len(names):
            raise ValueError("parameter names must be unique")
        for ident in [self.function_name, *names]:
            for form in {ident, to_lower_camel(ident)}:
                if form.lower() in _RESERVED_WORDS:
                    raise ValueError(f"{form!r} is a reserved word in a supported language")
        if self.tolerance is not None and self.comparison != "float_tolerance":
            raise ValueError("tolerance is only meaningful with comparison=float_tolerance")
        if self.comparison == "float_tolerance" and self.return_type not in ("float", "float[]"):
            raise ValueError("float_tolerance requires a float or float[] return type")
        if self.comparison == "unordered" and not is_array_type(self.return_type):
            raise ValueError("unordered comparison requires an array return type")
        return self


class CodingTestSeed(_Seed):
    """``coding_questions.tests`` item. Fields mirror
    :class:`app.services.coding.contracts.SandboxTestCase` plus ``description``
    (staff-facing; never shown to candidates for hidden tests)."""

    id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", max_length=40)]
    args: list[Any] | None = None
    expected: Any = None
    setup_sql: str | None = None
    expected_rows: list[list[Any]] | None = None
    ordered: bool = False
    hidden: bool = False
    description: str | None = Field(None, max_length=300)

    def to_sandbox_case(self) -> dict[str, Any]:
        """Payload accepted by ``SandboxTestCase`` (``description`` dropped)."""
        return self.model_dump(mode="json", exclude={"description"})


class CodingRubricSeed(_Seed):
    excellent: Sentence
    good: Sentence
    weak: Sentence
    criteria: dict[SnakeKey, Sentence] = Field(..., min_length=3)


class CodingQuestionSeed(_Seed):
    slug: Slug
    title: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    difficulty: Difficulty
    competency_keys: list[SnakeKey] = Field(..., min_length=1, max_length=8)
    role_tags: list[SnakeKey] = Field(..., min_length=1, max_length=8)
    prompt_markdown: Annotated[str, StringConstraints(min_length=80)]
    languages: list[CodeLanguage] = Field(..., min_length=1)
    signature: CodingSignature | None = None
    #: Function problems may omit any language: missing starters are generated
    #: from the signature after validation (see ``generated_starter_languages``).
    starter_code: dict[CodeLanguage, NonEmpty] = Field(default_factory=dict)
    reference_solutions: dict[CodeLanguage, NonEmpty]
    tests: list[CodingTestSeed] = Field(..., min_length=3, max_length=100)
    sql_setup: str | None = None
    expected_concepts: list[NonEmpty] = Field(..., min_length=2, max_length=12)
    optimal_complexity: dict[Literal["time", "space"], NonEmpty]
    rubric: CodingRubricSeed
    hints: list[Sentence] = Field(..., min_length=3, max_length=3, description="Exactly 3 graduated hints")
    followups: list[Sentence] = Field(..., min_length=1, max_length=8)
    common_errors: list[Sentence] = Field(..., min_length=1, max_length=8)
    time_limit_minutes: int = Field(20, ge=5, le=120)
    status: BankStatus = BankStatus.ACTIVE

    _generated_starters: frozenset[CodeLanguage] = PrivateAttr(default=frozenset())

    @field_validator("role_tags")
    @classmethod
    def _unique_role_tags(cls, value: list[str]) -> list[str]:
        # The vocabulary (legacy tags + roles.yaml keys) is a cross-reference
        # check in validate_catalog().
        if len(set(value)) != len(value):
            raise ValueError("role_tags must be unique")
        return value

    @field_validator("optimal_complexity")
    @classmethod
    def _complexity_complete(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"time", "space"}:
            raise ValueError("optimal_complexity needs both 'time' and 'space'")
        return value

    @property
    def is_sql(self) -> bool:
        return self.languages == [CodeLanguage.SQL]

    @property
    def visible_tests(self) -> list[CodingTestSeed]:
        return [t for t in self.tests if not t.hidden]

    @property
    def hidden_tests(self) -> list[CodingTestSeed]:
        return [t for t in self.tests if t.hidden]

    @property
    def generated_starter_languages(self) -> frozenset[CodeLanguage]:
        """Function languages whose starter was generated (absent from the YAML)."""
        return self._generated_starters

    @property
    def explicit_starter_code(self) -> dict[CodeLanguage, str]:
        """The starters written in the YAML (generated ones excluded)."""
        return {
            lang: code for lang, code in self.starter_code.items() if lang not in self._generated_starters
        }

    @model_validator(mode="after")
    def _consistent(self) -> CodingQuestionSeed:
        ids = [t.id for t in self.tests]
        if len(set(ids)) != len(ids):
            raise ValueError("test ids must be unique")
        if len(self.visible_tests) < 2 or len(self.hidden_tests) < 5:
            raise ValueError(
                f"need >= 2 visible and >= 5 hidden tests (got {len(self.visible_tests)} visible, "
                f"{len(self.hidden_tests)} hidden)"
            )
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("languages must be unique")
        if CodeLanguage.SQL in self.languages:
            self._check_sql()
        else:
            self._check_function()
        return self

    def _check_sql(self) -> None:
        if not self.is_sql:
            raise ValueError("SQL problems support exactly one language: sql")
        if self.signature is not None:
            raise ValueError("SQL problems compare result sets; leave signature empty")
        if not self.sql_setup or "create table" not in self.sql_setup.lower():
            raise ValueError("SQL problems need sql_setup with CREATE TABLE statements")
        for lang_map, label in (
            (self.starter_code, "starter_code"),
            (self.reference_solutions, "reference_solutions"),
        ):
            if set(lang_map) != {CodeLanguage.SQL}:
                raise ValueError(f"{label} must contain exactly the 'sql' entry")
        for test in self.tests:
            if test.args is not None or test.expected is not None:
                raise ValueError(f"test {test.id}: SQL tests use setup_sql/expected_rows, not args/expected")
            if test.expected_rows is None:
                raise ValueError(f"test {test.id}: expected_rows is required")

    def _check_function(self) -> None:
        if self.signature is None:
            raise ValueError("function problems need a signature")
        if self.sql_setup:
            raise ValueError("sql_setup is only valid for SQL problems")
        if set(self.languages) != set(FUNCTION_LANGUAGES):
            raise ValueError(f"function problems must support {[lang.value for lang in FUNCTION_LANGUAGES]}")
        stray_starters = sorted(lang.value for lang in set(self.starter_code) - set(FUNCTION_LANGUAGES))
        if stray_starters:
            raise ValueError(f"starter_code has unsupported languages {stray_starters}")
        missing_refs = [
            lang.value for lang in REQUIRED_REFERENCE_LANGUAGES if lang not in self.reference_solutions
        ]
        if missing_refs:
            raise ValueError(f"reference_solutions missing {missing_refs}")
        stray_refs = sorted(lang.value for lang in set(self.reference_solutions) - set(FUNCTION_LANGUAGES))
        if stray_refs:
            raise ValueError(f"function problems cannot have reference solutions in {stray_refs}")
        sig = self.signature
        for label, code_map in (
            ("starter_code", self.starter_code),
            ("reference_solutions", self.reference_solutions),
        ):
            for lang, code in code_map.items():
                entry = entry_point_name(sig.function_name, lang)
                if not re.search(rf"\b{re.escape(entry)}\s*\(", code):
                    raise ValueError(f"{label}.{lang.value} does not define entry point {entry!r}")
                if lang in (CodeLanguage.JAVA, CodeLanguage.CPP) and not re.search(
                    r"\bclass\s+Solution\b", code
                ):
                    raise ValueError(f"{label}.{lang.value} must declare class Solution")
                if lang == CodeLanguage.GO and not re.search(r"^\s*package\s+main\b", code, re.MULTILINE):
                    raise ValueError(f"{label}.go must be in package main")
        for test in self.tests:
            if test.setup_sql is not None or test.expected_rows is not None:
                raise ValueError(f"test {test.id}: function tests use args/expected")
            if test.args is None or len(test.args) != len(sig.params):
                raise ValueError(f"test {test.id}: expected {len(sig.params)} positional args")
            for param, arg in zip(sig.params, test.args, strict=True):
                if not value_matches_type(arg, param.type):
                    raise ValueError(f"test {test.id}: argument {param.name!r} is not a valid {param.type}")
            if not value_matches_type(test.expected, sig.return_type):
                raise ValueError(f"test {test.id}: expected value is not a valid {sig.return_type}")
        missing = [lang for lang in FUNCTION_LANGUAGES if lang not in self.starter_code]
        if missing:
            generated = starters_for(
                sig.model_dump(mode="json", exclude_none=True), missing, description=self.title
            )
            explicit = self.starter_code
            self.starter_code = {
                lang: explicit[lang] if lang in explicit else generated[lang.value]
                for lang in FUNCTION_LANGUAGES
            }
            self._generated_starters = frozenset(missing)

    def to_row(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "difficulty": self.difficulty.value,
            "competency_keys": list(self.competency_keys),
            "role_tags": list(self.role_tags),
            "prompt_markdown": self.prompt_markdown,
            "languages": [lang.value for lang in self.languages],
            "signature": self.signature.model_dump(mode="json", exclude_none=True) if self.signature else {},
            "starter_code": {lang.value: code for lang, code in self.starter_code.items()},
            "reference_solutions": {lang.value: code for lang, code in self.reference_solutions.items()},
            "tests": [t.model_dump(mode="json", exclude_none=True) for t in self.tests],
            "sql_setup": self.sql_setup,
            "expected_concepts": list(self.expected_concepts),
            "optimal_complexity": dict(self.optimal_complexity),
            "rubric": self.rubric.model_dump(mode="json"),
            "hints": list(self.hints),
            "followups": list(self.followups),
            "common_errors": list(self.common_errors),
            "time_limit_minutes": self.time_limit_minutes,
            "source": "curated",
            "status": self.status.value,
        }

    def embedding_text(self) -> str:
        return f"{self.title}. {self.prompt_markdown[:1500]} Concepts: {', '.join(self.expected_concepts)}"


class CodingRequirementsSeed(_Seed):
    """``jobs.coding_requirements``."""

    enabled: bool = False
    languages: list[CodeLanguage] = Field(default_factory=lambda: list(FUNCTION_LANGUAGES))
    difficulty: Difficulty = Difficulty.MEDIUM


class JobRequirementSeed(_Seed):
    kind: RequirementKind
    name: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    competency_key: SnakeKey | None = None
    description: str | None = None
    weight: float = Field(1.0, ge=0, le=10)
    min_years: float | None = Field(None, ge=0, le=40)

    def to_row(self, sort_order: int) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "competency_key": self.competency_key,
            "description": self.description,
            "weight": self.weight,
            "min_years": self.min_years,
            "sort_order": sort_order,
        }


class JobRoundSeed(_Seed):
    """A job round = a default template + overrides (deep-merged; lists replace)."""

    template: RoundType
    name: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    candidate_selectable: bool = False
    is_active: bool = True
    pinned_coding_slugs: list[Slug] = Field(default_factory=list, max_length=5)
    overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _no_identity_override(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"round_type", "name", "candidate_selectable"} & set(value)
        if forbidden:
            raise ValueError(f"set {sorted(forbidden)} on the round itself, not in overrides")
        return value


class JobSeed(_Seed):
    key: Slug
    title: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    department: Annotated[str, StringConstraints(max_length=120)] | None = None
    location: Annotated[str, StringConstraints(max_length=120)] | None = None
    employment_type: Annotated[str, StringConstraints(max_length=40)] | None = None
    seniority: Annotated[str, StringConstraints(max_length=40)] | None = None
    description: Annotated[str, StringConstraints(min_length=80)]
    experience_min_years: float | None = Field(None, ge=0, le=40)
    experience_max_years: float | None = Field(None, ge=0, le=50)
    status: JobStatus = JobStatus.OPEN
    interview_instructions: str | None = None
    coding_requirements: CodingRequirementsSeed = Field(default_factory=CodingRequirementsSeed)
    integrity_settings: IntegrityPolicy = Field(default_factory=IntegrityPolicy)
    recording_settings: RecordingPolicy = Field(default_factory=RecordingPolicy)
    candidate_report_mode: CandidateReportMode = CandidateReportMode.AFTER_REVIEW
    allow_candidate_round_selection: bool = False
    requirements: list[JobRequirementSeed] = Field(..., min_length=1)
    rounds: list[JobRoundSeed] = Field(..., min_length=1, max_length=12)

    @model_validator(mode="after")
    def _consistent(self) -> JobSeed:
        if (
            self.experience_min_years is not None
            and self.experience_max_years is not None
            and self.experience_max_years < self.experience_min_years
        ):
            raise ValueError("experience_max_years must be >= experience_min_years")
        names = [r.name for r in self.rounds]
        if len(set(names)) != len(names):
            raise ValueError("round names must be unique within a job")
        selectable = [r for r in self.rounds if r.candidate_selectable]
        if self.allow_candidate_round_selection and len(selectable) < 2:
            raise ValueError("allow_candidate_round_selection needs at least two candidate_selectable rounds")
        if not self.allow_candidate_round_selection and selectable:
            raise ValueError("candidate_selectable rounds require allow_candidate_round_selection")
        return self

    def to_row(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "department": self.department,
            "location": self.location,
            "employment_type": self.employment_type,
            "seniority": self.seniority,
            "description": self.description,
            "experience_min_years": self.experience_min_years,
            "experience_max_years": self.experience_max_years,
            "status": self.status.value,
            "interview_instructions": self.interview_instructions,
            "coding_requirements": self.coding_requirements.model_dump(mode="json"),
            "integrity_settings": self.integrity_settings.model_dump(mode="json"),
            "recording_settings": self.recording_settings.model_dump(mode="json"),
            "candidate_report_mode": self.candidate_report_mode.value,
            "allow_candidate_round_selection": self.allow_candidate_round_selection,
        }


class RoleSeed(_Seed):
    """A role of ``catalog/roles.yaml``: the vocabulary for ``role_tags``.

    Validation only (not stored): question and coding ``role_tags`` must be a
    role key or a legacy tag (:data:`LEGACY_ROLE_TAGS`).
    """

    key: SnakeKey
    title: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    family: Annotated[str, StringConstraints(min_length=2, max_length=80)]
    description: Sentence
    competency_keys: list[SnakeKey] = Field(..., min_length=1, max_length=60)
    round_types: list[RoundType] = Field(..., min_length=1, max_length=12)

    @field_validator("competency_keys", "round_types")
    @classmethod
    def _unique(cls, value: list[Any]) -> list[Any]:
        if len(set(value)) != len(value):
            raise ValueError("values must be unique")
        return value


@dataclass(frozen=True, slots=True)
class ResolvedRound:
    """A job round with its fully merged and validated configuration."""

    name: str
    round_type: RoundType
    sequence: int
    candidate_selectable: bool
    is_active: bool
    config: RoundConfig
    pinned_coding_slugs: tuple[str, ...] = ()

    def to_row(self, *, pinned_coding_ids: Sequence[str] = ()) -> dict[str, Any]:
        config = self.config.model_dump(mode="json")
        if pinned_coding_ids:
            config["coding"]["question_ids"] = [str(i) for i in pinned_coding_ids]
        return {
            "name": self.name,
            "round_type": self.round_type.value,
            "sequence": self.sequence,
            "duration_minutes": self.config.duration_minutes,
            "max_duration_minutes": self.config.max_duration_minutes,
            "expected_difficulty": self.config.expected_difficulty.value,
            "candidate_selectable": self.candidate_selectable,
            "is_active": self.is_active,
            "instructions": self.config.instructions,
            "config": config,
        }


@dataclass(frozen=True, slots=True)
class SeedCatalog:
    """Everything under ``samples/`` that the seeders load, validated."""

    root: Path
    competencies: list[CompetencySeed]
    round_templates: list[RoundTemplateSeed]
    questions: list[BankQuestionSeed]
    coding_questions: list[CodingQuestionSeed]
    jobs: list[JobSeed]
    sources: dict[str, Path] = field(default_factory=dict)  # record id -> (first) file it came from
    roles: list[RoleSeed] = field(default_factory=list)  # catalog/roles.yaml (optional)
    #: (kind, record id) -> every file defining it; kind is "question", "coding" or "job".
    locations: dict[tuple[str, str], list[Path]] = field(default_factory=dict)

    def templates_by_type(self) -> dict[RoundType, RoundTemplateSeed]:
        return {t.round_type: t for t in self.round_templates}

    def competency_keys(self) -> set[str]:
        return {c.key for c in self.competencies}

    def coding_by_slug(self) -> dict[str, CodingQuestionSeed]:
        return {c.slug: c for c in self.coding_questions}

    def allowed_role_tags(self) -> frozenset[str]:
        """Legacy tags plus the ``roles.yaml`` keys (when the file exists)."""
        return LEGACY_ROLE_TAGS | {r.key for r in self.roles}


# =============================================================================
# File loading
# =============================================================================

try:  # libyaml is ~10x faster when available
    _YamlLoader: type[yaml.SafeLoader] = yaml.CSafeLoader  # type: ignore[assignment]
except AttributeError:  # pragma: no cover
    _YamlLoader = yaml.SafeLoader


def default_samples_dir() -> Path:
    """``$SAMPLES_DIR`` when set, otherwise the nearest ``samples/`` directory
    above this package that contains ``catalog/`` (the repository root)."""
    override = os.environ.get(SAMPLES_DIR_ENV)
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_dir():
            raise SeedDataNotFound(f"{SAMPLES_DIR_ENV}={override} is not a directory")
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "samples"
        if (candidate / "catalog").is_dir():
            return candidate
    raise SeedDataNotFound(f"samples/ directory not found; set {SAMPLES_DIR_ENV} to the samples path")


def read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise SeedDataNotFound(str(path))
    with path.open("rb") as handle:
        return yaml.load(handle, Loader=_YamlLoader)  # noqa: S506 - SafeLoader/CSafeLoader only


def _format_validation_error(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<record>"
        out.append(f"{loc}: {err.get('msg')}")
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


Reader = Callable[[Path], Any]


def _document(
    path: Path,
    root: Path,
    list_key: str | None,
    problems: list[str],
    *,
    allowed_keys: Iterable[str] | None = None,
    read: Reader = read_yaml,
) -> Any:
    try:
        doc = read(path)
    except yaml.YAMLError as exc:
        problems.append(f"{_rel(path, root)}: invalid YAML: {exc}")
        return None
    except UnicodeDecodeError as exc:
        problems.append(f"{_rel(path, root)}: not UTF-8 text: {exc}")
        return None
    if not isinstance(doc, Mapping):
        problems.append(f"{_rel(path, root)}: top level must be a mapping")
        return None
    version = doc.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        problems.append(f"{_rel(path, root)}: unsupported version {version!r} (expected {SCHEMA_VERSION})")
        return None
    if allowed_keys is not None:
        allowed = {"version", *allowed_keys}
        unknown = sorted(str(k) for k in doc if k not in allowed)
        if unknown:
            problems.append(
                f"{_rel(path, root)}: unknown top-level keys {unknown} (expected {sorted(allowed)})"
            )
    if list_key is None:
        return {k: v for k, v in doc.items() if k != "version"}
    items = doc.get(list_key)
    if not isinstance(items, list):
        problems.append(f"{_rel(path, root)}: '{list_key}' must be a list")
        return None
    return doc


def _parse_items[M: BaseModel](
    model: type[M],
    items: Iterable[Any],
    *,
    path: Path,
    root: Path,
    id_field: str,
    problems: list[str],
    defaults: Mapping[str, Any] | None = None,
) -> list[M]:
    parsed: list[M] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            problems.append(f"{_rel(path, root)}: item {index}: must be a mapping")
            continue
        record = {**(defaults or {}), **raw}
        label = f"{id_field}={record.get(id_field)!r}" if record.get(id_field) else f"item {index}"
        try:
            parsed.append(model.model_validate(record))
        except ValidationError as exc:
            problems.extend(f"{_rel(path, root)}: {label}: {msg}" for msg in _format_validation_error(exc))
    return parsed


def iter_yaml_files(directory: Path, root: Path, problems: list[str] | None = None) -> list[Path]:
    """Every ``*.yaml`` file below ``directory`` (any depth), sorted by its path
    relative to ``root``. Dot-files and dot-directories are skipped; ``*.yml``
    files are reported to ``problems`` (they would otherwise be silently
    ignored)."""
    if not directory.is_dir():
        raise SeedDataNotFound(str(directory))
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if name.endswith(".yaml"):
                found.append(path)
            elif name.endswith(".yml") and problems is not None:
                problems.append(f"{_rel(path, root)}: not loaded: rename it to *.yaml")
    return sorted(found, key=lambda p: _rel(p, root))


# Kinds of catalog files (by location).
KIND_COMPETENCIES = "competencies"
KIND_ROUND_TEMPLATES = "round_templates"
KIND_ROLES = "roles"
KIND_QUESTION_BANK = "question_bank"
KIND_CODING = "coding"
KIND_JOBS = "jobs"

#: kind -> (record id field, location kind used in SeedCatalog.locations)
_KEYED_KINDS: dict[str, tuple[str, str]] = {
    KIND_QUESTION_BANK: ("external_key", "question"),
    KIND_CODING: ("slug", "coding"),
    KIND_JOBS: ("key", "job"),
}


@dataclass(slots=True)
class LoadedFile:
    """One catalog file: its validated records and the problems found in it."""

    path: Path
    kind: str
    records: list[Any] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def file_kind(path: Path, root: Path) -> str | None:
    """Kind of a catalog file from its location (``None`` = not loaded by the seeders)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if not path.name.endswith(".yaml") or any(part.startswith(".") for part in rel.parts):
        return None
    if rel == COMPETENCIES_FILE:
        return KIND_COMPETENCIES
    if rel == ROUND_TEMPLATES_FILE:
        return KIND_ROUND_TEMPLATES
    if rel == ROLES_FILE:
        return KIND_ROLES
    head = rel.parts[0] if len(rel.parts) > 1 else None
    return {
        str(QUESTION_BANK_DIR): KIND_QUESTION_BANK,
        str(CODING_DIR): KIND_CODING,
        str(JOBS_DIR): KIND_JOBS,
    }.get(head or "")


def catalog_files(root: Path, problems: list[str]) -> list[tuple[str, Path]]:
    """Every catalog file as ``(kind, path)`` in load order; ``*.yml`` files are
    reported to ``problems``. Raises :class:`SeedDataNotFound` when a required
    directory is missing."""
    files: list[tuple[str, Path]] = [
        (KIND_COMPETENCIES, root / COMPETENCIES_FILE),
        (KIND_ROUND_TEMPLATES, root / ROUND_TEMPLATES_FILE),
    ]
    if (root / ROLES_FILE).exists():
        files.append((KIND_ROLES, root / ROLES_FILE))
    files += [(KIND_QUESTION_BANK, p) for p in iter_yaml_files(root / QUESTION_BANK_DIR, root, problems)]
    files += [(KIND_CODING, p) for p in iter_yaml_files(root / CODING_DIR, root, problems)]
    files += [(KIND_JOBS, p) for p in iter_yaml_files(root / JOBS_DIR, root, problems)]
    return files


def _load_record_file[M: BaseModel](
    model: type[M], path: Path, root: Path, id_field: str, problems: list[str], read: Reader
) -> M | None:
    doc = _document(path, root, None, problems, read=read)
    if doc is None:
        return None
    items = _parse_items(model, [doc], path=path, root=root, id_field=id_field, problems=problems)
    if not items:
        return None
    ident = getattr(items[0], id_field)
    if path.stem != ident:
        problems.append(f"{_rel(path, root)}: file name must match {id_field} {ident!r}")
    return items[0]


def load_file(kind: str, path: Path, root: Path, *, read: Reader = read_yaml) -> LoadedFile:
    """Parse and validate one catalog file of ``kind`` (see :func:`file_kind`).

    Question bank files merge their ``defaults`` mapping into each question
    (question keys win); coding and job files hold one record whose id must
    equal the file stem. Raises :class:`SeedDataNotFound` for a missing file.
    """
    loaded = LoadedFile(path, kind)
    problems = loaded.problems
    if kind == KIND_COMPETENCIES:
        doc = _document(path, root, "competencies", problems, allowed_keys=("competencies",), read=read)
        if doc:
            loaded.records = _parse_items(
                CompetencySeed, doc["competencies"], path=path, root=root, id_field="key", problems=problems
            )
    elif kind == KIND_ROUND_TEMPLATES:
        doc = _document(path, root, "templates", problems, allowed_keys=("templates",), read=read)
        if doc:
            loaded.records = _parse_items(
                RoundTemplateSeed,
                doc["templates"],
                path=path,
                root=root,
                id_field="round_type",
                problems=problems,
            )
    elif kind == KIND_ROLES:
        doc = _document(path, root, "roles", problems, allowed_keys=("roles",), read=read)
        if doc:
            loaded.records = _parse_items(
                RoleSeed, doc["roles"], path=path, root=root, id_field="key", problems=problems
            )
    elif kind == KIND_QUESTION_BANK:
        doc = _document(path, root, "questions", problems, allowed_keys=("defaults", "questions"), read=read)
        defaults = (doc.get("defaults") or {}) if doc else {}
        if doc and not isinstance(defaults, Mapping):
            problems.append(f"{_rel(path, root)}: 'defaults' must be a mapping")
        elif doc:
            loaded.records = _parse_items(
                BankQuestionSeed,
                doc["questions"],
                path=path,
                root=root,
                id_field="external_key",
                problems=problems,
                defaults=defaults,
            )
    elif kind == KIND_CODING:
        item = _load_record_file(CodingQuestionSeed, path, root, "slug", problems, read)
        loaded.records = [item] if item is not None else []
    elif kind == KIND_JOBS:
        job = _load_record_file(JobSeed, path, root, "key", problems, read)
        loaded.records = [job] if job is not None else []
    else:
        raise ValueError(f"unknown catalog file kind {kind!r}")
    return loaded


def _register(
    items: Iterable[BaseModel],
    id_field: str,
    kind: str,
    path: Path,
    sources: dict[str, Path] | None,
    locations: dict[tuple[str, str], list[Path]] | None,
) -> None:
    for item in items:
        ident = str(getattr(item, id_field))
        if sources is not None:
            sources.setdefault(ident, path)
        if locations is not None:
            locations.setdefault((kind, ident), []).append(path)


def _load_kind(
    kind: str,
    root: Path | None,
    problems: list[str] | None,
    sources: dict[str, Path] | None = None,
    locations: dict[tuple[str, str], list[Path]] | None = None,
    *,
    read: Reader = read_yaml,
) -> list[Any]:
    root = root or default_samples_dir()
    collect = problems if problems is not None else []
    if kind == KIND_COMPETENCIES:
        paths = [root / COMPETENCIES_FILE]
    elif kind == KIND_ROUND_TEMPLATES:
        paths = [root / ROUND_TEMPLATES_FILE]
    elif kind == KIND_ROLES:
        paths = [root / ROLES_FILE] if (root / ROLES_FILE).exists() else []
    else:
        paths = iter_yaml_files(root / kind, root, collect)
    out: list[Any] = []
    for path in paths:
        loaded = load_file(kind, path, root, read=read)
        collect.extend(loaded.problems)
        if kind in _KEYED_KINDS:
            id_field, location_kind = _KEYED_KINDS[kind]
            _register(loaded.records, id_field, location_kind, path, sources, locations)
        out.extend(loaded.records)
    if problems is None and collect:
        raise SeedValidationError(collect)
    return out


def load_competencies(root: Path | None = None, *, problems: list[str] | None = None) -> list[CompetencySeed]:
    return _load_kind(KIND_COMPETENCIES, root, problems)


def load_round_templates(
    root: Path | None = None, *, problems: list[str] | None = None
) -> list[RoundTemplateSeed]:
    return _load_kind(KIND_ROUND_TEMPLATES, root, problems)


def load_roles(root: Path | None = None, *, problems: list[str] | None = None) -> list[RoleSeed]:
    """``catalog/roles.yaml`` (optional: ``[]`` when the file does not exist)."""
    return _load_kind(KIND_ROLES, root, problems)


def load_question_bank(
    root: Path | None = None,
    *,
    problems: list[str] | None = None,
    sources: dict[str, Path] | None = None,
    locations: dict[tuple[str, str], list[Path]] | None = None,
) -> list[BankQuestionSeed]:
    """Every ``question_bank/**/*.yaml`` file (any depth); a file-level
    ``defaults`` mapping is merged into each question (question keys win)."""
    return _load_kind(KIND_QUESTION_BANK, root, problems, sources, locations)


def load_coding_questions(
    root: Path | None = None,
    *,
    problems: list[str] | None = None,
    sources: dict[str, Path] | None = None,
    locations: dict[tuple[str, str], list[Path]] | None = None,
) -> list[CodingQuestionSeed]:
    """One problem per ``coding/<group>/<slug>.yaml`` (or legacy ``coding/<slug>.yaml``) file."""
    return _load_kind(KIND_CODING, root, problems, sources, locations)


def load_jobs(
    root: Path | None = None,
    *,
    problems: list[str] | None = None,
    sources: dict[str, Path] | None = None,
    locations: dict[tuple[str, str], list[Path]] | None = None,
) -> list[JobSeed]:
    """One demo job per ``jobs/<key>.yaml`` file."""
    return _load_kind(KIND_JOBS, root, problems, sources, locations)


def assemble_catalog(root: Path, files: Iterable[LoadedFile]) -> SeedCatalog:
    """A :class:`SeedCatalog` from loaded files (no cross-validation)."""
    by_kind: dict[str, list[Any]] = {
        kind: []
        for kind in (
            KIND_COMPETENCIES,
            KIND_ROUND_TEMPLATES,
            KIND_ROLES,
            KIND_QUESTION_BANK,
            KIND_CODING,
            KIND_JOBS,
        )
    }
    sources: dict[str, Path] = {}
    locations: dict[tuple[str, str], list[Path]] = {}
    for loaded in files:
        by_kind[loaded.kind].extend(loaded.records)
        if loaded.kind in _KEYED_KINDS:
            id_field, location_kind = _KEYED_KINDS[loaded.kind]
            _register(loaded.records, id_field, location_kind, loaded.path, sources, locations)
    return SeedCatalog(
        root=root,
        competencies=by_kind[KIND_COMPETENCIES],
        round_templates=by_kind[KIND_ROUND_TEMPLATES],
        questions=by_kind[KIND_QUESTION_BANK],
        coding_questions=by_kind[KIND_CODING],
        jobs=by_kind[KIND_JOBS],
        sources=sources,
        roles=by_kind[KIND_ROLES],
        locations=locations,
    )


def collect_catalog(
    root: Path, *, read: Reader = read_yaml
) -> tuple[SeedCatalog, list[str], list[LoadedFile]]:
    """Load every catalog file below ``root`` without raising on validation
    problems: ``(catalog, problems, files)`` where ``problems`` includes the
    cross-record checks of :func:`validate_catalog`."""
    problems: list[str] = []
    files = [load_file(kind, path, root, read=read) for kind, path in catalog_files(root, problems)]
    for loaded in files:
        problems.extend(loaded.problems)
    catalog = assemble_catalog(root, files)
    problems.extend(validate_catalog(catalog))
    return catalog, problems, files


# =============================================================================
# Round resolution and cross-validation
# =============================================================================


def deep_merge(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge: mappings merge key by key, anything else (lists
    included) replaces the base value."""
    merged: dict[str, Any] = dict(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def resolve_round(
    round_seed: JobRoundSeed, templates: Mapping[RoundType, RoundTemplateSeed], *, sequence: int
) -> ResolvedRound:
    """Merge a job round's overrides into its default template and validate
    the result as a :class:`RoundConfig`."""
    template = templates.get(round_seed.template)
    if template is None:
        raise ValueError(f"no default template for round type {round_seed.template.value!r}")
    base = template.config.model_dump(mode="json")
    merged = deep_merge(base, round_seed.overrides)
    merged["name"] = round_seed.name
    merged["candidate_selectable"] = round_seed.candidate_selectable
    config = RoundConfig.model_validate(merged)
    return ResolvedRound(
        name=round_seed.name,
        round_type=config.round_type,
        sequence=sequence,
        candidate_selectable=round_seed.candidate_selectable,
        is_active=round_seed.is_active,
        config=config,
        pinned_coding_slugs=tuple(round_seed.pinned_coding_slugs),
    )


def resolve_job_rounds(job: JobSeed, templates: Mapping[RoundType, RoundTemplateSeed]) -> list[ResolvedRound]:
    return [resolve_round(r, templates, sequence=i) for i, r in enumerate(job.rounds, start=1)]


def round_config_problems(config: RoundConfig, competency_keys: set[str]) -> list[str]:
    """Semantic checks beyond the RoundConfig schema."""
    problems: list[str] = []
    round_keys = config.competency_keys()
    if not round_keys:
        problems.append("round has no competencies")
    if len(set(round_keys)) != len(round_keys):
        problems.append("duplicate competency keys")
    unknown = sorted(set(round_keys) - competency_keys)
    if unknown:
        problems.append(f"unknown competency keys {unknown}")
    if not config.rubric:
        problems.append("round has no rubric")
    rubric_keys = [c.key for c in config.rubric]
    if len(set(rubric_keys)) != len(rubric_keys):
        problems.append("duplicate rubric category keys")
    for category in config.rubric:
        stray = sorted(set(category.competency_keys) - set(round_keys))
        if stray:
            problems.append(
                f"rubric category {category.key!r} references competencies outside the round {stray}"
            )
        bad_dims = sorted(set(category.dimensions) - ALLOWED_RUBRIC_DIMENSIONS)
        if bad_dims:
            problems.append(f"rubric category {category.key!r} has unknown dimensions {bad_dims}")
        if not category.competency_keys and not category.dimensions:
            problems.append(f"rubric category {category.key!r} rolls up nothing")
    if (
        config.coding.enabled
        and config.coding.duration_minutes >= config.duration_minutes
        and (config.round_type != RoundType.CODING)
    ):
        problems.append("coding section leaves no time for the rest of the round")
    if config.round_type in (RoundType.CODING, RoundType.COMBINED) and not config.coding.enabled:
        problems.append(f"{config.round_type.value} rounds must enable the coding section")
    return problems


def validate_catalog(catalog: SeedCatalog) -> list[str]:
    """Cross-record checks: uniqueness (across the whole tree), references and
    coverage."""
    problems: list[str] = []
    src = catalog.sources

    def where(record_id: str, default: str) -> str:
        path = src.get(record_id)
        return _rel(path, catalog.root) if path else default

    def duplicates(kind: str, label: str, ids: Iterable[str], default: str) -> None:
        for ident, count in Counter(ids).items():
            if count < 2:
                continue
            paths = catalog.locations.get((kind, ident))
            files = sorted({_rel(p, catalog.root) for p in paths}) if paths else [where(ident, default)]
            problems.extend(
                f"{name}: duplicate {label} {ident!r} (defined {count} times: {', '.join(files)})"
                for name in files
            )

    keys = [c.key for c in catalog.competencies]
    dupes = sorted(k for k, n in Counter(keys).items() if n > 1)
    if dupes:
        problems.append(f"{COMPETENCIES_FILE}: duplicate competency keys {dupes}")
    known = set(keys)

    types = [t.round_type for t in catalog.round_templates]
    missing_types = sorted(t.value for t in set(RoundType) - set(types))
    if missing_types:
        problems.append(f"{ROUND_TEMPLATES_FILE}: no default template for {missing_types}")
    if len(set(types)) != len(types):
        problems.append(f"{ROUND_TEMPLATES_FILE}: more than one template per round type")
    for template in catalog.round_templates:
        problems.extend(
            f"{ROUND_TEMPLATES_FILE}: {template.round_type.value}: {msg}"
            for msg in round_config_problems(template.config, known)
        )

    role_dupes = sorted(k for k, n in Counter(r.key for r in catalog.roles).items() if n > 1)
    if role_dupes:
        problems.append(f"{ROLES_FILE}: duplicate role keys {role_dupes}")
    for role in catalog.roles:
        unknown_competencies = sorted(set(role.competency_keys) - known)
        if unknown_competencies:
            problems.append(
                f"{ROLES_FILE}: role {role.key!r}: unknown competency keys {unknown_competencies}"
            )
    allowed_tags = catalog.allowed_role_tags()
    tag_hint = f"not a key of {ROLES_FILE} or a legacy tag" if catalog.roles else "not a legacy tag"

    duplicates(
        "question", "external_key", (q.external_key for q in catalog.questions), str(QUESTION_BANK_DIR)
    )
    for question in catalog.questions:
        loc = where(question.external_key, str(QUESTION_BANK_DIR))
        if question.competency_key not in known:
            problems.append(f"{loc}: {question.external_key}: unknown competency {question.competency_key!r}")
        unknown_tags = sorted(set(question.role_tags) - allowed_tags)
        if unknown_tags:
            problems.append(f"{loc}: {question.external_key}: unknown role tags {unknown_tags} ({tag_hint})")

    duplicates("coding", "slug", (c.slug for c in catalog.coding_questions), str(CODING_DIR))
    slugs = {c.slug for c in catalog.coding_questions}
    for coding in catalog.coding_questions:
        loc = where(coding.slug, str(CODING_DIR))
        unknown = sorted(set(coding.competency_keys) - known)
        if unknown:
            problems.append(f"{loc}: unknown competencies {unknown}")
        unknown_tags = sorted(set(coding.role_tags) - allowed_tags)
        if unknown_tags:
            problems.append(f"{loc}: unknown role tags {unknown_tags} ({tag_hint})")

    templates = catalog.templates_by_type()
    duplicates("job", "job key", (j.key for j in catalog.jobs), str(JOBS_DIR))
    titles: set[str] = set()
    for job in catalog.jobs:
        loc = where(job.key, str(JOBS_DIR))
        if job.title in titles:
            problems.append(f"{loc}: duplicate job title {job.title!r}")
        titles.add(job.title)
        for req in job.requirements:
            if req.competency_key and req.competency_key not in known:
                problems.append(f"{loc}: requirement {req.name!r}: unknown competency {req.competency_key!r}")
        for index, round_seed in enumerate(job.rounds, start=1):
            try:
                resolved = resolve_round(round_seed, templates, sequence=index)
            except (ValidationError, ValueError) as exc:
                detail = (
                    "; ".join(_format_validation_error(exc)) if isinstance(exc, ValidationError) else str(exc)
                )
                problems.append(f"{loc}: round {round_seed.name!r}: {detail}")
                continue
            problems.extend(
                f"{loc}: round {round_seed.name!r}: {msg}"
                for msg in round_config_problems(resolved.config, known)
            )
            for slug in round_seed.pinned_coding_slugs:
                if slug not in slugs:
                    problems.append(
                        f"{loc}: round {round_seed.name!r}: pinned coding slug {slug!r} not found"
                    )
            if round_seed.pinned_coding_slugs and not resolved.config.coding.enabled:
                problems.append(
                    f"{loc}: round {round_seed.name!r}: pins coding problems but coding is disabled"
                )
            if resolved.config.coding.enabled:
                if not job.coding_requirements.enabled:
                    problems.append(
                        f"{loc}: round {round_seed.name!r}: coding enabled but job coding is disabled"
                    )
                stray_langs = set(resolved.config.coding.languages) - set(job.coding_requirements.languages)
                if stray_langs:
                    problems.append(
                        f"{loc}: round {round_seed.name!r}: languages {sorted(v.value for v in stray_langs)} "
                        "not allowed by the job"
                    )
    return problems


# Parsed catalogs keyed by root, reused while no file below the root changed
# (tests and seed steps call load_catalog() many times per process).
_CATALOG_CACHE: dict[Path, tuple[tuple[Any, ...], SeedCatalog]] = {}
_CATALOG_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE_SIZE = 4
_TREE_PARTS = ("catalog", str(QUESTION_BANK_DIR), str(CODING_DIR), str(JOBS_DIR))


def tree_fingerprint(root: Path) -> tuple[Any, ...]:
    """(relative path, size, mtime_ns) of every file the loaders read; changes
    whenever a file is added, removed or modified."""
    entries: list[tuple[str, int, int]] = []
    for part in _TREE_PARTS:
        stack = [root / part]
        while stack:
            directory = stack.pop()
            try:
                scan = list(os.scandir(directory))
            except OSError:
                continue
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=True):
                    stack.append(Path(entry.path))
                elif entry.name.endswith((".yaml", ".yml")):
                    stat = entry.stat()
                    entries.append((entry.path, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(entries))


def _copy_catalog(catalog: SeedCatalog) -> SeedCatalog:
    return dataclasses.replace(
        catalog,
        competencies=list(catalog.competencies),
        round_templates=list(catalog.round_templates),
        questions=list(catalog.questions),
        coding_questions=list(catalog.coding_questions),
        jobs=list(catalog.jobs),
        sources=dict(catalog.sources),
        roles=list(catalog.roles),
        locations={k: list(v) for k, v in catalog.locations.items()},
    )


def load_catalog(root: Path | None = None, *, cache: bool = True) -> SeedCatalog:
    """Load and validate everything under ``samples/``; raises
    :class:`SeedValidationError` listing every problem found.

    With ``cache`` (default) a catalog parsed earlier in this process is
    reused while no file below ``root`` changed (see :func:`tree_fingerprint`);
    callers get their own copies of the record lists.
    """
    root = (root or default_samples_dir()).resolve()
    fingerprint = tree_fingerprint(root) if cache else ()
    if cache:
        with _CATALOG_CACHE_LOCK:
            hit = _CATALOG_CACHE.get(root)
        if hit is not None and hit[0] == fingerprint:
            return _copy_catalog(hit[1])
    catalog, problems, _ = collect_catalog(root)
    if problems:
        raise SeedValidationError(problems)
    if cache:
        with _CATALOG_CACHE_LOCK:
            _CATALOG_CACHE.pop(root, None)
            while len(_CATALOG_CACHE) >= _CATALOG_CACHE_SIZE:
                _CATALOG_CACHE.pop(next(iter(_CATALOG_CACHE)))
            _CATALOG_CACHE[root] = (fingerprint, catalog)
        return _copy_catalog(catalog)
    return catalog


__all__ = [
    "ALLOWED_RUBRIC_DIMENSIONS",
    "FUNCTION_LANGUAGES",
    "KIND_CODING",
    "KIND_COMPETENCIES",
    "KIND_JOBS",
    "KIND_QUESTION_BANK",
    "KIND_ROLES",
    "KIND_ROUND_TEMPLATES",
    "KNOWN_ROLE_TAGS",
    "LEGACY_ROLE_TAGS",
    "REQUIRED_REFERENCE_LANGUAGES",
    "ROLES_FILE",
    "SIGNATURE_TYPES",
    "BankQuestionSeed",
    "CodingQuestionSeed",
    "CodingSignature",
    "CodingTestSeed",
    "CompetencySeed",
    "JobRoundSeed",
    "JobSeed",
    "LoadedFile",
    "Reader",
    "ResolvedRound",
    "RoleSeed",
    "RoundTemplateSeed",
    "SeedCatalog",
    "SeedDataNotFound",
    "SeedValidationError",
    "assemble_catalog",
    "canonicalize_unordered",
    "catalog_files",
    "collect_catalog",
    "compare_results",
    "compare_sql_rows",
    "deep_merge",
    "default_samples_dir",
    "element_type",
    "entry_point_name",
    "file_kind",
    "is_array_type",
    "iter_yaml_files",
    "load_catalog",
    "load_coding_questions",
    "load_competencies",
    "load_file",
    "load_jobs",
    "load_question_bank",
    "load_roles",
    "load_round_templates",
    "read_yaml",
    "resolve_job_rounds",
    "resolve_round",
    "round_config_problems",
    "to_lower_camel",
    "tree_fingerprint",
    "validate_catalog",
    "value_matches_type",
]
