"""Adaptive end-of-turn detection (REALTIME_DESIGN.md section 4).

Two stages, so a pause in the middle of a thought never ends the answer:

1. **Provisional end** (:meth:`Endpointer.decide`, on every audio frame) on the
   text known so far (ordered segment finals + the latest partial):

   * ``endpoint_silence_short_ms`` (600 ms) when the text ends a complete
     sentence, has >= 6 words AND is settled (no segment still being
     transcribed - otherwise the words just spoken are unknown);
   * ``endpoint_fragment_hold_ms`` (4 s) for a short fragment that stops
     mid-sentence: at most 5 words ending with a continuation word ("so for
     that", "and the", "I think it was because", "okay so"); live intake
     2026-10-10: "so for that" + a thinking pause got a new question after
     1.7 s. The engine then answers a fragment with a soft nudge, never a new
     question (``app.services.interview.understanding``);
   * ``endpoint_silence_long_ms`` (1.7 s) when it ends with a conjunction or a
     filler ("and", "so", "but", "because", "um", "uh", "like" ...), even if
     Whisper put a period after it, or has fewer than 3 words;
   * ``think_pause_max_seconds`` when the candidate asked for time ("let me
     think", "give me a moment");
   * ``endpoint_silence_ms`` (850 ms) otherwise.

   The runtime then acknowledges and transcribes the last segment.

2. **Commit** (:func:`commit_required_ms`) once every segment is transcribed:
   the turn reaches the engine only when the silence also satisfies the
   FINAL text - long (1.7 s) when it is incomplete (trailing conjunction or
   filler, no terminal punctuation, fewer than 3 words), the think pause when
   the candidate asked for time. Speech that resumes before the commit merges
   back into the same answer.

Measured 2026-10-07 (tests/integration/realtime/test_stt_harness.py, answers
with 0.8-2.5 s hesitations): the provisional end alone cut 5 of 7 answers
off mid-thought, because the last segment's transcript was not back yet.

``control.end_turn`` ("Done answering") ends the turn immediately - no commit
wait - and ``max_utterance_seconds`` forces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EndpointReason = Literal[
    "short_complete",
    "base",
    "long_trailing",
    "long_short_text",
    "fragment",
    "think_pause",
    "max_utterance",
    "manual",
]

CONJUNCTIONS_AND_FILLERS: frozenset[str] = frozenset(
    {
        "and",
        "so",
        "but",
        "because",
        "um",
        "uh",
        "umm",
        "uhm",
        "erm",
        "er",
        "ah",
        "like",
        "or",
        "then",
        "also",
        "which",
        "that",
        "if",
        "when",
        "while",
        "although",
        "though",
        "since",
        "unless",
        "plus",
        "the",
        "a",
        "an",
        "to",
        "of",
        "with",
        "for",
        "my",
        "our",
        "is",
        "was",
        "were",
    }
)

#: Continuation words that leave a SHORT turn unfinished. Not fillers ("um" alone is hesitation) and not words that
#: often end a short complete answer ("I did that.", "It was.", "Thanks for that.").
FRAGMENT_TAILS: frozenset[str] = frozenset(
    "and so but because like or also plus then which if when while although though since unless the a an my our "
    "to of with".split()
)
FRAGMENT_MAX_WORDS = 5
#: "so for that", "and about this": a discourse marker + a pointer, no verb yet.
_FRAGMENT_POINTER = re.compile(
    r"^(?:(?:and|but|so|okay|ok|yeah|yes|um|uh|like)\s+){1,2}(?:for|about|regarding|on|in|with)\s+(?:that|this|it)$"
)

_THINK = re.compile(
    r"\b(?:let me (?:think|see|check|recall|remember)|give me (?:a|one) (?:moment|second|sec|minute|bit)|"
    r"(?:one|just a) (?:moment|second|sec|minute)|hold on|bear with me|let me just think|"
    r"i need (?:a|to) (?:moment|think)|thinking about (?:it|that|this)|good question,? let me)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s*$")
THINK_TAIL_CHARS = 80


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def ends_complete_sentence(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped or stripped.endswith("..."):
        return False
    return bool(_SENTENCE_END.search(stripped))


def asks_for_time(text: str) -> bool:
    tail = text[-THINK_TAIL_CHARS:]
    return bool(_THINK.search(tail))


def is_short_fragment(tokens: list[str]) -> bool:
    """A few words that stop mid-sentence ("so for that", "and the", "okay so"): the candidate is still
    formulating, so the turn waits longer (``fragment_ms``)."""
    if not 0 < len(tokens) <= FRAGMENT_MAX_WORDS:
        return False
    return tokens[-1] in FRAGMENT_TAILS or bool(_FRAGMENT_POINTER.match(" ".join(tokens)))


@dataclass(slots=True, frozen=True)
class EndpointParams:
    base_ms: int = 850
    short_ms: int = 600
    long_ms: int = 1700
    think_max_ms: int = 25_000
    max_utterance_ms: int = 240_000
    fragment_ms: int = 4000

    @classmethod
    def from_settings(cls, settings: object) -> EndpointParams:
        return cls(
            base_ms=int(getattr(settings, "endpoint_silence_ms", 850)),
            short_ms=int(getattr(settings, "endpoint_silence_short_ms", 600)),
            long_ms=int(getattr(settings, "endpoint_silence_long_ms", 1700)),
            fragment_ms=int(getattr(settings, "endpoint_fragment_hold_ms", 4000)),
            think_max_ms=int(getattr(settings, "think_pause_max_seconds", 25)) * 1000,
            max_utterance_ms=int(getattr(settings, "max_utterance_seconds", 240)) * 1000,
        )


def required_silence_ms(
    text: str, params: EndpointParams, *, settled: bool = True
) -> tuple[int, EndpointReason]:
    """Silence (ms) for the provisional end of turn, given the text so far.
    ``settled=False`` (a segment is still being transcribed) never allows the
    short wait: the newest words are not in ``text`` yet."""
    stripped = text.strip()
    if stripped and asks_for_time(stripped):
        return params.think_max_ms, "think_pause"
    tokens = words(stripped)
    if not tokens:
        return params.base_ms, "base"
    if is_short_fragment(tokens):
        return max(params.fragment_ms, params.long_ms), "fragment"
    if tokens[-1] in CONJUNCTIONS_AND_FILLERS:
        # "... and." is still unfinished: Whisper ends every segment with a period.
        return params.long_ms, "long_trailing"
    if len(tokens) < 3:
        return params.long_ms, "long_short_text"
    if settled and ends_complete_sentence(stripped) and len(tokens) >= 6:
        return params.short_ms, "short_complete"
    return params.base_ms, "base"


def commit_required_ms(text: str, params: EndpointParams) -> int:
    """Silence (ms) the FINAL transcript needs before the turn is committed to
    the engine (stage 2): incomplete-looking answers wait the long silence,
    a request for time the think pause, complete ones nothing extra."""
    stripped = text.strip()
    if not stripped:
        return 0
    if asks_for_time(stripped):
        return params.think_max_ms
    tokens = words(stripped)
    if not tokens:
        return 0
    if is_short_fragment(tokens):
        return max(params.fragment_ms, params.long_ms)
    if tokens[-1] in CONJUNCTIONS_AND_FILLERS or len(tokens) < 3 or not ends_complete_sentence(stripped):
        return params.long_ms
    return params.short_ms


@dataclass(slots=True)
class EndpointDecision:
    end: bool
    required_ms: int
    reason: EndpointReason


class Endpointer:
    """Stateless policy wrapper; the runtime feeds it the current numbers."""

    def __init__(self, params: EndpointParams) -> None:
        self.params = params

    def decide(
        self, text: str, *, silence_ms: int, turn_ms: int, speaking: bool, settled: bool = True
    ) -> EndpointDecision:
        if turn_ms >= self.params.max_utterance_ms:
            return EndpointDecision(True, 0, "max_utterance")
        required, reason = required_silence_ms(text, self.params, settled=settled)
        if speaking:
            return EndpointDecision(False, required, reason)
        return EndpointDecision(silence_ms >= required, required, reason)
