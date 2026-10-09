"""coding.evaluate: evaluation document, evidence, assistance, guardrails,
idempotency, fallback, the session bus event and the worker path."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

import app.workers.coding  # noqa: F401  (registers the coding.evaluate handler)
from app.ai.base import AIOutputInvalid, AIUnavailable
from app.core.lifecycle import drain_after_commit
from app.core.queue import PermanentJobError, subscribe_session_events
from app.db.models import BackgroundJob, CodeRun, CodingSession, CompetencyEvidence
from app.db.session import session_scope
from app.domain.enums import CodeLanguage, EvidenceSignal, ModelTask, RunKind
from app.schemas.ai import CodingEvaluation, EvidenceItem
from app.services.coding.evaluation import EvaluationRetry, evaluate_coding_session

pytestmark = pytest.mark.integration

HASHMAP = "def two_sum(nums, target):\n    seen = {}\n    for i, v in enumerate(nums):\n        if target - v in seen:\n            return [seen[target - v], i]\n        seen[v] = i\n    return []\n"
PASS_VISIBLE = "def two_sum(nums, target):\n    # PASS_VISIBLE\n    return []\n"


def model_evaluation(score: float = 8.5) -> CodingEvaluation:
    return CodingEvaluation(
        correctness_summary="Correct one-pass hash map solution.",
        tests_passed=99,  # overwritten with ground truth
        tests_total=99,
        time_complexity="O(n)",
        space_complexity="O(n)",
        complexity_assessment="Optimal.",
        code_quality=8,
        problem_solving=8,
        edge_case_handling=7,
        testing_approach=6,
        communication=7,
        process_observations=["Started with brute force, then switched to a hash map."],
        hints_impact="Two hints nudged the hash map idea.",
        strengths=["Clear variable names"],
        weaknesses=["Did not test duplicates"],
        evidence=[
            EvidenceItem(
                competency_key="data_structures",
                dimension="selection",
                signal=EvidenceSignal.POSITIVE,
                evidence="Chose a hash map for O(1) lookups.",
                quote="seen = {}",
                strength=0.8,
                confidence=0.8,
            ),
            EvidenceItem(
                competency_key="kubernetes",
                dimension="misc",
                signal=EvidenceSignal.NEUTRAL,
                evidence="Unrelated key is remapped.",
                strength=0.3,
                confidence=0.5,
            ),
        ],
        score_estimate=score,
        confidence=0.8,
    )


async def next_event(sub, wait_s: float = 3.0) -> dict | None:
    deadline = asyncio.get_running_loop().time() + wait_s
    while asyncio.get_running_loop().time() < deadline:
        event = await sub.get(timeout=0.2)
        if event is not None:
            return event
    return None


async def finished_session(
    service, env, *, code: str = HASHMAP, submit: bool = True, hints: int = 0, reason: str = "submitted"
) -> str:
    state = await service.start(
        str(env.session_id), str(env.questions["two_sum"]), question_id=None, language=CodeLanguage.PYTHON
    )
    cs_id = state.coding_session_id
    await service.record_update(cs_id, code=code, language=CodeLanguage.PYTHON, version=1)
    for _ in range(hints):
        await service.request_hint(cs_id, requested_via="button")
    if submit:
        await service.run(cs_id, kind=RunKind.SUBMIT, code=code, language=CodeLanguage.PYTHON)
    await service.finish(cs_id, reason=reason)
    return cs_id


async def test_evaluation_document_evidence_assistance_and_bus_event(
    service, coding_env, mock_ai, redis
) -> None:
    cs_id = await finished_session(service, coding_env, hints=2)
    mock_ai.llm.script(ModelTask.CODING_EVALUATION, model_evaluation())
    async with subscribe_session_events(coding_env.session_id) as sub:
        result = await evaluate_coding_session(cs_id, ai=mock_ai)
        event = await next_event(sub)
    assert result["is_fallback"] is False
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
        evidence = (
            await db.scalars(
                select(CompetencyEvidence).where(
                    CompetencyEvidence.coding_session_id == cs.id, CompetencyEvidence.source == "coding"
                )
            )
        ).all()
    doc = cs.evaluation
    assert doc["status"] == "completed" and doc["tests_passed"] == 10 and doc["tests_total"] == 10
    assert doc["score_estimate"] == 8.5 and doc["score_cap_applied"] is False
    assert doc["hints_used"] == 2 and doc["assistance_factor"] == pytest.approx(0.86)
    assert doc["adjusted_score"] == pytest.approx(round(8.5 * 0.86, 2))
    assert doc["competency_keys"] == ["algorithms", "data_structures", "problem_solving"]
    assert doc["final_run_id"] == str(cs.final_run_id)
    assert doc["difficulty"] == "easy" and doc["language"] == "python"
    assert set(doc["evidence_ids"]) == {str(e.id) for e in evidence}
    keys = {e.competency_key for e in evidence}
    assert "kubernetes" not in keys and keys <= {"algorithms", "data_structures", "problem_solving"}
    correctness = next(e for e in evidence if e.dimension == "correctness")
    assert correctness.signal == "positive" and "10 of 10" in correctness.evidence
    quoted = next(e for e in evidence if e.dimension == "selection")
    assert (
        quoted.transcript_ref["quote"] == "seen = {}" and quoted.transcript_ref["coding_session_id"] == cs_id
    )
    assert all(e.embedding is not None for e in evidence)
    assert event is not None and event["type"] == "coding.evaluated" and event["coding_session_id"] == cs_id
    assert event["adjusted_score"] == doc["adjusted_score"] and event["hints_used"] == 2
    # Prompt hygiene: fairness rules, untrusted fences, hints and ground-truth results are included.
    call = mock_ai.llm.calls_for(ModelTask.CODING_EVALUATION)[0]
    assert "Fairness and evidence rules" in call.messages[0]["content"]
    user = call.messages[1]["content"]
    assert "<candidate_code>" in user and "10/10 passed" in user and "level 1" in user and "level 2" in user


async def test_score_is_capped_by_the_test_results(service, coding_env, mock_ai) -> None:
    cs_id = await finished_session(service, coding_env, code=PASS_VISIBLE)
    mock_ai.llm.script(ModelTask.CODING_EVALUATION, model_evaluation(score=9.5))
    await evaluate_coding_session(cs_id, ai=mock_ai)
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
        negative = (
            await db.scalars(
                select(CompetencyEvidence).where(
                    CompetencyEvidence.coding_session_id == cs.id,
                    CompetencyEvidence.dimension == "correctness",
                )
            )
        ).one()
    assert cs.evaluation["tests_passed"] == 3 and cs.evaluation["tests_total"] == 10
    assert cs.evaluation["score_cap_applied"] is True
    assert cs.evaluation["score_estimate"] == pytest.approx(3 + 7 * 0.3)
    assert cs.evaluation["raw_score_estimate"] == 9.5
    assert negative.signal == "negative"


async def test_evaluation_is_idempotent_and_force_replaces_evidence(service, coding_env, mock_ai) -> None:
    cs_id = await finished_session(service, coding_env)
    mock_ai.llm.set_default(ModelTask.CODING_EVALUATION, model_evaluation())
    await evaluate_coding_session(cs_id, ai=mock_ai)
    skipped = await evaluate_coding_session(cs_id, ai=mock_ai)
    assert skipped["skipped"] is True
    await evaluate_coding_session(cs_id, ai=mock_ai, force=True)
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(CompetencyEvidence).where(
                    CompetencyEvidence.coding_session_id == uuid.UUID(cs_id),
                    CompetencyEvidence.source == "coding",
                )
            )
        ).all()
    assert len(rows) == 3  # 2 model items + 1 correctness item, not duplicated
    assert len(mock_ai.llm.calls_for(ModelTask.CODING_EVALUATION)) == 2


async def test_invalid_model_output_falls_back_for_human_review(service, coding_env, mock_ai) -> None:
    cs_id = await finished_session(service, coding_env, hints=1)
    mock_ai.llm.script(ModelTask.CODING_EVALUATION, AIOutputInvalid("bad json"))
    result = await evaluate_coding_session(cs_id, ai=mock_ai, is_last_attempt=False)
    assert result["is_fallback"] is True
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
    assert cs.evaluation["is_fallback"] is True and cs.evaluation["needs_human_review"] is True
    assert cs.evaluation["confidence"] < 0.5 and cs.evaluation["assistance_factor"] == pytest.approx(0.93)


async def test_transient_model_outage_retries_before_the_last_attempt(service, coding_env, mock_ai) -> None:
    cs_id = await finished_session(service, coding_env)
    mock_ai.llm.set_default(ModelTask.CODING_EVALUATION, AIUnavailable("down"))
    with pytest.raises(EvaluationRetry):
        await evaluate_coding_session(cs_id, ai=mock_ai, is_last_attempt=False)
    result = await evaluate_coding_session(cs_id, ai=mock_ai, is_last_attempt=True)
    assert result["is_fallback"] is True


async def test_final_code_never_submitted_gets_a_system_run(
    service, coding_env, mock_ai, fake_sandbox
) -> None:
    cs_id = await finished_session(service, coding_env, submit=False, reason="time_limit")
    mock_ai.llm.script(ModelTask.CODING_EVALUATION, model_evaluation())
    await evaluate_coding_session(cs_id, ai=mock_ai)
    async with session_scope() as db:
        runs = (await db.scalars(select(CodeRun).where(CodeRun.coding_session_id == uuid.UUID(cs_id)))).all()
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
    assert len(runs) == 1 and runs[0].kind == "submit" and runs[0].sandbox_meta["system"] is True
    assert cs.final_run_id == runs[0].id
    assert cs.evaluation["tests_total"] == 10
    assert len(fake_sandbox.requests[-1].tests) == 10


async def test_active_sessions_are_not_evaluated(service, coding_env, mock_ai) -> None:
    state = await service.start(
        str(coding_env.session_id),
        str(coding_env.questions["two_sum"]),
        question_id=None,
        language=CodeLanguage.PYTHON,
    )
    with pytest.raises(PermanentJobError):
        await evaluate_coding_session(state.coding_session_id, ai=mock_ai)


async def test_worker_runs_the_job_end_to_end(service, coding_env, mock_ai, worker_factory, redis) -> None:
    from app.ai.registry import set_ai

    previous = set_ai(mock_ai)
    try:
        mock_ai.llm.script(ModelTask.CODING_EVALUATION, model_evaluation())
        cs_id = await finished_session(service, coding_env)
        await drain_after_commit()  # the stream message is sent by an after-commit task
        assert await redis.xlen("jobs:coding.evaluate") == 1
        worker = worker_factory(job_types=["coding.evaluate"])
        processed = await worker.drain(timeout=15)
    finally:
        set_ai(previous)
    assert processed >= 1
    async with session_scope() as db:
        job = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "coding.evaluate"))).one()
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
    assert job.status == "succeeded"
    assert cs.evaluation["status"] == "completed"
