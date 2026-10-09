"""Session creation, preparation, start, persistence and rebuild."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, ValidationFailed
from app.db.models import BackgroundJob, Interview, InterviewEvent, InterviewSession, Question, Transcript
from app.db.session import session_scope
from app.domain.enums import ModelTask, SessionStatus, TurnAction
from app.schemas.policies import IntegrityPolicy, PolicySnapshot, RecordingPolicy
from app.services.interview.contracts import UtteranceBegin, UtteranceMeta
from app.services.interview.state_store import StateConflict, state_key

from .harness import build_world, collect, create_session, metas, round_config_dict

pytestmark = pytest.mark.integration


async def test_create_or_resume_freezes_config_and_is_idempotent(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    again = await create_session(interview_env, world)
    assert again == sid
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert row.status == "created" and row.profile_id is not None
        assert row.round_config["name"] == "Technical Screening"
        # All profile claims are kept; the policy only probes those >= min_priority.
        assert row.state["claims"].keys() == {"c1", "c2", "c3"}
        jobs = (
            (await db.execute(select(BackgroundJob).where(BackgroundJob.type == "session.prepare")))
            .scalars()
            .all()
        )
        assert len(jobs) == 1 and jobs[0].payload == {"session_id": sid}
        events = (
            (await db.execute(select(InterviewEvent).where(InterviewEvent.session_id == uuid.UUID(sid))))
            .scalars()
            .all()
        )
        assert [e.type for e in events] == ["session.created"]
    state = await interview_env["make_engine"]().load_state(sid)
    assert state.candidate_name == "Jane Doe" and state.candidate_first_name == "Jane"
    assert set(state.competencies) == {"kubernetes", "aws", "communication"}
    assert state.time_limit_s == 1800 and state.max_time_s == 2100
    assert state.audio_recording_allowed and state.monitoring_enabled
    assert state.job_summary["requirements"][0]["name"] == "Kubernetes"


async def test_create_rejects_completed_or_invalid(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        row.status = "completed"
    with pytest.raises(Conflict):
        await create_session(interview_env, world)
    world2 = await build_world(interview_env["mock_ai"])
    with pytest.raises(ValidationFailed):
        await create_session(interview_env, world2, round_id=uuid.uuid4())


async def test_round_selection_required(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    async with session_scope() as db:
        interview = await db.get(Interview, world.interview_id)
        interview.round_id = None
        interview.allow_round_selection = True
    with pytest.raises(ValidationFailed) as exc:
        await create_session(interview_env, world)
    assert exc.value.code == "round_required"


async def test_no_profile_and_no_audio_consent(interview_env) -> None:
    policy = PolicySnapshot(
        recording=RecordingPolicy(record_answer_audio=True), integrity=IntegrityPolicy(enabled=False)
    )
    world = await build_world(
        interview_env["mock_ai"], with_profile=False, consent_audio=False, policy=policy
    )
    sid = await create_session(interview_env, world)
    state = await interview_env["make_engine"]().load_state(sid)
    assert state.claims == {} and state.candidate_profile == {}
    assert not state.audio_recording_allowed and not state.monitoring_enabled


async def test_prepare_then_start_emits_intro_and_prepared_first_question(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)
    await engine.prepare_session(sid)  # idempotent
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.READY and state.plan is not None and state.opening_text
    assert state.prepared_first_question is not None
    turn_calls_after_prepare = len(interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION))
    events = await collect(engine.start(sid))
    begins = [e for e in events if isinstance(e, UtteranceBegin)]
    assert [b.kind for b in begins] == ["intro", "question"]
    assert begins[0].text == state.opening_text
    assert begins[1].text and begins[1].question_id  # pre-generated: not streamed
    # No live LLM call was needed to start.
    assert len(interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)) == turn_calls_after_prepare
    meta = metas(events)[-1]
    assert meta.decision["action"] == TurnAction.VERIFY_CLAIM.value and meta.decision["claim_id"] == "c1"
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.LIVE and state.started_at is not None
    assert state.claims["c1"].status.value == "in_progress"
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert row.status == "live" and row.state_version == state.version and row.plan is not None
        interview = await db.get(Interview, world.interview_id)
        assert interview.status == "in_progress"
        questions = (
            (await db.execute(select(Question).where(Question.session_id == uuid.UUID(sid)))).scalars().all()
        )
        assert (
            len(questions) == 1
            and questions[0].claim_id == "c1"
            and questions[0].decision["source"] == "prepared"
        )
        transcripts = (
            (
                await db.execute(
                    select(Transcript).where(Transcript.session_id == uuid.UUID(sid)).order_by(Transcript.seq)
                )
            )
            .scalars()
            .all()
        )
        assert [t.speaker for t in transcripts] == ["interviewer", "interviewer"] and [
            t.seq for t in transcripts
        ] == [1, 2]
        types = [
            e.type
            for e in (
                await db.execute(
                    select(InterviewEvent)
                    .where(InterviewEvent.session_id == uuid.UUID(sid))
                    .order_by(InterviewEvent.seq)
                )
            ).scalars()
        ]
        assert types == ["session.created", "session.prepared", "session.started", "turn.decision"]
    # A second start re-asks instead of re-introducing.
    again = await collect(engine.start(sid))
    assert [e.kind for e in again if isinstance(e, UtteranceBegin)] == ["resume"]


async def test_start_without_preparation_falls_back_and_streams(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"](start_wait_for_plan_s=0.1)
    events = await collect(engine.start(sid))
    kinds_ = [type(e).__name__ for e in events]
    assert kinds_[0] == "UtteranceBegin" and "SpeechDelta" in kinds_
    state = await engine.load_state(sid)
    assert state.plan_source == "fallback" and state.status is SessionStatus.LIVE
    assert "Maya" in state.opening_text


async def test_prepare_does_not_overwrite_live_session(interview_env) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"](start_wait_for_plan_s=0.05)
    await collect(engine.start(sid))
    live = await engine.load_state(sid)
    await engine.prepare_session(sid)
    after = await engine.load_state(sid)
    assert after.status is SessionStatus.LIVE and after.plan == live.plan


async def test_state_cas_and_rebuild_from_postgres(interview_env, redis) -> None:
    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)
    await collect(engine.start(sid))
    store = engine.store
    a = await store.load(sid)
    b = await store.load(sid)
    await store.save(a, durable=True)
    with pytest.raises(StateConflict):
        await store.save(b)
    # Redis loss: the next load rebuilds from the Postgres snapshot.
    await redis.delete(state_key(sid))
    rebuilt = await store.load(sid)
    assert rebuilt is not None and rebuilt.version == a.version and rebuilt.status is SessionStatus.LIVE
    assert await redis.exists(state_key(sid))


async def test_candidate_view(interview_env) -> None:
    world = await build_world(
        interview_env["mock_ai"], config=round_config_dict(coding={"enabled": True, "duration_minutes": 10})
    )
    sid = await create_session(interview_env, world)
    async with session_scope() as db:
        view = await interview_env["sessions"].candidate_view(db, sid)
    assert view["status"] == "created" and view["round"]["name"] == "Technical Screening"
    assert view["coding"]["enabled"] and view["coding"]["duration_minutes"] == 10
    assert view["report"]["available"] is False
    assert "score" not in str(view).lower()


async def test_expired_invite_is_rejected(interview_env) -> None:
    from datetime import UTC, datetime, timedelta

    world = await build_world(interview_env["mock_ai"])
    async with session_scope() as db:
        (await db.get(Interview, world.interview_id)).invite_expires_at = datetime.now(UTC) - timedelta(
            minutes=1
        )
    with pytest.raises(Conflict) as exc:
        await create_session(interview_env, world)
    assert exc.value.code == "interview_expired"


async def test_late_snapshot_never_regresses_terminal_status(interview_env) -> None:
    from app.domain.enums import SessionEndReason
    from app.services.interview.sessions import mark_session_completed

    world = await build_world(interview_env["mock_ai"])
    sid = await create_session(interview_env, world)
    engine = interview_env["make_engine"]()
    await engine.prepare_session(sid)
    await collect(engine.start(sid))
    events = await collect(engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED))
    closing = [e for e in events if isinstance(e, UtteranceMeta)][-1]
    async with session_scope() as db:
        assert await mark_session_completed(db, sid)
    state = await engine.store.load(sid)
    assert state.status is SessionStatus.COMPLETED
    stale = state.model_copy(deep=True)
    stale.status = SessionStatus.COMPLETING
    stale.version += 5
    async with session_scope() as db:
        await engine.store.write_snapshot(db, stale)
    # A late delivery ack is ignored on an ended session.
    await engine.mark_utterance_delivered(sid, closing.utterance_id, spoken_text=closing.full_text)
    async with session_scope() as db:
        assert (await db.get(InterviewSession, uuid.UUID(sid))).status == "completed"


async def test_malformed_round_config_is_policy_invalid_conflict_not_500(interview_env) -> None:
    """Security review (LOW): a legacy/malformed ``rounds.config`` (round_type outside the enum)
    used to raise pydantic.ValidationError -> HTTP 500 at POST /portal/interviews/{id}/session."""
    from app.db.models import Round

    world = await build_world(interview_env["mock_ai"])
    async with session_scope() as db:
        rnd = await db.get(Round, world.round_id)
        rnd.config = {**rnd.config, "round_type": "legacy_phone_screen"}
    with pytest.raises(Conflict) as info:
        await create_session(interview_env, world)
    assert info.value.code == "policy_invalid" and info.value.status_code == 409
    assert "misconfigured" in info.value.message
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(InterviewSession).where(InterviewSession.interview_id == world.interview_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []  # nothing half-created
