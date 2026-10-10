"""Barge-in decisions: spoken portion, backchannel classification, FSM rules."""

from __future__ import annotations

import pytest

from app.domain.enums import TurnState
from app.realtime.turns import (
    UNSPOKEN_WORDS_FOR_ANSWER,
    ChunkRecord,
    TurnController,
    is_backchannel,
    is_repeat_or_clarification,
    resume_after_backchannel,
    spoken_portion,
    unspoken_words,
)
from app.services.interview.contracts import InterruptedUtterance

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


# ------------------------------------------------------------------ short replies over the end of a question (S10)

QUESTION = "Got it. Did you own the Karpenter configuration yourself?"


def _interrupted(spoken: str) -> InterruptedUtterance:
    return InterruptedUtterance("u1", "q1", QUESTION, spoken, played_ms=1000)


def test_backchannel_in_the_middle_of_a_question_resumes_it() -> None:
    assert resume_after_backchannel(_interrupted("Got it."), "Mm-hmm.", typed=False)
    assert resume_after_backchannel(_interrupted("Got it. Did you own the"), "Okay.", typed=False)
    assert resume_after_backchannel(_interrupted(""), "Yeah.", typed=False)


def test_short_reply_over_the_last_words_is_an_answer() -> None:
    assert not resume_after_backchannel(_interrupted(QUESTION), "Yes.", typed=False)
    assert not resume_after_backchannel(
        _interrupted("Got it. Did you own the Karpenter configuration"), "Yes.", typed=False
    )
    assert not resume_after_backchannel(
        _interrupted("Got it. Did you own the Karpenter"), "Okay, sure.", typed=False
    )


def test_resume_threshold_is_exact() -> None:
    words = QUESTION.split()
    heard_all_but_limit = " ".join(words[: len(words) - UNSPOKEN_WORDS_FOR_ANSWER])
    assert not resume_after_backchannel(_interrupted(heard_all_but_limit), "Right.", typed=False)
    one_more_unspoken = " ".join(words[: len(words) - UNSPOKEN_WORDS_FOR_ANSWER - 1])
    assert resume_after_backchannel(_interrupted(one_more_unspoken), "Right.", typed=False)
    assert unspoken_words(QUESTION, heard_all_but_limit) == UNSPOKEN_WORDS_FOR_ANSWER
    assert unspoken_words(QUESTION, None) == len(words) and unspoken_words(QUESTION, QUESTION + " extra") == 0


def test_a_question_still_being_generated_always_resumes() -> None:
    streaming = InterruptedUtterance(
        "u1", "q1", "Got it. Did", "Got it. Did", played_ms=300, text_complete=False
    )
    assert resume_after_backchannel(streaming, "Yes.", typed=False)
    assert not resume_after_backchannel(
        _interrupted(QUESTION), "Yes.", typed=False
    )  # complete and fully heard


def test_noise_keeps_resuming_even_at_the_end() -> None:
    assert resume_after_backchannel(_interrupted(QUESTION), "", typed=False)
    assert resume_after_backchannel(_interrupted(QUESTION), "   ", typed=False)


def test_typed_substantive_and_uninterrupted_turns_never_resume() -> None:
    assert not resume_after_backchannel(_interrupted("Got it."), "Yes.", typed=True)
    assert not resume_after_backchannel(_interrupted("Got it."), "Yes, but we used four zones.", typed=False)
    assert not resume_after_backchannel(None, "Yes.", typed=False)
