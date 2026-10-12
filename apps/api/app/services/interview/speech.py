"""Spoken-style rules for interviewer speech.

* :func:`style_issues` - deterministic detection of praise / judgement of the
  candidate's answer, critique of the answer, comments about speech
  recognition and narration of intent (logged as decision deviations).
* :class:`OpenerFilter` - streaming guard for the first sentence of a turn.
  Models like to open with an evaluative acknowledgement ("That sounds like a
  solid, tested approach.", "Your answer was quite abstract.") even when told
  not to; speech is spoken as it streams, so it cannot be corrected later.
  The filter holds back only openings that *can* become such a sentence
  (decided on the first one or two words, so ordinary openings such as "How",
  "What was", "Got it", "Okay, so" or "You mentioned" pass with no delay),
  waits for that first sentence to end, and drops it when it judges the
  answer. A neutral restatement ("Got it, a canary setup with Argo
  Rollouts.") is kept: that is the brief, specific acknowledgement wanted.
"""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_WORD = re.compile(r"[A-Za-z']+")

_STRONG_PRAISE = (
    r"great|excellent|fantastic|awesome|amazing|wonderful|brilliant|outstanding|superb|terrific|impressive|"
    r"impressed|perfect|perfectly|well done|nailed it|spot on"
)
_MILD = (
    r"solid|robust|thorough|comprehensive|smart|clever|elegant|mature|rigorous|thoughtful|sensible|pragmatic|"
    r"nice|good|strong|interesting|clear|reasonable|neat|cool|valid|fair|sound|well[- ]thought(?:[- ]out)?|"
    r"well[- ]structured|systematic|textbook|classic|clean|tidy|efficient|effective|elegant|significant|"
    r"detailed|helpful distinction|insightful|compelling|commendable|excellent"
)
_ACHIEVEMENT = (
    r"(?:that'?s|that is|what|sounds like)(?: a| an)?(?: really| very| pretty| quite)? (?:significant|big|huge|major|"
    r"real|meaningful|substantial|massive|nice|great|solid|impressive) (?:improvement|win|achievement|reduction|"
    r"result|impact|gain|accomplishment|jump|drop|saving|savings|outcome|change)"
)
_THING = (
    r"approach|answer|plan|setup|set-up|example|point|question|practice|strategy|explanation|thinking|choice|"
    r"solution|design|way|framework|process|decision|call|foundation|method|methodology|architecture|response|"
    r"baseline|overview|walkthrough|rundown|breakdown|summary|account|safeguard|pattern|habit|instinct|result"
)
PRAISE = re.compile(
    rf"\b(?:{_STRONG_PRAISE})\b"
    rf"|\b(?:that'?s|that is|this is|it'?s|it is|sounds like|seems like|looks like|what)(?: a| an)?"
    rf"(?: really| very| pretty| quite| such a| such an)? (?:{_MILD})\b"
    rf"|\b(?:{_MILD}) (?:{_THING})\b"
    rf"|\b{_ACHIEVEMENT}\b"
    rf"|\b(?:is|was|are|were|looks|sounds|seems)(?: a| an)?(?: really| very| pretty| quite)? (?:{_MILD})"
    rf"(?: [\w-]+){{0,2}} (?:{_THING}|practice|practices|choice|move|idea)\b"
    rf"|\b(?:is|was|are|were|looks|sounds|seems)(?: really| very| pretty| quite)? (?:{_MILD}|{_STRONG_PRAISE})\s*[.!,]"
    r"|\b(?:sounds|looks|seems)(?: like)?(?: a| an)?(?: really| very| pretty| quite)? (?:resilient|reliable|"
    r"scalable|secure|safe|sensible|reasonable|effective|efficient|mature|well[- ]designed|well[- ]run|"
    r"well[- ]architected|battle[- ]tested|tested)\b"
    r"|\bi (?:really )?(?:like|love|appreciate) (?:that|how|this|your)\b"
    r"|\bi (?:really )?(?:appreciate|value) the (?:clarity|clarification|detail|details|honesty|candou?r|transparency|"
    r"openness|thoroughness|precision|context|specifics)\b"
    r"|\b(?:thanks|thank you) for (?:being|your) (?:so )?(?:honest|honesty|upfront|transparent|transparency|candid|"
    r"candou?r|open)\b"
    r"|\b(?:handled|described|explained|covered|answered|articulated|laid out|walked me through) "
    r"(?:[\w'-]+ ){0,6}(?:well|nicely|clearly|thoroughly|perfectly)\b",
    re.IGNORECASE,
)
CRITIQUE = re.compile(
    r"\byour (?:answer|response|explanation|description)s? (?:was|were|is|are|felt|feels|seemed|seems|sounded|"
    r"sounds)\b"
    r"|\b(?:feels|felt|sounds|sounded|seems|seemed|was|is|stays|remains) (?:a bit |quite |rather |a little |"
    r"pretty |very |still |somewhat )?(?:abstract|vague|generic|high[- ]level|unclear|thin|light on detail|"
    r"confusing|contradictory|incomplete|off[- ]topic)\b"
    r"|\bthat doesn'?t (?:really )?(?:explain|answer|address|tell me)\b"
    r"|\b(?:isn'?t|is not|wasn'?t|was not|aren'?t|are not) (?:really )?(?:a |an )?(?:true|real|proper|actual)\b"
    r"|\byou (?:didn'?t|did not|haven'?t|have not) (?:really )?(?:answer|address|explain|name|mention|say|specify|"
    r"give|provide|cover)\b"
    r"|\b(?:that'?s|that is|you'?re|you are) (?:exactly |absolutely |not )?(?:right|correct|wrong|incorrect)\b"
    r"|^\s*(?:exactly|correct|precisely)\s*[.,!]",
    re.IGNORECASE,
)
ASR_COMMENT = re.compile(
    r"\b(?:did you mean|i think you meant|you probably meant|transcri\w+|speech recognition|misheard|"
    r"mis-?recogni\w+|typo|mispronounc\w+)\b",
    re.IGNORECASE,
)
NARRATION = re.compile(
    r"\bto (?:verify|validate|assess|evaluate|gauge)\b|\bi need to (?:understand|verify|assess)\b"
    r"|\bfor my (?:assessment|evaluation|notes)\b|\bi(?:'d| would) like to (?:verify|assess|evaluate|validate)\b"
    r"|\bmy next question\b|\bto get a (?:clearer|better|fuller) (?:picture|sense|idea|understanding)\b",
    re.IGNORECASE,
)
_PLEASANTRY = re.compile(
    r"\b(?:have|enjoy) (?:a|the) (?:great|good|nice|wonderful|fantastic) (?:day|evening|weekend|rest of (?:your|the) day)\b"
    r"|\bgreat rest of your day\b",
    re.IGNORECASE,
)

# First words that can start an evaluative acknowledgement; anything else is
# emitted at once. A second word from _SAFE_SECOND also releases immediately.
_RISKY_FIRST = frozenset(
    {
        "that", "that's", "thats", "this", "great", "excellent", "nice", "solid", "good", "perfect",
        "impressive", "wonderful", "fantastic", "awesome", "amazing", "brilliant", "love", "sounds", "it",
        "it's", "what", "you", "you've", "your", "i", "thanks", "thank", "okay", "ok", "alright", "interesting",
        "cool", "really", "very", "exactly", "correct", "precisely", "wow", "fair", "clearly",
    }
)  # fmt: skip
_QUESTION_WORDS = {"so", "and", "now", "how", "what", "which", "when", "why", "where", "who", "can", "could"}
_SAFE_SECOND: dict[str, set[str]] = {
    "what": {"was", "were", "is", "are", "did", "do", "does", "would", "happened", "made", "kind", "part", "tools", "if"},
    "you": {"mentioned", "said", "talked", "noted", "brought", "had", "were", "ran", "used", "led", "chose", "built"},
    "you've": {"mentioned", "said", "talked"},
    "your": {"resume", "cv", "profile"},
    "i": {"want", "wanted", "see", "understand", "follow", "hear", "noticed"},
    "okay": _QUESTION_WORDS | {"let's", "lets", "switching", "shifting", "moving", "on", "tell", "walk"},
    "ok": _QUESTION_WORDS | {"let's", "lets", "switching", "shifting", "moving", "on", "tell", "walk"},
    "alright": _QUESTION_WORDS | {"let's", "lets", "switching", "shifting", "moving", "on", "tell", "walk"},
}  # fmt: skip
_MAX_HOLD_CHARS = 180


def style_issues(text: str) -> list[str]:
    """Deviation codes for interviewer speech (logged, never raised)."""
    cleaned = _PLEASANTRY.sub(" ", text)
    issues: list[str] = []
    if PRAISE.search(cleaned):
        issues.append("praise")
    if CRITIQUE.search(cleaned):
        issues.append("critique")
    if ASR_COMMENT.search(cleaned):
        issues.append("asr_comment")
    if NARRATION.search(cleaned):
        issues.append("narration")
    return issues


def is_evaluative_sentence(sentence: str) -> bool:
    """True for a short acknowledgement sentence that judges the answer (not a question)."""
    s = sentence.strip()
    if not s or s.endswith("?"):
        return False
    if len(_WORD.findall(s)) > 18:
        return False
    s = _PLEASANTRY.sub(" ", s)
    return bool(PRAISE.search(s) or CRITIQUE.search(s))


_ACK_WORDS = (
    "okay", "ok", "alright", "all right", "right", "got it", "sure", "great", "thanks", "thank you", "understood",
    "i see", "good", "perfect", "nice", "cool", "makes sense",
)  # fmt: skip
_ACK_ECHO = re.compile(
    r"^\s*(?:" + "|".join(re.escape(w) for w in sorted(_ACK_WORDS, key=len, reverse=True)) + r")\s*[,.!;:\u2013\u2014-]+\s*",
    re.IGNORECASE,
)


_CHALLENGE = re.compile(r"\b(actually)\b,?\s*", re.IGNORECASE)
_TRAILING_WORD = re.compile(r"[A-Za-z]+$")


class ToneFilter:
    """Streaming filter that removes "actually" from interviewer speech.

    "How did you actually handle ...?" / "Which role actually involved ...?" reads as doubting the candidate (owner
    report); the speech rules forbid it but the model still used it. The word carries no content in a question, so
    it is dropped deterministically ("Actually, how ..." -> "How ..."). Only a trailing partial word is held
    between chunks (it may be the start of "actually")."""

    def __init__(self) -> None:
        self._held = ""
        self._strip = False  # a word was just dropped: the next text starts without its leading space
        self._capitalize = False  # ... and with a capital letter (the dropped word opened a sentence)
        self.dropped = 0

    def feed(self, text: str) -> str:
        buffer = self._held + text
        partial = _TRAILING_WORD.search(buffer)
        cut = partial.start() if partial else len(buffer)
        out, self._held = buffer[:cut], buffer[cut:]
        return self._clean(out)

    def flush(self) -> str:
        out, self._held = self._held, ""
        return self._clean(out)

    def _clean(self, text: str) -> str:
        out: list[str] = []
        pos = 0
        for match in _CHALLENGE.finditer(text):
            out.append(self._carry(text[pos : match.start()]))
            pos = match.end()
            self.dropped += 1
            self._strip = True
            self._capitalize = match.group(1)[0] == "A"  # sentence-initial "Actually, how ..." -> "How ..."
        out.append(self._carry(text[pos:]))
        return "".join(out)

    def _carry(self, text: str) -> str:
        """Apply what a dropped word left pending to the text that follows it."""
        if self._strip:
            text = text.lstrip()
            if not text:
                return ""
            self._strip = False
        if self._capitalize and text:
            text = text[0].upper() + text[1:]
            self._capitalize = False
        return text


class AckEchoFilter:
    """Streaming filter for a turn the runtime already acknowledged out loud ("Okay." right after the answer): a
    bare acknowledgement the reply starts with ("Right, so ...", "Got it. What ...") is dropped, so the candidate
    never hears two in a row. Anything else passes through unchanged."""

    def __init__(self) -> None:
        self._buffer = ""
        self._decided = False
        self.dropped: str | None = None

    def _may_still_be_ack(self, text: str) -> bool:
        lowered = text.strip().lower()
        if not lowered:
            return True
        for word in _ACK_WORDS:
            if word.startswith(lowered):
                return True  # the word is still streaming ("Rig" ...)
            if lowered.startswith(word) and not lowered[len(word) :].strip():
                return True  # the word is complete, its punctuation not yet
        return False

    def feed(self, text: str) -> str:
        if self._decided:
            return text
        self._buffer += text
        match = _ACK_ECHO.match(self._buffer)
        if match:
            rest = self._buffer[match.end() :]
            if not rest.strip():
                return ""  # wait for what follows the acknowledgement
            self._decided = True
            self.dropped = match.group(0).strip()
            self._buffer = ""
            rest = rest.lstrip()
            return rest[:1].upper() + rest[1:]
        if self._may_still_be_ack(self._buffer):
            return ""
        return self._release()

    def _release(self) -> str:
        self._decided = True
        out, self._buffer = self._buffer, ""
        return out

    def flush(self) -> str:
        """End of stream: an acknowledgement that is the whole utterance is kept (something must be said)."""
        if self._decided:
            return ""
        return self._release()


class QuestionLimiter:
    """Streaming guard: an asking turn ends at its first question. Text after the first
    question mark (a second question, an extra request) is not spoken."""

    def __init__(self) -> None:
        self.done = False
        self.dropped = ""

    def feed(self, text: str) -> str:
        if self.done:
            self.dropped += text
            return ""
        index = text.find("?")
        if index < 0:
            return text
        self.done = True
        self.dropped += text[index + 1 :]
        return text[: index + 1]


class OpenerFilter:
    """Streaming filter that drops an evaluative first sentence (see module doc)."""

    def __init__(self) -> None:
        self._buffer = ""
        self._decided = False
        self.dropped: str | None = None

    def _release(self) -> str:
        self._decided = True
        out, self._buffer = self._buffer, ""
        return out

    def _needs_hold(self, text: str) -> bool | None:
        """True = hold until the first sentence ends, False = release now,
        None = not enough words yet to decide."""
        words = [w.lower() for w in _WORD.findall(text)]
        if words and (text[-1:].isalpha() or text[-1:] == "'"):
            words = words[:-1]  # the last word may still be streaming ("Exa" + "ctly")
        if not words:
            return None
        first = words[0]
        if first not in _RISKY_FIRST:
            return False
        safe = _SAFE_SECOND.get(first)
        if safe:
            if len(words) < 2:
                return None
            if words[1] in safe:
                return False
        return True

    def feed(self, text: str) -> str:
        if self._decided:
            return text
        self._buffer += text
        hold = self._needs_hold(self._buffer)
        if hold is None:
            return ""
        if hold is False:
            return self._release()
        end = _SENTENCE_END.search(self._buffer)
        if end is None:
            return self._release() if len(self._buffer) > _MAX_HOLD_CHARS else ""
        first = self._buffer[: end.end()]
        rest = self._buffer[end.end() :]
        if not is_evaluative_sentence(first):
            return self._release()
        if rest.strip():
            self.dropped = first.strip()
            self._decided = True
            self._buffer = ""
            return rest.lstrip()
        # Only the evaluative opener has arrived: wait for what follows it.
        return self._release() if len(self._buffer) > _MAX_HOLD_CHARS else ""

    def flush(self) -> str:
        """End of stream: release whatever is held (an evaluative opener that
        is the whole utterance is kept - something must be said)."""
        if self._decided:
            return ""
        return self._release()


__all__ = [
    "ASR_COMMENT",
    "AckEchoFilter",
    "CRITIQUE",
    "NARRATION",
    "PRAISE",
    "OpenerFilter",
    "QuestionLimiter",
    "ToneFilter",
    "is_evaluative_sentence",
    "style_issues",
]
