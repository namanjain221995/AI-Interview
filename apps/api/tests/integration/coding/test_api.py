"""Coding REST APIs: coding question CRUD (org scoped, global read-only, RBAC),
sandbox validation, and the staff coding view of a session."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, CodingQuestion
from app.db.session import session_scope
from app.domain.enums import CodeLanguage, Role, RunKind, RunStatus
from app.services.coding.contracts import ExecuteRequest, ExecuteResponse

from .conftest import SECRET_MARK, all_pass

pytestmark = pytest.mark.integration

API = "/api/v1/coding-questions"


def new_question(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Count Positive Numbers",
        "difficulty": "easy",
        "competency_keys": ["algorithms"],
        "role_tags": ["backend"],
        "prompt_markdown": "Return how many numbers in `nums` are strictly positive.\n\nExample: [1, -2, 3] -> 2",
        "languages": ["python", "javascript", "go"],
        "signature": {
            "function_name": "count_positive",
            "params": [{"name": "nums", "type": "int[]"}],
            "return_type": "int",
        },
        "starter_code": {"python": "def count_positive(nums: list[int]) -> int:\n    return 0\n"},
        "reference_solutions": {
            "python": "def count_positive(nums):\n    return sum(1 for n in nums if n > 0)\n"
        },
        "tests": [
            {"id": "t1", "args": [[1, -2, 3]], "expected": 2},
            {"id": "t2", "args": [[]], "expected": 0, "description": "empty input"},
            {"id": "t3", "args": [[-1, -2]], "expected": 0, "hidden": True, "description": "only negatives"},
        ],
        "expected_concepts": ["single pass"],
        "optimal_complexity": {"time": "O(n)", "space": "O(1)"},
        "hints": [
            "Look at each number once.",
            "Count only values greater than zero.",
            "Loop and increment a counter when n > 0.",
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
async def catalog(coding_env) -> dict[str, uuid.UUID]:
    return coding_env.questions


async def test_list_shows_org_and_global_questions_with_filters(auth_client, coding_env, catalog) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    created = await recruiter.post(API, json=new_question())
    assert created.status_code == 201, created.text
    other = await auth_client(Role.RECRUITER)  # another organization
    foreign = await other.post(API, json=new_question(title="Foreign Question"))
    assert foreign.status_code == 201

    listing = (await recruiter.get(API, params={"limit": 200})).json()
    assert listing["total"] == len(catalog) + 1
    first = listing["items"][0]
    assert first["title"] == "Count Positive Numbers"  # organization rows first
    # Summary rows (default view): test counts, no heavy or staff-only fields.
    assert (first["visible_test_count"], first["hidden_test_count"]) == (2, 1)
    heavy = {
        "prompt_markdown",
        "tests",
        "starter_code",
        "reference_solutions",
        "signature",
        "rubric",
        "hints",
    }
    assert all(heavy.isdisjoint(item) for item in listing["items"])
    titles = {item["title"] for item in listing["items"]}
    assert "Foreign Question" not in titles

    full = (await recruiter.get(API, params={"limit": 5, "view": "full"})).json()
    assert full["total"] == listing["total"] and full["items"][0]["tests"][2]["hidden"] is True
    assert all(item["reference_solutions"] is None and item["starter_code"] for item in full["items"])
    assert (await recruiter.get(API, params={"view": "everything"})).status_code == 422

    async with session_scope() as db:
        sql_rows = (
            await db.scalars(
                select(CodingQuestion.slug).where(
                    CodingQuestion.organization_id.is_(None), CodingQuestion.languages.contains(["sql"])
                )
            )
        ).all()
    sql = (await recruiter.get(API, params={"language": "sql", "limit": 200})).json()
    assert sql_rows and sql["total"] == len(sql_rows) and {i["languages"][0] for i in sql["items"]} == {"sql"}
    assert (await recruiter.get(API, params={"scope": "organization"})).json()["total"] == 1
    assert (await recruiter.get(API, params={"scope": "global", "difficulty": "hard"})).json()["total"] >= 1
    assert (await recruiter.get(API, params={"q": "two sum"})).json()["items"][0]["slug"] == "two_sum"
    comp = (await recruiter.get(API, params={"competency": "sql"})).json()
    assert comp["total"] >= 1 and all("sql" in i["competency_keys"] for i in comp["items"])
    paged = (await recruiter.get(API, params={"limit": 5, "offset": 5})).json()
    assert len(paged["items"]) == 5 and paged["offset"] == 5


async def test_get_global_and_foreign_questions(auth_client, coding_env, catalog) -> None:
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=coding_env.organization_id)
    response = await interviewer.get(f"{API}/{catalog['two_sum']}")
    assert response.status_code == 200
    body = response.json()
    assert body["is_global"] is True and body["reference_solutions"]["python"]
    assert any(t["hidden"] for t in body["tests"])  # staff see hidden tests
    other = await auth_client(Role.RECRUITER)
    foreign_id = (await other.post(API, json=new_question())).json()["id"]
    assert (await interviewer.get(f"{API}/{foreign_id}")).status_code == 404


async def test_create_generates_missing_starters_audits_and_rejects_duplicates(
    auth_client, coding_env
) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    response = await recruiter.post(API, json=new_question())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "count_positive_numbers" and body["organization_id"] == str(
        coding_env.organization_id
    )
    assert body["source"] == "curated" and body["is_global"] is False
    assert set(body["starter_code"]) == {"python", "javascript", "go"}
    assert "function countPositive(nums)" in body["starter_code"]["javascript"]
    assert "func countPositive(nums []int) int" in body["starter_code"]["go"]
    async with session_scope() as db:
        log = (await db.scalars(select(AuditLog).where(AuditLog.action == "coding_question.create"))).one()
    assert log.resource_id == body["id"] and log.details["generated_starters"] == ["javascript", "go"]
    duplicate = await recruiter.post(API, json=new_question())
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"tests": [{"id": "t1", "args": [["a"]], "expected": 1}]}, "not a valid int"),
        ({"tests": [{"id": "t1", "args": [[1]], "expected": 1, "hidden": True}]}, "visible test"),
        (
            {"languages": ["sql"], "signature": None, "starter_code": {}, "reference_solutions": {}},
            "sql_setup",
        ),
        ({"reference_solutions": {"java": "class Solution {}"}}, "not listed in languages"),
        ({"hints": ["a", "b", "c", "d"]}, "at most 3"),
        (
            {
                "signature": {
                    "function_name": "class",
                    "params": [{"name": "nums", "type": "int[]"}],
                    "return_type": "int",
                }
            },
            "reserved",
        ),
    ],
)
async def test_create_validation_errors(auth_client, coding_env, overrides, fragment) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    response = await recruiter.post(API, json=new_question(**overrides))
    assert response.status_code == 422, response.text
    assert fragment in response.text


async def test_rbac(auth_client, coding_env, catalog, candidate_auth_client) -> None:
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=coding_env.organization_id)
    assert (await interviewer.get(API)).status_code == 200
    assert (await interviewer.post(API, json=new_question())).status_code == 403
    assert (await interviewer.post(f"{API}/{catalog['two_sum']}/validate")).status_code == 403
    candidate = await candidate_auth_client(organization_id=coding_env.organization_id)
    assert (await candidate.get(API)).status_code == 403
    assert (await candidate.get(f"{API}/{catalog['two_sum']}")).status_code == 403


async def test_patch_org_question_global_is_read_only(auth_client, coding_env, catalog) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    created = (await recruiter.post(API, json=new_question())).json()
    async with session_scope() as db:
        row = await db.get(CodingQuestion, uuid.UUID(created["id"]))
        row.embedding = [0.0] * 1024
    patched = await recruiter.patch(
        f"{API}/{created['id']}", json={"title": "Count Strictly Positive Numbers", "status": "draft"}
    )
    assert patched.status_code == 200, patched.text
    assert (
        patched.json()["title"] == "Count Strictly Positive Numbers" and patched.json()["status"] == "draft"
    )
    assert patched.json()["has_embedding"] is False  # embedded text changed -> stale embedding cleared
    invalid = await recruiter.patch(
        f"{API}/{created['id']}", json={"tests": [{"id": "t1", "args": [[1]], "expected": "x"}]}
    )
    assert invalid.status_code == 422
    blocked = await recruiter.patch(f"{API}/{catalog['two_sum']}", json={"title": "Hacked"})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "global_read_only"
    other = await auth_client(Role.ADMIN)
    assert (await other.patch(f"{API}/{created['id']}", json={"title": "Nope"})).status_code == 404
    async with session_scope() as db:
        assert (await db.scalars(select(AuditLog).where(AuditLog.action == "coding_question.update"))).one()


async def test_validate_runs_every_reference_solution(auth_client, coding_env, catalog, fake_sandbox) -> None:
    fake_sandbox.judge = all_pass
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    response = await recruiter.post(
        f"{API}/{catalog['two_sum']}/validate", params={"include_starters": "true"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert {r["language"] for r in body["results"]} == {
        "python",
        "javascript",
        "typescript",
        "java",
        "go",
        "cpp",
    }
    assert all(r["status"] == "passed" and r["passed"] == r["total"] == 10 for r in body["results"])
    assert len(body["starters"]) == 6
    reference_requests = [r for r in fake_sandbox.requests if len(r.tests) == 10]
    assert len(reference_requests) == 6
    starter_requests = [r for r in fake_sandbox.requests if len(r.tests) == 3]
    assert len(starter_requests) == 6 and all(not t.hidden for r in starter_requests for t in r.tests)


async def test_validate_reports_failures_and_missing_references(
    auth_client, coding_env, fake_sandbox
) -> None:
    def judge(request: ExecuteRequest) -> ExecuteResponse:
        response = all_pass(request)
        if request.language == CodeLanguage.GO:
            tests = [
                t.model_copy(update={"passed": t.id != "t3", "actual": 99, "error": None})
                for t in response.tests
            ]
            return response.model_copy(
                update={"status": RunStatus.FAILED, "tests": tests, "passed": len(tests) - 1}
            )
        return response

    fake_sandbox.judge = judge
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    body = new_question(
        reference_solutions={
            "python": "def count_positive(nums): return 0",
            "go": "package main\nfunc countPositive(nums []int) int { return 0 }\n",
        }
    )
    qid = (await recruiter.post(API, json=body)).json()["id"]
    result = (await recruiter.post(f"{API}/{qid}/validate")).json()
    by_lang = {r["language"]: r for r in result["results"]}
    assert result["ok"] is False
    assert by_lang["python"]["status"] == "passed"
    assert by_lang["go"]["status"] == "failed" and by_lang["go"]["failures"][0]["id"] == "t3"
    assert by_lang["go"]["failures"][0]["hidden"] is True
    assert by_lang["javascript"]["status"] == "skipped"


async def test_session_coding_view_for_staff_only(
    auth_client, candidate_auth_client, coding_env, service
) -> None:
    state = await service.start(
        str(coding_env.session_id),
        str(coding_env.questions["two_sum"]),
        question_id=None,
        language=CodeLanguage.PYTHON,
    )
    cs_id = state.coding_session_id
    code = "def two_sum(nums, target):\n    # PASS_VISIBLE\n    return []\n"
    await service.record_update(cs_id, code=code, language=CodeLanguage.PYTHON, version=1)
    await service.run(cs_id, kind=RunKind.SUBMIT, code=code, language=CodeLanguage.PYTHON)
    await service.request_hint(cs_id, requested_via="voice")
    await service.finish(cs_id, reason="submitted")

    recruiter = await auth_client(Role.INTERVIEWER, organization_id=coding_env.organization_id)
    response = await recruiter.get(f"/api/v1/sessions/{coding_env.session_id}/coding")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == cs_id and item["status"] == "submitted" and item["end_reason"] == "submitted"
    assert item["question"]["slug"] == "two_sum"
    assert [s["seq"] for s in item["snapshots"]] == [1, 2] and item["snapshots"][1]["diff_from_previous"]
    run = item["runs"][0]
    assert run["kind"] == "submit" and run["passed_count"] == 3 and run["total_count"] == 10
    assert any(
        t["hidden"] and SECRET_MARK in str(t["actual"]) for t in run["test_results"]
    )  # staff view keeps hidden details
    assert item["hints"][0]["level"] == 1 and item["hints"][0]["requested_via"] == "voice"
    assert item["hints_used"] == 1 and item["final_run_id"] == run["id"]
    async with session_scope() as db:
        log = (await db.scalars(select(AuditLog).where(AuditLog.action == "session.coding.view"))).one()
    assert log.resource_id == str(coding_env.session_id)

    outsider = await auth_client(Role.ADMIN)
    assert (await outsider.get(f"/api/v1/sessions/{coding_env.session_id}/coding")).status_code == 404
    candidate = await candidate_auth_client(organization_id=coding_env.organization_id)
    assert (await candidate.get(f"/api/v1/sessions/{coding_env.session_id}/coding")).status_code == 403
    assert (await recruiter.get(f"/api/v1/sessions/{uuid.uuid4()}/coding")).status_code == 404
