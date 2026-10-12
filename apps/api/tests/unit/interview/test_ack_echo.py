"""Tier 1: after the runtime's "Okay." the reply never starts with a second bare acknowledgement."""

from __future__ import annotations

import pytest

from app.services.interview.speech import AckEchoFilter


def _stream(chunks: list[str]) -> str:
    f = AckEchoFilter()
    out = "".join(f.feed(c) for c in chunks)
    return out + f.flush()


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (["Right, so how", " did you roll it back?"], "So how did you roll it back?"),
        (["Got", " it. What", " was the blast radius?"], "What was the blast radius?"),
        (["Okay. ", "How did you measure the 40% saving?"], "How did you measure the 40% saving?"),
        (["Thanks", " - which metric did you watch first?"], "Which metric did you watch first?"),
        (["Makes sense. And who owned", " the rollout?"], "And who owned the rollout?"),
    ],
)
def test_a_bare_acknowledgement_at_the_start_is_dropped(chunks: list[str], expected: str) -> None:
    assert _stream(chunks) == expected


@pytest.mark.parametrize(
    "chunks",
    [
        ["Rightsizing the nodes, how did you decide the instance types?"],
        ["Okay so walk me through", " the canary analysis."],  # no punctuation: a sentence, not an acknowledgement
        ["Sure enough the cache warmed up?"],
        ["How did you handle the failover?"],
        ["Great"],  # the whole utterance: kept, something must be said
    ],
)
def test_anything_else_passes_unchanged(chunks: list[str]) -> None:
    assert _stream(chunks) == "".join(chunks)


def test_only_the_first_acknowledgement_is_dropped() -> None:
    assert _stream(["Okay, ", "okay, let's move on."]) == "Okay, let's move on."


def test_the_model_is_told_the_acknowledgement_was_already_said() -> None:
    from app.services.interview.contracts import CandidateTurn
    from app.services.interview.engine import InterviewEngine

    from .factories import ask, round_config, state

    s = state(rc=round_config(), elapsed_s=5 * 60)
    ask(s, "kubernetes")
    engine = InterviewEngine()
    plain = CandidateTurn(text="We used Argo Rollouts with a canary.", started_ms=0, ended_ms=4000)
    acknowledged = CandidateTurn(
        text="We used Argo Rollouts with a canary.", started_ms=0, ended_ms=4000, acknowledged="Okay."
    )
    assert engine._interruption_note(s, plain) is None
    note = engine._interruption_note(s, acknowledged) or ""
    assert '"Okay."' in note and "do not acknowledge" in note
