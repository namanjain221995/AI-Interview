"""``IntakeTurn`` wire schema (every field required, speech first) and the speech-first streaming path
(``turns.stream_speech_object`` applies OpenerFilter / QuestionLimiter to schemas with ``SPEECH_FIRST_TURN``)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.base import CallContext
from app.ai.mock import MockAIServices
from app.domain.enums import ModelTask
from app.services.intake.turn_schema import (
    INTAKE_TURN_SCHEMA,
    IntakeTurn,
    VerificationGrade,
    recover_turn,
    synthesize_turn,
)
from app.services.interview.config import EngineConfig
from app.services.interview.turns import TurnOutcome, stream_speech_object

FIELDS = [
    "speech",
    "move",
    "item_key",
    "answer_quality",
    "claim_signal",
    "target_bullet",
    "missing_specifics",
    "reason",
]


def turn_payload(**overrides) -> dict:
    payload = {
        "speech": "What did you personally build there?",
        "move": "ask_item",
        "item_key": "resume.C1.ownership",
        "answer_quality": "adequate",
        "claim_signal": None,
        "target_bullet": None,
        "missing_specifics": [],
        "reason": "ownership rung",
    }
    payload.update(overrides)
    return payload


def test_wire_schema_requires_every_field_in_speech_first_order() -> None:
    schema = INTAKE_TURN_SCHEMA["schema"]
    assert INTAKE_TURN_SCHEMA["name"] == "IntakeTurn"
    assert list(schema["properties"]) == FIELDS
    assert schema["required"] == FIELDS
    assert IntakeTurn.SPEECH_FIRST_TURN is True


def test_model_validation_rules() -> None:
    IntakeTurn.model_validate(turn_payload())
    with pytest.raises(ValidationError):
        IntakeTurn.model_validate({k: v for k, v in turn_payload().items() if k != "claim_signal"})
    with pytest.raises(ValidationError):
        IntakeTurn.model_validate(turn_payload(move="change_topic"))
    with pytest.raises(ValidationError):
        IntakeTurn.model_validate(turn_payload(target_bullet=5))
    with pytest.raises(ValidationError):
        IntakeTurn.model_validate(turn_payload(missing_specifics=["a", "b", "c", "d", "e"]))
    with pytest.raises(ValidationError):
        IntakeTurn.model_validate(turn_payload(extra="nope"))


def test_recover_and_synthesize() -> None:
    raw = '{"speech": "How did you measure it?", "move": "follow_up", "item_key": "resume.C1.mechanics.clarify", "answer_quality": "vague", "claim_si'
    turn = recover_turn(raw, "How did you measure it?", move="ask_item", item_key="x", reason="partial")
    assert (turn.move, turn.item_key, turn.answer_quality.value) == (
        "follow_up",
        "resume.C1.mechanics.clarify",
        "vague",
    )
    assert turn.claim_signal is None and turn.reason == "partial"
    garbage = recover_turn("not json at all {{", "Hello there?", move="clarify", item_key="k", reason="r")
    assert garbage.move == "clarify" and garbage.speech == "Hello there?"
    assert synthesize_turn("", move="bogus", item_key="k", reason="r").move == "ask_item"


def test_verification_grade_schema() -> None:
    VerificationGrade.model_validate({"quality": "adequate", "matched_key_points": [1, 3]})
    with pytest.raises(ValidationError):
        VerificationGrade.model_validate({"quality": "excellent", "matched_key_points": []})


async def _stream(payload: dict) -> tuple[str, TurnOutcome]:
    ai = MockAIServices()
    ai.llm.script(ModelTask.TURN_DECISION, json.dumps(payload))
    outcome = TurnOutcome()
    spoken: list[str] = []
    async for delta in stream_speech_object(
        ai,
        [{"role": "user", "content": "x"}],
        schema=IntakeTurn,
        json_schema=INTAKE_TURN_SCHEMA,
        context=CallContext(task=ModelTask.TURN_DECISION, prompt_version="intake-turn@test"),
        utterance_id="u1",
        outcome=outcome,
        config=EngineConfig(closing_reserve_s=60, default_interviewer_name="Maya"),
    ):
        spoken.append(delta.text)
    return "".join(spoken), outcome


async def test_streaming_applies_the_opener_filter_and_question_limiter() -> None:
    speech = "Great answer, really impressive. What did you personally build? And what broke first?"
    spoken, outcome = await _stream(turn_payload(speech=speech, move="follow_up"))
    assert spoken.strip() == "What did you personally build?"
    assert isinstance(outcome.extra, IntakeTurn) and outcome.extra.move == "follow_up"
    assert outcome.speech.strip() == "What did you personally build?"
    assert outcome.dropped_opener and outcome.dropped_tail


async def test_streaming_plain_question_passes_unchanged() -> None:
    spoken, outcome = await _stream(turn_payload())
    assert spoken == "What did you personally build there?"
    assert outcome.extra.item_key == "resume.C1.ownership"
