"""Streaming guard for what the interviewer attributes to the candidate.

Speech is spoken while it streams, so an invented "Earlier you noted X" cannot be taken back afterwards (the
post-check in the engine can only log it and correct the next turn). The guard releases text as it arrives,
except a sentence that OPENS with an attribution - "You mentioned ...", "Earlier you said ...", "As you
described, ...", "Your answer about ...", also after a short acknowledgement ("Okay, so you mentioned ...").
That sentence is held until it ends, or until the question starts inside it ("You mentioned X, so how ...?"),
and then:

* released unchanged when the candidate's own turns support it (:mod:`grounding`);
* dropped when it is unsupported and only a statement before the question, so the question is still asked;
* replaced by a regeneration when it is unsupported and the question depends on it (the attribution sits
  inside the question, or the move is an inconsistency probe): the turn's stream restarts once with a
  correction (:attr:`AttributionGuard.regenerate`); without a regeneration left it is dropped;
* rewritten into a short acknowledgement that keeps the specific ("Right, Karpenter for node provisioning.")
  when it is a supported "You mentioned X." preamble and the same frame was used in one of the interviewer's
  last three questions - a person does not open every follow-up with "You mentioned ...".

Holding costs the time the held sentence takes to stream (about 0.2-0.3 s) on turns that open with an
attribution; any other sentence passes through as it arrives. References framed as the resume are not held
(the claim text is in the directive); the engine's post-check covers them and mid-sentence attributions.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable

from app.services.interview.grounding import GroundingContext, Reference, find_references
from app.services.interview.state import InterviewState

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
# A sentence end followed by whitespace (a "." at the end of the buffer may still become "3.5").
_SENTENCE_END = re.compile(r"[.!?](?=\s)")
_FINAL_END = re.compile(r"[.!?]\s*$")
# The question starts inside the held sentence: "You mentioned X, so how ...", "You noted X; what ...".
_CLAUSE_END = re.compile(
    r"(?:[,;:]|\s[-–—])\s+(?:and\s+|so\s+|but\s+|then\s+)?(?=(?:how|what|which|why|when|where|who|"
    r"could|can|would|will|did|do|does|is|are|was|were|should|tell|walk|help|talk)\b)",
    re.IGNORECASE,
)
_ACK_WORDS = frozenset(
    {
        "okay",
        "ok",
        "alright",
        "right",
        "so",
        "and",
        "got",
        "it",
        "thanks",
        "thank",
        "now",
        "well",
        "mm",
        "sure",
    }
)
_ATTR_FIRST = frozenset(
    {"mentioned", "said", "told", "described", "noted", "brought", "talked", "explained", "pointed",
     "indicated", "stated", "referred", "walked"}
)  # fmt: skip
_ANSWER_NOUNS = frozenset({"answer", "point", "comment", "example", "description", "story"})
_LEADING_CONNECTIVE = re.compile(r"^\s*(?:(?:and|so|but|then)\s+)?", re.IGNORECASE)
#: Hold at most this many characters (an unusually long sentence is judged on what has arrived).
MAX_HOLD_CHARS = 280
#: Longest specific kept when a repeated "You mentioned X." preamble is turned into an acknowledgement.
REWRITE_MAX_WORDS = 14
ACK_REWRITES = ("Right", "Okay", "So")

FRAME = re.compile(
    r"\b(?:you(?:'ve| have)?\s+(?:also\s+|just\s+)?(?:mentioned|said|noted|described|told me|talked about|"
    r"brought up)|earlier,?\s+you|as you (?:said|mentioned|described|noted))\b",
    re.IGNORECASE,
)


def attribution_frame_recent(state: InterviewState, *, turns: int = 3) -> bool:
    """True when one of the interviewer's last ``turns`` questions used a "you mentioned"-type frame."""
    replies = [
        t.text
        for t in state.conversation
        if t.speaker == "interviewer" and t.text and not t.interrupted and "?" in t.text
    ][-turns:]
    return any(FRAME.search(r) for r in replies)


def previous_opener(state: InterviewState) -> str | None:
    for t in reversed(state.conversation):
        if t.speaker == "interviewer" and t.text:
            words = _WORD.findall(t.text.lower())
            return words[0] if words else None
    return None


def _words(text: str) -> list[str]:
    """Complete words only (the last one may still be streaming)."""
    lowered = text.lower().replace("’", "'")
    words = _WORD.findall(lowered)
    if words and (lowered[-1:].isalnum() or lowered[-1:] == "'"):
        words = words[:-1]
    return words


def opens_with_attribution(words: list[str]) -> bool | None:
    """True / False, or None while the first words cannot decide yet."""
    i = 0
    while i < len(words) and i < 3 and words[i] in _ACK_WORDS:
        i += 2 if words[i] == "thank" and i + 1 < len(words) and words[i + 1] == "you" else 1
    if i >= len(words):
        return None
    first, rest = words[i], words[i + 1 : i + 4]
    if first in ("earlier", "previously"):
        return None if not rest else rest[0] in ("you", "you've")
    if first in ("you", "you've", "youve"):
        if not rest:
            return None
        if rest[0] in ("also", "just", "earlier", "previously", "have"):
            return None if len(rest) < 2 else rest[1] in _ATTR_FIRST
        if rest[0] == "were":
            return None if len(rest) < 2 else rest[1] == "saying"
        return rest[0] in _ATTR_FIRST
    if first == "as":
        if len(rest) < 2:
            return None
        return rest[0] == "you" and rest[1] in _ATTR_FIRST
    if first == "your":
        if not rest:
            return None
        if rest[0] in ("earlier", "previous", "last"):
            return None if len(rest) < 2 else rest[1] in _ANSWER_NOUNS
        return rest[0] in _ANSWER_NOUNS
    return False


class AttributionGuard:
    """See module doc. ``context`` builds the grounding sources lazily (first held sentence only)."""

    def __init__(
        self,
        context: Callable[[], GroundingContext],
        *,
        frame_recent: bool = False,
        probe: bool = False,
        allow_regenerate: bool = True,
        seed: str = "",
        avoid_ack: str | None = None,
        released_before: bool = False,
        after_ack: bool = False,
    ) -> None:
        self._context_factory = context
        self._context: GroundingContext | None = None
        self.frame_recent = frame_recent
        self.probe = probe
        self.allow_regenerate = allow_regenerate
        self._seed = seed
        self._avoid_ack = (avoid_ack or "").lower()
        self._buffer = ""
        self._mode = "start"  # start | pass | hold | done
        self._fix_start = False  # capitalize the question after a dropped attribution clause
        self.released_any = released_before
        # The realtime layer already said a short acknowledgement out loud: a rewrite adds none of its own.
        self.after_ack = after_ack
        self.unsupported: Reference | None = None
        self.dropped: list[str] = []
        self.rewritten: str | None = None
        self.regenerate: Reference | None = None
        self.held = 0
        self.hold_ms = 0.0
        self._hold_started: float | None = None

    # ------------------------------------------------------------ helpers
    def context(self) -> GroundingContext:
        if self._context is None:
            self._context = self._context_factory()
        return self._context

    def _ack(self) -> str:
        options = [a for a in ACK_REWRITES if a.lower() != self._avoid_ack] or list(ACK_REWRITES)
        digest = int(hashlib.sha1(self._seed.encode()).hexdigest(), 16)  # noqa: S324 - variation only
        return options[digest % len(options)]

    def _out(self, text: str) -> str:
        if text and not self.released_any:
            text = text.lstrip()  # nothing spoken yet (e.g. after a dropped first sentence)
        if text and self._fix_start:
            stripped = _LEADING_CONNECTIVE.sub("", text.lstrip(), count=1)
            if stripped:
                text = stripped[0].upper() + stripped[1:]
                self._fix_start = False
            else:
                return ""
        if text.strip():
            self.released_any = True
        if "?" in text:
            self._mode = "done"
        return text

    def _rewrite(self, unit: str, ref: Reference) -> str | None:
        """'You mentioned X.' -> 'Right, X.' (None: drop the preamble instead)."""
        phrase = ref.phrase.strip().rstrip(".")
        words = phrase.split()
        if not 2 <= len(words) <= REWRITE_MAX_WORDS or " as " in f" {phrase.lower()} ":
            return None  # too short / long, or "You described X as Y." (no natural acknowledgement form)
        trailing = unit[len(unit.rstrip()) :]
        if self.released_any or self.after_ack:
            return phrase[0].upper() + phrase[1:] + "." + trailing
        lead = _words(unit[: ref.start] + " ")
        ack = next((w for w in lead if w in ("okay", "ok", "alright", "right", "so")), None)
        ack_text = ack.capitalize() if ack and ack != "ok" else ("OK" if ack == "ok" else self._ack())
        return f"{ack_text}, {phrase}." + trailing

    def _judge(self, unit: str, kind: str) -> str:
        """Decide a held unit (a whole sentence, or the attribution clause before the question)."""
        if self._hold_started is not None:
            self.hold_ms += (time.monotonic() - self._hold_started) * 1000.0
            self._hold_started = None
        self.held += 1
        ctx = self.context()
        bad = ctx.unsupported(unit)
        if bad is not None:
            self.unsupported = self.unsupported or bad
            depends = self.probe or kind == "clause" or unit.rstrip().endswith("?")
            if depends and self.allow_regenerate:
                self.regenerate = bad
                return ""
            self.dropped.append(unit.strip())
            if kind == "clause":
                self._fix_start = True
            return ""
        if (
            self.frame_recent
            and not self.probe
            and kind == "sentence"
            and unit.rstrip().endswith(".")
            and self.rewritten is None
        ):
            refs = [r for r in find_references(unit) if not r.resume]
            if refs and refs[0].start <= 30:
                replaced = self._rewrite(unit, refs[0])
                self.rewritten = unit.strip()
                if replaced is None:
                    self.dropped.append(unit.strip())
                    return ""
                return replaced
        return unit

    # ------------------------------------------------------------ streaming
    def feed(self, text: str) -> str:
        if self.regenerate is not None:
            return ""
        if self._mode == "done":
            return self._out(text)
        self._buffer += text
        out: list[str] = []
        while self._buffer and self.regenerate is None:
            if self._mode == "done":
                out.append(self._out(self._buffer))
                self._buffer = ""
                break
            if self._mode == "pass":
                end = _SENTENCE_END.search(self._buffer)
                if end is None:
                    out.append(self._out(self._buffer))
                    self._buffer = ""
                    break
                out.append(self._out(self._buffer[: end.end()]))
                self._buffer = self._buffer[end.end() :]
                if self._mode != "done":
                    self._mode = "start"
                continue
            if self._mode == "start":
                if not self._buffer.strip():
                    out.append(self._out(self._buffer))
                    self._buffer = ""
                    break
                decision = opens_with_attribution(_words(self._buffer))
                if decision is None:
                    if _SENTENCE_END.search(self._buffer) or len(self._buffer) > 60:
                        decision = False
                    else:
                        break
                self._mode = "hold" if decision else "pass"
                if decision:
                    self._hold_started = time.monotonic()
                continue
            # hold
            sentence = _SENTENCE_END.search(self._buffer)
            clause = _CLAUSE_END.search(self._buffer)
            if clause is not None and (sentence is None or clause.start() < sentence.start()):
                unit, self._buffer = self._buffer[: clause.end()], self._buffer[clause.end() :]
                out.append(self._out(self._judge(unit, "clause")))
                if self._mode != "done":
                    self._mode = "pass"
                continue
            if sentence is not None:
                unit, self._buffer = self._buffer[: sentence.end()], self._buffer[sentence.end() :]
                out.append(self._out(self._judge(unit, "sentence")))
                if self._mode != "done":
                    self._mode = "start"
                continue
            if len(self._buffer) > MAX_HOLD_CHARS:
                unit, self._buffer = self._buffer, ""
                out.append(self._out(self._judge(unit, "sentence")))
                self._mode = "pass" if self._mode != "done" else "done"
            break
        return "".join(out)

    def flush(self) -> str:
        """End of stream: judge a held sentence that ended without a following space."""
        if self.regenerate is not None or not self._buffer:
            self._buffer = ""
            return ""
        rest, self._buffer = self._buffer, ""
        if self._mode == "hold" or (self._mode == "start" and opens_with_attribution(_words(rest + " "))):
            judged = self._judge(rest, "sentence")
            return self._out(judged)
        return self._out(rest)


def guard_for_turn(
    state: InterviewState,
    *,
    probe: bool,
    allow_regenerate: bool = True,
    released_before: bool = False,
    after_ack: bool = False,
) -> AttributionGuard:
    return AttributionGuard(
        lambda: GroundingContext.from_state(state),
        frame_recent=attribution_frame_recent(state),
        probe=probe,
        allow_regenerate=allow_regenerate,
        seed=f"{state.session_id}:{state.turn_index}",
        avoid_ack=previous_opener(state),
        released_before=released_before,
        after_ack=after_ack,
    )


__all__ = [
    "ACK_REWRITES",
    "AttributionGuard",
    "FRAME",
    "attribution_frame_recent",
    "guard_for_turn",
    "opens_with_attribution",
    "previous_opener",
]
