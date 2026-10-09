"""Barge-in decisions: spoken portion, backchannel classification, FSM rules."""

from __future__ import annotations

import pytest

from app.domain.enums import TurnState
from app.realtime.turns import (
    ChunkRecord,
    TurnController,
    is_backchannel,
    is_repeat_or_clarification,
    spoken_portion,
)

CHUNKS = [
    ChunkRecord(chunk_id=11, index=0, text="Thanks, that's helpful.", duration_ms=1200, offset_ms=0),
    ChunkRecord(
        chunk_id=12,
        index=1,
        text="How did you decide on three availability zones for the cluster?",
        duration_ms=3000,
        offset_ms=1200,
    ),
    ChunkRecord(chunk_id=13, index=2, text="And what did it cost?", duration_ms=1000, offset_ms=4200),
]


@pytest.mark.parametrize(
    ("played_ms", "spoken", "next_index"),
    [
        (0, "", 0),
        (600, "Thanks,", 0),  # half of a 2-word chunk -> 1 word, chunk 0 re-spoken
        (1150, "Thanks, that's helpful.", 1),  # >= 90 % counts as fully played
        (1200, "Thanks, that's helpful.", 1),
        (2700, "Thanks, that's helpful. How did you decide on", 1),  # 1.5 of 3.0 s -> 5 of 11 words
        (4200, "Thanks, that's helpful. How did you decide on three availability zones for the cluster?", 2),
        (
            9999,
            "Thanks, that's helpful. How did you decide on three availability zones for the cluster? And what did it cost?",
            3,
        ),
    ],
)
def test_spoken_portion_from_played_ms(played_ms: int, spoken: str, next_index: int) -> None:
    assert spoken_portion(CHUNKS, played_ms) == (spoken, next_index)


def test_spoken_portion_ignores_chunk_order_and_handles_empty() -> None:
    assert spoken_portion(list(reversed(CHUNKS)), 1200)[0] == "Thanks, that's helpful."
    assert spoken_portion([], 5000) == ("", 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Mm-hmm.",
        "mhm",
        "Yeah.",
        "Okay",
        "Sure, sure.",
        "Right.",
        "Got it.",
        "Yeah, okay.",
        "uh-huh",
        "I see",
    ],
)
def test_backchannels(text: str) -> None:
    assert is_backchannel(text)


@pytest.mark.parametrize(
    "text",
    [
        "Yes, but we actually used four zones.",
        "Sorry, could you repeat that?",
        "Okay so the main reason was cost",
        "No",
        "Wait",
        "Yeah okay sure right",  # more than 3 words
    ],
)
def test_not_backchannels(text: str) -> None:
    assert not is_backchannel(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sorry, could you repeat the question please?", True),
        ("Can you say that again?", True),
        ("What do you mean by blast radius?", True),
        ("Could you clarify the scope?", True),
        ("I didn't catch that", True),
        ("We repeated the load test twice.", False),  # whole-word match only
        ("We used Karpenter for autoscaling.", False),
    ],
)
def test_repeat_or_clarification(text: str, expected: bool) -> None:
    assert is_repeat_or_clarification(text) is expected


def test_turn_fsm_transitions_and_callbacks() -> None:
    seen: list[tuple[TurnState, TurnState, str | None]] = []
    fsm = TurnController(on_change=lambda s, p, r: seen.append((s, p, r)))
    assert fsm.to(TurnState.AI_SPEAKING, "intro")
    assert fsm.to(TurnState.LISTENING, "delivered")
    assert fsm.to(TurnState.CANDIDATE_SPEAKING, "speech")
    assert fsm.to(TurnState.PROCESSING, "silence")
    assert fsm.to(TurnState.AI_SPEAKING, "question")
    assert fsm.to(TurnState.CANDIDATE_SPEAKING, "barge_in")
    assert fsm.to(TurnState.PAUSED, "disconnect")
    assert fsm.to(TurnState.LISTENING, "resumed")
    assert fsm.to(TurnState.ENDED, "closing")
    assert not fsm.to(TurnState.LISTENING, "after end")
    assert [s for s, _, _ in seen][-1] is TurnState.ENDED
    assert seen[0] == (TurnState.AI_SPEAKING, TurnState.IDLE, "intro")


def test_turn_fsm_rejects_invalid_and_ignores_same_state() -> None:
    calls: list[TurnState] = []
    fsm = TurnController(on_change=lambda s, p, r: calls.append(s))
    assert fsm.to(TurnState.IDLE)  # same state: no-op, accepted
    assert calls == []
    fsm.state = TurnState.ENDED
    assert not fsm.can(TurnState.PAUSED)
