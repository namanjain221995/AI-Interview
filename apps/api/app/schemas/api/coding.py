"""DTOs for the coding endpoints: coding question management
(``/coding-questions``) and the staff coding view of a session
(``GET /sessions/{id}/coding``)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.domain.enums import BankStatus, CodeLanguage, Difficulty, RunStatus
from app.schemas.api.common import APIModel
from app.seed.loaders import CodingSignature, value_matches_type

FUNCTION_LANGUAGES = frozenset(
    {
        CodeLanguage.PYTHON,
        CodeLanguage.JAVASCRIPT,
        CodeLanguage.TYPESCRIPT,
        CodeLanguage.JAVA,
        CodeLanguage.GO,
        CodeLanguage.CPP,
    }
)
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=2, max_length=120)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=80)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=600)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodingTestIn(_Strict):
    """One test case. Function problems use ``args``/``expected``; SQL problems
    use ``setup_sql``/``expected_rows``. ``description`` is staff-facing (never
    shown to candidates for hidden tests)."""

    id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=40)]
    args: list[Any] | None = None
    expected: Any = None
    setup_sql: str | None = Field(None, max_length=20_000)
    expected_rows: list[list[Any]] | None = None
    ordered: bool = False
    hidden: bool = False
    description: str | None = Field(None, max_length=300)


class _QuestionFields(_Strict):
    title: Annotated[str, StringConstraints(min_length=3, max_length=200)]
    difficulty: Difficulty
    competency_keys: list[Key] = Field(default_factory=list, max_length=8)
    role_tags: list[Key] = Field(default_factory=list, max_length=8)
    prompt_markdown: Annotated[str, StringConstraints(min_length=20, max_length=20_000)]
    languages: list[CodeLanguage] = Field(..., min_length=1, max_length=7)
    signature: CodingSignature | None = None
    starter_code: dict[CodeLanguage, Annotated[str, StringConstraints(max_length=50_000)]] = Field(
        default_factory=dict
    )
    reference_solutions: dict[CodeLanguage, Annotated[str, StringConstraints(max_length=100_000)]] = Field(
        default_factory=dict
    )
    tests: list[CodingTestIn] = Field(..., min_length=1, max_length=100)
    sql_setup: str | None = Field(None, max_length=50_000)
    expected_concepts: list[ShortText] = Field(default_factory=list, max_length=12)
    optimal_complexity: dict[Literal["time", "space"], Annotated[str, StringConstraints(max_length=60)]] = (
        Field(default_factory=dict)
    )
    rubric: dict[str, Any] = Field(default_factory=dict)
    hints: list[Annotated[str, StringConstraints(min_length=1, max_length=600)]] = Field(
        default_factory=list,
        max_length=3,
        description="Up to three graduated hints (missing levels are generated live)",
    )
    followups: list[ShortText] = Field(default_factory=list, max_length=8)
    common_errors: list[ShortText] = Field(default_factory=list, max_length=8)
    time_limit_minutes: int = Field(20, ge=5, le=120)
    status: BankStatus = BankStatus.ACTIVE

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, value: list[CodeLanguage]) -> list[CodeLanguage]:
        if len(set(value)) != len(value):
            raise ValueError("languages must be unique")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> _QuestionFields:
        ids = [t.id for t in self.tests]
        if len(set(ids)) != len(ids):
            raise ValueError("test ids must be unique")
        if not any(not t.hidden for t in self.tests):
            raise ValueError("at least one visible test is required")
        langs = set(self.languages)
        for label, mapping in (
            ("starter_code", self.starter_code),
            ("reference_solutions", self.reference_solutions),
        ):
            extra = sorted(lang.value for lang in set(mapping) - langs)
            if extra:
                raise ValueError(f"{label} has languages not listed in languages: {extra}")
        if CodeLanguage.SQL in langs:
            self._check_sql()
        else:
            self._check_function()
        return self

    def _check_sql(self) -> None:
        if self.languages != [CodeLanguage.SQL]:
            raise ValueError("SQL problems support exactly one language: sql")
        if self.signature is not None:
            raise ValueError("SQL problems compare result sets; leave signature empty")
        if not self.sql_setup or "create table" not in self.sql_setup.lower():
            raise ValueError("SQL problems need sql_setup with CREATE TABLE statements")
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
        unknown = sorted(lang.value for lang in set(self.languages) - FUNCTION_LANGUAGES)
        if unknown:
            raise ValueError(f"unsupported languages for a function problem: {unknown}")
        sig = self.signature
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

    def to_row(self) -> dict[str, Any]:
        return {
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
            "rubric": dict(self.rubric),
            "hints": list(self.hints),
            "followups": list(self.followups),
            "common_errors": list(self.common_errors),
            "time_limit_minutes": self.time_limit_minutes,
            "status": self.status.value,
        }


class CodingQuestionCreate(_QuestionFields):
    slug: Slug | None = Field(None, description="Generated from the title when omitted")
    organization_id: uuid.UUID | None = Field(None, description="SUPER_ADMIN only: target organization")


class CodingQuestionUpdate(_Strict):
    """Partial update; the merged question is validated as a whole."""

    slug: Slug | None = None
    title: str | None = None
    difficulty: Difficulty | None = None
    competency_keys: list[str] | None = None
    role_tags: list[str] | None = None
    prompt_markdown: str | None = None
    languages: list[CodeLanguage] | None = None
    signature: dict[str, Any] | None = None
    starter_code: dict[CodeLanguage, str] | None = None
    reference_solutions: dict[CodeLanguage, str] | None = None
    tests: list[dict[str, Any]] | None = None
    sql_setup: str | None = None
    expected_concepts: list[str] | None = None
    optimal_complexity: dict[str, str] | None = None
    rubric: dict[str, Any] | None = None
    hints: list[str] | None = None
    followups: list[str] | None = None
    common_errors: list[str] | None = None
    time_limit_minutes: int | None = None
    status: BankStatus | None = None


class CodingQuestionOut(APIModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    is_global: bool
    slug: str
    title: str
    difficulty: str
    competency_keys: list[str]
    role_tags: list[str]
    prompt_markdown: str
    languages: list[str]
    signature: dict[str, Any]
    starter_code: dict[str, str]
    reference_solutions: dict[str, str] | None = None
    tests: list[dict[str, Any]]
    sql_setup: str | None
    expected_concepts: list[str]
    optimal_complexity: dict[str, str]
    rubric: dict[str, Any]
    hints: list[str]
    followups: list[str]
    common_errors: list[str]
    time_limit_minutes: int
    source: str
    status: str
    has_embedding: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(
        cls, row: Any, *, include_solutions: bool = True, has_embedding: bool | None = None
    ) -> CodingQuestionOut:
        """``has_embedding``: pass it when the query deferred the vector."""
        return cls(
            id=row.id,
            organization_id=row.organization_id,
            is_global=row.organization_id is None,
            slug=row.slug,
            title=row.title,
            difficulty=row.difficulty,
            competency_keys=list(row.competency_keys or []),
            role_tags=list(row.role_tags or []),
            prompt_markdown=row.prompt_markdown,
            languages=list(row.languages or []),
            signature=dict(row.signature or {}),
            starter_code=dict(row.starter_code or {}),
            reference_solutions=dict(row.reference_solutions or {}) if include_solutions else None,
            tests=list(row.tests or []),
            sql_setup=row.sql_setup,
            expected_concepts=list(row.expected_concepts or []),
            optimal_complexity=dict(row.optimal_complexity or {}),
            rubric=dict(row.rubric or {}),
            hints=list(row.hints or []),
            followups=list(row.followups or []),
            common_errors=list(row.common_errors or []),
            time_limit_minutes=row.time_limit_minutes,
            source=row.source,
            status=row.status,
            has_embedding=has_embedding if has_embedding is not None else row.embedding is not None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class CodingQuestionSummaryOut(APIModel):
    """List row of ``GET /coding-questions`` (default ``view=summary``): no
    prompt, signature, starters, solutions, tests, rubric or hints (~7 KB per
    problem in the full view); test counts instead. ``GET /coding-questions/{id}``
    returns everything."""

    id: uuid.UUID
    organization_id: uuid.UUID | None
    is_global: bool
    slug: str
    title: str
    difficulty: str
    competency_keys: list[str]
    role_tags: list[str]
    languages: list[str]
    time_limit_minutes: int
    source: str
    status: str
    has_embedding: bool = False
    visible_test_count: int = 0
    hidden_test_count: int = 0
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> CodingQuestionSummaryOut:
        """``row``: the summary columns plus ``has_embedding``, ``test_count``
        and ``hidden_test_count`` (computed by the database)."""
        tests = int(row.test_count or 0)
        hidden = int(row.hidden_test_count or 0)
        return cls(
            id=row.id,
            organization_id=row.organization_id,
            is_global=row.organization_id is None,
            slug=row.slug,
            title=row.title,
            difficulty=row.difficulty,
            competency_keys=list(row.competency_keys or []),
            role_tags=list(row.role_tags or []),
            languages=list(row.languages or []),
            time_limit_minutes=row.time_limit_minutes,
            source=row.source,
            status=row.status,
            has_embedding=bool(row.has_embedding),
            visible_test_count=tests - hidden,
            hidden_test_count=hidden,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ValidationTestFailure(BaseModel):
    id: str
    hidden: bool
    error: str | None = None
    expected: Any = None
    actual: Any = None


class LanguageValidationResult(BaseModel):
    language: CodeLanguage
    status: RunStatus | Literal["skipped"]
    passed: int = 0
    total: int = 0
    runtime_ms: int | None = None
    error: str | None = None
    failures: list[ValidationTestFailure] = Field(default_factory=list)


class CodingQuestionValidation(BaseModel):
    ok: bool
    coding_question_id: uuid.UUID
    results: list[LanguageValidationResult]
    starters: list[LanguageValidationResult] = Field(
        default_factory=list,
        description="Starter code compiled against the visible tests (include_starters=true)",
    )
    duration_ms: float
    validated_at: datetime


# --------------------------------------------------------------- session view


class CodingQuestionBrief(APIModel):
    id: uuid.UUID
    slug: str
    title: str
    difficulty: str
    prompt_markdown: str
    languages: list[str]
    competency_keys: list[str]
    expected_concepts: list[str]
    optimal_complexity: dict[str, str]
    source: str


class CodeSnapshotOut(APIModel):
    id: uuid.UUID
    coding_session_id: uuid.UUID
    seq: int
    language: str
    code: str
    diff_from_previous: str | None
    lines_added: int
    lines_removed: int
    char_count: int
    trigger: str
    elapsed_seconds: float | None
    created_at: datetime


class CodeRunOut(APIModel):
    id: uuid.UUID
    coding_session_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    kind: str
    language: str
    status: str
    stdout: str | None
    stderr: str | None
    compile_output: str | None
    exit_code: int | None
    test_results: list[dict[str, Any]]
    passed_count: int
    total_count: int
    runtime_ms: int | None
    memory_kb: int | None
    elapsed_seconds: float | None
    platform_fault: bool = False
    system_run: bool = False
    sandbox_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CodingHintOut(APIModel):
    id: uuid.UUID
    coding_session_id: uuid.UUID
    level: int
    content: str
    source: str
    requested_via: str
    snapshot_id: uuid.UUID | None
    elapsed_seconds: float | None
    delivered_at: datetime | None
    created_at: datetime


class CodingSessionDetailOut(APIModel):
    id: uuid.UUID
    session_id: uuid.UUID
    coding_question_id: uuid.UUID
    question_id: uuid.UUID | None
    language: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    time_limit_seconds: int
    hints_available: int
    hints_used: int
    paste_event_count: int
    final_code: str | None
    final_run_id: uuid.UUID | None
    end_reason: str | None = None
    evaluation: dict[str, Any]
    question: CodingQuestionBrief | None = None
    snapshots: list[CodeSnapshotOut]
    runs: list[CodeRunOut]
    hints: list[CodingHintOut]
    created_at: datetime
    updated_at: datetime


def slug_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"q_{slug}"
    return slug[:100].rstrip("_") or "question"
