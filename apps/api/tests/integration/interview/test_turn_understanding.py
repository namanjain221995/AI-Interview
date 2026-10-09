"""Replay of the owner's live session (f1dc3a1b, 2026-10-10) through the generic engine (mock AI): audio checks,
questions about the interview, "okay", "let me think" and mid-sentence fragments are answered deterministically
without a model call, without an answer row and without consuming the pending question; real answers go to the
model as before (and an answer that opens with an audio check is acknowledged)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import Answer, BackgroundJob, Transcript
from app.db.session import session_scope
from app.domain.enums import ModelTask
from app.services.interview import understanding as U

from .harness import metas
from .test_engine_interviews import Driver, _setup

pytestmark = pytest.mark.integration


async def count_answers(sid: str) -> int:
    async with session_scope() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(Answer).where(Answer.session_id == uuid.UUID(sid))
            )
            or 0
        )


async def evaluate_jobs(sid: str) -> int:
    async with session_scope() as db:
        rows = await db.execute(select(BackgroundJob).where(BackgroundJob.type == "answer.evaluate"))
        return sum(1 for job in rows.scalars().all() if (job.payload or {}).get("session_id") == sid)


async def candidate_lines(sid: str) -> list[str]:
    async with session_scope() as db:
        rows = await db.execute(
            select(Transcript)
            .where(Transcript.session_id == uuid.UUID(sid), Transcript.speaker == "candidate")
            .order_by(Transcript.seq)
        )
        return [r.text for r in rows.scalars().all()]


def turn_calls(env) -> int:
    return len(env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION))


async def test_live_transcript_replay_through_the_generic_engine(interview_env) -> None:  # noqa: PLR0915
    env = interview_env
    _, sid, engine = await _setup(env)
    d = Driver(env, engine, sid)
    await d.start()
    first_question = (await d.engine.load_state(sid)).current_question()
    assert first_question is not None
    question_sentence = U.question_sentence(first_question.text)
    calls = turn_calls(env)

    async def flow(text: str, intent: str) -> str:
        events = await d.answer(text, advance=4, speculate=False, evaluate=False)
        [meta] = metas(events)
        assert meta.decision["flow"] == intent, (text, meta.decision)
        assert meta.question_id == first_question.id  # still the pending question
        state = await d.engine.load_state(sid)
        assert state.current_question_id == first_question.id
        return meta.full_text

    # The audio checks the live session swallowed as answers.
    text = await flow("Okay, so can you hear?", "audio_check")
    assert text.split(". ")[0] + "." in U.AUDIO_ACKS and text.endswith(question_sentence)
    text = await flow("Can you hear me?", "audio_check")
    assert text.endswith(question_sentence)
    text = await flow("But first answer me, like, am I able to do it or not?", "process_question")
    assert text.startswith(U.PROCESS_REPLIES["ability"])
    text = await flow("Okay, so am I properly audible?", "audio_check")
    # A third check on the same question also suggests checking the speaker (they may not hear us).
    assert U.VOLUME_HINT in text
    assert await flow("Okay.", "readiness") in U.READY_REPLIES
    assert await flow("so for that", "incomplete") in U.NUDGES
    assert await flow("Let me think.", "thinking") == U.THINKING_REPLY
    text = await flow("How long is this interview?", "process_question")
    assert text.startswith("We have about ")
    assert turn_calls(env) == calls, "flow turns never call the model"
    assert await count_answers(sid) == 0 and await evaluate_jobs(sid) == 0
    lines = await candidate_lines(sid)
    assert "Can you hear me?" in lines and "so for that" in lines  # kept in the transcript

    # A real answer goes to the model and consumes the question.
    events = await d.answer(
        "[adequate] We ran EKS with managed node groups and Karpenter across three zones.",
        advance=40,
        speculate=False,
        evaluate=False,
    )
    meta = metas(events)[-1]
    assert "flow" not in meta.decision and turn_calls(env) == calls + 1
    assert await count_answers(sid) == 1 and await evaluate_jobs(sid) == 1
    second = (await d.engine.load_state(sid)).current_question_id
    assert second is not None and second != first_question.id

    # An answer that opens with an audio check: answered as the item, the reply starts with the acknowledgement.
    await d.answer(
        "Hello, can you hear me? Yes, so I also set up the CI/CD pipeline with GitHub Actions and Argo CD for "
        "every service.",
        advance=40,
        speculate=False,
        evaluate=False,
    )
    prompt = str(env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)[-1].messages[-1].get("content", ""))
    assert 'begin your reply with "Yes, I can hear you clearly."' in prompt
    assert await count_answers(sid) == 2

    # "Exactly." is a very short answer: the existing clarification path (never a flow reply).
    events = await d.answer("Exactly.", advance=5, speculate=False, evaluate=False)
    meta = metas(events)[-1]
    assert "flow" not in meta.decision and meta.decision["action"] == "clarify_question"


async def test_audio_check_while_paused_is_not_kept_as_an_answer(interview_env) -> None:
    from app.services.interview.contracts import CandidateTurn, TimerEvent

    from .harness import collect

    env = interview_env
    _, sid, engine = await _setup(env)
    d = Driver(env, engine, sid)
    await d.start()
    await collect(engine.handle_timer(sid, TimerEvent(kind="disconnect", elapsed_s=0)))
    events = await collect(
        engine.handle_candidate_turn(
            sid, CandidateTurn(text="Hello, can you hear me?", started_ms=1, ended_ms=2)
        )
    )
    assert events == [] and await count_answers(sid) == 0 and await evaluate_jobs(sid) == 0
