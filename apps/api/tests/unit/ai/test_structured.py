from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.ai.base import AIOutputInvalid, AIUnavailable
from app.ai.structured import (
    StructuredParseError,
    describe_schema,
    feedback_message,
    parse_structured,
    response_format,
    response_format_for,
    run_structured,
    schema_for,
    strip_wrappers,
)
from app.domain.enums import AnswerQuality
from app.schemas.ai import AnswerAssessment, TurnDecision


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=2)
    score: float = Field(0.5, ge=0, le=1)
    quality: AnswerQuality = AnswerQuality.ADEQUATE


GOOD = '{"name": "kafka", "tags": ["a"], "score": 0.7, "quality": "strong"}'


def test_valid_json_is_not_marked_repaired() -> None:
    parsed = parse_structured(GOOD, Item)
    assert parsed.value.name == "kafka"
    assert parsed.repaired is False


@pytest.mark.parametrize(
    "raw",
    [
        f"```json\n{GOOD}\n```",
        f"Here you go:\n{GOOD}\nHope that helps!",
        f"<think>\nI should answer {{carefully}}\n</think>\n{GOOD}",
        f"</think>\n\n{GOOD}",
        f"```\n{GOOD}\n```",
    ],
)
def test_wrappers_are_stripped_without_repair(raw: str) -> None:
    parsed = parse_structured(raw, Item)
    assert parsed.value.quality is AnswerQuality.STRONG
    assert parsed.repaired is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"name": "kafka", "tags": ["a",], "score": 0.7,}',  # trailing commas
        "{'name': 'kafka', 'score': 0.7}",  # single quotes
        '{"name": "kafka", "score": 0.7',  # truncated
        '[{"name": "kafka"}]',  # wrapped in a list
        '{name: "kafka"}',  # unquoted key
    ],
)
def test_json_repair_paths(raw: str) -> None:
    parsed = parse_structured(raw, Item)
    assert parsed.value.name == "kafka"
    assert parsed.repaired is True


def test_safe_fixes_truncate_clamp_drop_and_normalise() -> None:
    raw = '{"name": "averyveryverylongname", "tags": ["a", "b", "c"], "score": 1.7, "quality": "Strong", "extra": 1}'
    parsed = parse_structured(raw, Item)
    assert parsed.repaired
    assert parsed.value.name == "averyveryv"
    assert parsed.value.tags == ["a", "b"]
    assert parsed.value.score == 1
    assert parsed.value.quality is AnswerQuality.STRONG


def test_nested_list_fixes() -> None:
    class Outer(BaseModel):
        items: list[Item]

    raw = '{"items": [{"name": "ok"}, {"name": "toolongnamehere", "score": -3}]}'
    parsed = parse_structured(raw, Outer)
    assert parsed.value.items[1].name == "toolongnam"
    assert parsed.value.items[1].score == 0


@pytest.mark.parametrize("raw", ["", "   ", "no json here", "null", '"just a string"', "42"])
def test_unparseable_outputs_raise_with_feedback(raw: str) -> None:
    with pytest.raises(StructuredParseError) as info:
        parse_structured(raw, Item)
    assert info.value.feedback
    assert isinstance(info.value, AIOutputInvalid)


def test_semantic_errors_give_field_feedback() -> None:
    with pytest.raises(StructuredParseError) as info:
        parse_structured('{"tags": [], "quality": "excellent"}', Item)
    assert "name" in info.value.feedback
    assert "quality" in info.value.feedback
    message = feedback_message(info.value)
    assert message["role"] == "user"
    assert "ONLY one JSON object" in message["content"]


def test_strip_wrappers_keeps_braces_inside_strings() -> None:
    raw = 'text {"a": "x } y", "b": {"c": 1}} trailing'
    assert strip_wrappers(raw) == '{"a": "x } y", "b": {"c": 1}}'


def test_turn_decision_schema_order_and_response_format() -> None:
    schema = schema_for(TurnDecision)
    assert next(iter(schema["properties"])) == "speech"
    fmt = response_format_for(TurnDecision)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "TurnDecision"
    assert fmt["json_schema"]["strict"] is True
    assert schema_for(TurnDecision) is schema  # cached
    bare = response_format({"type": "object", "title": "My Thing!"})
    assert bare["json_schema"]["name"] == "My_Thing_"
    wrapped = response_format({"name": "x", "schema": {"type": "object"}, "strict": False})
    assert wrapped["json_schema"]["strict"] is False


def test_describe_schema_lists_fields() -> None:
    text = describe_schema(AnswerAssessment)
    assert '"quality":' in text and '"strong"' in text
    assert "(optional)" in text


async def test_run_structured_retry_then_success() -> None:
    replies = iter(['{"name": 5}', GOOD])
    seen: list[list[dict]] = []

    async def generate(conversation: list[dict], attempt: int) -> str:
        seen.append(conversation)
        return next(replies)

    outcome = await run_structured(Item, [{"role": "user", "content": "go"}], generate, max_attempts=3)
    assert outcome.attempts == 2
    assert outcome.value.name == "kafka"
    assert len(seen[0]) == 1
    assert seen[1][1] == {"role": "assistant", "content": '{"name": 5}'}
    assert "name" in seen[1][2]["content"]


async def test_run_structured_fallback_and_errors() -> None:
    async def bad(conversation: list[dict], attempt: int) -> str:
        return "nope"

    with pytest.raises(AIOutputInvalid):
        await run_structured(Item, [], bad, max_attempts=2)
    outcome = await run_structured(Item, [], bad, max_attempts=2, fallback=lambda: Item(name="fb"))
    assert outcome.used_fallback and outcome.value.name == "fb" and outcome.attempts == 2

    async def down(conversation: list[dict], attempt: int) -> str:
        raise AIUnavailable("down")

    with pytest.raises(AIUnavailable):
        await run_structured(Item, [], down)
    outcome = await run_structured(Item, [], down, fallback=lambda: Item(name="fb"))
    assert outcome.used_fallback and "AIUnavailable" in (outcome.error or "")
