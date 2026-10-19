"""Turn understanding: what a candidate utterance IS, decided before any interview item is consumed.

The owner's live intake session (f1dc3a1b, 2026-10-10) showed the cost of treating every utterance as an answer:
"Okay, so can you hear?" was taken as the self-introduction, "Can you hear me?" as the project answer and "But
first answer me, like, am I able to do it or not?" as the first résumé answer, while "so for that" (a candidate
pausing mid-sentence) got a new clarification question that credited them with a topic they never raised. A
human interviewer first decides what was said, then answers it:

* answer / partial answer -> the pending item is consumed as before;
* audio or connection check ("can you hear me", "am I audible", "hello?", "are you there") -> "Yes, I can hear
  you clearly." and the pending question again, in a short form;
* question about the interview itself ("should I start", "am I able to do it", "how long should I speak", "can I
  use notes", "are you an AI") -> a brief, honest answer from what the platform knows, then the question;
* readiness / acknowledgement only ("okay", "sure", "okay so") on an open question -> "Sure, go ahead." (wait);
* thinking ("let me think", "one second") -> "Of course, take your time." (wait);
* incomplete fragment ("so for that", "and the") -> keep listening: a soft nudge, never a new question;
* away / interruption ("sorry, someone is at the door") -> "No problem, take your time.";
* repeat, clarification, garbled, brief, "I don't know", role questions -> the existing flows (``special.py``).

Flow turns never consume the pending item and never create an ``answers`` row (the candidate transcript keeps
them). Deterministic patterns only (~0.1 ms per turn): the turn path has no budget for a classification round
trip before the reply streams (first speech ~0.6-0.9 s), a wrong flow reply is cheap (the candidate simply
answers next) while a swallowed audio check is exactly what the owner reported. Anything ambiguous stays an
answer; the turn model then sees it with the rule "if they asked you something, answer it first".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.enums import TurnAction
from app.services.interview.special import (
    SpecialKind,
    SpecialResult,
    classify,
    normalize,
    substance_words,
    words,
)


class TurnIntent(StrEnum):
    ANSWER = "answer"
    PARTIAL_ANSWER = (
        "partial_answer"  # a very short reply (special BRIEF): the existing one-clarification rule
    )
    AUDIO_CHECK = "audio_check"
    PROCESS_QUESTION = "process_question"
    READINESS = "readiness"
    REPEAT = "repeat"
    CLARIFY = "clarify"
    THINKING = "thinking"
    INCOMPLETE = "incomplete"
    OFF_TOPIC = "off_topic"
    GARBLED = "garbled"
    CANDIDATE_QUESTION = "candidate_question"  # role / company / program: job facts, or deferred in intake
    OTHER = "other"  # instruction, hint request, "no more questions", empty: the special handling


#: Intents answered with a deterministic reply that never consumes the pending item.
FLOW_INTENTS: frozenset[TurnIntent] = frozenset(
    {
        TurnIntent.AUDIO_CHECK,
        TurnIntent.PROCESS_QUESTION,
        TurnIntent.READINESS,
        TurnIntent.THINKING,
        TurnIntent.INCOMPLETE,
        TurnIntent.OFF_TOPIC,
    }
)
#: Flow replies per question before the turn goes the ordinary way (a candidate who keeps saying "okay" gets the
#: question rephrased instead of "go ahead" forever; repeated fragments are then treated as the answer).
FLOW_LIMITS: dict[TurnIntent, int] = {TurnIntent.READINESS: 2, TurnIntent.INCOMPLETE: 2}
#: Substance words after a leading audio check that make the turn a real answer ("Can you hear me? So I'm Heet,
#: I work at TechSara as an AI engineer ..."): answered as the item, with the audio acknowledgement in front.
MIXED_MIN_SUBSTANCE = 4
#: Process / audio questions are short; a long turn that contains such words is an answer about something else.
FLOW_MAX_WORDS = 18


@dataclass(frozen=True, slots=True)
class Understanding:
    intent: TurnIntent
    special: SpecialResult  # the underlying special-turn classification (unchanged semantics)
    topic: str | None = None  # audio: check | hello | connection | cant_hear_you; process: start | ...
    ack: str | None = None  # an answer that opened with an audio check: say this before the next question
    matched: str | None = None

    @property
    def flow(self) -> bool:
        return self.intent in FLOW_INTENTS

    @property
    def consumes_item(self) -> bool:
        """False for every turn that must leave the pending question pending."""
        return not self.flow and self.intent is not TurnIntent.REPEAT


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_YOU = r"(?:you|u|ya)"
# ------------------------------------------------------------------------------------------- audio checks
# Addressed to the interviewer ("you" / "me" / "my voice"): reliable anywhere at the start of a turn.
_AUDIO_STRONG = _rx(
    rf"\b(?:can|could|can't|cant|are|r)\s+{_YOU}\s+(?:\w+\s+){{0,2}}?(?:able\s+to\s+)?(?:hear|listen(?:\s+to)?)\s+"
    r"(?:me|my\s+voice|my\s+audio|us|what\s+i(?:'m|\s+am)\s+saying)\b",
    rf"^(?:\w+\s+){{0,3}}?(?:can|could|are)\s+{_YOU}\s+(?:able\s+to\s+)?hear(?:\s+(?:now|properly|clearly|okay|ok|"
    r"well|it|this|anything))?$",
    rf"\b{_YOU}\s+(?:can|are\s+able\s+to)\s+(?:hear|listen\s+to)\s+me\b",
    r"\bam\s+i\s+(?:\w+\s+)?(?:audible|clearly\s+audible|loud\s+enough|coming\s+through|being\s+heard|reaching\s+you)\b",
    r"^(?:\w+\s+){0,2}?am\s+i\s+(?:clear|heard)(?:\s+(?:now|enough|to\s+you))?$",
    r"\b(?:is|was)\s+my\s+(?:voice|audio|sound|mic|microphone)(?:\s+\w+){0,2}?\s+(?:audible|clear|coming|working|"
    r"reaching|okay|ok|fine|good|loud\s+enough|on|breaking|cutting)\b",
    r"\b(?:are|r)\s+(?:you|u)\s+(?:getting|receiving)\s+my\s+(?:voice|audio|sound)\b",
    rf"\b(?:are|r)\s+{_YOU}\s+(?:still\s+)?(?:there|with\s+me|listening)\b",
    rf"\bdid\s+{_YOU}\s+(?:hear|get)\s+(?:me|my\s+voice)\b",
    r"\b(?:is|was)\s+(?:the|my)\s+(?:connection|internet|network)(?:\s+\w+){0,2}?\s+(?:okay|ok|fine|good|stable|"
    r"working|clear)\b",
    r"\b(?:is|was)\s+there\s+(?:an?\s+|any\s+)?(?:audio|sound|voice|mic|network|connection)\s+(?:issue|problem)\b",
)
# Weak cues ("voice is coming?", "is it audible?", "hello?"): only in a short question-like turn.
_AUDIO_WEAK = _rx(
    r"\b(?:my\s+)?(?:voice|audio|sound)\s+(?:is\s+)?(?:coming|audible|clear|reaching)\b",
    r"\b(?:is|was)\s+(?:it|this|that|the\s+audio|the\s+sound|the\s+voice|everything)\s+(?:\w+\s+)?audible\b",
    r"^(?:am\s+i\s+)?audible$",
    r"^(?:\w+\s+){0,2}?hearing\s+me$",
)
_HELLO_WORDS = frozenset({"hello", "hi", "hey", "hallo", "helo", "hii", "hiya"})
_GREETING_ONLY = re.compile(
    r"^(?:(?:hello|hi|hey|hallo|helo|hii)\b[\s,]*)+(?:(?:there|ma'?am|mam|sir|madam|everyone|good\s+(?:morning|"
    r"afternoon|evening))\b[\s,]*)*$|^good\s+(?:morning|afternoon|evening)(?:\s+(?:ma'?am|mam|sir|madam|everyone))?$"
)
_CONNECTION_TROUBLE = _rx(
    r"\bmy\s+(?:internet|network|connection|wifi|wi\s?fi)\s+(?:is|was|got|went)\s+(?:\w+\s+)?(?:slow|bad|weak|"
    r"unstable|lagging|down|disconnected|cut|poor|breaking|off)\b",
    r"\b(?:i|we)\s+(?:got|was|were)\s+disconnected\b",
    r"\bsorry,?\s+(?:my\s+)?(?:network|internet|connection)\s+(?:issue|problem)\b",
)
_CANT_HEAR_YOU = _rx(
    rf"\bi\s+(?:can't|cant|cannot|can\s+not|am\s+not\s+able\s+to)\s+hear\s+{_YOU}\b",
    r"\byour\s+(?:voice|audio|sound)\s+(?:is\s+)?(?:not|isn't)\s+(?:coming|audible|clear|reaching)\b",
    rf"\b{_YOU}\s+(?:are|r)\s+not\s+audible\b",
)
_TESTING_ONLY = re.compile(r"^(?:(?:hello|hi)\s+)?(?:(?:testing|mic\s+check|test)\s*){1,3}$")
_VIDEO_CHECK = re.compile(
    rf"\b(?:can|could|are)\s+{_YOU}\s+(?:able\s+to\s+)?see\s+(?:me|my\s+(?:screen|face|video|camera))\b"
)
#: Words that may precede an audio / process question without making it part of an answer ("But first answer me,
#: like, am I able to do it or not?", "I was asking whether you can hear me").
_META_WORDS = frozenset(
    "first answer please tell sorry wait quick question hello excuse sir maam madam before start starting asking "
    "whether wanted want know just checking check confirm need said ask again actually listen".split()
)

# --------------------------------------------------------------------------------------- process questions
_START = _rx(
    r"\b(?:should|shall|can|could|may|must|do)\s+i\s+(?:just\s+|now\s+|please\s+|first\s+|go\s+ahead\s+and\s+)?"
    r"(?:start|begin|proceed|go\s+ahead|answer(?:\s+(?:now|it|this|that|the\s+question))?|speak|talk|"
    r"introduce\s+myself|say\s+it)(?:\s+(?:now|then|here|first|please|ma'?am|mam|sir|madam|answering|speaking))*$",
    r"\bam\s+i\s+(?:allowed|supposed|ready)\s+to\s+(?:start|begin|answer|speak|talk|go|proceed)\b",
    r"\bam\s+i\s+able\s+to\s+(?:start|begin|answer|speak|talk|go\s+ahead|proceed)\b",
    r"\b(?:do|would)\s+you\s+want\s+me\s+to\s+(?:start|begin|go\s+ahead|answer|introduce\s+myself)\b",
    r"\bis\s+it\s+my\s+turn\b",
    r"^(?:so\s+|okay\s+|ok\s+|then\s+|now\s+)?i\s+(?:can|should|shall|will)\s+(?:start|begin|go\s+ahead|proceed)"
    r"(?:\s+now)?$",
    r"^(?:so\s+|okay\s+|ok\s+)?(?:shall|should|can)\s+i$",
    r"\b(?:is\s+it|it's|it\s+is)\s+(?:ok(?:ay)?|fine|alright|all\s+right)\s+(?:if|for\s+me\s+to|to)\s+(?:i\s+)?"
    r"(?:start|begin|answer|go\s+ahead|continue)\b",
    r"\b(?:do|should)\s+i\s+(?:have|need)\s+to\s+(?:introduce\s+myself|start|answer|speak|talk)\b",
)
_CONTINUE = _rx(
    r"\b(?:should|shall|can|may)\s+i\s+(?:continue|go\s+on|keep\s+going|carry\s+on)\b",
    r"^(?:\w+\s+){0,2}?(?:continue|go\s+on)$",
)
# "But first answer me, like, am I able to do it or not?": the candidate is checking that everything works.
_ABILITY = _rx(
    r"\bam\s+i\s+able\s+to\s+do\s+(?:it|this|that)\b",
    r"\bis\s+(?:it|this|everything)\s+(?:working|okay|ok|fine|alright|all\s+right)(?:\s+(?:now|on\s+your\s+side|"
    r"there|or\s+not))?$",
    r"\bis\s+everything\s+(?:okay|ok|fine|alright|working)\b",
    r"\bam\s+i\s+doing\s+(?:it|this)\s+(?:right|correctly|properly)\b",
)
_ANSWER_LENGTH = _rx(
    r"\bhow\s+(?:long|much(?:\s+time)?|many\s+minutes|much\s+detail)\s+(?:should|do|can|must|will|shall)\s+i\s+"
    r"(?:speak|talk|answer|take|have|go\s+on|get|say)\b",
    r"\bhow\s+(?:long|much\s+time|many\s+minutes)\s+(?:do\s+)?i\s+(?:have|get|should\s+(?:speak|talk)|can\s+"
    r"(?:speak|take))\b",
    r"\bhow\s+(?:long|detailed)\s+should\s+(?:the|my|this)\s+answer\s+be\b",
    r"\bis\s+there\s+(?:a|any)\s+time\s+limit\b",
)
_TIME_LEFT = _rx(
    r"\bhow\s+(?:long|much\s+time)\s+(?:is|will|does|would)\s+(?:this|the)\s+(?:interview|session|call|meeting)"
    r"(?:\s+(?:take|last|be))?\b",
    r"\bhow\s+much\s+(?:more\s+)?time\s+(?:is\s+)?(?:left|remaining)\b",
    r"\bhow\s+many\s+(?:more\s+)?questions\b",
    r"\bhow\s+long\s+(?:is\s+)?(?:left|remaining)\b",
)
_NOTES = _rx(
    r"\b(?:can|could|may|should|shall)\s+i\s+(?:use|refer\s+to|look\s+at|check|read\s+from|see|open|take|keep|"
    r"write\s+(?:it\s+)?(?:down|on))\s+(?:my\s+|some\s+|a\s+|the\s+|any\s+)?(?:notes?|notebook|paper|pen|resume|cv|"
    r"laptop|phone|mobile|google|internet|chat\s?gpt|documentation|docs|book|slides?)\b",
    r"\bis\s+it\s+(?:ok(?:ay)?|fine|allowed)\s+(?:if\s+i|to)\s+(?:use|look\s+at|refer\s+to|check)\b",
)
_AI = _rx(
    r"\b(?:are\s+you|is\s+this|am\s+i\s+(?:talking|speaking)\s+(?:to|with))\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:real\s+(?:person|human|interviewer)|human|person|bot|robot|ai|computer|machine|automated)\b",
    r"\bis\s+(?:this|it)\s+(?:an?\s+)?(?:ai|automated|bot)\s+interview\b",
)
_FEEDBACK = _rx(
    r"\bis\s+(?:my\s+answer|that|this)\s+(?:correct|right|okay|ok|fine|good|enough)\b",
    r"\bwas\s+(?:that|this|my\s+answer|it)\s+(?:okay|ok|fine|good|enough|correct|right|alright)\b",
    r"\bam\s+i\s+(?:doing\s+(?:ok(?:ay)?|well|good|fine|alright)|on\s+the\s+right\s+track)\b",
    r"\b(?:did|have)\s+i\s+(?:answer(?:ed)?|do(?:ne)?)\s+(?:it\s+|that\s+|this\s+)?(?:correctly|right|well|okay|ok)\b",
)
_NEXT_STEPS = _rx(
    r"\bwhat(?:'s|\s+is|\s+are)\s+the\s+next\s+steps?\b",
    r"\bwhen\s+(?:will|would|do|can)\s+i\s+(?:hear|know|get\s+(?:the\s+)?(?:result|results|feedback|response))\b",
    r"\bwhat\s+happens\s+(?:next|after\s+(?:this|the\s+interview))\b",
)

# ---------------------------------------------------------------------------------- away / interruptions
_AWAY = _rx(
    r"\b(?:someone|somebody)\s+(?:is|was)\s+(?:at\s+the\s+door|calling|here|knocking)\b",
    r"\bmy\s+(?:phone|mobile)\s+(?:is|was)\s+ringing\b",
    r"\b(?:sorry|apologies|excuse\s+me)\b.{0,40}\b(?:noise|disturbance|interruption|background\s+(?:noise|sound))\b",
    r"\bi\s+was\s+(?:talking|speaking)\s+to\s+(?:someone|somebody|my\s+\w+)\b",
    r"\b(?:sorry|excuse\s+me)\b.{0,20}\b(?:someone|somebody)\s+(?:came|is\s+here|called|interrupted)\b",
)

# ------------------------------------------------------------------------------------- readiness / ack
_ACK_WORDS = frozenset(
    "okay ok yes yeah yep yup ya sure alright right fine cool great so um uh hmm hm ah oh well sir maam ma'am mam "
    "madam ji haan haa han ready absolutely definitely certainly then understood thanks perfect good nice".split()
)
_READY_PHRASE = _rx(
    r"(?:i'm|i\s+am)\s+ready",
    r"let's\s+(?:start|begin|go|do\s+(?:it|this))",
    r"(?:let\s+me|i'll|i\s+will)\s+(?:start|begin|try|go\s+ahead)",
    r"(?:of\s+course|no\s+problem|sounds\s+good|makes\s+sense|i\s+see|i\s+understand|got\s+it|yes\s+please|"
    r"thank\s+you|all\s+right)",
    rf"i\s+can\s+hear\s+{_YOU}(?:\s+(?:now|clearly|fine|well|properly))?",
)

# -------------------------------------------------------------------------------- incomplete fragments
#: Words that leave a short turn unfinished ("so for that", "and the", "because"); fillers are WANTS_TIME.
_CONTINUATION_TAILS = frozenset(
    "and so but because or like then also which if when while although though since unless plus the a an to of "
    "with my our".split()
)
_FRAGMENT_PHRASE = re.compile(
    r"^(?:(?:and|but|so|okay|ok|yeah|yes|um|uh)\s+){0,2}(?:(?:for|about|regarding|on|in|with)\s+(?:that|this|it)|"
    r"basically|actually|the\s+thing\s+is|what\s+happened\s+(?:was|is)|what\s+i\s+did\s+(?:was|is)|first\s+of\s+all|"
    r"firstly|to\s+be\s+honest|i\s+mean|i\s+would\s+say|let's\s+say|in\s+(?:that|this)\s+case|so\s+basically|"
    r"so\s+what\s+i)$"
)
FRAGMENT_MAX_WORDS = 8
LOW_CONFIDENCE = 0.35

# ------------------------------------------------------------------------------------------ question shape
_YES_NO_START = re.compile(
    r"^(?:(?:and|so|also|now|okay|ok|then|right|great|sure)\s+)?(?:are|is|do|does|did|have|has|had|can|could|"
    r"would|will|should|was|were|am|shall|may)\b"
)
_OPEN_REQUEST = re.compile(
    r"^(?:\w+\s+){0,2}?(?:can|could|would|will)\s+you\s+(?:please\s+|briefly\s+|quickly\s+|just\s+)*(?:walk|tell|"
    r"describe|explain|share|talk|give|elaborate|say|introduce|go|take|help|clarify|expand|outline|list|name|"
    r"summari[sz]e|think|compare|show|paint|recall|remember|break|pick|teach)\b"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


# --------------------------------------------------------------------------- questions to the interviewer
# A short question addressed to the interviewer that no fixed reply covers ("What's your name?", "Who am I talking
# to?", "Is this being recorded?", "What is this program about?"). A human interviewer answers it and then asks the
# question again, so it becomes a candidate question (topic "interviewer") that the turn model answers while the
# pending item stays pending. The whole turn must be the question: substance before it, or a tag question closing
# an answer ("..., you know?"), keeps the turn an answer.
_ASK_LEAD = re.compile(
    r"^(?:(?:okay|ok|so|and|but|also|sorry|hello|hi|hey|wait|um+|uh+|hmm+|actually|first|sir|ma'am|maam|madam|"
    r"one\s+(?:thing|question|second)|quick\s+question|just\s+curious|by\s+the\s+way|"
    r"before\s+(?:that|we\s+start|i\s+(?:start|answer)))\s+)+"
)
_ASK_START = re.compile(
    r"^(?:what|what's|whats|who|who's|whos|whom|whose|where|where's|when|why|how|how's|which|is|are|am|do|does|"
    r"did|can|could|would|will|should|may|shall|have|has|tell\s+me)\b"
)
_ASK_TARGET = _rx(
    r"\b(?:you|u|ya|your|yours|yourself)\b",
    r"\b(?:this|the)\s+(?:interview|call|session|meeting|program|programme|course|training|internship|company|"
    r"organization|organisation|platform|process)\b",
    r"\b(?:recorded|recording)\b",
    r"\bwho\s+(?:am\s+i\s+(?:talking|speaking)|is\s+(?:this|speaking|there))\b",
)
_ASK_TAG = re.compile(r"\b(?:you\s+know|right|isn't\s+it|no|correct|yeah|okay|ok)$")
# Reasoning or first-person reporting: an answer that happens to open with a (rhetorical) question.
_ASK_ANSWERISH = re.compile(
    r"\b(?:because|since|so\s+we|we\s+(?:used|built|had|did|were|picked|chose)|i\s+(?:used|built|did|was|had|"
    r"worked|picked|chose))\b"
)


def _asks_interviewer(norm: str, clean: str, n: int, ends_question: bool) -> str | None:
    """The question (lead fillers removed) when the whole turn is a short question to the interviewer, else None."""
    if n > FLOW_MAX_WORDS or _ASK_ANSWERISH.search(clean):
        return None
    _, mark, tail = norm.partition("?")
    if mark and tail.strip() and not tail.rstrip().endswith("?") and len(substance_words(tail)) > 1:
        return None  # "How did I handle it? I talked to my manager ...": the question opens an answer
    body = _ASK_LEAD.sub("", clean)
    if not body or _ASK_TARGET.search(body) is None:
        return None
    if _ASK_START.match(body) or (ends_question and not _ASK_TAG.search(body)):
        return body
    return None


def _clean(text: str) -> str:
    """Normalised text without punctuation (apostrophes kept), for full-turn patterns."""
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", normalize(text)).split())


def question_sentence(question_text: str | None) -> str:
    """The question sentence of an interviewer utterance (the last one ending with "?", else the last)."""
    sentences = [s.strip() for s in _SENTENCE.split((question_text or "").strip()) if s.strip()]
    if not sentences:
        return ""
    for sentence in reversed(sentences):
        if sentence.endswith("?"):
            return sentence
    return sentences[-1]


_CLAUSE_SPLIT = re.compile(r"\s+[—–-]\s+|[,;:]\s+")


def is_yes_no_question(question_text: str | None) -> bool:
    """ "Are you comfortable with that?" / "... time-boxed — can you commit the hours?" (a bare "yes" answers it)
    vs "Could you walk me through ..." / "What ..." / "Do you prefer X or Y?"."""
    sentence = question_sentence(question_text)
    if not sentence:
        return False
    pieces = [sentence, _CLAUSE_SPLIT.split(sentence)[-1]]
    for piece in (_clean(p) for p in pieces):
        if (
            piece
            and _YES_NO_START.match(piece)
            and not _OPEN_REQUEST.match(piece)
            and " or " not in f" {piece} "  # a choice, not yes/no
        ):
            return True
    return False


def short_reinvite(question_text: str | None, *, max_words: int = 22) -> str:
    """The pending question in a short form (its question sentence), never the whole long prompt again."""
    sentence = question_sentence(question_text)
    if sentence and len(words(sentence)) <= max_words:
        return sentence
    return "Please go ahead with your answer whenever you're ready."


def _tokens(text: str) -> list[str]:
    return words(text)


def _content(text: str) -> list[str]:
    """Substance words that are not meta words around a question to the interviewer."""
    return [w for w in substance_words(text) if w not in _META_WORDS]


def _audio(norm: str, clean: str, n: int, ends_question: bool) -> tuple[str, re.Match[str] | None] | None:
    """(topic, match) of an audio / connection check, or None."""
    if n <= 4 and clean and (_GREETING_ONLY.match(clean) or _TESTING_ONLY.match(clean)):
        return "hello", None
    m = _CANT_HEAR_YOU.search(norm)
    if m and n <= FLOW_MAX_WORDS:
        return "cant_hear_you", m
    m = _CONNECTION_TROUBLE.search(norm)
    if m and n <= FLOW_MAX_WORDS:
        return "connection", m
    m = _AUDIO_STRONG.search(clean)
    if m:
        return "check", m
    m = _VIDEO_CHECK.search(clean)
    if m and n <= FLOW_MAX_WORDS:
        return "video", m
    if n <= 10 and (ends_question or " or not" in f" {clean}" or clean.endswith((" na", " no"))):
        m = _AUDIO_WEAK.search(clean)
        if m:
            return "check", m
    tokens = clean.split()
    if tokens and tokens[0] in _HELLO_WORDS and n <= 4 and len(substance_words(clean)) <= 1:
        return "hello", None  # "Hi Priyanshi", "hello hello?"
    return None


def _process(clean: str, n: int, ends_question: bool) -> str | None:
    """The topic of a question about the interview itself, or None. The question must be short, nothing
    substantive may precede it (else it is part of an answer) and little may follow it ("Is this correct: a list is
    mutable and a tuple is not?" is an answer that asks for confirmation)."""
    if n > FLOW_MAX_WORDS:
        return None
    asks = (
        ends_question
        or bool(_YES_NO_START.match(clean))
        or clean.startswith(("how ", "what ", "when ", "so how ", "sorry how ", "and how "))
        or " or not" in f" {clean}"
    )
    for topic, pattern in (
        ("answer_length", _ANSWER_LENGTH),
        ("time_left", _TIME_LEFT),
        ("ability", _ABILITY),
        ("start", _START),
        ("continue", _CONTINUE),
        ("notes", _NOTES),
        ("ai", _AI),
        ("feedback", _FEEDBACK),
    ):
        m = pattern.search(clean)
        if m is None or not (asks or topic in ("start", "continue")):
            continue
        if len(_content(clean[: m.start()])) > 1 or len(_content(clean[m.end() :])) > 2:
            continue
        return topic
    return None


def _readiness(clean: str, tokens: list[str]) -> bool:
    if not tokens or len(tokens) > 6:
        return False
    if all(t in _ACK_WORDS for t in tokens):
        return True
    rest = " ".join(t for t in tokens if t not in _ACK_WORDS)
    return bool(rest) and bool(_READY_PHRASE.fullmatch(rest) or _READY_PHRASE.fullmatch(clean))


def _incomplete(clean: str, tokens: list[str], ends_question: bool) -> bool:
    if not tokens or ends_question or len(tokens) > FRAGMENT_MAX_WORDS:
        return False
    if len(substance_words(clean)) > 2:
        return False
    return tokens[-1] in _CONTINUATION_TAILS or bool(_FRAGMENT_PHRASE.match(clean))


def understand(
    text: str,
    *,
    question_text: str | None = None,
    awaiting_answer: bool = True,
    questions_invited: bool = False,
    coding_active: bool = False,
    accepts_brief: bool = False,
    stt_confidence: float | None = None,
) -> Understanding:
    """Classify one candidate turn (~0.1 ms). ``question_text`` is the pending interviewer question (its shape
    decides whether a bare "yes" is an answer); ``accepts_brief`` marks items where a short reply is a valid
    answer; ``questions_invited`` is True once the candidate was invited to ask their questions."""
    special = classify(
        text,
        awaiting_answer=awaiting_answer,
        coding_active=coding_active,
        questions_invited=questions_invited,
    )
    norm = normalize(text)
    clean = _clean(text)
    tokens = _tokens(text)
    n = len(tokens)
    ends_question = norm.rstrip().endswith("?")

    if special.kind is SpecialKind.EMPTY:
        return Understanding(TurnIntent.OTHER, special)
    if special.kind is SpecialKind.REPEAT:
        return Understanding(TurnIntent.REPEAT, special, matched=special.matched)
    if special.kind in (SpecialKind.INSTRUCTION, SpecialKind.WANTS_HINT):
        return Understanding(TurnIntent.OTHER, special, matched=special.matched)

    # Audio / connection checks: answered whatever else the turn looks like (a short question addressed to us).
    audio = _audio(norm, clean, n, ends_question)
    if audio is not None:
        topic, match = audio
        if match is not None and topic in ("check", "video"):
            before = clean[: match.start()]
            after = clean[match.end() :]
            if len(_content(before)) > 1:
                audio = None  # "the customer kept asking can you hear me": part of an answer
            elif len(_content(after)) >= MIXED_MIN_SUBSTANCE:
                ack = "Yes, I can hear you clearly."
                return Understanding(TurnIntent.ANSWER, special, topic=topic, ack=ack, matched=match.group(0))
        if audio is not None:
            return Understanding(
                TurnIntent.AUDIO_CHECK, special, topic=topic, matched=match.group(0) if match else clean
            )

    # Questions about the interview itself: a brief honest answer, the item stays pending.
    process_topic = _process(clean, n, ends_question)
    if process_topic is not None:
        return Understanding(TurnIntent.PROCESS_QUESTION, special, topic=process_topic, matched=clean)
    if special.kind is SpecialKind.CANDIDATE_QUESTION:
        next_steps = "next_steps" if _NEXT_STEPS.search(clean) else None
        return Understanding(
            TurnIntent.CANDIDATE_QUESTION, special, topic=next_steps, matched=special.matched
        )
    asked = (
        _asks_interviewer(norm, clean, n, ends_question)
        if special.kind is SpecialKind.NONE and not special.suspect and not questions_invited
        else None
    )
    if asked is not None:
        return Understanding(
            TurnIntent.CANDIDATE_QUESTION,
            SpecialResult(SpecialKind.CANDIDATE_QUESTION, 0.75, asked),
            topic="interviewer",
            matched=asked,
        )

    if n <= 20 and _AWAY.search(norm):
        return Understanding(TurnIntent.OFF_TOPIC, special, topic="away", matched=clean)

    if special.kind is SpecialKind.WANTS_TIME:
        return Understanding(TurnIntent.THINKING, special, matched=special.matched)
    if special.kind is SpecialKind.CLARIFICATION:
        return Understanding(TurnIntent.CLARIFY, special, matched=special.matched)
    if special.kind in (SpecialKind.DONT_KNOW, SpecialKind.NO_QUESTIONS):
        return Understanding(
            TurnIntent.ANSWER if special.kind is SpecialKind.DONT_KNOW else TurnIntent.OTHER, special
        )

    open_question = awaiting_answer and not questions_invited and not accepts_brief
    if open_question and not is_yes_no_question(question_text) and _readiness(clean, tokens):
        return Understanding(TurnIntent.READINESS, special, topic="ready", matched=clean)
    if awaiting_answer and not questions_invited and _incomplete(clean, tokens, ends_question):
        return Understanding(TurnIntent.INCOMPLETE, special, topic="fragment", matched=clean)

    if special.kind is SpecialKind.GARBLED:
        return Understanding(TurnIntent.GARBLED, special)
    if (
        stt_confidence is not None
        and stt_confidence < LOW_CONFIDENCE
        and n <= 6
        and len(substance_words(clean)) <= 2
    ):
        return Understanding(TurnIntent.GARBLED, SpecialResult(SpecialKind.GARBLED, 0.6, clean))
    if special.kind is SpecialKind.BRIEF:
        return Understanding(TurnIntent.PARTIAL_ANSWER, special)
    return Understanding(TurnIntent.ANSWER, special)


# ================================================================================================= replies

AUDIO_ACKS: tuple[str, ...] = (
    "Yes, I can hear you clearly.",
    "Yes, I can hear you well.",
    "Yes, your voice is coming through clearly.",
)
HELLO_ACK = "Hello, I can hear you clearly."
VIDEO_ACK = "I can hear you clearly, and that's all I need for this interview."
CONNECTION_ACK = "No problem, I can hear you clearly now."
CANT_HEAR_ACK = (
    "I can hear you clearly. If my voice isn't reaching you, please check your speaker or headphone volume."
)
VOLUME_HINT = "If you can't hear me, please check your speaker or headphone volume."
READY_REPLIES: tuple[str, ...] = (
    "Sure, go ahead.",
    "Great, go ahead.",
    "Okay, go ahead whenever you're ready.",
)
READY_AGAIN = "Go ahead whenever you're ready."
NUDGES: tuple[str, ...] = ("Take your time, go on.", "Go on, I'm listening.", "I'm listening, go ahead.")
THINKING_REPLY = "Of course, take your time."
AWAY_REPLY = "No problem, take your time."
PROCESS_REPLIES: dict[str, str] = {
    "start": "Yes, please go ahead.",
    "continue": "Yes, please go on.",
    "ability": "Yes, you're all set, and I can hear you clearly.",
    "notes": "Please answer in your own words, from what you know.",
    "ai": "I'm an AI interviewer, and the hiring team reviews every interview.",
    "feedback": "I don't comment on answers during the interview; the hiring team reviews everything afterwards.",
    "next_steps": "After the interview, the hiring team reviews it and contacts you about next steps.",
}
#: Replies that end with the pending question (short form) so the candidate knows what to answer.
_WITH_QUESTION = frozenset(
    {"start", "ability", "notes", "ai", "feedback", "answer_length", "time_left", "next_steps"}
)


def pick(options: tuple[str, ...], seed: str) -> str:
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]  # noqa: S324 - variation only


def _join(*parts: str | None) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def _spoken_seconds(seconds: int) -> str:
    if seconds <= 45:
        return f"about {seconds} seconds"
    if seconds < 75:
        return "about a minute"
    if seconds < 105:
        return "about a minute and a half"
    minutes = round(seconds / 60)
    return f"about {minutes} minutes"


def answer_length_reply(soft_limit_s: int | None) -> str:
    if soft_limit_s:
        return f"{_spoken_seconds(int(soft_limit_s)).capitalize()} is plenty, and there's no need to rush."
    return "There's no strict limit; a minute or two is usually enough."


def time_left_reply(remaining_s: float | None) -> str:
    if remaining_s is None:
        return "We'll finish within the planned time."
    if remaining_s <= 60:
        return "We're close to the end now."
    minutes = max(1, round(remaining_s / 60))
    return f"We have about {minutes} minute{'s' if minutes != 1 else ''} left."


def flow_reply(
    heard: Understanding,
    *,
    question_text: str | None,
    reinvite: str | None = None,
    streak: int = 0,
    seed: str = "",
    remaining_s: float | None = None,
    soft_limit_s: int | None = None,
) -> str:
    """The interviewer's deterministic reply to a flow turn. ``reinvite`` overrides the short form of the pending
    question (intake templates); ``streak`` = flow replies of this intent already given on this question."""
    question = (reinvite or short_reinvite(question_text)) if question_text or reinvite else None
    intent = heard.intent
    if intent is TurnIntent.AUDIO_CHECK:
        if heard.topic == "cant_hear_you":
            return _join(CANT_HEAR_ACK, question)
        lead = {"hello": HELLO_ACK, "connection": CONNECTION_ACK, "video": VIDEO_ACK}.get(
            heard.topic or "", pick(AUDIO_ACKS, seed)
        )
        if streak >= 2:
            lead = _join(lead, VOLUME_HINT)
        return _join(lead, question)
    if intent is TurnIntent.PROCESS_QUESTION or (
        intent is TurnIntent.CANDIDATE_QUESTION and heard.topic == "next_steps"
    ):
        topic = heard.topic or "start"
        if topic == "answer_length":
            lead = answer_length_reply(soft_limit_s)
        elif topic == "time_left":
            lead = time_left_reply(remaining_s)
        else:
            lead = PROCESS_REPLIES.get(topic, PROCESS_REPLIES["start"])
        return _join(lead, question if topic in _WITH_QUESTION else None)
    if intent is TurnIntent.READINESS:
        if streak >= 1:
            return _join(READY_AGAIN, question)
        return pick(READY_REPLIES, seed)
    if intent is TurnIntent.THINKING:
        return THINKING_REPLY
    if intent is TurnIntent.INCOMPLETE:
        return pick(NUDGES, seed)
    if intent is TurnIntent.OFF_TOPIC:
        return AWAY_REPLY
    return _join(question) or THINKING_REPLY


def flow_action(heard: Understanding) -> TurnAction:
    """The decision action recorded for a flow reply (never a question-creating action)."""
    if heard.intent in (TurnIntent.AUDIO_CHECK, TurnIntent.PROCESS_QUESTION, TurnIntent.CANDIDATE_QUESTION):
        return TurnAction.CLARIFY_QUESTION
    return TurnAction.ACKNOWLEDGE_AND_WAIT


# ------------------------------------------------------------------------------------- per-question streaks


def flow_count(state: Any, intent: TurnIntent) -> int:
    """Flow replies of ``intent`` already given on the current question (``InterviewState`` duck-typed)."""
    if getattr(state, "flow_question_id", None) != getattr(state, "current_question_id", None):
        return 0
    return int((getattr(state, "flow_counts", None) or {}).get(intent.value, 0))


def note_flow(state: Any, intent: TurnIntent) -> int:
    """Count one flow reply on the current question; returns the count before it."""
    if state.flow_question_id != state.current_question_id:
        state.flow_question_id = state.current_question_id
        state.flow_counts = {}
    before = int(state.flow_counts.get(intent.value, 0))
    state.flow_counts = {**state.flow_counts, intent.value: before + 1}
    return before


def flow_allowed(state: Any, heard: Understanding) -> bool:
    """Whether a flow reply is still right on this question (FLOW_LIMITS), else the turn goes the ordinary way."""
    if not heard.flow:
        return False
    limit = FLOW_LIMITS.get(heard.intent)
    return limit is None or flow_count(state, heard.intent) < limit


ANSWER_FIRST_RULE = (
    "If the candidate's last words were a question to you (about you, the interview, the role or the program), answer "
    "it like a friendly human interviewer in a few honest words first and then ask your question again; it is not "
    "their answer. Never say they mentioned, meant or were about to say something they did not say."
)


__all__ = [
    "ANSWER_FIRST_RULE",
    "FLOW_INTENTS",
    "FLOW_LIMITS",
    "TurnIntent",
    "Understanding",
    "answer_length_reply",
    "flow_action",
    "flow_allowed",
    "flow_count",
    "flow_reply",
    "is_yes_no_question",
    "note_flow",
    "question_sentence",
    "short_reinvite",
    "time_left_reply",
    "understand",
]
