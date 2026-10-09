"""Candidate payloads never contain hidden tests, reference solutions or
hidden-test outputs; staff records keep everything."""

from __future__ import annotations

import json

import pytest

from app.domain.enums import CodeLanguage, RunKind, RunStatus
from app.schemas.rounds import CodingSection
from app.services.coding.contracts import SandboxTestResult
from app.services.coding.questions import (
    allowed_languages,
    build_execute_request,
    candidate_test_results,
    candidate_test_view,
    choose_language,
    first_error_excerpt,
    public_question,
    staff_test_results,
    visible_tests,
)

from .conftest import make_question


def results_for(question, *, hidden_secret: str = "SECRET") -> list[SandboxTestResult]:
    out = []
    for test in question.tests:
        out.append(
            SandboxTestResult(
                id=test["id"],
                passed=not test.get("hidden"),
                actual=f"{hidden_secret}-actual-{test['id']}" if test.get("hidden") else test.get("expected"),
                expected=test.get("expected"),
                error=f"{hidden_secret}-error-{test['id']}" if test.get("hidden") else None,
                runtime_ms=1.0,
                hidden=bool(test.get("hidden")),
            )
        )
    return out


def test_public_question_has_visible_tests_only_and_no_solutions(two_sum) -> None:
    public = public_question(two_sum, [CodeLanguage.PYTHON, CodeLanguage.GO])
    payload = public.model_dump_json()
    hidden = [t for t in two_sum.tests if t.get("hidden")]
    assert hidden, "fixture needs hidden tests"
    assert {t["id"] for t in public.visible_tests} == {t["id"] for t in visible_tests(two_sum)}
    for test in hidden:
        assert f'"{test["id"]}"' not in payload
        assert test["description"] not in payload
    assert "seen.get(target - nums[i])" not in payload  # typescript reference body
    assert "complement" not in payload  # python/javascript reference bodies
    assert "rubric" not in payload and "hints" not in payload
    assert set(public.starter_code) == {"python", "go"}
    assert all(t["hidden"] is False for t in public.visible_tests)


def test_candidate_view_refuses_hidden_tests(two_sum) -> None:
    hidden = next(t for t in two_sum.tests if t.get("hidden"))
    with pytest.raises(ValueError):
        candidate_test_view(hidden)


def test_candidate_results_drop_hidden_entries_entirely(two_sum) -> None:
    results = results_for(two_sum)
    visible = candidate_test_results(two_sum, results)
    blob = json.dumps(visible)
    assert "SECRET" not in blob
    assert len(visible) == len(visible_tests(two_sum))
    assert all(v["passed"] for v in visible)
    hidden_ids = {t["id"] for t in two_sum.tests if t.get("hidden")}
    assert not hidden_ids & {v["id"] for v in visible}


def test_candidate_results_ignore_results_the_sandbox_flags_hidden(two_sum) -> None:
    first_visible = visible_tests(two_sum)[0]["id"]
    sneaky = [SandboxTestResult(id=first_visible, passed=False, actual="SECRET", hidden=True)]
    assert candidate_test_results(two_sum, sneaky) == []


def test_staff_results_keep_hidden_details(two_sum) -> None:
    staff = staff_test_results(two_sum, results_for(two_sum))
    hidden = [r for r in staff if r["hidden"]]
    assert hidden and all("SECRET" in str(r["actual"]) for r in hidden)
    assert all("args" in r and "description" in r for r in hidden)


def test_sql_staff_results_use_expected_rows(sql_question) -> None:
    results = [
        SandboxTestResult(id=t["id"], passed=True, actual=t["expected_rows"], hidden=bool(t.get("hidden")))
        for t in sql_question.tests
    ]
    staff = staff_test_results(sql_question, results)
    assert staff[0]["expected"] == sql_question.tests[0]["expected_rows"]
    assert "setup_sql" in staff[0]


def test_test_run_sends_visible_tests_submit_sends_all(two_sum) -> None:
    test_req = build_execute_request(two_sum, kind=RunKind.TEST, code="x", language=CodeLanguage.PYTHON)
    submit_req = build_execute_request(two_sum, kind=RunKind.SUBMIT, code="x", language=CodeLanguage.PYTHON)
    run_req = build_execute_request(
        two_sum, kind=RunKind.RUN, code="x", language=CodeLanguage.PYTHON, stdin="1 2"
    )
    assert [t.id for t in test_req.tests] == [t["id"] for t in visible_tests(two_sum)]
    assert all(not t.hidden for t in test_req.tests)
    assert len(submit_req.tests) == len(two_sum.tests)
    assert run_req.mode == "run" and run_req.tests == [] and run_req.stdin == "1 2"
    # descriptions never reach the sandbox (extra="forbid" contract model)
    assert "description" not in submit_req.model_dump_json()
    assert test_req.signature["function_name"] == "two_sum"


def test_sql_requests_carry_the_setup(sql_question) -> None:
    req = build_execute_request(sql_question, kind=RunKind.SUBMIT, code="SELECT 1", language=CodeLanguage.SQL)
    assert req.sql_setup and "CREATE TABLE" in req.sql_setup
    assert req.signature is None
    assert req.tests[0].setup_sql


def test_language_rules(two_sum, sql_question) -> None:
    section = CodingSection(languages=[CodeLanguage.GO, CodeLanguage.PYTHON])
    assert allowed_languages(two_sum, section) == [CodeLanguage.GO, CodeLanguage.PYTHON]
    assert choose_language(two_sum, section, CodeLanguage.PYTHON) == CodeLanguage.PYTHON
    assert (
        choose_language(two_sum, section, CodeLanguage.JAVA) == CodeLanguage.GO
    )  # not allowed -> first allowed
    assert allowed_languages(sql_question, section) == [CodeLanguage.SQL]
    assert allowed_languages(sql_question, section, strict=True) == [CodeLanguage.SQL]
    python_only = make_question("two_sum", languages=["python"])
    assert allowed_languages(python_only, CodingSection(languages=[CodeLanguage.GO]), strict=True) == []
    assert allowed_languages(python_only, CodingSection(languages=[CodeLanguage.GO])) == [CodeLanguage.PYTHON]


def test_error_excerpt_never_uses_hidden_test_errors() -> None:
    tests = [
        {"id": "t9", "hidden": True, "error": "SECRET"},
        {"id": "t1", "hidden": False, "error": "IndexError"},
    ]
    assert first_error_excerpt(RunStatus.FAILED.value, "", "", tests) == "IndexError"
    assert (
        first_error_excerpt(RunStatus.COMPILE_ERROR.value, "", "SyntaxError: bad", tests)
        == "SyntaxError: bad"
    )
    assert first_error_excerpt(RunStatus.FAILED.value, "", "", [tests[0]]) is None
