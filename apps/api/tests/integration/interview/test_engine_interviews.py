"""Full interview simulations with MockAIServices (Postgres + Redis).

The simulated interviewer model follows the policy directive in its prompt;
candidate answers carry quality markers that drive the scripted quick
assessment and deep evaluation."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.ai.base import AIUnavailable
from app.db.models import (
    Answer,
    AnswerEvaluation,
    BackgroundJob,
    CompetencyEvidence,
    Interview,
    InterviewEvent,
    InterviewSession,
    Question,
    ScoreSnapshot,
)
from app.db.session import session_scope
from app.domain.enums import Difficulty, ModelTask, SessionEndReason, SessionStatus, TurnAction
from app.services.interview.adapters import ScoringPort
from app.services.interview.contracts import (
    CandidateTurn,
    InterruptedUtterance,
    SessionClosing,
    SpeechDelta,
    TimerEvent,
    UtteranceBegin,
    UtteranceMeta,
)
from app.services.interview.evaluation import EvaluationService
from app.services.interview.scoring_fallback import FallbackScoring

from .harness import build_world, collect, create_session, metas

pytestmark = pytest.mark.integration

STUB_SCORING = ScoringPort(api=FallbackScoring(), recompute=None)


class Driver:
    """Drives one session like the realtime runtime would."""

    def __init__(self, env: dict[str, Any], engine, sid: str, *, scoring: ScoringPort = STUB_SCORING) -> None:
        self.env = env
        self.engine = engine
        self.sid = sid
        self.clock = env["clock"]
        self.ms = 0
        self.evaluations = EvaluationService(ai=env["mock_ai"], scoring=scoring)
        self.decisions: list[dict[str, Any]] = []
        self.closing: SessionClosing | None = None

    async def deliver(self, events: list[Any]) -> None:
        for e in events:
            if isinstance(e, UtteranceMeta):
                self.decisions.append(e.decision)
                await self.engine.mark_utterance_delivered(self.sid, e.utterance_id, spoken_text=e.full_text)
            if isinstance(e, SessionClosing):
                self.closing = e

    async def start(self) -> list[Any]:
        events = await collect(self.engine.start(self.sid))
        await self.deliver(events)
        return events

    async def answer(
        self, text: str, *, advance: float = 90.0, speculate: bool = True, evaluate: bool = True, **turn: Any
    ) -> list[Any]:
        self.clock.advance(advance)
        if speculate:
            await self.engine.on_partial_transcript(self.sid, text, silence_started=True)
            await self.engine.wait_speculative(self.sid)
        started = self.ms + 1000
        self.ms = started + int(advance * 1000) - 2000
        events = await collect(
            self.engine.handle_candidate_turn(
                self.sid, CandidateTurn(text=text, started_ms=started, ended_ms=self.ms, **turn)
            )
        )
        await self.deliver(events)
        if evaluate:
            await self.run_evaluations()
        return events

    async def run_evaluations(self) -> None:
        state = await self.engine.load_state(self.sid)
        for answer_id in list(state.pending_evaluations):
            await self.evaluations.evaluate(self.sid, answer_id)

    async def state(self):
        return await self.engine.load_state(self.sid)


async def _setup(env, *, config: dict[str, Any] | None = None, prepare: bool = True, **engine_kwargs):
    world = await build_world(
        env["mock_ai"],
        config=config,
        with_coding_question=bool(config and config.get("coding", {}).get("enabled")),
    )
    sid = await create_session(env, world)
    engine = env["make_engine"](**engine_kwargs)
    if prepare:
        await engine.prepare_session(sid)
    return world, sid, engine


def actions(driver: Driver) -> list[str]:
    return [d.get("action") for d in driver.decisions]


async def questions(sid: str) -> list[Question]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(
                    select(Question).where(Question.session_id == uuid.UUID(sid)).order_by(Question.sequence)
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------- strong candidate


async def test_strong_candidate_difficulty_rises_and_interview_stops_when_sufficient(interview_env) -> None:
    world, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    for i in range(16):
        await d.answer(
            f"[strong] Detailed answer {i}: we ran 40 nodes with Karpenter, HPA on custom metrics, PDBs and multi-AZ ingress; I owned it end to end."
        )
        if d.closing:
            break
    assert d.closing is not None and d.closing.reason is SessionEndReason.SUFFICIENT_EVIDENCE
    acts = actions(d)
    assert TurnAction.INCREASE_DIFFICULTY.value in acts
    assert (
        acts[-2] == TurnAction.INVITE_CANDIDATE_QUESTIONS.value
        and acts[-1] == TurnAction.CLOSE_INTERVIEW.value
    )
    qs = await questions(sid)
    asked = [q for q in qs if q.action not in ("invite_candidate_questions",)]
    assert len(asked) <= 9  # well under max_questions (14): stops once evidence is sufficient
    assert any(q.difficulty == "hard" for q in qs)
    state = await d.state()
    assert all(state.competencies[k].sufficient_evidence for k in ("kubernetes", "aws"))
    assert (
        state.status is SessionStatus.COMPLETING and state.end_reason is SessionEndReason.SUFFICIENT_EVIDENCE
    )
    async with session_scope() as db:
        jobs = {j.type for j in (await db.execute(select(BackgroundJob))).scalars()}
        assert {"scoring.finalize", "report.generate", "answer.evaluate", "session.prepare"} <= jobs
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert (
            row.status == "completing"
            and row.end_reason == "sufficient_evidence"
            and row.ended_at is not None
        )
        assert (await db.get(Interview, world.interview_id)).status == "completed"
        evidence = (
            (
                await db.execute(
                    select(CompetencyEvidence).where(CompetencyEvidence.session_id == uuid.UUID(sid))
                )
            )
            .scalars()
            .all()
        )
        assert evidence and all(e.embedding is not None for e in evidence)
        snapshots = (
            (await db.execute(select(ScoreSnapshot).where(ScoreSnapshot.session_id == uuid.UUID(sid))))
            .scalars()
            .all()
        )
        assert snapshots


# ---------------------------------------------------------------- uncertain candidate


async def test_uncertain_candidate_gets_followups_up_to_depth_then_moves_on(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    for i in range(5):
        await d.answer(
            f"[partial] Answer {i}: we had some autoscaling on CPU, I think, with a few manual tweaks."
        )
    acts = actions(d)
    # intro, verify_claim (kubernetes), three follow-ups (depth 3), then a new topic on another competency.
    assert acts[:2] == ["introduce", "verify_claim"], acts
    assert acts[2:5] == ["ask_followup"] * 3, acts
    assert acts[5] in ("verify_claim", "change_topic", "ask_question"), acts
    last = d.decisions[5]
    assert last["competency_key"] == "aws"
    assert last["directive"]["max_followups_reached"] is True
    assert (
        d.decisions[4]["directive"]["followups_on_topic"] == 2
    )  # the limit stayed at the full depth while uncertain


async def test_hedged_answers_get_one_gentle_followup_then_another_area(interview_env) -> None:
    """Harness finding (mid / contradictor personas): three follow-ups in a row on one sub-topic after "I
    wasn't directly involved" / "The team handled that". After a hedged answer the topic gets at most one
    gentle follow-up (about the part the candidate did themselves), then the interview moves on."""
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    for i in range(3):
        await d.answer(
            f"[partial] Answer {i}: we had some autoscaling, I think it was set up by another team."
        )
    acts = actions(d)
    assert acts[:3] == ["introduce", "verify_claim", "ask_followup"], acts
    assert acts[3] in ("verify_claim", "change_topic", "ask_question"), acts
    assert d.decisions[2]["competency_key"] == "kubernetes"
    assert d.decisions[3]["competency_key"] != "kubernetes"
    assert d.decisions[3]["directive"]["max_followups_reached"] is True
    prompts = [
        c.messages[-1]["content"] for c in interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)
    ]
    # Prompts after answers 0, 1, 2: gentle follow-up, move on, gentle follow-up on the new topic.
    assert "one gentle question about the part they did themselves" in prompts[-3]
    assert "move to a different area now" in prompts[-2]
    assert "one gentle question about the part they did themselves" in prompts[-1]


async def test_vague_answer_requests_specificity(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    events = await d.answer("[vague] We optimized the costs a lot and things got much better overall.")
    meta = metas(events)[-1]
    assert meta.decision["action"] == "request_specificity"
    assert meta.decision["directive"]["answer_quality"] == "vague"
    assert meta.decision["directive"]["recommended_action"] == "request_specificity"
    turn_calls = interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)
    prompt = turn_calls[-1].messages[-1]["content"]
    # Derived from model output over the candidate's answer: fenced as data.
    assert "Missing specifics to ask for" in prompt
    assert "<derived_missing_specifics>\ntime range\n</derived_missing_specifics>" in prompt
    assert interview_env["mock_ai"].llm.calls_for(
        ModelTask.ANSWER_ASSESSMENT
    )  # speculative quick assessment ran


async def test_contradiction_is_verified_respectfully(interview_env) -> None:
    interview_env["evaluator"].contradiction_on = "only 3 nodes"
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await d.answer("[adequate] Honestly it was only 3 nodes in production, the rest was staging.")
    # The evaluation found a medium-severity contradiction; it is applied before the next decision.
    events = await d.answer("[adequate] We also used managed node groups with spot instances.")
    meta = metas(events)[-1]
    assert meta.decision["action"] == "verify_contradiction"
    assert meta.decision["directive"]["open_contradiction"] is not None
    state = await d.state()
    assert any(c.status == "verifying" for c in state.contradictions)
    await d.answer(
        "[adequate] Sorry, to clarify: production had 40 nodes; the 3 nodes were the platform tooling cluster."
    )
    await d.answer("[adequate] We used Terraform for all of it.")
    state = await d.state()
    assert all(c.status != "verifying" for c in state.contradictions)


# ---------------------------------------------------------------- time limit


async def test_time_limit_closes_the_interview(interview_env) -> None:
    world, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await d.answer("[adequate] First answer about EKS.")
    events = await d.answer("[adequate] Second answer.", advance=27.5 * 60)
    assert isinstance(events[-1], SessionClosing) and events[-1].reason is SessionEndReason.TIME_LIMIT
    closing = [e for e in events if isinstance(e, UtteranceBegin)][-1]
    assert closing.kind == "closing" and closing.text and "?" not in closing.text
    state = await d.state()
    assert state.status is SessionStatus.COMPLETING and state.closing_utterance_id
    # Further calls are no-ops on an ended session.
    assert (
        await collect(
            engine.handle_candidate_turn(sid, CandidateTurn(text="hello?", started_ms=0, ended_ms=1))
        )
        == []
    )
    assert await collect(engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED)) == []


async def test_max_time_timer_and_candidate_end(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    events = await collect(engine.handle_timer(sid, TimerEvent(kind="max_time", elapsed_s=2100)))
    assert isinstance(events[-1], SessionClosing) and events[-1].reason is SessionEndReason.TIME_LIMIT
    _, sid2, engine2 = await _setup(interview_env)
    d2 = Driver(interview_env, engine2, sid2)
    await d2.start()
    events2 = await collect(engine2.end_session(sid2, SessionEndReason.CANDIDATE_ENDED))
    closing = [e for e in events2 if isinstance(e, UtteranceBegin)]
    assert closing and "stop here" in closing[0].text
    assert events2[-1].reason is SessionEndReason.CANDIDATE_ENDED


async def test_cancelled_session_is_aborted_without_reports(interview_env) -> None:
    world, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    events = await collect(engine.end_session(sid, SessionEndReason.CANCELLED))
    assert not [e for e in events if isinstance(e, UtteranceBegin)]
    state = await d.state()
    assert state.status is SessionStatus.ABORTED
    async with session_scope() as db:
        jobs = {j.type for j in (await db.execute(select(BackgroundJob))).scalars()}
        assert "report.generate" not in jobs
        assert (await db.get(Interview, world.interview_id)).status == "cancelled"


# ---------------------------------------------------------------- LLM failure


async def test_llm_failure_falls_back_to_bank_question_and_continues(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await engine.drain_background()  # post-decision prefetch filled the bank cache
    interview_env["mock_ai"].llm.script(
        ModelTask.TURN_DECISION, AIUnavailable("main down"), AIUnavailable("still down")
    )
    events = await d.answer("[adequate] We used EKS with managed node groups.", speculate=False)
    begins = [e for e in events if isinstance(e, UtteranceBegin)]
    deltas = [e for e in events if isinstance(e, SpeechDelta)]
    assert len(begins) == 1 and isinstance(events[0], UtteranceBegin)  # begin before any model call
    assert len(deltas) == 1  # the fallback text arrives whole, not streamed
    meta = metas(events)[-1]
    assert meta.decision["fallback"] is True and meta.question_id and meta.full_text == deltas[0].text
    assert meta.decision["bank_question_id"] is not None  # top cached bank question
    state = await d.state()
    assert state.fallbacks_used == 1
    # The interview continues normally afterwards.
    events = await d.answer("[strong] Follow-up answer with details.", speculate=False)
    assert metas(events)[-1].decision["fallback"] is False


async def test_llm_failure_without_bank_uses_template(interview_env) -> None:
    _, sid, engine = await _setup(interview_env, prepare=False)
    async with session_scope() as db:
        from app.db.models import BankQuestion

        for row in (await db.execute(select(BankQuestion))).scalars():
            row.status = "retired"
    await engine.prepare_session(sid)
    d = Driver(interview_env, engine, sid)
    await d.start()
    interview_env["mock_ai"].llm.script(ModelTask.TURN_DECISION, AIUnavailable("x"), AIUnavailable("y"))
    events = await d.answer("[adequate] Some answer.", speculate=False)
    delta = [e for e in events if isinstance(e, SpeechDelta)][0]
    assert delta.text.endswith("?") and metas(events)[-1].decision["bank_question_id"] is None


# ---------------------------------------------------------------- barge-in


async def test_barge_in_records_interruption_and_backchannel_resumes(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    interview_env["mock_ai"].llm.chunk_size = 4
    d = Driver(interview_env, engine, sid)
    await d.start()
    interview_env["clock"].advance(60)
    stream = engine.handle_candidate_turn(
        sid, CandidateTurn(text="[adequate] We used EKS.", started_ms=1000, ended_ms=8000)
    )
    begin = None
    spoken = ""
    seen = []
    async for event in stream:
        seen.append(type(event).__name__)
        if isinstance(event, UtteranceBegin):
            begin = event
        if isinstance(event, SpeechDelta):
            spoken += event.text
            if "?" in spoken:  # the question sentence was generated; audio is still playing
                break
    await stream.aclose()  # barge-in: closes the LLM stream
    assert begin is not None, seen
    state = await engine.load_state(sid)
    record = state.utterance_by_id(begin.utterance_id)
    assert record is not None and record.text == spoken.strip()
    qid = record.question_id
    async with session_scope() as db:
        q = await db.get(Question, uuid.UUID(qid))
        assert "interrupted_before_complete" in q.decision["deviations"]
    await engine.handle_interruption(
        sid,
        InterruptedUtterance(
            utterance_id=begin.utterance_id,
            question_id=qid,
            full_text=record.text,
            spoken_text=spoken[:8],
            played_ms=900,
        ),
    )
    async with session_scope() as db:
        q = await db.get(Question, uuid.UUID(qid))
        assert q.interrupted and q.spoken_text == spoken[:8].strip()
        events = [
            e.type
            for e in (
                await db.execute(select(InterviewEvent).where(InterviewEvent.session_id == uuid.UUID(sid)))
            ).scalars()
        ]
        assert "utterance.interrupted" in events
    # Backchannel ("mm-hmm"): continue from the next unplayed sentence.
    resumed = await collect(
        engine.handle_backchannel_resume(
            sid,
            InterruptedUtterance(
                utterance_id=begin.utterance_id,
                question_id=qid,
                full_text=record.text,
                spoken_text=spoken[:8],
                played_ms=900,
            ),
        )
    )
    begins = [e for e in resumed if isinstance(e, UtteranceBegin)]
    # Resumes at the start of the first sentence that was not completely heard.
    assert (
        begins
        and begins[0].text == record.text[: record.text.index("?") + 1]
        and begins[0].question_id == qid
    )
    async with session_scope() as db:
        assert not (await db.get(Question, uuid.UUID(qid))).interrupted


async def test_interrupting_turn_tells_the_model_what_was_heard(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    state = await d.state()
    last = state.utterances[-1]
    await engine.handle_interruption(
        sid,
        InterruptedUtterance(
            utterance_id=last.id,
            question_id=last.question_id,
            full_text=last.text,
            spoken_text="Your resume",
            played_ms=500,
        ),
    )
    await d.answer(
        "Sorry to cut in, do you mean the production cluster?", speculate=False, interrupted_ai=True
    )
    prompt = interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)[-1].messages[-1]["content"]
    assert "the candidate interrupted you" in prompt and '"Your resume"' in prompt
    assert "has not heard your current question yet" in prompt


# ---------------------------------------------------------------- repeat / silence / reconnect


async def test_repeat_request_reasks_without_model_call(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    before = len(interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION))
    state = await d.state()
    current = state.current_question()
    events = await d.answer("Sorry, could you repeat the question?", speculate=False)
    begin = [e for e in events if isinstance(e, UtteranceBegin)][0]
    from app.services.interview.turns import question_core

    assert begin.question_id == current.id and question_core(current.text) in begin.text
    assert len(interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)) == before
    async with session_scope() as db:
        assert (
            not (await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid)))).scalars().all()
        )
    events = await collect(engine.repeat_question(sid))
    assert [e for e in events if isinstance(e, UtteranceBegin)][0].question_id == current.id


async def test_silence_prompts_then_moves_on(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    state = await d.state()
    assert state.awaiting_answer
    first = await collect(engine.handle_timer(sid, TimerEvent(kind="silence_timeout", elapsed_s=60)))
    assert "repeat the question" in [e for e in first if isinstance(e, UtteranceBegin)][0].text
    await d.deliver(first)
    second = await collect(engine.handle_timer(sid, TimerEvent(kind="silence_timeout", elapsed_s=90)))
    assert "move on" in [e for e in second if isinstance(e, UtteranceBegin)][0].text
    await d.deliver(second)
    third = await collect(engine.handle_timer(sid, TimerEvent(kind="silence_timeout", elapsed_s=120)))
    meta = metas(third)[-1]
    assert meta.decision["action"] in ("change_topic", "verify_claim", "ask_question")
    assert meta.decision["competency_key"] != "kubernetes"


async def test_disconnect_pauses_clock_and_resume_reasks(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await d.answer("[adequate] We ran EKS.", speculate=False)
    state = await d.state()
    question = state.current_question()
    elapsed = state.elapsed_seconds(interview_env["clock"]())
    events = await collect(engine.handle_timer(sid, TimerEvent(kind="disconnect", elapsed_s=elapsed)))
    assert [type(e).__name__ for e in events] == ["StateChanged"]
    paused = await d.state()
    assert paused.status is SessionStatus.PAUSED
    interview_env["clock"].advance(300)
    assert (await d.state()).elapsed_seconds(interview_env["clock"]()) == pytest.approx(elapsed, abs=0.01)
    resumed = await collect(engine.resume(sid, reconnect=True))
    begin = [e for e in resumed if isinstance(e, UtteranceBegin)][0]
    assert begin.kind == "resume" and begin.question_id == question.id
    assert begin.text.startswith("Sorry, we got disconnected for a moment. As I was asking:")
    state = await d.state()
    assert state.status is SessionStatus.LIVE and state.reconnects == 1
    async with session_scope() as db:
        assert (await db.get(InterviewSession, uuid.UUID(sid))).reconnect_count == 1


async def test_reconnect_after_unanswered_reply_regenerates(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    # The candidate's turn was persisted but the reply never went out (stream closed before speech).
    stream = engine.handle_candidate_turn(
        sid, CandidateTurn(text="[adequate] An answer.", started_ms=1, ended_ms=2)
    )
    await stream.aclose()
    resumed = await collect(engine.resume(sid, reconnect=True))
    assert metas(resumed), "a reply to the pending answer is generated on resume"


# ---------------------------------------------------------------- speculative turns / evaluation


async def test_speculative_turn_persists_only_when_completed(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    stream = engine.handle_candidate_turn(
        sid, CandidateTurn(text="[adequate] Maybe finished.", started_ms=1, ended_ms=2, speculative=True)
    )
    async for event in stream:
        if isinstance(event, SpeechDelta):
            break
    await stream.aclose()
    async with session_scope() as db:
        assert (
            not (await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid)))).scalars().all()
        )
    events = await d.answer(
        "[adequate] Maybe finished. And confirmed now.", speculate=False, evaluate=False, speculative=True
    )
    assert metas(events)
    async with session_scope() as db:
        answers = (
            (await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid)))).scalars().all()
        )
        assert len(answers) == 1
        jobs = (
            (await db.execute(select(BackgroundJob).where(BackgroundJob.type == "answer.evaluate")))
            .scalars()
            .all()
        )
        assert len(jobs) == 1


async def test_evaluation_is_idempotent_and_applied_before_next_decision(interview_env, redis) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await d.answer("[strong] A strong answer with numbers.", evaluate=False)
    state = await d.state()
    answer_id = state.pending_evaluations[0]
    from app.core.queue import subscribe_session_events

    async with subscribe_session_events(sid) as sub:
        result = await d.evaluations.evaluate(sid, answer_id)
        event = None
        for _ in range(20):
            event = await sub.get(timeout=0.5)
            if event is not None:
                break
    assert result["quality"] == "strong" and not result["fallback"]
    assert event["type"] == "evaluation.ready" and event["answer_id"] == answer_id
    assert "kubernetes" in event["competencies"]
    again = await d.evaluations.evaluate(sid, answer_id)
    assert again["idempotent"] is True
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    select(AnswerEvaluation).where(AnswerEvaluation.answer_id == uuid.UUID(answer_id))
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        evidence = (
            (
                await db.execute(
                    select(CompetencyEvidence).where(CompetencyEvidence.answer_id == uuid.UUID(answer_id))
                )
            )
            .scalars()
            .all()
        )
        assert len(evidence) == 2 and all(e.transcript_ref["answer_id"] == answer_id for e in evidence)
        assert (await db.get(Answer, uuid.UUID(answer_id))).status == "evaluated"
    # The live engine applies it at the start of the next decision.
    await d.answer("[strong] Next answer.", evaluate=False)
    state = await d.state()
    assert answer_id not in state.pending_evaluations
    assert state.competencies["kubernetes"].evidence_count >= 2


async def test_evaluation_fallback_on_last_attempt(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await d.answer("[adequate] Answer.", evaluate=False)
    answer_id = (await d.state()).pending_evaluations[0]
    interview_env["mock_ai"].llm.script(ModelTask.ANSWER_EVALUATION, AIUnavailable("down"))
    with pytest.raises(AIUnavailable):
        await d.evaluations.evaluate(sid, answer_id, is_last_attempt=False)
    interview_env["mock_ai"].llm.script(ModelTask.ANSWER_EVALUATION, AIUnavailable("down"))
    result = await d.evaluations.evaluate(sid, answer_id, is_last_attempt=True)
    assert result["fallback"] is True
    async with session_scope() as db:
        row = (
            await db.execute(
                select(AnswerEvaluation).where(AnswerEvaluation.answer_id == uuid.UUID(answer_id))
            )
        ).scalar_one()
        assert row.is_fallback and row.confidence == 0.2 and "human review" in row.evaluation["summary"]
        assert (await db.get(Answer, uuid.UUID(answer_id))).status == "needs_review"


async def test_real_scoring_module_is_compatible(interview_env) -> None:
    from app.services.interview.adapters import discover_scoring

    port = discover_scoring()
    _, sid, engine = await _setup(interview_env, scoring=port)
    d = Driver(interview_env, engine, sid, scoring=port)
    await d.start()
    for i in range(3):
        await d.answer(f"[strong] Strong answer {i} with concrete numbers.")
    state = await d.state()
    assert state.competencies["kubernetes"].evidence_count > 0
    if port.recompute is not None:
        from app.db.models import Score

        async with session_scope() as db:
            assert (
                await db.execute(select(Score).where(Score.session_id == uuid.UUID(sid)))
            ).scalars().first() is not None


async def test_difficulty_never_jumps_two_levels(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    for i in range(6):
        await d.answer(f"[strong] Strong answer {i} with concrete numbers and trade-offs.")
        if d.closing:
            break
    qs = await questions(sid)
    order = {"easy": 0, "medium": 1, "hard": 2}
    by_comp: dict[str, list[int]] = {}
    for q in qs:
        if q.competency_keys:
            by_comp.setdefault(q.competency_keys[0], []).append(order[q.difficulty])
    for levels in by_comp.values():
        assert all(abs(b - a) <= 1 for a, b in zip(levels, levels[1:], strict=False))
    assert Difficulty.HARD.value in {q.difficulty for q in qs}


async def test_answer_audio_stored_only_with_policy_and_consent(interview_env) -> None:
    from app.db.models import MediaAsset

    wav = b"RIFF" + b"\x00" * 64
    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()
    await d.answer(
        "[adequate] An answer with recorded audio for the record.",
        speculate=False,
        evaluate=False,
        audio_wav=wav,
    )
    async with session_scope() as db:
        answer = (await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid)))).scalar_one()
        assert (
            answer.audio_storage_key
            == f"orgs/{(await d.state()).organization_id}/sessions/{sid}/audio/{answer.id}.wav"
        )
        media = (
            await db.execute(select(MediaAsset).where(MediaAsset.storage_key == answer.audio_storage_key))
        ).scalar_one()
        assert media.kind == "answer_audio" and media.retention_until is not None
    # Without consent for audio recording nothing is stored.
    world2 = await build_world(interview_env["mock_ai"], consent_audio=False)
    sid2 = await create_session(interview_env, world2)
    engine2 = interview_env["make_engine"]()
    await engine2.prepare_session(sid2)
    d2 = Driver(interview_env, engine2, sid2)
    await d2.start()
    await d2.answer(
        "[adequate] An answer that must not be recorded at all.",
        speculate=False,
        evaluate=False,
        audio_wav=wav,
    )
    async with session_scope() as db:
        answer2 = (await db.execute(select(Answer).where(Answer.session_id == uuid.UUID(sid2)))).scalar_one()
        assert answer2.audio_storage_key is None


async def test_backchannel_resume_regenerates_a_truncated_utterance(interview_env) -> None:
    _, sid, engine = await _setup(interview_env)
    interview_env["mock_ai"].llm.chunk_size = 3
    d = Driver(interview_env, engine, sid)
    await d.start()
    stream = engine.handle_candidate_turn(
        sid, CandidateTurn(text="[adequate] We used EKS everywhere.", started_ms=1000, ended_ms=8000)
    )
    begin = None
    async for event in stream:
        if isinstance(event, UtteranceBegin):
            begin = event
        if isinstance(event, SpeechDelta):
            break  # barge-in after the very first words: the sentence was never completed
    await stream.aclose()
    state = await engine.load_state(sid)
    record = state.utterance_by_id(begin.utterance_id)
    assert record.text[-1:] not in ".?!"
    events = await collect(
        engine.handle_backchannel_resume(
            sid,
            InterruptedUtterance(
                utterance_id=record.id,
                question_id=record.question_id,
                full_text=record.text,
                spoken_text="",
                played_ms=100,
            ),
        )
    )
    meta = metas(events)[-1]
    assert "?" in meta.full_text and meta.decision["action"] != "resume_after_backchannel"
    prompt = interview_env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)[-1].messages[-1]["content"]
    assert "cut off by a brief acknowledgement" in prompt


async def test_late_evaluation_updates_claims_after_session_end(interview_env) -> None:
    from app.schemas.ai import ClaimVerification

    _, sid, engine = await _setup(interview_env)
    d = Driver(interview_env, engine, sid)
    await d.start()  # first question verifies claim c1
    await d.answer(
        "[strong] I personally ran the 40-node EKS platform with Karpenter and multi-AZ ingress.",
        evaluate=False,
    )
    answer_id = (await d.state()).pending_evaluations[0]
    await collect(engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED))
    assert (await d.state()).claims["c1"].status.value == "in_progress"

    def verified(call):
        ev = interview_env["evaluator"](call)
        return ev.model_copy(
            update={
                "claim_verifications": [
                    ClaimVerification(claim_id="c1", status="verified", note="credible detail")
                ]
            }
        )

    interview_env["mock_ai"].llm.script(ModelTask.ANSWER_EVALUATION, verified)
    await d.evaluations.evaluate(sid, answer_id)
    state = await d.state()
    assert state.status is SessionStatus.COMPLETING
    assert state.claims["c1"].status.value == "verified" and answer_id not in state.pending_evaluations
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert row.state["claims"]["c1"]["status"] == "verified"  # durable snapshot for the reports
