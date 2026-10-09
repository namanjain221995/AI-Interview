"""Coding inside the interview: start, observer comments, hints (button and
voice), candidate speech while coding, submission and the follow-up."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Question
from app.db.session import session_scope
from app.domain.enums import RunKind, RunStatus, TurnAction
from app.schemas.realtime import CodingRunResultMsg
from app.services.interview.adapters import CodingPort
from app.services.interview.contracts import (
    CodingActivity,
    CodingEnded,
    CodingHintIssued,
    CodingStarted,
    TimerEvent,
    UtteranceBegin,
    UtteranceMeta,
)

from .harness import (
    FakeCodingService,
    build_world,
    collect,
    create_session,
    make_observer,
    metas,
    round_config_dict,
)
from .test_engine_interviews import Driver

pytestmark = pytest.mark.integration


def coding_config(**overrides):
    cfg = round_config_dict(
        competencies=[
            {"key": "kubernetes", "name": "Kubernetes", "weight": 1.5},
            {"key": "aws", "name": "AWS", "weight": 1.2},
            {"key": "data_structures", "name": "Data Structures", "weight": 0.8},
        ],
        coding={
            "enabled": True,
            "duration_minutes": 10,
            "difficulty": "easy",
            "min_seconds_between_comments": 20,
        },
    )
    cfg.update(overrides)
    return cfg


async def _coding_setup(env, *, observer_speaks_on=frozenset({"run_completed"}), service=True):
    world = await build_world(env["mock_ai"], config=coding_config(), with_coding_question=True)
    sid = await create_session(env, world)
    fake = FakeCodingService(str(world.coding_question_id)) if service else None
    engine = env["make_engine"](
        coding=CodingPort(service=fake, observer=make_observer(set(observer_speaks_on)), source="test")
    )
    await engine.prepare_session(sid)
    driver = Driver(env, engine, sid)
    await driver.start()
    return world, sid, engine, driver, fake


async def test_coding_flow_with_hints_comments_and_followup(interview_env) -> None:
    world, sid, engine, d, fake = await _coding_setup(interview_env)
    await engine.force_coding(sid)
    events = await d.answer("[adequate] We used Terraform modules for all clusters and environments.")
    names = [type(e).__name__ for e in events]
    assert names[-3:] == ["UtteranceBegin", "UtteranceMeta", "CodingStarted"]
    transition = [e for e in events if isinstance(e, UtteranceBegin)][-1]
    assert (
        transition.kind == "transition"
        and "Most Frequent Error Service" in transition.text
        and transition.question_id
    )
    started = events[-1]
    assert isinstance(started, CodingStarted) and started.coding.coding_session_id == fake.session_id
    state = await d.state()
    assert state.coding.active and state.coding.question_id == transition.question_id
    assert state.coding.language.value == "python"  # preferred from the profile (Python 6 years > Go 3)
    async with session_scope() as db:
        q = await db.get(Question, uuid.UUID(transition.question_id))
        assert q.action == "start_coding" and str(q.coding_question_id) == str(world.coding_question_id)
    cs_id = fake.session_id

    # Button hint -> CodingHintIssued + short spoken hint.
    hint_events = await collect(
        engine.handle_coding_activity(sid, CodingActivity(kind="hint_requested", coding_session_id=cs_id))
    )
    issued = [e for e in hint_events if isinstance(e, CodingHintIssued)][0]
    assert issued.level == 1 and issued.hints_used == 1 and issued.hints_remaining == 2
    hint_speech = [e for e in hint_events if isinstance(e, UtteranceBegin)][0]
    assert hint_speech.kind == "hint" and "hash map" in hint_speech.text

    # Voice hint request while coding.
    voice = await d.answer("Can I get a hint please?", speculate=False, evaluate=False)
    assert [e for e in voice if isinstance(e, CodingHintIssued)][0].level == 2
    assert ("hint", "voice") in fake.calls

    # Edits are silent; a finished run lets the observer comment.
    assert (
        await collect(
            engine.handle_coding_activity(
                sid, CodingActivity(kind="edit", coding_session_id=cs_id, code="x", version=3)
            )
        )
        == []
    )
    run = CodingRunResultMsg(
        coding_session_id=cs_id, run_id="r1", kind=RunKind.TEST, status=RunStatus.FAILED, passed=1, total=2
    )
    comment_events = await collect(
        engine.handle_coding_activity(
            sid, CodingActivity(kind="run_completed", coding_session_id=cs_id, run=run)
        )
    )
    comment = [e for e in comment_events if isinstance(e, UtteranceBegin)][0]
    assert comment.kind == "comment" and "time complexity" in comment.text and comment.question_id
    state = await d.state()
    assert state.coding.runs == 1 and state.coding.last_run.passed == 1 and state.coding.comments_made == 1

    # Candidate answers the comment while coding: short coding-mode reply, evaluated as an answer.
    reply = await d.answer(
        "I think it is O(n squared) because of the nested loop, should I optimise?",
        speculate=False,
        evaluate=False,
    )
    meta = metas(reply)[-1]
    assert meta.decision["action"] in (TurnAction.CODING_COMMENT.value, TurnAction.ACKNOWLEDGE_AND_WAIT.value)
    prompt = (
        interview_env["mock_ai"]
        .llm.calls_for(__import__("app.domain.enums", fromlist=["ModelTask"]).ModelTask.TURN_DECISION)[-1]
        .messages[-1]["content"]
    )
    assert "CODING EXERCISE IN PROGRESS" in prompt and "<code_current>" in prompt
    state = await d.state()
    assert state.pending_evaluations  # answer to the observer's question is evaluated

    # Backchannels while coding stay silent.
    assert metas(await d.answer("okay", speculate=False, evaluate=False)) == []

    # Submission -> CodingEnded + follow-up about the solution.
    end_events = await collect(
        engine.handle_coding_activity(sid, CodingActivity(kind="submitted", coding_session_id=cs_id))
    )
    ended = [e for e in end_events if isinstance(e, CodingEnded)][0]
    assert ended.reason == "submitted" and fake.finished == [(cs_id, "submitted")]
    follow = metas(end_events)[-1]
    assert follow.decision["action"] == TurnAction.ASK_FOLLOWUP.value
    assert follow.decision["competency_key"] == "data_structures"
    state = await d.state()
    assert not state.coding.active and state.coding.completed and state.coding.problems_done == 1
    async with session_scope() as db:
        types = [
            q.action
            for q in (
                await db.execute(
                    select(Question).where(Question.session_id == uuid.UUID(sid)).order_by(Question.sequence)
                )
            ).scalars()
        ]
        assert "start_coding" in types and "coding_comment" in types


async def test_coding_time_up_and_time_warning(interview_env) -> None:
    _, sid, engine, d, fake = await _coding_setup(interview_env, observer_speaks_on=frozenset())
    await engine.force_coding(sid)
    await d.answer("[adequate] Answer before coding starts with enough words.")
    cs_id = fake.session_id
    interview_env["clock"].advance(6 * 60)
    warn = await collect(
        engine.handle_coding_activity(sid, CodingActivity(kind="time_warning", coding_session_id=cs_id))
    )
    begin = [e for e in warn if isinstance(e, UtteranceBegin)][0]
    assert "five minutes left" in begin.text
    assert (
        await collect(
            engine.handle_coding_activity(sid, CodingActivity(kind="time_warning", coding_session_id=cs_id))
        )
        == []
    )
    interview_env["clock"].advance(5 * 60)
    end_events = await collect(
        engine.handle_coding_activity(sid, CodingActivity(kind="time_up", coding_session_id=cs_id))
    )
    assert [e for e in end_events if isinstance(e, CodingEnded)][0].reason == "time_limit"
    assert fake.finished[-1] == (cs_id, "time_limit")
    assert metas(end_events)  # the interview resumes with a question


async def test_coding_unavailable_is_skipped_gracefully(interview_env) -> None:
    _, sid, engine, d, _ = await _coding_setup(interview_env, service=False)
    await engine.force_coding(sid)
    events = await d.answer("[adequate] An answer with enough words in it.")
    assert not [e for e in events if isinstance(e, CodingStarted)]
    assert metas(events)[-1].decision["action"] != "start_coding"
    state = await d.state()
    assert state.coding.unavailable and not state.force_coding


async def test_session_end_during_coding_closes_coding(interview_env) -> None:
    _, sid, engine, d, fake = await _coding_setup(interview_env)
    await engine.force_coding(sid)
    await d.answer("[adequate] Answer before the coding exercise starts.")
    events = await collect(engine.handle_timer(sid, TimerEvent(kind="time_up", elapsed_s=1800)))
    assert [e for e in events if isinstance(e, CodingEnded)][0].reason == "session_ended"
    assert fake.finished[-1] == (fake.session_id, "session_ended")
    assert type(events[-1]).__name__ == "SessionClosing"


async def test_stale_coding_activity_is_ignored(interview_env) -> None:
    _, sid, engine, d, fake = await _coding_setup(interview_env)
    assert (
        await collect(
            engine.handle_coding_activity(sid, CodingActivity(kind="idle", coding_session_id="nope"))
        )
        == []
    )
    del d, fake
    _ = UtteranceMeta


async def test_engine_owns_coding_clock_with_pauses_excluded(interview_env) -> None:
    _, sid, engine, d, fake = await _coding_setup(interview_env, observer_speaks_on=frozenset())
    await engine.force_coding(sid)
    await d.answer("[adequate] Answer before the coding exercise starts here.")
    cs_id = fake.session_id
    interview_env["clock"].advance(5.5 * 60)
    warn = await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0)))
    assert "five minutes left" in [e for e in warn if isinstance(e, UtteranceBegin)][0].text
    # A disconnect pauses the interview clock and therefore the coding clock.
    await collect(engine.handle_timer(sid, TimerEvent(kind="disconnect", elapsed_s=0)))
    interview_env["clock"].advance(20 * 60)
    await collect(engine.resume(sid, reconnect=True))
    state = await d.state()
    assert state.coding.active
    remaining = state.coding.time_limit_s - (
        state.elapsed_seconds(interview_env["clock"]()) - state.coding.started_at_s
    )
    assert remaining == pytest.approx(270, abs=1)
    assert await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0))) == []
    interview_env["clock"].advance(4.6 * 60)
    ended = await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0)))
    assert [e for e in ended if isinstance(e, CodingEnded)][0].reason == "time_limit"
    assert fake.finished[-1] == (cs_id, "time_limit")
    assert metas(ended), "the interview resumes after the coding time ran out"


async def test_coding_evaluation_refreshes_competency_state(interview_env) -> None:
    from app.db.models import CodingSession, CompetencyEvidence
    from app.schemas.ai import CodingEvaluation

    world, sid, engine, d, fake = await _coding_setup(interview_env, observer_speaks_on=frozenset())
    await engine.force_coding(sid)
    await d.answer("[adequate] Answer before the coding exercise starts here.")
    cs_id = fake.session_id
    await collect(
        engine.handle_coding_activity(sid, CodingActivity(kind="submitted", coding_session_id=cs_id))
    )
    before = (await d.state()).competencies["data_structures"]
    assert before.evidence_count == 0
    evaluation = CodingEvaluation(
        correctness_summary="All tests pass",
        tests_passed=7,
        tests_total=7,
        code_quality=8,
        problem_solving=8,
        edge_case_handling=7,
        score_estimate=8.0,
        confidence=0.8,
    )
    async with session_scope() as db:
        db.add(
            CodingSession(
                id=uuid.UUID(cs_id),
                session_id=uuid.UUID(sid),
                coding_question_id=world.coding_question_id,
                language="python",
                status="submitted",
                hints_used=1,
                evaluation=evaluation.model_dump(mode="json"),
            )
        )
        await db.flush()
        for dim in ("correctness", "complexity"):
            db.add(
                CompetencyEvidence(
                    session_id=uuid.UUID(sid),
                    competency_key="data_structures",
                    coding_session_id=uuid.UUID(cs_id),
                    source="coding",
                    signal="positive",
                    dimension=dim,
                    evidence=f"coding {dim}",
                    strength=0.8,
                    confidence=0.8,
                    difficulty="easy",
                )
            )
    # Any engine operation applies it (coding.evaluated -> reload) before deciding.
    await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0)))
    state = await d.state()
    after = state.competencies["data_structures"]
    assert after.evidence_count == 2 and after.score_estimate > 5.0 and after.confidence > 0
    assert cs_id in state.coding.evaluated_session_ids
    async with session_scope() as db:
        from app.db.models import InterviewEvent

        types = [
            e.type
            for e in (
                await db.execute(select(InterviewEvent).where(InterviewEvent.session_id == uuid.UUID(sid)))
            ).scalars()
        ]
        assert types.count("coding.evaluation_applied") == 1
    await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0)))  # applied once only
    async with session_scope() as db:
        types = [
            e.type
            for e in (
                await db.execute(select(InterviewEvent).where(InterviewEvent.session_id == uuid.UUID(sid)))
            ).scalars()
        ]
        assert types.count("coding.evaluation_applied") == 1


async def test_coding_documents_with_bookkeeping_feed_live_observations(interview_env) -> None:
    """Audit (MEDIUM): ``coding_sessions.evaluation`` holds the CodingEvaluation fields plus
    bookkeeping keys; strict validation (extra=forbid) used to skip the row, so the live engine
    ignored coding results. Pending/failed documents still contribute nothing."""
    from app.db.models import CodingSession
    from app.schemas.ai import CodingEvaluation
    from app.schemas.rounds import RoundConfig
    from app.services.interview.adapters import ScoringPort
    from app.services.interview.evaluation import build_observations, coding_evaluation_from_document
    from app.services.interview.scoring_fallback import FallbackScoring

    world, sid, engine, d, fake = await _coding_setup(interview_env, observer_speaks_on=frozenset())
    await engine.force_coding(sid)
    await d.answer("[adequate] Answer before the coding exercise starts here.")
    cs_id = fake.session_id
    await collect(
        engine.handle_coding_activity(sid, CodingActivity(kind="submitted", coding_session_id=cs_id))
    )
    document = {
        **CodingEvaluation(
            correctness_summary="All tests pass",
            tests_passed=7,
            tests_total=7,
            code_quality=8,
            problem_solving=8,
            edge_case_handling=7,
            score_estimate=8.0,
            confidence=0.8,
        ).model_dump(mode="json"),
        # bookkeeping stored by the coding module next to the schema fields
        "status": "completed",
        "is_fallback": False,
        "raw_score_estimate": 8.0,
        "score_cap": 8.0,
        "score_cap_applied": False,
        "hints_used": 1,
        "assistance_factor": 0.93,
        "adjusted_score": 7.44,
        "competency_keys": ["data_structures"],
        "difficulty": "easy",
        "language": "python",
        "final_run_id": str(uuid.uuid4()),
        "final_run_status": "passed",
        "model_call_id": None,
        "prompt_version": "coding.evaluate@x",
        "evaluated_at": "2026-10-07T12:00:00Z",
        "evidence_ids": [],
        "needs_human_review": False,
    }
    assert coding_evaluation_from_document(document) is not None
    assert coding_evaluation_from_document({**document, "status": "pending"}) is None
    assert coding_evaluation_from_document({}) is None
    async with session_scope() as db:
        db.add(
            CodingSession(
                id=uuid.UUID(cs_id),
                session_id=uuid.UUID(sid),
                coding_question_id=world.coding_question_id,
                language="python",
                status="submitted",
                hints_used=1,
                evaluation=document,
            )
        )
    state = await d.state()
    port = ScoringPort(api=FallbackScoring(), recompute=None)
    async with session_scope() as db:
        observations, _ = await build_observations(
            db, uuid.UUID(sid), RoundConfig.model_validate(state.round_config.model_dump()), port
        )
    coding = [o for o in observations if o.coding_session_id == cs_id]
    assert [o.competency_key for o in coding] == ["data_structures"]
    assert coding[0].assistance_factor < 1.0  # one hint used, priced transparently
    # The live engine applies it (and only once).
    await collect(engine.handle_timer(sid, TimerEvent(kind="tick", elapsed_s=0)))
    state = await d.state()
    assert cs_id in state.coding.evaluated_session_ids
    assert state.competencies["data_structures"].score_estimate > 5.0
