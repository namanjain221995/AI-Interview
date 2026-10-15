"""The interviewer never says "actually" (owner report: "how did you actually ..." reads as doubting them)."""

from __future__ import annotations

import pytest

from app.services.interview.speech import ToneFilter


def stream(text: str, size: int) -> str:
    f = ToneFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size))
    return out + f.flush()


@pytest.mark.parametrize("size", [1, 3, 7, 100])
@pytest.mark.parametrize(
    "said,heard",
    [
        ("Which role actually involved those three clusters?", "Which role involved those three clusters?"),
        ("How did you actually handle a node failure?", "How did you handle a node failure?"),
        ("Actually, how did you measure it?", "How did you measure it?"),
        ("Thanks. Actually, what changed after that?", "Thanks. What changed after that?"),
        ("What was the factual basis for that number?", "What was the factual basis for that number?"),
        ("How did you size the node groups?", "How did you size the node groups?"),
    ],
)
def test_actually_is_dropped_whatever_the_chunking(said: str, heard: str, size: int) -> None:
    assert stream(said, size) == heard
