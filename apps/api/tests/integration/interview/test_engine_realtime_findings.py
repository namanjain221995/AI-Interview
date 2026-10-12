"""Behaviours requested after the realtime runtime ran the engine live:
turn-path latency guarantees, the metadata tail budget, paused sessions,
profile-aware preparation, grounding of references."""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import Answer, BackgroundJob, CandidateProfile, CandidateResume, InterviewSession, Question
from app.db.session import session_scope
from app.domain.enums import ModelTask, SessionStatus
from app.services.interview.contracts import (
    CandidateTurn,
    SpeechDelta,
    StateChanged,
    TimerEvent,
    UtteranceBegin,
)

from .harness import PROFILE, build_world, collect, create_session, metas
from .test_engine_interviews import Driver, _setup

pytestmark = pytest.mark.integration


async def test_turn_path_yields_begin_first_and_does_no_retrieval_before_speech(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await engine.drain_background()
    mock = interview_env["mock_ai"]
    mock.embeddings.calls.clear()
    mock.reranker.calls.clear()
    mock.llm.chunk_delay = 0.02
    started = time.perf_counter()
    first_event_ms = first_delta_ms = None
    retrieval_calls_before_speech = None
    events = []
    stream = engine.handle_candidate_turn(
        sid, CandidateTurn(text="[adequate] We ran EKS with managed node groups.", started_ms=1, ended_ms=2)
    )
    async for event in stream:
        if first_event_ms is None:
            first_event_ms = (time.perf_counter() - started) * 1000
            assert isinstance(event, UtteranceBegin) and event.text is None
        if isinstance(event, SpeechDelta) and first_delta_ms is None:
            first_delta_ms = (time.perf_counter() - started) * 1000
            retrieval_calls_before_speech = len(mock.embeddings.calls) + len(mock.reranker.calls)
        events.append(event)
    assert retrieval_calls_before_speech == 0, "no embedding/rerank round trip before the first speech delta"
    assert first_event_ms < first_delta_ms
    meta = metas(events)[-1]
    latency = meta.decision["latency"]
    assert latency["prepare_ms"] < 100 and latency["first_delta_from_call_ms"] < 1000
    async with session_scope() as db:  # the answer row was persisted concurrently
        assert (
            len((await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid)))).scalars().all())
            == 1
        )


async def test_tail_is_bounded_and_fields_are_recovered(interview_env) -> None:
    _, sid, engine = await _setup(interview_env, turn_tail_min_s=0.05, turn_tail_s_per_word=0.0)
    d = Driver(interview_env, engine, sid)
    await d.start()
    mock = interview_env["mock_ai"]
    mock.llm.chunk_size = 8
    mock.llm.chunk_delay = 0.01
    events = await d.answer("[partial] We had some autoscaling set up by another team.", speculate=True)
    meta = metas(events)[-1]
    assert meta.decision["latency"]["tail_cut"] is True and "tail_cut" in meta.decision["deviations"]
    # Fields that arrived before the cut are kept (action comes right after speech).
    assert meta.decision["action"] == "ask_followup"
    assert meta.full_text.startswith("How did you handle ")
    async with session_scope() as db:
        assert (await db.get(Question, uuid.UUID(meta.question_id))) is not None


async def test_answers_received_while_paused_are_kept_and_answered_on_resume(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await collect(engine.handle_timer(sid, TimerEvent(kind="disconnect", elapsed_s=0)))
    events = await collect(
        engine.handle_candidate_turn(
            sid,
            CandidateTurn(
                text="[adequate] Late transcription of my answer about EKS.", started_ms=1, ended_ms=2
            ),
        )
    )
    assert events == []  # nothing is spoken while paused
    state = await d.state()
    assert state.status is SessionStatus.PAUSED and state.conversation[-1].speaker == "candidate"
    async with session_scope() as db:
        assert (
            len((await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid)))).scalars().all())
            == 1
        )
        jobs = (
            (await db.execute(select(BackgroundJob).where(BackgroundJob.type == "answer.evaluate")))
            .scalars()
            .all()
        )
        assert len(jobs) == 1
    resumed = await collect(engine.resume(sid, reconnect=True))
    assert metas(resumed), "the reply to the late answer is generated on resume"
    assert (await d.state()).status is SessionStatus.LIVE


async def _pending_resume(world) -> None:
    async with session_scope() as db:
        db.add(
            CandidateResume(
                candidate_id=world.candidate_id,
                organization_id=world.organization_id,
                interview_id=world.interview_id,
                storage_key=f"orgs/{world.organization_id}/pending.txt",
                original_filename="cv.txt",
                content_type="text/plain",
                size_bytes=10,
                sha256="1" * 64,
                status="extracting",
            )
        )


async def test_prepare_waits_for_profile_then_attaches_it(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"], with_profile=False)
    await _pending_resume(world)
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"](start_wait_for_plan_s=0.1)
    await engine.prepare_session(sid)
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.PREPARING and state.prepare_attempts == 1 and state.plan is None
    async with session_scope() as db:
        retry = (
            await db.execute(
                select(BackgroundJob).where(BackgroundJob.dedupe_key == f"session.prepare:{sid}:wait:1")
            )
        ).scalar_one()
        assert retry.run_after is not None  # delayed re-enqueue
    # start() must not begin while the profile is pending.
    events = await collect(engine.start(sid))
    assert [type(e) for e in events] == [StateChanged] and events[0].reason == "preparing"
    assert (await engine.load_state(sid)).status is SessionStatus.PREPARING
    # The profile lands: the next attempt attaches it and plans.
    async with session_scope() as db:
        resume = (
            await db.execute(
                select(CandidateResume).where(CandidateResume.candidate_id == world.candidate_id)
            )
        ).scalar_one()
        resume.status = "ready"
        db.add(
            CandidateProfile(
                candidate_id=world.candidate_id,
                resume_id=resume.id,
                job_id=world.job_id,
                profile={**PROFILE, "candidate_id": str(world.candidate_id)},
                status="partial",
            )
        )
    await engine.prepare_session(sid)
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.READY and state.profile_status == "partial"
    assert set(state.claims) == {"c1", "c2", "c3"} and state.plan is not None
    async with session_scope() as db:
        assert (await db.get(InterviewSession, uuid.UUID(sid))).profile_id is not None
    events = await collect(engine.start(sid))
    assert [e.kind for e in events if isinstance(e, UtteranceBegin)] == ["intro", "question"]


async def test_profile_wait_budget_then_fallback_plan(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"], with_profile=False)
    await _pending_resume(world)
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)  # starts the wait clock

    def _expire(s):
        s.profile_wait_started_at = s.profile_wait_started_at - timedelta(minutes=7)
        return True

    await engine.store.save_with_retry(sid, _expire)
    plan_calls = len(interview_env["mock_ai"].llm.calls_for(ModelTask.INTERVIEW_PLANNING))
    await engine.prepare_session(sid)
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.READY and state.profile_status == "timeout"
    assert state.plan_source == "fallback" and state.claims == {}
    assert len(interview_env["mock_ai"].llm.calls_for(ModelTask.INTERVIEW_PLANNING)) == plan_calls


async def test_profile_wait_budget_uses_the_quick_profile(interview_env) -> None:
    """After the wait budget the candidate's quick profile (deterministic read of the resume, status "quick")
    is attached instead of interviewing without any profile."""
    assert interview_env["make_engine"]().config.profile_wait_budget_s <= 120
    world = await build_world(interview_env["mock_ai"], with_profile=False)
    await _pending_resume(world)
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)  # starts the wait clock
    assert (await engine.load_state(sid)).status is SessionStatus.PREPARING
    async with session_scope() as db:
        resume = (
            await db.execute(
                select(CandidateResume).where(CandidateResume.candidate_id == world.candidate_id)
            )
        ).scalar_one()
        quick = CandidateProfile(
            candidate_id=world.candidate_id,
            resume_id=resume.id,
            job_id=world.job_id,
            profile={**PROFILE, "candidate_id": str(world.candidate_id)},
            status="quick",
        )
        db.add(quick)
        await db.flush()
        quick_id = quick.id

    def _expire(s):
        s.profile_wait_started_at = s.profile_wait_started_at - timedelta(minutes=2)
        return True

    await engine.store.save_with_retry(sid, _expire)
    await engine.prepare_session(sid)
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.READY and state.profile_status == "quick"
    assert state.candidate_profile and set(state.claims) == {"c1", "c2", "c3"}
    async with session_scope() as db:
        assert (await db.get(InterviewSession, uuid.UUID(sid))).profile_id == quick_id


async def test_no_resume_means_no_wait(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"], with_profile=False)
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.READY and state.profile_status == "missing"


async def test_ungrounded_reference_is_flagged_and_corrected_next_turn(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    from .harness import SimulatedInterviewer

    def invented(call):
        decision = SimulatedInterviewer()(call)
        return decision.model_copy(
            update={
                "speech": "You mentioned a misconfigured liveness probe earlier. What was the root cause?"
            }
        )

    interview_env["mock_ai"].llm.script(ModelTask.TURN_DECISION, invented)
    events = await d.answer("[adequate] We used managed node groups with spot capacity.", speculate=False)
    assert "ungrounded_reference" in metas(events)[-1].decision["deviations"]
    await d.answer("[adequate] We also used Terraform for the clusters.", speculate=False)
    prompt = interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)[-1].messages[-1]["content"]
    assert "never said" in prompt and "misconfigured liveness probe" in prompt
    assert (await d.state()).ungrounded_reference is None  # the corrected reply was grounded


async def test_prepare_bank_candidates_are_shared_with_the_api_process(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    worker_engine = interview_env["make_engine"]()
    await worker_engine.prepare_session(sid)  # "worker" process
    api_engine = interview_env["make_engine"]()  # separate process: empty memory cache
    assert api_engine._bank_cache == {}
    d = Driver(interview_env, api_engine, sid)
    await d.start()
    interview_env["mock_ai"].embeddings.calls.clear()
    events = await d.answer(
        "[strong] Detailed answer with numbers, decisions and trade-offs on autoscaling.", speculate=False
    )
    prompt = interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)[-1].messages[-1]["content"]
    assert "inspiration from the question bank" in prompt  # served from the shared Redis cache
    assert metas(events)[-1].decision["directive"]["bank_candidates"]
