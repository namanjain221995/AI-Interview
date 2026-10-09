"""Fixtures for the coding integration tests: a live interview session with a
coding-enabled round, the curated coding catalog, a fake sandbox and a coding
service wired to the mock AI."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from app.core.security import generate_invite_token
from app.db.models import Candidate, CodingQuestion, Interview, InterviewSession, Job, Round
from app.db.session import session_scope
from app.domain.enums import CodeLanguage, Difficulty, RoundType, RunStatus
from app.schemas.rounds import CodingSection, RoundCompetency, RoundConfig
from app.seed.loaders import load_coding_questions
from app.services.coding.contracts import ExecuteRequest, ExecuteResponse, SandboxTestResult
from app.services.coding.sandbox_client import SandboxFault, platform_error_response, set_sandbox_client
from app.services.coding.service import CodingServiceImpl

SECRET_MARK = "SECRET"


def all_pass(request: ExecuteRequest) -> ExecuteResponse:
    results = [
        SandboxTestResult(
            id=t.id, passed=True, actual=t.expected, expected=t.expected, runtime_ms=1.0, hidden=t.hidden
        )
        for t in request.tests
    ]
    return ExecuteResponse(
        request_id=request.request_id,
        status=RunStatus.PASSED,
        tests=results,
        passed=len(results),
        total=len(results),
        runtime_ms=30,
    )


def judge(request: ExecuteRequest) -> ExecuteResponse:
    """Deterministic fake execution driven by markers in the code."""
    code = request.code
    if request.mode == "run":
        return ExecuteResponse(
            request_id=request.request_id,
            status=RunStatus.PASSED,
            stdout=f"ran:{request.stdin or ''}",
            exit_code=0,
            runtime_ms=5,
        )
    if "COMPILE_ERROR" in code:
        return ExecuteResponse(
            request_id=request.request_id,
            status=RunStatus.COMPILE_ERROR,
            compile_output="SyntaxError: invalid syntax (line 2)",
            total=len(request.tests),
        )
    if "TIMEOUT" in code:
        return ExecuteResponse(
            request_id=request.request_id,
            status=RunStatus.TIMEOUT,
            stderr="Time limit exceeded",
            total=len(request.tests),
        )
    results: list[SandboxTestResult] = []
    for test in request.tests:
        if "RUNTIME_ERROR" in code:
            passed = False
        elif "PASS_ALL" in code or "seen[" in code:
            passed = True
        elif "PASS_VISIBLE" in code:
            passed = not test.hidden
        else:
            passed = False
        actual = test.expected if passed else (f"{SECRET_MARK}_ACTUAL_{test.id}" if test.hidden else [])
        error = None
        if "RUNTIME_ERROR" in code:
            error = f"{SECRET_MARK}_HIDDEN_ERROR" if test.hidden else "IndexError: list index out of range"
        elif not passed and test.hidden:
            error = f"{SECRET_MARK}_HIDDEN_ERROR"
        results.append(
            SandboxTestResult(
                id=test.id,
                passed=passed,
                actual=actual,
                expected=test.expected,
                error=error,
                runtime_ms=1.5,
                hidden=test.hidden,
            )
        )
    passed_count = sum(r.passed for r in results)
    if "RUNTIME_ERROR" in code:
        status = RunStatus.ERROR
    else:
        status = RunStatus.PASSED if passed_count == len(results) else RunStatus.FAILED
    return ExecuteResponse(
        request_id=request.request_id,
        status=status,
        stdout="debug print\n",
        stderr="Traceback: IndexError" if status == RunStatus.ERROR else "",
        exit_code=0,
        runtime_ms=40,
        memory_kb=9000,
        tests=results,
        passed=passed_count,
        total=len(results),
        meta={"image": "fake"},
    )


class FakeSandbox:
    def __init__(self) -> None:
        self.requests: list[ExecuteRequest] = []
        self.fail_next: list[SandboxFault] = []
        self.judge: Callable[[ExecuteRequest], ExecuteResponse] = judge

    async def execute(self, request: ExecuteRequest) -> ExecuteResponse:
        self.requests.append(request)
        if self.fail_next:
            raise self.fail_next.pop(0)
        return self.judge(request)

    async def execute_safe(self, request: ExecuteRequest) -> tuple[ExecuteResponse, SandboxFault | None]:
        try:
            return await self.execute(request), None
        except SandboxFault as fault:
            return platform_error_response(request, fault), fault

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class CodingEnv:
    organization_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    candidate_id: uuid.UUID
    interview_id: uuid.UUID
    session_id: uuid.UUID
    questions: dict[str, uuid.UUID] = field(default_factory=dict)  # slug -> id (global catalog)


def round_config(**coding: Any) -> RoundConfig:
    section = {
        "enabled": True,
        "duration_minutes": 20,
        "languages": [CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT, CodeLanguage.GO],
        "difficulty": Difficulty.EASY,
        "min_seconds_between_comments": 30,
    }
    section.update(coding)
    return RoundConfig(
        round_type=RoundType.CODING,
        name="Coding Round",
        duration_minutes=40,
        competencies=[
            RoundCompetency(key="algorithms", weight=1.2),
            RoundCompetency(key="data_structures", weight=1.0),
            RoundCompetency(key="problem_solving", weight=1.0),
        ],
        coding=CodingSection(**section),
    )


async def seed_catalog_questions() -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    async with session_scope() as db:
        for seed in load_coding_questions():
            row = CodingQuestion(id=uuid.uuid4(), organization_id=None, **seed.to_row())
            db.add(row)
            ids[seed.slug] = row.id
    return ids


async def create_session(
    organization_id: uuid.UUID, config: RoundConfig, *, status: str = "live"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    now = datetime.now(UTC)
    _, token_hash = generate_invite_token()
    async with session_scope() as db:
        job = Job(
            organization_id=organization_id,
            title="Backend Engineer",
            description="Builds APIs",
            status="open",
        )
        candidate = Candidate(
            organization_id=organization_id,
            full_name="Casey Candidate",
            email=f"casey-{secrets.token_hex(4)}@example.test",
        )
        db.add_all([job, candidate])
        await db.flush()
        rnd = Round(
            job_id=job.id,
            name=config.name,
            round_type=config.round_type.value,
            config=config.model_dump(mode="json"),
        )
        db.add(rnd)
        await db.flush()
        interview = Interview(
            organization_id=organization_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=rnd.id,
            status="in_progress",
            invite_token_hash=token_hash,
            invite_expires_at=now + timedelta(days=7),
        )
        db.add(interview)
        await db.flush()
        session = InterviewSession(
            interview_id=interview.id,
            organization_id=organization_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=rnd.id,
            status=status,
            round_config=config.model_dump(mode="json"),
            started_at=now - timedelta(minutes=5),
            state={
                "elapsed_before_pause_s": 0.0,
                "last_resumed_at": (now - timedelta(minutes=5)).isoformat(),
            },
        )
        db.add(session)
        await db.flush()
        return job.id, rnd.id, candidate.id, interview.id, session.id


@pytest.fixture
def fake_sandbox() -> AsyncIterator[FakeSandbox]:
    sandbox = FakeSandbox()
    previous = set_sandbox_client(sandbox)
    try:
        yield sandbox
    finally:
        set_sandbox_client(previous)


@pytest_asyncio.fixture
async def coding_env(org_factory: Callable[..., Awaitable[uuid.UUID]], redis: Any) -> CodingEnv:
    org_id = await org_factory()
    questions = await seed_catalog_questions()
    job_id, round_id, candidate_id, interview_id, session_id = await create_session(org_id, round_config())
    return CodingEnv(org_id, job_id, round_id, candidate_id, interview_id, session_id, questions)


@pytest.fixture
def service(fake_sandbox: FakeSandbox, mock_ai: Any) -> CodingServiceImpl:
    return CodingServiceImpl(sandbox=fake_sandbox, ai=mock_ai)
