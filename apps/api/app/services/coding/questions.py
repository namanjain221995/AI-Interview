"""Coding question helpers: sandbox payloads, candidate-safe views, language
rules and redaction of hidden tests.

Redaction rules (enforced here, tested in ``tests/unit/coding``):

* candidates see only *visible* tests (inputs, expected values, their own
  outputs and errors) plus aggregate pass counts that include hidden tests;
* hidden test inputs, expected values, actual outputs, error messages and
  descriptions never leave the staff side (``code_runs.test_results`` keeps
  them for reviewers);
* reference solutions, rubrics, hints that were not yet issued and hidden
  test descriptions are never part of a candidate payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from app.db.models import CodingQuestion
from app.domain.enums import CodeLanguage, RunKind
from app.schemas.realtime import CodingQuestionPublic
from app.schemas.rounds import CodingSection
from app.services.coding.contracts import ExecuteRequest, ExecuteResponse, SandboxTestCase, SandboxTestResult

DEFAULT_TIME_LIMIT_MS = 5000
DEFAULT_COMPILE_TIME_LIMIT_MS = 15000
DEFAULT_MEMORY_LIMIT_MB = 256
MAX_CANDIDATE_OUTPUT_CHARS = 16_000
MAX_STORED_OUTPUT_CHARS = 64_000
_SANDBOX_TEST_FIELDS = ("id", "args", "expected", "setup_sql", "expected_rows", "ordered", "hidden")


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[: limit - 200]
    return f"{head}\n... [{len(text) - len(head)} characters truncated]"


def is_sql(question: CodingQuestion) -> bool:
    return list(question.languages or []) == [CodeLanguage.SQL.value]


def question_languages(question: CodingQuestion) -> list[CodeLanguage]:
    out: list[CodeLanguage] = []
    for value in question.languages or []:
        try:
            lang = CodeLanguage(value)
        except ValueError:
            continue
        if lang not in out:
            out.append(lang)
    return out


def allowed_languages(
    question: CodingQuestion, section: CodingSection | None, *, strict: bool = False
) -> list[CodeLanguage]:
    """Languages the candidate may use: the question's languages restricted to
    the round's (round order first). SQL problems are always SQL. Without
    ``strict``, a question sharing no language with the round (e.g. a pinned
    problem) keeps its own languages rather than becoming unusable."""
    q_langs = question_languages(question)
    if is_sql(question):
        return [CodeLanguage.SQL]
    if section is None or not section.languages:
        return q_langs
    ordered = [lang for lang in section.languages if lang in q_langs]
    if strict:
        return ordered
    return ordered or q_langs


def choose_language(
    question: CodingQuestion, section: CodingSection | None, requested: CodeLanguage | str | None
) -> CodeLanguage:
    allowed = allowed_languages(question, section)
    if not allowed:
        raise ValueError("coding question supports no language")
    if requested is not None:
        try:
            lang = CodeLanguage(requested)
        except ValueError:
            lang = None
        if lang in allowed:
            return lang  # type: ignore[return-value]
    return allowed[0]


def supports_any(question: CodingQuestion, languages: Iterable[CodeLanguage | str]) -> bool:
    wanted = {CodeLanguage(lang) for lang in languages}
    if not wanted:
        return True
    return bool(wanted & set(question_languages(question)))


# ----------------------------------------------------------------- tests


def visible_tests(question: CodingQuestion) -> list[dict[str, Any]]:
    return [t for t in question.tests or [] if not t.get("hidden")]


def hidden_tests(question: CodingQuestion) -> list[dict[str, Any]]:
    return [t for t in question.tests or [] if t.get("hidden")]


def sandbox_case(test: dict[str, Any]) -> SandboxTestCase:
    return SandboxTestCase(**{k: test[k] for k in _SANDBOX_TEST_FIELDS if k in test and test[k] is not None})


def sandbox_cases(question: CodingQuestion, *, include_hidden: bool) -> list[SandboxTestCase]:
    tests = list(question.tests or []) if include_hidden else visible_tests(question)
    return [sandbox_case(t) for t in tests]


def candidate_test_view(test: dict[str, Any]) -> dict[str, Any]:
    """A *visible* test as shown to the candidate (never call for hidden tests)."""
    if test.get("hidden"):
        raise ValueError("hidden tests are never shown to candidates")
    view: dict[str, Any] = {"id": test["id"], "hidden": False}
    for key in ("args", "expected", "setup_sql", "expected_rows", "ordered", "description"):
        if test.get(key) is not None:
            view[key] = test[key]
    return view


def public_question(question: CodingQuestion, languages: Sequence[CodeLanguage]) -> CodingQuestionPublic:
    starters = {
        lang.value: question.starter_code[lang.value]
        for lang in languages
        if lang.value in (question.starter_code or {})
    }
    return CodingQuestionPublic(
        coding_question_id=str(question.id),
        title=question.title,
        prompt_markdown=question.prompt_markdown,
        difficulty=question.difficulty,
        languages=list(languages),
        starter_code=starters,
        visible_tests=[candidate_test_view(t) for t in visible_tests(question)],
        signature=dict(question.signature) if question.signature else None,
    )


def starter_code(question: CodingQuestion, language: CodeLanguage) -> str:
    return str((question.starter_code or {}).get(language.value) or "")


# --------------------------------------------------------------- requests


def build_execute_request(
    question: CodingQuestion,
    *,
    kind: RunKind,
    code: str,
    language: CodeLanguage,
    stdin: str | None = None,
    request_id: str | None = None,
    time_limit_ms: int = DEFAULT_TIME_LIMIT_MS,
) -> ExecuteRequest:
    request_id = request_id or uuid.uuid4().hex
    common: dict[str, Any] = {
        "request_id": request_id,
        "language": language,
        "code": code,
        "time_limit_ms": time_limit_ms,
        "compile_time_limit_ms": DEFAULT_COMPILE_TIME_LIMIT_MS,
        "memory_limit_mb": DEFAULT_MEMORY_LIMIT_MB,
        "sql_setup": question.sql_setup if language == CodeLanguage.SQL else None,
    }
    if kind == RunKind.RUN:
        return ExecuteRequest(mode="run", stdin=stdin, **common)
    tests = sandbox_cases(question, include_hidden=kind == RunKind.SUBMIT)
    return ExecuteRequest(
        mode="tests",
        signature=dict(question.signature) if question.signature else None,
        tests=tests,
        **common,
    )


# -------------------------------------------------------------- redaction


def staff_test_results(
    question: CodingQuestion, results: Sequence[SandboxTestResult]
) -> list[dict[str, Any]]:
    """Full per-test records for ``code_runs.test_results`` (staff only)."""
    by_id = {t.get("id"): t for t in question.tests or []}
    out: list[dict[str, Any]] = []
    for result in results:
        source = by_id.get(result.id, {})
        hidden = bool(source.get("hidden", result.hidden))
        record: dict[str, Any] = {
            "id": result.id,
            "passed": result.passed,
            "hidden": hidden,
            "actual": result.actual,
            "expected": source.get("expected", result.expected)
            if "expected_rows" not in source
            else source.get("expected_rows"),
            "runtime_ms": result.runtime_ms,
            "error": truncate(result.error, 2000) or None,
        }
        if "args" in source:
            record["args"] = source.get("args")
        if source.get("setup_sql"):
            record["setup_sql"] = source.get("setup_sql")
        if source.get("description"):
            record["description"] = source.get("description")
        out.append(record)
    return out


def candidate_test_results(
    question: CodingQuestion, results: Sequence[SandboxTestResult]
) -> list[dict[str, Any]]:
    """Visible test details only. Hidden tests contribute solely to the pass
    counts of the run message - not even their ids are listed."""
    by_id = {t.get("id"): t for t in question.tests or []}
    out: list[dict[str, Any]] = []
    for result in results:
        source = by_id.get(result.id)
        if source is None or source.get("hidden") or result.hidden:
            continue
        view = candidate_test_view(source)
        view.update(
            {
                "passed": result.passed,
                "actual": result.actual,
                "runtime_ms": result.runtime_ms,
                "error": truncate(result.error, 2000) or None,
            }
        )
        out.append(view)
    return out


def candidate_output(response: ExecuteResponse) -> dict[str, Any]:
    """stdout/stderr/compile output trimmed for the candidate message."""
    return {
        "stdout": truncate(response.stdout, MAX_CANDIDATE_OUTPUT_CHARS),
        "stderr": truncate(response.stderr, MAX_CANDIDATE_OUTPUT_CHARS),
        "compile_output": truncate(response.compile_output, MAX_CANDIDATE_OUTPUT_CHARS),
    }


def first_error_excerpt(
    response_status: str,
    stderr: str | None,
    compile_output: str | None,
    tests: Sequence[dict[str, Any]],
    limit: int = 400,
) -> str | None:
    """Short candidate-safe error excerpt (compile output, stderr or the first
    failing *visible* test's error)."""
    if response_status == "compile_error":
        text = compile_output or stderr
    else:
        text = stderr or next((t.get("error") for t in tests if not t.get("hidden") and t.get("error")), None)
    if not text:
        return None
    return truncate(text.strip(), limit)


def embedding_text(question: CodingQuestion) -> str:
    """Same text as :meth:`app.seed.loaders.CodingQuestionSeed.embedding_text`."""
    return f"{question.title}. {(question.prompt_markdown or '')[:1500]} Concepts: {', '.join(question.expected_concepts or [])}"
