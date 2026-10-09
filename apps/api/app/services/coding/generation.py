"""Generated coding problems (used only when ``CodingSection.allow_generated``
and neither a pinned nor a retrieved problem is available).

The main model writes a complete function problem (statement, signature,
tests, Python reference solution, three graduated hints, rubric). Before the
problem can be used it must pass:

1. schema + signature checks (supported types, reserved words, comparison
   rules - the catalog's :class:`~app.seed.loaders.CodingSignature`),
2. type checks of every test argument / expected value against the signature,
3. at least 2 visible and 3 hidden tests,
4. the reference solution passing *every* test in the real sandbox.

Starter code for all six function languages is produced deterministically
from the signature (:mod:`app.services.coding.starters`), so it always matches
the harness conventions. One retry with the validation feedback is allowed.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.db.models import CodingQuestion
from app.db.session import session_scope
from app.domain.enums import BankStatus, CodeLanguage, Difficulty, ModelTask, QuestionSource, RunStatus
from app.seed.loaders import CodingSignature, value_matches_type
from app.services.coding.contracts import ExecuteRequest, SandboxTestCase
from app.services.coding.prompts import GENERATION_SYSTEM, GENERATION_USER
from app.services.coding.sandbox_client import SandboxClient, SandboxFault, get_sandbox_client
from app.services.coding.schemas import GeneratedCodingProblem
from app.services.coding.starters import FUNCTION_LANGUAGES, starters_for

log = get_logger(__name__)

MIN_VISIBLE_TESTS = 2
MIN_HIDDEN_TESTS = 3
GENERATION_TIMEOUT_S = 90.0
MAX_GENERATION_ATTEMPTS = 2


class GeneratedProblemInvalid(ValueError):
    """The generated problem failed validation (message is model feedback)."""


@dataclass(slots=True)
class ValidatedProblem:
    problem: GeneratedCodingProblem
    signature: dict[str, Any]
    tests: list[dict[str, Any]]


def slugify(text: str, *, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"p_{slug}"
    return slug[:max_length].rstrip("_")


def validate_generated(problem: GeneratedCodingProblem) -> ValidatedProblem:
    """Static validation (no sandbox). Raises :class:`GeneratedProblemInvalid`."""
    try:
        signature = CodingSignature.model_validate(
            {
                "function_name": problem.function_name,
                "params": [{"name": p.name, "type": p.type} for p in problem.params],
                "return_type": problem.return_type,
                "comparison": problem.comparison,
            }
        )
    except ValidationError as exc:
        raise GeneratedProblemInvalid(f"invalid signature: {exc.errors()[0].get('msg')}") from exc
    tests: list[dict[str, Any]] = []
    for index, test in enumerate(problem.tests, start=1):
        try:
            args = json.loads(test.args_json)
            expected = json.loads(test.expected_json)
        except json.JSONDecodeError as exc:
            raise GeneratedProblemInvalid(f"test {index}: args_json/expected_json is not valid JSON") from exc
        if not isinstance(args, list) or len(args) != len(signature.params):
            raise GeneratedProblemInvalid(
                f"test {index}: args_json must be a JSON array with {len(signature.params)} elements"
            )
        for param, arg in zip(signature.params, args, strict=True):
            if not value_matches_type(arg, param.type):
                raise GeneratedProblemInvalid(
                    f"test {index}: argument {param.name!r} is not a valid {param.type}"
                )
        if not value_matches_type(expected, signature.return_type):
            raise GeneratedProblemInvalid(
                f"test {index}: expected value is not a valid {signature.return_type}"
            )
        record: dict[str, Any] = {
            "id": f"t{index}",
            "args": args,
            "expected": expected,
            "hidden": bool(test.hidden),
        }
        if test.description:
            record["description"] = test.description[:200]
        tests.append(record)
    visible = sum(1 for t in tests if not t["hidden"])
    hidden = len(tests) - visible
    if visible < MIN_VISIBLE_TESTS or hidden < MIN_HIDDEN_TESTS:
        raise GeneratedProblemInvalid(
            f"need at least {MIN_VISIBLE_TESTS} visible and {MIN_HIDDEN_TESTS} hidden tests (got {visible} visible, {hidden} hidden)"
        )
    if not re.search(rf"\bdef\s+{re.escape(problem.function_name)}\s*\(", problem.reference_solution_python):
        raise GeneratedProblemInvalid(
            f"reference_solution_python must define a top-level function {problem.function_name}"
        )
    if any(not h.strip() for h in problem.hints):
        raise GeneratedProblemInvalid("all three hints must be non-empty")
    return ValidatedProblem(problem, signature.model_dump(mode="json", exclude_none=True), tests)


async def verify_reference(sandbox: SandboxClient, validated: ValidatedProblem) -> None:
    """Run the Python reference against every test; raises on any failure."""
    request = ExecuteRequest(
        request_id=f"gen-{uuid.uuid4().hex}",
        language=CodeLanguage.PYTHON,
        code=validated.problem.reference_solution_python,
        mode="tests",
        signature=validated.signature,
        tests=[
            SandboxTestCase(**{k: v for k, v in t.items() if k != "description"}) for t in validated.tests
        ],
    )
    response = await sandbox.execute(request)  # SandboxFault propagates (cannot validate)
    if response.status != RunStatus.PASSED or response.passed != response.total:
        failing = [t.id for t in response.tests if not t.passed][:5]
        detail = (response.compile_output or response.stderr or "").strip()[:300]
        raise GeneratedProblemInvalid(
            f"the reference solution did not pass all tests (status {response.status.value}, "
            f"{response.passed}/{response.total}, failing {failing}){': ' + detail if detail else ''}"
        )


async def generate_problem(
    *,
    organization_id: uuid.UUID,
    competency_keys: Sequence[str],
    difficulty: Difficulty,
    minutes: int,
    avoid_titles: Sequence[str] = (),
    ai: AIServices | None = None,
    sandbox: SandboxClient | None = None,
    session_id: str | None = None,
) -> CodingQuestion | None:
    """Generate, validate (sandbox) and store a coding problem; ``None`` if it
    could not be produced. No transaction is held while the model and the
    sandbox work; the validated row is inserted in its own transaction."""
    services = ai or get_ai()
    runner = sandbox or get_sandbox_client()
    feedback = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        messages = [
            {"role": "system", "content": GENERATION_SYSTEM.render()},
            {
                "role": "user",
                "content": GENERATION_USER.render(
                    difficulty=difficulty.value,
                    competencies=", ".join(k.replace("_", " ") for k in competency_keys)
                    or "algorithms and problem solving",
                    minutes=minutes,
                    avoid=", ".join(avoid_titles[:30]) or "none",
                    feedback=feedback,
                ),
            },
        ]
        try:
            result = await services.llm.structured(
                GeneratedCodingProblem,
                messages,
                context=CallContext(
                    task=ModelTask.QUESTION_GENERATION,
                    session_id=session_id,
                    organization_id=str(organization_id),
                    prompt_version=GENERATION_USER.version,
                ),
                temperature=0.5,
                max_tokens=4000,
                timeout=GENERATION_TIMEOUT_S,
                max_attempts=2,
            )
            validated = validate_generated(result.value)
            await verify_reference(runner, validated)
        except GeneratedProblemInvalid as exc:
            feedback = str(exc)[:500]
            log.warning("coding.generation.invalid", attempt=attempt, reason=feedback)
            continue
        except SandboxFault as exc:
            log.warning("coding.generation.sandbox_unavailable", reason=exc.reason)
            return None
        except AIError as exc:
            log.warning("coding.generation.model_failed", attempt=attempt, error=type(exc).__name__)
            return None
        problem = validated.problem
        question = CodingQuestion(
            id=uuid.uuid4(),
            organization_id=organization_id,
            slug=f"generated_{slugify(problem.title, max_length=80)}_{secrets.token_hex(3)}",
            title=problem.title[:200],
            difficulty=difficulty.value,
            competency_keys=[k for k in competency_keys if k][:8] or ["problem_solving"],
            role_tags=["general"],
            prompt_markdown=problem.prompt_markdown,
            languages=[lang.value for lang in FUNCTION_LANGUAGES],
            signature=validated.signature,
            starter_code=starters_for(validated.signature, description=problem.title),
            reference_solutions={CodeLanguage.PYTHON.value: problem.reference_solution_python},
            tests=validated.tests,
            sql_setup=None,
            expected_concepts=list(problem.expected_concepts),
            optimal_complexity={"time": problem.time_complexity, "space": problem.space_complexity},
            rubric={
                "excellent": problem.rubric_excellent,
                "good": problem.rubric_good,
                "weak": problem.rubric_weak,
            },
            hints=list(problem.hints),
            followups=list(problem.followups),
            common_errors=list(problem.common_errors),
            time_limit_minutes=max(5, min(120, minutes)),
            source=QuestionSource.GENERATED.value,
            status=BankStatus.ACTIVE.value,
        )
        async with session_scope() as db:
            db.add(question)
        log.info(
            "coding.generation.created",
            coding_question_id=str(question.id),
            attempt=attempt,
            tests=len(validated.tests),
        )
        return question
    return None
