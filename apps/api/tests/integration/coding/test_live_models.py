"""Live checks of the coding prompts against the real main model (opt-in:
``-m live -s``). One small call per test; prints latency and the output."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.ai.base import CallContext
from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices
from app.core.config import get_settings
from app.db.models import CodeRun, CodeSnapshot, CodingHint, CodingQuestion, CodingSession
from app.domain.enums import CodeLanguage, Difficulty, ModelTask
from app.schemas.ai import CodingEvaluation
from app.seed.loaders import load_coding_questions
from app.services.coding.analysis import analyze
from app.services.coding.contracts import CodingContext
from app.services.coding.evaluation import _Inputs, build_evaluation_messages
from app.services.coding.observer import decide
from app.services.coding.service import CodingServiceImpl

pytestmark = pytest.mark.live

NESTED = (
    "def two_sum(nums, target):\n"
    "    for i in range(len(nums)):\n"
    "        for j in range(i + 1, len(nums)):\n"
    "            if nums[i] + nums[j] == target:\n"
    "                return [i, j]\n"
    "    return []\n"
)
SEED = next(s for s in load_coding_questions() if s.slug == "two_sum")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def ai() -> AsyncIterator[RealAIServices]:
    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    yield services
    await services.aclose()


def question() -> CodingQuestion:
    return CodingQuestion(id=uuid.uuid4(), organization_id=None, **SEED.to_row())


async def test_live_observation_on_a_nested_loop(ai) -> None:
    context = CodingContext(
        coding_session_id=str(uuid.uuid4()),
        question_title=SEED.title,
        prompt_markdown=SEED.prompt_markdown,
        difficulty=Difficulty.EASY,
        language=CodeLanguage.PYTHON,
        current_code=NESTED,
        previous_code=None,
        recent_diff=None,
        snapshots_count=3,
        elapsed_s=240,
        time_limit_s=900,
        seconds_since_last_edit=9,
        expected_concepts=list(SEED.expected_concepts),
        optimal_complexity=dict(SEED.optimal_complexity),
    )
    state = {"baseline_features": analyze(SEED.starter_code[CodeLanguage.PYTHON], "python").as_dict()}
    started = time.perf_counter()
    decision = await decide(context, "idle", state, ai=ai)
    elapsed = time.perf_counter() - started
    print(
        f"\n[live] observation {elapsed * 1000:.0f} ms used_model={decision.used_model if decision else None}: {decision.speech if decision else None!r}"
    )
    assert decision is not None and decision.trigger == "structural_change"
    if decision.should_speak:
        assert decision.speech and "```" not in decision.speech and len(decision.speech.split()) <= 45


async def test_live_generated_hint(ai) -> None:
    q = question()
    q.hints = []
    service = CodingServiceImpl(ai=ai)
    session = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    started = time.perf_counter()
    text, speech = await service._generate_hint(
        q,
        session,
        level=2,
        code=NESTED,
        language="python",
        previous=["Think about what value would complete the pair."],
        last_run=None,
        allow_code=False,
    )
    print(
        f"\n[live] hint level 2 {(time.perf_counter() - started) * 1000:.0f} ms: {text!r} / spoken: {speech!r}"
    )
    assert text and "```" not in text


async def test_live_coding_evaluation(ai) -> None:
    q = question()
    now = datetime.now(UTC)
    final = SEED.reference_solutions[CodeLanguage.PYTHON]
    cs = CodingSession(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        coding_question_id=q.id,
        language="python",
        status="submitted",
        started_at=now - timedelta(minutes=9),
        ended_at=now,
        time_limit_seconds=900,
        hints_available=3,
        hints_used=1,
        final_code=final,
        observer_state={"end_reason": "submitted"},
    )
    snaps = [
        CodeSnapshot(
            id=uuid.uuid4(),
            coding_session_id=cs.id,
            seq=1,
            language="python",
            code=q.starter_code["python"],
            lines_added=3,
            lines_removed=0,
            char_count=10,
            trigger="autosave",
            elapsed_seconds=0,
        ),
        CodeSnapshot(
            id=uuid.uuid4(),
            coding_session_id=cs.id,
            seq=2,
            language="python",
            code=NESTED,
            lines_added=5,
            lines_removed=1,
            char_count=len(NESTED),
            trigger="run",
            elapsed_seconds=200,
        ),
        CodeSnapshot(
            id=uuid.uuid4(),
            coding_session_id=cs.id,
            seq=3,
            language="python",
            code=final,
            lines_added=6,
            lines_removed=5,
            char_count=len(final),
            trigger="submit",
            elapsed_seconds=500,
        ),
    ]
    run = CodeRun(
        id=uuid.uuid4(),
        coding_session_id=cs.id,
        snapshot_id=snaps[2].id,
        kind="submit",
        language="python",
        status="passed",
        test_results=[],
        passed_count=10,
        total_count=10,
        elapsed_seconds=500,
        sandbox_meta={},
    )
    hint = CodingHint(
        id=uuid.uuid4(),
        coding_session_id=cs.id,
        level=1,
        content=SEED.hints[0],
        source="curated",
        requested_via="button",
        elapsed_seconds=150,
    )
    inputs = _Inputs(
        cs,
        q,
        SimpleNamespace(id=cs.session_id, organization_id=uuid.uuid4()),
        [run],
        snaps,
        [hint],
        ["I'll start with brute force, then use a dictionary to look up the complement in O(1)."],
    )
    messages = build_evaluation_messages(inputs, run, ["algorithms", "data_structures", "problem_solving"])
    started = time.perf_counter()
    result = await ai.llm.structured(
        CodingEvaluation,
        messages,
        context=CallContext(task=ModelTask.CODING_EVALUATION),
        temperature=0.2,
        max_tokens=2500,
        timeout=150,
    )
    elapsed = time.perf_counter() - started
    value = result.value
    print(
        f"\n[live] evaluation {elapsed * 1000:.0f} ms attempts={result.attempts} score={value.score_estimate} conf={value.confidence} complexity={value.time_complexity}"
    )
    print(f"[live] summary: {value.correctness_summary}")
    print(f"[live] hints_impact: {value.hints_impact}")
    assert 0 <= value.score_estimate <= 10
