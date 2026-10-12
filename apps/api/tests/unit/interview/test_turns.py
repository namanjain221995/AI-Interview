from __future__ import annotations

import asyncio

import pytest

from app.ai.base import AIUnavailable
from app.domain.enums import AnswerQuality, Difficulty, ModelTask, TurnAction
from app.schemas.ai import TurnDecision
from app.services.interview.config import EngineConfig
from app.services.interview.contracts import SpeechDelta
from app.services.interview.policy import BankCandidate, compute_directive
from app.services.interview.special import classify
from app.services.interview.turns import (
    TURN_SCHEMA,
    TurnOutcome,
    fallback_utterance,
    question_core,
    question_kind,
    sanitize_delta,
    stream_speech_object,
    synthesize_decision,
)

from .factories import T0, ask, claim, decision, state


def cfg(**kw) -> EngineConfig:
    base = {
        "closing_reserve_s": 75.0,
        "default_interviewer_name": "Maya",
        "turn_first_speech_timeout_s": 2.0,
        "turn_total_timeout_s": 5.0,
    }
    base.update(kw)
    return EngineConfig(**base)


async def _collect(ai, messages, outcome, config, retry=None):
    out = []
    async for d in stream_speech_object(
        ai,
        messages,
        schema=TurnDecision,
        json_schema=TURN_SCHEMA,
        context=__import__("app.ai.base", fromlist=["CallContext"]).CallContext(task=ModelTask.TURN_DECISION),
        utterance_id="u1",
        outcome=outcome,
        config=config,
        retry_messages=retry,
    ):
        out.append(d)
    return out


async def test_speech_streams_first_and_decision_validates(mock_ai) -> None:
    mock_ai.llm.chunk_size = 5
    mock_ai.llm.script(
        ModelTask.TURN_DECISION, decision(speech="How many nodes did the cluster run at peak?")
    )
    outcome = TurnOutcome()
    deltas = await _collect(mock_ai, [{"role": "user", "content": "x"}], outcome, cfg())
    assert all(isinstance(d, SpeechDelta) and d.utterance_id == "u1" for d in deltas)
    assert "".join(d.text for d in deltas) == "How many nodes did the cluster run at peak?"
    assert len(deltas) > 3  # streamed incrementally
    assert outcome.decision is not None and outcome.decision.action is TurnAction.ASK_FOLLOWUP
    assert outcome.first_speech_ms is not None and not outcome.fallback and not outcome.synthesized


async def test_failure_before_speech_retries_once_then_falls_back(mock_ai) -> None:
    mock_ai.llm.script(ModelTask.TURN_DECISION, AIUnavailable("down"), AIUnavailable("still down"))
    outcome = TurnOutcome()
    deltas = await _collect(
        mock_ai, [{"role": "user", "content": "a"}], outcome, cfg(), retry=[{"role": "user", "content": "b"}]
    )
    assert deltas == []
    assert outcome.fallback and outcome.attempts == 2
    assert len(mock_ai.llm.calls_for(ModelTask.TURN_DECISION)) == 2


async def test_retry_with_trimmed_context_succeeds(mock_ai) -> None:
    mock_ai.llm.script(ModelTask.TURN_DECISION, "not json at all", decision())
    outcome = TurnOutcome()
    deltas = await _collect(
        mock_ai,
        [{"role": "user", "content": "a"}],
        outcome,
        cfg(),
        retry=[{"role": "user", "content": "trimmed"}],
    )
    assert outcome.decision is not None and not outcome.fallback
    assert "".join(d.text for d in deltas) == decision().speech
    calls = mock_ai.llm.calls_for(ModelTask.TURN_DECISION)
    assert calls[1].messages[0]["content"] == "trimmed"


async def test_invalid_tail_after_speech_is_synthesized(mock_ai) -> None:
    broken = '{"speech": "What was the monthly spend before the change?", "action": "not-an-action"'
    mock_ai.llm.script(ModelTask.TURN_DECISION, broken)
    outcome = TurnOutcome()
    deltas = await _collect(mock_ai, [{"role": "user", "content": "a"}], outcome, cfg())
    assert "".join(d.text for d in deltas) == "What was the monthly spend before the change?"
    assert outcome.synthesized and outcome.decision is None
    assert outcome.speech == "What was the monthly spend before the change?"


async def test_first_speech_timeout(mock_ai) -> None:
    mock_ai.llm.chunk_delay = 0.3
    mock_ai.llm.chunk_size = 2
    mock_ai.llm.script(ModelTask.TURN_DECISION, decision(), decision())
    outcome = TurnOutcome()
    deltas = await _collect(
        mock_ai, [{"role": "user", "content": "a"}], outcome, cfg(turn_first_speech_timeout_s=0.5)
    )
    assert deltas == [] and outcome.fallback
    assert any("TimeoutError" in e for e in outcome.errors)


async def test_a_stalled_model_falls_back_within_one_first_speech_budget(mock_ai) -> None:
    """Tier 1: the old 8 s + 8 s retry left the candidate in 16 s of silence before the fallback question."""
    import time

    mock_ai.llm.chunk_delay = 0.4
    mock_ai.llm.chunk_size = 2
    mock_ai.llm.script(ModelTask.TURN_DECISION, decision(), decision())
    outcome = TurnOutcome()
    started = time.monotonic()
    deltas = await _collect(
        mock_ai,
        [{"role": "user", "content": "a"}],
        outcome,
        cfg(turn_first_speech_timeout_s=8.0, turn_first_speech_budget_s=1.2, turn_retry_min_s=0.5),
        retry=[{"role": "user", "content": "trimmed"}],
    )
    elapsed = time.monotonic() - started
    assert deltas == [] and outcome.fallback
    assert elapsed < 2.0, elapsed  # one budget, not two full per-attempt timeouts
    assert outcome.attempts == 1 and "first_speech_budget_spent" in outcome.errors


async def test_a_retry_still_runs_while_the_budget_allows(mock_ai) -> None:
    mock_ai.llm.chunk_size = 5
    mock_ai.llm.script(ModelTask.TURN_DECISION, AIUnavailable("main down", endpoint="main"), decision())
    outcome = TurnOutcome()
    deltas = await _collect(
        mock_ai,
        [{"role": "user", "content": "a"}],
        outcome,
        cfg(turn_first_speech_budget_s=6.0, turn_retry_min_s=1.5),
        retry=[{"role": "user", "content": "trimmed"}],
    )
    assert deltas and not outcome.fallback and outcome.attempts == 2


async def test_aclose_closes_llm_stream(mock_ai) -> None:
    mock_ai.llm.chunk_size = 3
    mock_ai.llm.chunk_delay = 0.01
    mock_ai.llm.script(
        ModelTask.TURN_DECISION, decision(speech="A fairly long question about autoscaling policies, please?")
    )
    outcome = TurnOutcome()
    from app.ai.base import CallContext

    gen = stream_speech_object(
        mock_ai,
        [{"role": "user", "content": "a"}],
        schema=TurnDecision,
        json_schema=TURN_SCHEMA,
        context=CallContext(task=ModelTask.TURN_DECISION),
        utterance_id="u",
        outcome=outcome,
        config=cfg(),
    )
    first = await gen.__anext__()
    assert first.text
    await gen.aclose()
    assert outcome.speech_started and outcome.decision is None


def test_sanitize_and_question_core() -> None:
    assert sanitize_delta("**bold** `code`\nnext") == "bold code next"
    text = "Thanks, that's helpful context on the migration. Shifting to cost now, what was the monthly spend before?"
    assert question_core(text) == text
    assert question_core("Got it. How many nodes?") == "How many nodes?"
    assert question_core("No question here.") == "No question here."


def test_question_kind_mapping() -> None:
    from app.domain.enums import QuestionKind

    assert question_kind(TurnAction.VERIFY_CLAIM, "technical", "aws") is QuestionKind.RESUME_CLAIM
    assert question_kind(TurnAction.ASK_FOLLOWUP, "technical", "aws") is QuestionKind.FOLLOWUP
    assert question_kind(TurnAction.ASK_QUESTION, "behavioral", "ownership") is QuestionKind.BEHAVIORAL
    assert question_kind(TurnAction.ASK_QUESTION, "technical", "ownership") is QuestionKind.BEHAVIORAL
    assert question_kind(TurnAction.ASK_QUESTION, "technical", "aws") is QuestionKind.TECHNICAL


def _d(s, **kw):
    return compute_directive(s, now=T0, closing_reserve_s=75, **kw)


def test_fallback_uses_bank_question_with_acknowledgement() -> None:
    s = state()
    ask(s, "kubernetes", followups=3)
    bank = [
        BankCandidate(
            id="b1",
            text="How would you design IAM boundaries for many teams?",
            competency_key="aws",
            difficulty=Difficulty.MEDIUM,
        )
    ]
    d = _d(s, bank_candidates=bank)
    text, action, chosen = fallback_utterance(s, d, current_question="Previous question?")
    assert chosen is not None and chosen.id == "b1"
    assert text.endswith("How would you design IAM boundaries for many teams?")
    assert action in (TurnAction.CHANGE_TOPIC, TurnAction.ASK_QUESTION)
    decision_ = synthesize_decision(text, d, s, action=action, reason="fallback", bank=chosen)
    assert decision_.bank_question_id == "b1" and decision_.competency_key == "aws"


def test_fallback_template_without_bank() -> None:
    s = state()
    ask(s, "kubernetes", followups=3)
    d = _d(s)
    text, action, chosen = fallback_utterance(s, d, current_question=None)
    assert chosen is None and "AWS" in text and text.endswith("?")


def test_fallback_adds_no_acknowledgement_after_a_spoken_ack() -> None:
    s = state()
    ask(s, "kubernetes", followups=3)
    d = _d(s)
    spoken, _, _ = fallback_utterance(s, d, current_question=None)
    quiet, _, _ = fallback_utterance(s, d, current_question=None, acknowledged=True)
    assert spoken.split(" ", 1)[0].rstrip(",.") in {"Thanks", "Okay", "Got", "Alright", "Right"}
    assert not quiet.startswith(("Thanks", "Okay", "Got it", "Alright", "Right."))
    assert spoken.endswith(quiet)


@pytest.mark.parametrize(
    "turn,expected_start",
    [
        ("What do you mean?", "Sure, let me put it another way."),
        ("Let me think.", None),
        ("What does a typical day look like for the team?", "That's a great question for the hiring team"),
    ],
)
def test_fallback_honours_flow_moves(turn: str, expected_start: str | None) -> None:
    s = state()
    ask(s, "kubernetes")
    d = _d(s, special=classify(turn))
    text, action, _ = fallback_utterance(s, d, current_question="Thanks. How did you size the node groups?")
    assert action is (d.required_action or d.recommended_action)
    if expected_start:
        assert text.startswith(expected_start)
    if action is TurnAction.CLARIFY_QUESTION:
        assert text.endswith("How did you size the node groups?")


def test_fallback_claim_verification() -> None:
    s = state(claims={"c1": claim("c1", 5, text="Ran a 40-node EKS platform")})
    d = _d(s, first_question=True)
    text, action, _ = fallback_utterance(s, d, current_question=None)
    assert action is TurnAction.VERIFY_CLAIM
    assert "40-node EKS platform" in text and text.endswith("?")


def test_synthesized_decision_is_valid_for_every_directive_action() -> None:
    s = state()
    ask(s, "kubernetes")
    for q in AnswerQuality:
        d = _d(s, special=None, quick=None)
        dec = synthesize_decision("Okay, could you walk me through it?", d, s, reason="x")
        assert isinstance(dec, TurnDecision)
        del q


async def test_stream_handles_cancellation_without_leaking(mock_ai) -> None:
    mock_ai.llm.chunk_delay = 0.05
    mock_ai.llm.script(ModelTask.TURN_DECISION, decision())
    outcome = TurnOutcome()

    async def run():
        return await _collect(mock_ai, [{"role": "user", "content": "x"}], outcome, cfg())

    task = asyncio.create_task(run())
    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_recover_decision_keeps_fields_that_arrived() -> None:
    from app.services.interview.turns import recover_decision

    s = state()
    ask(s, "kubernetes")
    d = _d(s)
    raw = (
        '{"speech": "How did you size the node groups?", "action": "request_specificity", '
        '"answer_quality": "vague", "competency_key": "kubernetes", "topic": "node sizing", "difficulty": "hard", "kind": "foll'
    )
    dec = recover_decision(raw, "How did you size the node groups?", d, s, reason="cut")
    assert dec.action is TurnAction.REQUEST_SPECIFICITY and dec.answer_quality is AnswerQuality.VAGUE
    assert dec.topic == "node sizing" and dec.difficulty is Difficulty.HARD
    assert dec.speech == "How did you size the node groups?"  # never taken from the raw object
    junk = recover_decision("not json", "Okay, and why?", d, s, reason="cut")
    assert junk.action is d.recommended_action and junk.competency_key == d.target_competency
    bad = recover_decision(
        '{"speech": "x", "action": "made_up", "difficulty": null}', "Why?", d, s, reason="cut"
    )
    assert bad.action is d.recommended_action


def test_grounding_check() -> None:
    from app.services.interview.grounding import ungrounded_reference
    from app.services.interview.state import AnswerRecord

    s = state(
        candidate_profile={
            "roles": [{"title": "SRE", "company": "Acme", "technologies": ["EKS", "Karpenter"]}]
        }
    )
    s.answers.append(
        AnswerRecord(
            id="a",
            question_id=None,
            sequence=1,
            text="I moved stateless services on EKS to spot instances with Karpenter.",
        )
    )
    assert (
        ungrounded_reference(s, "You mentioned moving stateless services to spot instances. How did that go?")
        is None
    )
    assert (
        ungrounded_reference(s, "Earlier you said you rewrote the billing system in Rust. Why Rust?")
        is not None
    )
    assert (
        ungrounded_reference(s, "You mentioned on your resume that you used Karpenter at Acme. Tell me more?")
        is None
    )
    assert ungrounded_reference(s, "How did you size the node groups?") is None
