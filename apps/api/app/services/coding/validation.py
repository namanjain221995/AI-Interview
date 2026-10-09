"""Sandbox validation of a coding question: every reference solution must pass
every test (visible and hidden); optionally every starter must at least
compile against the visible tests. Used by
``POST /coding-questions/{id}/validate``."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.db.models import CodingQuestion
from app.domain.enums import CodeLanguage, RunKind, RunStatus
from app.schemas.api.coding import CodingQuestionValidation, LanguageValidationResult, ValidationTestFailure
from app.services.coding.questions import build_execute_request, question_languages, truncate
from app.services.coding.sandbox_client import SANDBOX_BUSY_MESSAGE, SandboxClient

log = get_logger(__name__)

VALIDATION_CONCURRENCY = 3
_MAX_FAILURES = 10


def _result(language: CodeLanguage, response, fault) -> LanguageValidationResult:  # type: ignore[no-untyped-def]
    if fault is not None:
        return LanguageValidationResult(
            language=language, status=RunStatus.ERROR, error=SANDBOX_BUSY_MESSAGE, total=response.total
        )
    failures = [
        ValidationTestFailure(
            id=t.id,
            hidden=t.hidden,
            error=truncate(t.error, 500) or None,
            expected=t.expected,
            actual=t.actual,
        )
        for t in response.tests
        if not t.passed
    ][:_MAX_FAILURES]
    error = None
    if response.status in (RunStatus.COMPILE_ERROR, RunStatus.REJECTED):
        error = truncate(response.compile_output or response.stderr, 1500) or None
    elif response.status != RunStatus.PASSED:
        error = truncate(response.stderr, 1500) or None
    return LanguageValidationResult(
        language=language,
        status=response.status,
        passed=response.passed,
        total=response.total,
        runtime_ms=response.runtime_ms,
        error=error,
        failures=failures,
    )


async def validate_question(
    question: CodingQuestion, sandbox: SandboxClient, *, include_starters: bool = False
) -> CodingQuestionValidation:
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(VALIDATION_CONCURRENCY)

    async def run_one(language: CodeLanguage, code: str, kind: RunKind) -> LanguageValidationResult:
        async with semaphore:
            request = build_execute_request(
                question, kind=kind, code=code, language=language, request_id=f"validate-{uuid.uuid4().hex}"
            )
            response, fault = await sandbox.execute_safe(request)
            return _result(language, response, fault)

    async def skipped(language: CodeLanguage, reason: str) -> LanguageValidationResult:
        return LanguageValidationResult(language=language, status="skipped", error=reason)

    languages = question_languages(question)
    reference_tasks = []
    for lang in languages:
        code = (question.reference_solutions or {}).get(lang.value)
        reference_tasks.append(
            run_one(lang, code, RunKind.SUBMIT)
            if code
            else skipped(lang, "No reference solution for this language.")
        )
    starter_tasks = []
    if include_starters:
        for lang in languages:
            code = (question.starter_code or {}).get(lang.value)
            starter_tasks.append(
                run_one(lang, code, RunKind.TEST)
                if code
                else skipped(lang, "No starter code for this language.")
            )
    results = list(await asyncio.gather(*reference_tasks))
    starters = list(await asyncio.gather(*starter_tasks)) if starter_tasks else []
    executed = [r for r in results if r.status != "skipped"]
    ok = bool(executed) and all(r.status == RunStatus.PASSED and r.passed == r.total for r in executed)
    # A starter only has to compile and run; wrong answers are expected.
    broken_starters = [
        s for s in starters if s.status in (RunStatus.COMPILE_ERROR, RunStatus.REJECTED, RunStatus.ERROR)
    ]
    ok = ok and not broken_starters
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    log.info(
        "coding.question.validated",
        coding_question_id=str(question.id),
        ok=ok,
        languages=len(results),
        executed=len(executed),
        duration_ms=duration_ms,
    )
    return CodingQuestionValidation(
        ok=ok,
        coding_question_id=question.id,
        results=results,
        starters=starters,
        duration_ms=duration_ms,
        validated_at=datetime.now(UTC),
    )
