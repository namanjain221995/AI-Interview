"""Conversation floor ownership + barge-in helpers.

* :class:`TurnController` - the turn FSM (``app.domain.enums.TurnState``)
  with an explicit transition table. Invalid transitions are refused and
  logged; PAUSED and ENDED are reachable from anywhere.
* :func:`spoken_portion` - what the candidate actually heard of an utterance,
  from the played milliseconds and the chunk durations (REALTIME_DESIGN.md
  section 7).
* :func:`is_backchannel` / :func:`is_repeat_or_clarification` - cheap text
  classifiers used for barge-in resolution and acknowledgement skipping.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.core.logging import get_logger
from app.domain.enums import TurnState

log = get_logger(__name__)

S = TurnState

TRANSITIONS: dict[TurnState, frozenset[TurnState]] = {
    S.IDLE: frozenset({S.AI_SPEAKING, S.LISTENING, S.PROCESSING, S.CODING, S.CANDIDATE_SPEAKING}),
    S.AI_SPEAKING: frozenset({S.LISTENING, S.CANDIDATE_SPEAKING, S.CODING, S.PROCESSING}),
    S.LISTENING: frozenset({S.CANDIDATE_SPEAKING, S.PROCESSING, S.AI_SPEAKING, S.CODING}),
    S.CANDIDATE_SPEAKING: frozenset({S.PROCESSING, S.LISTENING, S.AI_SPEAKING, S.CODING}),
    S.PROCESSING: frozenset({S.AI_SPEAKING, S.LISTENING, S.CANDIDATE_SPEAKING, S.CODING, S.IDLE}),
    S.CODING: frozenset({S.CANDIDATE_SPEAKING, S.PROCESSING, S.AI_SPEAKING, S.LISTENING}),
    S.PAUSED: frozenset({S.IDLE, S.LISTENING, S.AI_SPEAKING, S.PROCESSING, S.CODING, S.CANDIDATE_SPEAKING}),
    S.ENDED: frozenset(),
}


class TurnController:
    """Tracks who holds the floor; ``on_change(state, previous, reason)`` is
    called after every accepted transition."""

    def __init__(self, on_change: Callable[[TurnState, TurnState, str | None], None] | None = None) -> None:
        self.state: TurnState = TurnState.IDLE
        self._on_change = on_change
        self.history: list[tuple[TurnState, str | None]] = []

    def can(self, target: TurnState) -> bool:
        if self.state is TurnState.ENDED:
            return False
        if target in (TurnState.PAUSED, TurnState.ENDED):
            return True
        return target is self.state or target in TRANSITIONS[self.state]

    def to(self, target: TurnState, reason: str | None = None) -> bool:
        if target is self.state:
            return True
        if not self.can(target):
            log.warning(
                "realtime.turn.invalid_transition",
                current=self.state.value,
                target=target.value,
                reason=reason,
            )
            return False
        previous = self.state
        self.state = target
        self.history.append((target, reason))
        if len(self.history) > 200:
            del self.history[:100]
        if self._on_change is not None:
            self._on_change(target, previous, reason)
        return True

    @property
    def ended(self) -> bool:
        return self.state is TurnState.ENDED


# ------------------------------------------------------------ spoken portion


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: int
    index: int
    text: str
    duration_ms: int
    offset_ms: int  # start of this chunk inside the utterance audio


FULLY_PLAYED_FRACTION = 0.9


def spoken_portion(chunks: Sequence[ChunkRecord], played_ms: int) -> tuple[str, int]:
    """Return ``(spoken_text, next_unplayed_index)``.

    Fully played chunks count entirely; the chunk being played when audio
    stopped contributes the proportional share of its words (rounded down),
    and is the one to re-speak on a backchannel resume."""
    parts: list[str] = []
    played_ms = max(0, int(played_ms))
    for chunk in sorted(chunks, key=lambda c: c.index):
        end = chunk.offset_ms + chunk.duration_ms
        if played_ms >= end or (
            chunk.duration_ms > 0 and played_ms - chunk.offset_ms >= FULLY_PLAYED_FRACTION * chunk.duration_ms
        ):
            parts.append(chunk.text)
            continue
        if played_ms > chunk.offset_ms and chunk.duration_ms > 0:
            fraction = (played_ms - chunk.offset_ms) / chunk.duration_ms
            tokens = chunk.text.split()
            take = int(len(tokens) * fraction)
            if take > 0:
                parts.append(" ".join(tokens[:take]))
        return " ".join(parts).strip(), chunk.index
    last = max((c.index for c in chunks), default=-1)
    return " ".join(parts).strip(), last + 1


# -------------------------------------------------------------- classifiers

BACKCHANNEL_WORDS: frozenset[str] = frozenset(
    {
        "mm",
        "mmm",
        "hmm",
        "hm",
        "mhm",
        "mmhmm",
        "mmhm",
        "uhhuh",
        "uh",
        "huh",
        "yeah",
        "yea",
        "yes",
        "yep",
        "yup",
        "okay",
        "ok",
        "kay",
        "sure",
        "right",
        "alright",
        "got",
        "it",
        "i",
        "see",
        "cool",
        "great",
        "true",
        "exactly",
        "of",
        "course",
        "makes",
        "sense",
        "aha",
        "ah",
        "oh",
        "nice",
        "good",
        "fine",
        "absolutely",
        "definitely",
        "totally",
        "indeed",
        "all",
        "thanks",
        "thank",
        "you",
    }
)
_NORMALIZE_BC = re.compile(r"[^a-z\s]")


def _bc_tokens(text: str) -> list[str]:
    cleaned = text.lower().replace("-", "").replace("’", "'")
    cleaned = _NORMALIZE_BC.sub(" ", cleaned)
    return cleaned.split()


def is_backchannel(text: str, *, max_words: int = 3) -> bool:
    """Empty, or <= 3 words that are all acknowledgement tokens ("mm-hmm",
    "yeah", "okay", "sure", "right", "got it")."""
    tokens = _bc_tokens(text)
    if not tokens:
        return True
    if len(tokens) > max_words:
        return False
    return all(t in BACKCHANNEL_WORDS for t in tokens)


_REPEAT = re.compile(
    r"\b(?:repeat|say (?:that|it) again|come again|rephrase|what do you mean|could you clarify|can you clarify|"
    r"clarify (?:that|the question)|pardon|didn'?t (?:catch|hear|get) (?:that|it|the question)|what was the question|"
    r"one more time|sorry,? what)\b",
    re.IGNORECASE,
)


def is_repeat_or_clarification(text: str) -> bool:
    return bool(_REPEAT.search(text or ""))
