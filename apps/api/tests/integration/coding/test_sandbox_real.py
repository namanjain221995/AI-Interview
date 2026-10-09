"""Against the real sandbox service (``-m sandbox``; skipped when it is not
reachable): curated reference solutions pass every test through the coding
module's client and request builder; generated starters compile in all six
languages; the full service flow works end to end.

The catalog holds ~1k problems, so by default only the 16 original problems
(top-level ``samples/coding/*.yaml``) run in every reference language, plus a
deterministic sample of ``SANDBOX_SAMPLE_SIZE`` others chosen by a stable hash
of the slug (in each of their reference languages). ``SANDBOX_TEST_ALL=1`` runs
every problem in every reference language.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.db.models import CodingQuestion
from app.domain.enums import CodeLanguage, RunKind, RunStatus
from app.seed.loaders import load_catalog
from app.services.coding.contracts import ExecuteRequest, SandboxTestCase
from app.services.coding.questions import build_execute_request
from app.services.coding.sandbox_client import build_sandbox_client
from app.services.coding.service import CodingServiceImpl
from app.services.coding.starters import FUNCTION_LANGUAGES, starters_for


def _sandbox_up() -> bool:
    try:
        return httpx.get(f"{get_settings().sandbox_base_url}/health", timeout=3).json().get("status") == "ok"
    except Exception:
        return False


pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(not _sandbox_up(), reason="sandbox service not reachable"),
]

SANDBOX_SAMPLE_SIZE = 25
RUN_ALL = os.environ.get("SANDBOX_TEST_ALL", "").strip().lower() in {"1", "true", "yes"}

CATALOG = load_catalog()
SEEDS = {seed.slug: seed for seed in CATALOG.coding_questions}
ORIGINAL = sorted(
    slug
    for slug in SEEDS
    if CATALOG.locations[("coding", slug)][0].parent == CATALOG.root / "coding"  # legacy top-level files
)


def _stable_rank(slug: str) -> str:
    return hashlib.sha256(slug.encode()).hexdigest()


def selected_slugs() -> list[str]:
    if RUN_ALL:
        return sorted(SEEDS)
    others = sorted((s for s in SEEDS if s not in ORIGINAL), key=_stable_rank)
    return ORIGINAL + sorted(others[:SANDBOX_SAMPLE_SIZE])


CASES = [
    (slug, CodeLanguage(lang))
    for slug in selected_slugs()
    for lang in sorted(SEEDS[slug].reference_solutions)
]


def question(slug: str) -> CodingQuestion:
    return CodingQuestion(id=uuid.uuid4(), organization_id=None, **SEEDS[slug].to_row())


@pytest.fixture
async def sandbox():
    client = build_sandbox_client()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.parametrize(("slug", "language"), CASES, ids=[f"{s}-{lang.value}" for s, lang in CASES])
async def test_curated_reference_solution_passes_all_tests(
    sandbox, slug: str, language: CodeLanguage
) -> None:
    q = question(slug)
    request = build_execute_request(
        q, kind=RunKind.SUBMIT, code=q.reference_solutions[language.value], language=language
    )
    started = time.perf_counter()
    response = await sandbox.execute(request)
    elapsed = (time.perf_counter() - started) * 1000
    print(
        f"SANDBOX {slug} {language.value} {response.status.value} {response.passed}/{response.total} {elapsed:.0f}ms"
    )
    assert response.status == RunStatus.PASSED, (
        response.compile_output,
        response.stderr,
        [t for t in response.tests if not t.passed][:3],
    )
    assert response.passed == response.total == len(q.tests)
    assert any(t.hidden for t in response.tests)


ALL_TYPES = {
    "function_name": "mixed_types",
    "params": [
        {"name": "count", "type": "int"},
        {"name": "big", "type": "long[]"},
        {"name": "grid", "type": "string[][]"},
        {"name": "flag", "type": "bool"},
        {"name": "ratio", "type": "float"},
        {"name": "words", "type": "string[]"},
    ],
    "return_type": "float[]",
}


@pytest.mark.parametrize("language", FUNCTION_LANGUAGES, ids=[lang.value for lang in FUNCTION_LANGUAGES])
async def test_generated_starters_compile_and_run_in_every_language(sandbox, language: CodeLanguage) -> None:
    code = starters_for(ALL_TYPES, [language], description="Starter compile check")[language.value]
    request = ExecuteRequest(
        request_id=f"starter-{uuid.uuid4().hex}",
        language=language,
        code=code,
        mode="tests",
        signature=ALL_TYPES,
        tests=[SandboxTestCase(id="t1", args=[3, [1, 2], [["a", "b"]], True, 0.5, ["x"]], expected=[1.5])],
    )
    response = await sandbox.execute(request)
    print(f"STARTER {language.value} {response.status.value}")
    # The starter returns an empty array: a wrong answer, but it must compile and run.
    assert response.status == RunStatus.FAILED, (response.compile_output, response.stderr)


async def test_curated_starters_compile(sandbox) -> None:
    q = question("two_sum")
    for language in FUNCTION_LANGUAGES:
        request = build_execute_request(
            q, kind=RunKind.TEST, code=q.starter_code[language.value], language=language
        )
        response = await sandbox.execute(request)
        assert response.status in (RunStatus.FAILED, RunStatus.PASSED), (language, response.compile_output)


@pytest.mark.integration
async def test_full_service_flow_against_the_real_sandbox(coding_env, mock_ai) -> None:
    sandbox = build_sandbox_client()
    service = CodingServiceImpl(sandbox=sandbox, ai=mock_ai)
    try:
        state = await service.start(
            str(coding_env.session_id),
            str(coding_env.questions["two_sum"]),
            question_id=None,
            language=CodeLanguage.PYTHON,
        )
        cs_id = state.coding_session_id
        timings: dict[str, float] = {}
        started = time.perf_counter()
        visible = await service.run(
            cs_id, kind=RunKind.TEST, code=state.code or "", language=CodeLanguage.PYTHON
        )
        timings["test_starter"] = time.perf_counter() - started
        assert visible.status == RunStatus.FAILED and visible.total == 3
        reference = SEEDS["two_sum"].reference_solutions[CodeLanguage.PYTHON]
        started = time.perf_counter()
        submit = await service.run(cs_id, kind=RunKind.SUBMIT, code=reference, language=CodeLanguage.PYTHON)
        timings["submit_reference"] = time.perf_counter() - started
        assert submit.status == RunStatus.PASSED and (submit.passed, submit.total) == (10, 10)
        assert [t["id"] for t in submit.tests] == ["t1", "t2", "t3"]
        go_reference = SEEDS["two_sum"].reference_solutions[CodeLanguage.GO]
        await service.record_update(cs_id, code=go_reference, language=CodeLanguage.GO, version=1)
        started = time.perf_counter()
        go = await service.run(cs_id, kind=RunKind.SUBMIT, code=go_reference, language=CodeLanguage.GO)
        timings["submit_go"] = time.perf_counter() - started
        assert go.status == RunStatus.PASSED and go.passed == 10
        broken = await service.run(
            cs_id,
            kind=RunKind.TEST,
            code="package main\nfunc twoSum(nums []int, target int) []int { return nums[",
            language=CodeLanguage.GO,
        )
        assert broken.status == RunStatus.COMPILE_ERROR and broken.compile_output
        print("SERVICE_FLOW", {k: round(v * 1000) for k, v in timings.items()})
        await service.finish(cs_id, reason="submitted")
    finally:
        await sandbox.aclose()
