"""Test-hook routes, queue job handlers and memory summarisation."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

import app.workers.interview  # noqa: F401 - registers the job handlers
from app.db.models import AnswerEvaluation, BackgroundJob, Transcript
from app.db.session import session_scope
from app.domain.enums import ModelTask, SessionStatus
from app.schemas.ai import MemorySummary
from app.services.interview.engine import set_engine
from app.services.interview.memory import memory_key, summarize_session_memory
from app.services.interview.records import add_transcript

from .harness import build_world, collect, create_session, round_config_dict
from .test_engine_interviews import Driver

pytestmark = pytest.mark.integration


async def test_test_hook_routes(interview_env, client) -> None:
    world = await build_world(
        interview_env["mock_ai"], config=round_config_dict(coding={"enabled": True, "duration_minutes": 10})
    )
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    previous = set_engine(engine)
    try:
        await engine.prepare_session(sid)
        await collect(engine.start(sid))
        r = await client.post(f"/api/v1/test/sessions/{sid}/advance-clock", json={"seconds": 600})
        assert r.status_code == 200, r.text
        assert r.json()["elapsed_s"] >= 600 and r.json()["status"] == "live"
        state = await engine.load_state(sid)
        assert state.elapsed_before_pause_s >= 600
        r = await client.post(f"/api/v1/test/sessions/{sid}/force-coding")
        assert r.status_code == 200 and r.json() == {
            "session_id": sid,
            "scheduled": True,
            "coding_enabled": True,
            "coding_active": False,
        }
        assert (await engine.load_state(sid)).force_coding
        assert (
            await client.post(f"/api/v1/test/sessions/{uuid.uuid4()}/advance-clock", json={"seconds": 5})
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/test/sessions/{sid}/advance-clock", json={"seconds": -5})
        ).status_code == 422
    finally:
        set_engine(previous)


async def test_test_hooks_refuse_when_disabled(interview_env, client, settings) -> None:
    settings.enable_test_hooks = False
    r = await client.post(f"/api/v1/test/sessions/{uuid.uuid4()}/force-coding")
    assert r.status_code == 404


async def test_session_prepare_and_answer_evaluate_jobs_run_through_the_queue(
    interview_env, worker_factory
) -> None:
    world = await build_world(interview_env["mock_ai"])
    engine = interview_env["make_engine"]()
    previous = set_engine(engine)
    try:
        sid = await create_session(interview_env, world)  # enqueues session.prepare after commit
        worker = worker_factory(job_types=["session.prepare", "answer.evaluate"])
        assert await worker.drain(timeout=20) >= 1
        state = await engine.load_state(sid)
        assert state.status is SessionStatus.READY and state.plan is not None
        d = Driver(interview_env, engine, sid)
        await d.start()
        await d.answer(
            "[strong] We ran forty nodes with Karpenter and HPA on custom metrics.", evaluate=False
        )
        assert await worker.drain(timeout=20) >= 1
        async with session_scope() as db:
            assert (await db.execute(select(AnswerEvaluation))).scalars().first() is not None
            statuses = {j.type: j.status for j in (await db.execute(select(BackgroundJob))).scalars()}
        assert statuses["session.prepare"] == "succeeded" and statuses["answer.evaluate"] == "succeeded"
    finally:
        set_engine(previous)


async def test_an_llm_outage_delays_evaluation_instead_of_storing_a_fallback(
    interview_env, worker_factory
) -> None:
    """S11 (audit B3): the model is down for the first two attempts; the answer still gets a real evaluation."""
    from app.ai.base import AIUnavailable

    mock_ai = interview_env["mock_ai"]
    world = await build_world(mock_ai)
    engine = interview_env["make_engine"]()
    previous = set_engine(engine)
    try:
        sid = await create_session(interview_env, world)
        worker = worker_factory(job_types=["session.prepare", "answer.evaluate"])
        assert await worker.drain(timeout=20) >= 1
        d = Driver(interview_env, engine, sid)
        await d.start()
        await d.answer(
            "[strong] We ran forty nodes with Karpenter and HPA on custom metrics.", evaluate=False
        )
        queued = mock_ai.llm.pending(ModelTask.ANSWER_EVALUATION)
        down = AIUnavailable("main LLM down", endpoint="main")
        mock_ai.llm.fail_next(ModelTask.ANSWER_EVALUATION, down, down)
        assert mock_ai.llm.pending(ModelTask.ANSWER_EVALUATION) == queued + 2
        assert await worker.drain(timeout=20) >= 1
        async with session_scope() as db:
            evaluation = (await db.execute(select(AnswerEvaluation))).scalars().one()
            job = (
                (await db.execute(select(BackgroundJob).where(BackgroundJob.type == "answer.evaluate")))
                .scalars()
                .one()
            )
        assert evaluation.is_fallback is False
        assert job.status == "succeeded" and job.attempts >= 3
    finally:
        set_engine(previous)


async def test_memory_summary_job_and_application(interview_env, redis) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)
    await collect(engine.start(sid))
    async with session_scope() as db:
        for i in range(14):
            await add_transcript(db, sid, speaker="interviewer", text_value=f"Question {i}?")
            await add_transcript(db, sid, speaker="candidate", text_value=f"Answer {i} about EKS")
    interview_env["mock_ai"].llm.script(
        ModelTask.MEMORY_SUMMARY,
        MemorySummary(summary="Covered EKS scale (40 nodes) and Terraform state.", key_facts=["40 nodes"]),
    )
    result = await summarize_session_memory(sid)
    assert result is not None and result["upto"] == 6  # 14 answers - 8 kept verbatim
    call = interview_env["mock_ai"].llm.calls_for(ModelTask.MEMORY_SUMMARY)[-1]
    assert (
        "<candidate_turn>" in call.messages[0]["content"]
        and "Fairness and evidence rules" in call.messages[0]["content"]
    )
    assert json.loads(await redis.get(memory_key(sid)))["key_facts"] == ["40 nodes"]
    # Nothing new to summarise for the same window.
    assert await summarize_session_memory(sid, previous_summary=result["summary"], previous_upto=6) is None
    # The live engine applies it before its next decision.
    from app.services.interview.contracts import TimerEvent

    await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0)))
    state = await engine.load_state(sid)
    assert state.memory_upto == 6 and "40 nodes" in state.memory_summary
    async with session_scope() as db:
        assert (
            len(
                (await db.execute(select(Transcript).where(Transcript.session_id == uuid.UUID(sid))))
                .scalars()
                .all()
            )
            >= 30
        )
