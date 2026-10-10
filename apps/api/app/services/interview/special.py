"""Fast, deterministic classification of candidate turns (~0 ms).

These heuristics catch the turns that change the *flow* rather than carry an
answer: repeat requests, clarification requests, "let me think",
"I don't know", backchannels ("mm-hmm"), hint requests and questions about
the role or the process. Anything that does not match clearly is left to the
model (``SpecialResult.kind == NONE``): a false positive here would derail
the conversation, so every rule requires a short turn or an unambiguous
phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import AnswerQuality


class SpecialKind(StrEnum):
    NONE = "none"
    REPEAT = "repeat"
    CLARIFICATION = "clarification"
    WANTS_TIME = "wants_time"
    DONT_KNOW = "dont_know"
    BACKCHANNEL = "backchannel"
    CANDIDATE_QUESTION = "candidate_question"
    WANTS_HINT = "wants_hint"
    EMPTY = "empty"
    NO_QUESTIONS = "no_questions"  # declines to ask questions after being invited
    BRIEF = "brief"  # a one-to-four word reply to a substantive question (likely cut off): ask for more
    GARBLED = "garbled"  # fragments without substance (likely a recognition/endpointing glitch)
    INSTRUCTION = "instruction"  # text that tries to instruct the interviewer (prompt injection)


SPECIAL_QUALITY: dict[SpecialKind, AnswerQuality | None] = {
    SpecialKind.NONE: None,
    SpecialKind.REPEAT: AnswerQuality.CLARIFICATION_REQUEST,
    SpecialKind.CLARIFICATION: AnswerQuality.CLARIFICATION_REQUEST,
    SpecialKind.WANTS_TIME: AnswerQuality.WANTS_TIME,
    SpecialKind.DONT_KNOW: AnswerQuality.NO_ANSWER,
    SpecialKind.BACKCHANNEL: None,  # "yeah"/"okay" alone: the model invites elaboration
    SpecialKind.CANDIDATE_QUESTION: AnswerQuality.CANDIDATE_QUESTION,
    SpecialKind.WANTS_HINT: AnswerQuality.WANTS_HINT,
    SpecialKind.EMPTY: AnswerQuality.NO_ANSWER,
    SpecialKind.NO_QUESTIONS: None,  # flow signal only (the policy closes); never a quality judgement
    SpecialKind.BRIEF: None,  # flow signal: ask them to say more (never scored)
    SpecialKind.GARBLED: None,  # flow signal: ask them to say more (never scored)
    SpecialKind.INSTRUCTION: AnswerQuality.OFF_TOPIC,  # redirect to the question; the attempt is ignored
}

# Turns that are not substantive answers (no deep evaluation).
NON_ANSWER_KINDS: frozenset[SpecialKind] = frozenset(
    {
        SpecialKind.REPEAT,
        SpecialKind.CLARIFICATION,
        SpecialKind.WANTS_TIME,
        SpecialKind.BACKCHANNEL,
        SpecialKind.CANDIDATE_QUESTION,
        SpecialKind.WANTS_HINT,
        SpecialKind.EMPTY,
        SpecialKind.NO_QUESTIONS,
        SpecialKind.BRIEF,
        SpecialKind.GARBLED,
    }
)


@dataclass(slots=True, frozen=True)
class SpecialResult:
    kind: SpecialKind
    confidence: float = 0.0
    matched: str | None = None
    # An answer that contains a phrase addressed to the system without being one (kept for the turn model and
    # the audit trail; the answer is still evaluated).
    suspect: str | None = None

    @property
    def quality(self) -> AnswerQuality | None:
        return SPECIAL_QUALITY[self.kind]

    @property
    def is_special(self) -> bool:
        return self.kind is not SpecialKind.NONE

    @property
    def is_answer(self) -> bool:
        """True when the turn should be treated (and evaluated) as an answer."""
        return self.kind not in NON_ANSWER_KINDS


NONE_RESULT = SpecialResult(SpecialKind.NONE)

_WORD = re.compile(r"[a-z0-9']+")
_FILLERS = frozenset({"um", "uh", "erm", "er", "hmm", "ah", "oh", "well", "so", "like"})


def normalize(text: str) -> str:
    """Lower-case, unify apostrophes/dashes, collapse whitespace."""
    cleaned = (text or "").lower().replace("’", "'").replace("‘", "'").replace("—", " ").replace("-", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def words(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in _FILLERS]


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_BACKCHANNELS = frozenset(
    {
        "mm hmm",
        "mmhmm",
        "mhm",
        "mm",
        "hmm",
        "uh huh",
        "uh hum",
        "yeah",
        "yea",
        "yep",
        "yup",
        "yes",
        "ok",
        "okay",
        "sure",
        "right",
        "alright",
        "all right",
        "got it",
        "i see",
        "cool",
        "fine",
        "makes sense",
        "understood",
        "ok sure",
        "okay sure",
        "yeah sure",
        "yes sure",
        "right right",
        "ok got it",
        "okay got it",
        "sounds good",
    }
)

_THINKING = frozenset({"hmm", "hm", "mm", "um", "uh", "erm", "er", "ah", "uhm", "umm", "hmmm"})

_REPEAT = _rx(
    r"\b(?:can|could|would|will) you (?:please )?(?:repeat|say) (?:that|it|the question|this|what you said)",
    r"\b(?:can|could|would|will) you (?:please )?(?:repeat|rephrase)\b",
    r"\brepeat (?:that|the question|it|please)\b",
    r"\bsay (?:that|it) again\b",
    r"\b(?:one more time|once more)\b",
    r"\b(?:i )?(?:didn't|did not|couldn't|could not) (?:quite )?(?:catch|hear|get) (?:that|it|the question|what you said|you)\b",
    r"\bwhat was the question\b",
    r"\bcome again\b",
    r"^(?:sorry|pardon)(?: me)?\??$",
    r"\byou (?:cut|broke) (?:out|up)\b",
)

_CLARIFY = _rx(
    r"\bwhat do you mean\b",
    r"\bwhat exactly do you mean\b",
    r"\b(?:can|could) you (?:please )?(?:clarify|elaborate on the question|be more specific|explain (?:the|what you mean|that question))\b",
    r"\bdo you mean\b",
    r"\bare you asking\b",
    r"\b(?:are|were) you referring to\b",
    r"\b(?:i'm|i am) not sure (?:i )?(?:understand|follow) the question\b",
    r"\b(?:i )?(?:don't|do not) (?:quite )?(?:understand|follow) the question\b",
    r"\bwhat (?:kind|sort|type) of (?:\w+ )?(?:are you looking for|do you want|do you mean)\b",
    r"\bwhich (?:one|part|aspect|project|role|company|system|environment) (?:do you mean|are you asking about|should i)\b",
    r"\bin what (?:context|sense)\b",
    r"\bjust to (?:clarify|confirm|make sure)\b.*\?$",
)

_WANTS_TIME = _rx(
    r"\blet me (?:just )?(?:think|see|recall|remember|gather my thoughts|collect my thoughts)\b",
    r"\b(?:give|allow) me (?:a|one) (?:moment|second|sec|minute|bit)\b",
    r"\b(?:just )?(?:a|one) (?:moment|second|sec|minute)(?: please)?\b",
    r"\bhold on\b",
    r"\bbear with me\b",
    r"\b(?:can|could|may) i (?:take|have) a (?:moment|second|minute)\b",
    r"\bthat's a (?:good|great|tough|hard) (?:one|question)\b",
    r"\bi need (?:a moment|a second|to think)\b",
)

_DONT_KNOW = _rx(
    r"\bi(?: \w+){0,2} (?:don't|do not|dont) know\b",
    r"\bno idea\b",
    r"\bi(?:'m| am)(?: \w+)? not (?:\w+ )?(?:sure|familiar)\b",
    r"\bi (?:have|'ve) (?:never|not) (?:used|worked with|done|heard of|tried|dealt with)\b",
    r"\bi haven't (?:used|worked with|done|heard of|tried|dealt with)\b",
    r"\bi (?:can't|cannot|can not|don't|do not) (?:recall|remember)\b",
    r"\bnot (?:familiar|sure) with\b",
    r"\bnever (?:used|worked with|done)\b",
    r"\bi(?:'d| would) (?:have to|need to) (?:look (?:it|that) up|google)\b",
    r"\bi(?:'m| am) (?:drawing a blank|blanking)\b",
    r"^(?:i(?:'ll| will)? )?pass\b",
    r"\bskip (?:this|that)(?: one| question)?\b",
)

_WANTS_HINT = _rx(
    r"\b(?:can|could|may) i (?:get|have) (?:a |another |one more )?(?:hint|clue|nudge)\b",
    r"\b(?:give|show) me (?:a |another )?(?:hint|clue|nudge)\b",
    r"\b(?:any|a) hints?\b",
    r"\bi(?:'d| would) like (?:a |another )?hint\b",
    r"\bneed (?:a |another )?(?:hint|clue)\b",
)

# Questions about the role, team, company or process (answered from job facts).
_CANDIDATE_Q = _rx(
    r"\bwhat (?:does|would) (?:a|the) (?:typical )?(?:day|week|team|role|position|job)\b",
    r"\bwhat(?:'s| is| are) the (?:next steps?|process|team|tech stack|stack|salary|compensation|timeline|culture|role)\b",
    r"\bwhen (?:will|would|do|can) i (?:hear|know|get)\b",
    r"\bhow (?:big|large) is the team\b",
    r"\bhow many (?:people|engineers|developers) (?:are )?(?:on|in) the team\b",
    r"\b(?:is|would) (?:this|the) (?:role|position|job) (?:be )?(?:remote|hybrid|on site|onsite|full time|contract)\b",
    r"\bwhat (?:technologies|tools|languages|frameworks) (?:does|do|would) (?:the|your) (?:team|company)\b",
    r"\b(?:can|could|may) i ask (?:you )?(?:a|something|about)\b",
    r"\bwho (?:would|will) i (?:be )?(?:report|work)(?:ing)? (?:to|with)\b",
    r"\bwhat are the next steps\b",
    r"\bhow (?:does|do) (?:you|the team|the company) (?:measure|evaluate|handle|approach|deploy|work)\b",
    r"\bdo you (?:have|offer|use)\b.*\?$",
    r"\b(?:can|could|may) i ask\b",
    r"\bhow (?:big|large) (?:is )?the (?:\w+ )?team\b",
    r"\bwho (?:would|will|do|does) (?:i|this role|the role) (?:report|work)\b",
    r"\bwhat does (?:the |a )?(?:typical |normal )?(?:on[- ]call|rotation|team|role|day|week|process|interview "
    r"process|hiring process|career|growth|onboarding|stack|platform team)(?: \w+){0,3} look like\b",
    r"\b(?:on[- ]call|tech stack|team size|reporting line|career path|growth|onboarding|working hours|remote)\b.*\?$",
)

# Declining the invitation to ask questions ("No, I think you've covered everything, thanks").
_NO_QUESTIONS = _rx(
    r"\b(?:i )?(?:don'?t|do not) (?:really )?have (?:any )?(?:more |further |other |additional )?questions\b",
    r"\bno (?:more |further |other |additional |real )?questions\b",
    r"\bnothing (?:else|more|further|from me|at the moment|right now|for now)\b",
    r"\bthat'?s (?:all|everything|it)(?: from me| for me)?\b",
    r"\bi(?:'m| am) (?:good|all set|fine|set)\b",
    r"\b(?:you'?ve|you have|we'?ve|we have) covered (?:it|everything|it all|a lot)\b",
    r"\bi think (?:that )?(?:covers|covered) (?:it|everything)\b",
    r"^(?:no|nope|not really|none)(?:,? (?:thanks|thank you|i'?m good|that'?s all))?[.!]?$",
)
_DECLINE_START = re.compile(r"^(?:no|nope|not really|none|nothing|nah|i(?:'m| am) (?:good|fine|all set))\b")
_INTERROGATIVE_START = re.compile(
    r"^(?:so |and |also |but |ok(?:ay)? |sure |great |thanks |thank you |right )?"
    r"(?:what|how|who|when|where|why|which|is|are|do|does|did|can|could|will|would|should|may|might)\b"
)

# Asking which aspect the interviewer means ("are you looking for X, or should I focus on Y?").
_CLARIFY_OPTIONS = _rx(
    r"\b(?:are you|were you) (?:looking for|asking about|interested in|more interested in)\b",
    r"\b(?:do|would) you (?:want|like|prefer) me to (?:focus|talk|go|start|cover|describe|explain)\b",
    r"\b(?:should|shall) i (?:focus|talk|go|start|cover|describe|explain)\b",
    r"\bjust to (?:make sure|clarify|confirm)\b",
    r"\bor (?:would you|do you|should i)\b",
)
_WANTS_TIME_FILLER = re.compile(
    r"\b(?:about (?:that|it|this|the question)|for (?:a|one) (?:second|sec|moment|minute|bit)|real quick|"
    r"quickly|here|please|ok(?:ay)?|so|um+|uh+|hmm+|well|just|actually|for a little)\b"
)
# Text addressed to the system rather than to the question (prompt injection). Two tiers:
# * ``_INSTRUCTION``: unambiguous commands about the scoring, the outcome or the interviewer's own rules; an
#   instruction wherever it occurs in the turn.
# * ``_INSTRUCTION_IMPERATIVE``: the generic jailbreak openers ("ignore all previous instructions", "act as
#   a ...", "you are now a ...", "from now on ..."). They are also everyday technical vocabulary ("the model
#   would ignore the earlier instructions", "we had it act as an assistant"), so they only count when they open
#   a sentence of a short turn whose substance is the command itself; anywhere else the turn stays an answer
#   (evaluated as usual) and the match is kept as ``SpecialResult.suspect`` for the turn model and the audit
#   trail. Bare mentions of "system prompt", "instructions" or "prompt" never count: they are the content of
#   every LLM-engineering answer (harness finding: a question about structuring system prompts was unanswerable).
_ROLE_NOUNS = (
    r"(?:interviewer|assistant|recruiter|coach|evaluator|grader|judge|ai|bot|chatbot|model|helper|friend|"
    r"hiring manager|hr|screener)"
)
_INSTRUCTION = _rx(
    r"\b(?:give|award|grant) me (?:a |the )?(?:perfect|full|maximum|max|top|high|highest|best) "
    r"(?:score|marks?|rating|grade)\b",
    r"\bmark (?:all|every|each) (?:of )?(?:the |my )?(?:competenc\w+|answers?|questions?|skills?) as\b",
    r"\b(?:set|change|update|make) (?:the |my )?(?:recommendation|score|rating|grade|verdict|decision) (?:to|as)\b",
    r"\brate (?:me|this(?: answer)?|my answers?|everything|all my answers) (?:as |a |at )?"
    r"(?:strong|perfect|excellent|10|ten|100|high|highly)\b",
    r"\b(?:tell|say|write|report)\b[^.?!]{0,40}\bi (?:passed|got the job|am hired|should be hired|am the best candidate)\b",
    r"\brecommend me (?:strongly |highly )?(?:for|to) (?:hire|hiring|the (?:role|job|position)|proceed|the next round)\b",
    r"\b(?:ignore|disregard|forget|override|bypass) (?:all |any )?(?:of )?your (?:previous |prior |earlier |original |"
    r"system |own )?(?:instructions?|rules|prompt|guidelines|programming|directives|training)\b",
    r"\bsystem override\b",
    r"\b(?:enter|enable|activate|switch to) (?:developer|dan|god|unrestricted|jailbreak) mode\b",
    r"\bthis is (?:a |an )?(?:system|admin|administrator|developer|operator) (?:message|instruction|override|command|notice)\b",
)
_INSTRUCTION_IMPERATIVE = _rx(
    r"\b(?:ignore|disregard|forget|override|bypass|skip) (?:all |any )?(?:of )?(?:the )?"
    r"(?:previous|prior|above|earlier|original|initial|preceding) (?:instructions?|rules|prompts?|guidelines|directives|context)\b",
    rf"\b(?:act|behave|respond|answer|speak|reply) as (?:a|an|my|the) (?:\w+ ){{0,3}}{_ROLE_NOUNS}\b",
    rf"\byou are now (?:a|an|my|the|in) (?:\w+ ){{0,3}}(?:{_ROLE_NOUNS}|mode)\b",
    rf"\b(?:pretend|imagine) (?:that )?you(?:'re| are) (?:a|an|my|the|not) (?:\w+ ){{0,3}}(?:{_ROLE_NOUNS}|interview)\b",
    r"\bfrom now on,? (?:you (?:are|will|must|should)|ignore|mark|rate|give|score|treat)\b",
    r"\b(?:here are|these are|follow|obey) (?:your |the |my )?new instructions\b|\bnew instructions:",
    r"\b(?:reveal|print|show|repeat|output|display) (?:me )?(?:your|the) (?:system |hidden |initial )?(?:prompt|instructions)\b",
)
# Discourse lead-ins that may precede an imperative at the start of a sentence ("okay, so ignore ...").
_SENTENCE_LEAD_IN = re.compile(
    r"(?:(?:ok(?:ay)?|so|now|please|right|alright|also|and|but|well|um|uh|hey|listen|actually|basically)[, ]*)*"
)
# A turn with at most this many substance words that opens with a jailbreak phrase is the instruction itself.
INSTRUCTION_SHORT_TURN_WORDS = 12


def _opens_sentence(norm: str, pos: int) -> bool:
    """True when ``pos`` starts a sentence of ``norm`` (optionally after a lead-in such as "okay, so")."""
    prefix = norm[:pos]
    start = max(prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?")) + 1
    return _SENTENCE_LEAD_IN.fullmatch(prefix[start:].strip()) is not None


_SUBSTANCE_STOP = frozenset(
    "the a an and or but so of to in on for with that this it its was were is are be been i we you they he she "
    "me my our us them their there then when where what which who how if as at by from into about like just "
    "kind sort thing things stuff yeah yes no okay ok well um uh hmm er ah oh mm really basically actually "
    "maybe guess think know mean sure right also very much more some any all do did does done have has had "
    "get got go going went can could would should will".split()
)


def substance_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in _SUBSTANCE_STOP and len(w) > 2]


_QUESTION_REFERENCE = re.compile(r"\b(?:the question|you mean|you asking|you referring|repeat|rephrase)\b")


def classify(
    text: str,
    *,
    awaiting_answer: bool = True,
    coding_active: bool = False,
    questions_invited: bool = False,
) -> SpecialResult:
    """Classify one candidate turn. ``awaiting_answer`` is True when an
    interviewer question is pending (candidate questions are then only
    recognised when they clearly concern the role/process).
    ``questions_invited`` is True after the interviewer invited the
    candidate's questions: any question is then a candidate question and a
    decline ("no further questions") is recognised."""
    norm = normalize(text)
    if not norm or not re.search(r"[a-z0-9]", norm):
        return SpecialResult(SpecialKind.EMPTY, 1.0)
    tokens = words(norm)
    content = content_words(norm)
    n = len(tokens)
    stripped = re.sub(r"[^a-z0-9' ]", "", norm).strip()
    ends_question = norm.rstrip().endswith("?")

    # Thinking sounds alone ("hmm", "um...") - the candidate is thinking.
    if n <= 3 and (not content or stripped in _THINKING):
        return SpecialResult(SpecialKind.WANTS_TIME, 0.8, stripped)
    # Pure backchannel ("mm-hmm", "okay", "yeah sure") - at most 3 words.
    if n <= 3 and (stripped in _BACKCHANNELS or " ".join(content) in _BACKCHANNELS):
        return SpecialResult(SpecialKind.BACKCHANNEL, 0.9, stripped)

    # Hint requests (meaningful in coding; outside coding they are declined politely).
    if n <= 16:
        m = _WANTS_HINT.search(norm)
        if m:
            return SpecialResult(SpecialKind.WANTS_HINT, 0.9 if coding_active else 0.75, m.group(0))

    # Repeat requests are short and unambiguous.
    if n <= 14:
        m = _REPEAT.search(norm)
        if m:
            return SpecialResult(SpecialKind.REPEAT, 0.95, m.group(0))

    # Attempts to instruct the interviewer (prompt injection): never followed; the turn is redirected. The
    # generic openers only count when they are the turn (see the tiers above); otherwise they mark the answer
    # as suspect and it is evaluated like any other.
    m = _INSTRUCTION.search(norm)
    if m:
        return SpecialResult(SpecialKind.INSTRUCTION, 0.9, m.group(0))
    suspect: str | None = None
    for m in _INSTRUCTION_IMPERATIVE.finditer(norm):
        if _opens_sentence(norm, m.start()) and len(substance_words(norm)) <= INSTRUCTION_SHORT_TURN_WORDS:
            return SpecialResult(SpecialKind.INSTRUCTION, 0.75, m.group(0))
        suspect = suspect or m.group(0)

    # Clarification requests: short, about the question itself.
    if n <= 30:
        m = _CLARIFY.search(norm)
        if m and (ends_question or n <= 15 or _QUESTION_REFERENCE.search(norm)):
            return SpecialResult(SpecialKind.CLARIFICATION, 0.85, m.group(0))
    # Longer clarifying questions that offer options ("are you looking for X, or should I focus on Y?").
    if awaiting_answer and n <= 70 and ends_question and not questions_invited:
        m = _CLARIFY_OPTIONS.search(norm)
        if m and norm.count("?") <= 2:
            return SpecialResult(SpecialKind.CLARIFICATION, 0.8, m.group(0))

    # "Let me think" / "give me a second" when nothing substantive follows.
    if n <= 16:
        m = _WANTS_TIME.search(norm)
        if m:
            rest = _WANTS_TIME_FILLER.sub(" ", norm[m.end() :])
            if len(words(rest)) <= 2:
                return SpecialResult(SpecialKind.WANTS_TIME, 0.9, m.group(0))

    # "I don't know" / "I haven't used that" without an attempt.
    if n <= 18:
        m = _DONT_KNOW.search(norm)
        if m:
            attempt = re.search(
                r"\b(?:but|though|however|i think|i guess|i would|i'd|probably|maybe|might)\b", norm
            )
            remaining = len(words(norm[m.end() :]))
            if remaining <= 2 or (not attempt and remaining <= 6):
                return SpecialResult(SpecialKind.DONT_KNOW, 0.85, m.group(0))

    # After the invitation to ask questions: a decline closes, any question is the candidate's.
    if questions_invited and n <= 70:
        asks = ends_question or bool(_INTERROGATIVE_START.match(norm))
        m = _NO_QUESTIONS.search(norm)
        if m and not ends_question:
            return SpecialResult(SpecialKind.NO_QUESTIONS, 0.85, m.group(0))
        # Short declines in free form ("No, thank you so much.", "Not really, thanks for your time"): a reply
        # that starts with a negation and asks nothing is a decline, not a candidate question (S9).
        decline = _DECLINE_START.match(norm) if not asks and n <= 12 else None
        if decline:
            return SpecialResult(SpecialKind.NO_QUESTIONS, 0.8, decline.group(0))
        if asks:
            return SpecialResult(SpecialKind.CANDIDATE_QUESTION, 0.85, "invited")

    # Questions about the role/company/process.
    if n <= 40 and (ends_question or not awaiting_answer):
        m = _CANDIDATE_Q.search(norm)
        if m and not _QUESTION_REFERENCE.search(norm):
            return SpecialResult(SpecialKind.CANDIDATE_QUESTION, 0.8, m.group(0))

    # Too little to work with: a few words, or fragments without substance (often a speech-
    # recognition or end-of-turn glitch). The interviewer asks for more; nothing is scored.
    if awaiting_answer and not questions_invited and not ends_question and n <= 20:
        if len(substance_words(norm)) <= 1:
            kind = SpecialKind.BRIEF if n <= 4 else SpecialKind.GARBLED
            return SpecialResult(kind, 0.7, stripped)

    return SpecialResult(SpecialKind.NONE, suspect=suspect) if suspect else NONE_RESULT


# Hedged / partial answers: the candidate says someone else did it, they were not involved, did not go into
# the details or are not sure. They stay answers (evaluated as usual); the policy uses them to stop drilling
# (at most one gentle follow-up on that topic, then another area) - harness finding: three follow-ups in a row
# after "I wasn't directly involved" / "The team handled the initial configuration".
HEDGE_NOT_INVOLVED = "not_involved"
HEDGE_NO_DETAIL = "no_detail"
HEDGE_UNSURE = "unsure"
HEDGE_NO_EXPERIENCE = "no_experience"
_ACTION_VERBS = (
    r"(?:handle|handled|own|owned|manage|managed|work on|worked on|set up|setup|configure|configured|design|"
    r"designed|architect|architected|lead|led|touch|touched|deal with|dealt with|build|built|write|wrote|run|ran|"
    r"implement|implemented|"
    r"drive|drove|do that|do the|do it|make|made|decide|decided)"
)
_HEDGES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        HEDGE_NOT_INVOLVED,
        _rx(
            r"\bi (?:wasn'?t|was not|was never)(?: \w+){0,2} (?:involved|part of|responsible|the one|in charge|"
            r"hands on|privy|exposed)\b",
            rf"\bi (?:didn'?t|did not|never)(?: (?:personally|directly|really|actually|myself))? {_ACTION_VERBS}\b",
            r"\b(?:that|this|it|those|these)(?: \w+){0,3} (?:was|were) (?:handled|managed|owned|done|led|set up|"
            r"configured|run|built|designed|taken care of|implemented|driven|decided) (?:\w+ )?by\b",
            r"\b(?:was|were|are|is) (?:handled|managed|owned|run|operated|maintained|led|designed|built|"
            r"taken care of|looked after) by (?:the|a|an|another|a different|a separate|the other|my|our|other)"
            r"(?: [\w']+){0,2} (?:team|teams|teammates?|colleagues?|manager|lead|engineers?|architects?|group)\b",
            r"\bmy (?:direct |hands on |personal |own |actual )?(?:responsibility|scope|role|part|involvement|"
            r"experience)(?: \w+){0,2} (?:was|is) (?:limited|restricted|only|just)\b",
            r"\b(?:someone|somebody|others) else\b",
            r"\b(?:the|another|a different|a separate|the other|my|our)(?: [\w']+){0,2} (?:team|teammates?|"
            r"colleagues?|manager|lead|engineers?|architects?)(?: (?:actually|already|mostly|really|just|"
            r"basically|originally|initially))? (?:handled|managed|owned|took care of|set up|"
            r"configured|led|ran|built|designed|implemented|drove|decided|was responsible|were responsible|did that|"
            r"did the|did it)\b",
            r"\bi can'?t (?:really |honestly )?speak to\b",
        ),
    ),
    (
        HEDGE_NO_DETAIL,
        _rx(
            r"\bi (?:didn'?t|did not|never)(?: really| personally)? (?:dig|get|go|look|dive)(?: \w+){0,2} "
            r"(?:into|deep|deeper)\b",
            r"\b(?:don'?t|do not|can'?t|cannot) (?:really |quite )?(?:remember|recall)(?: \w+){0,3} (?:details|"
            r"specifics|exact|numbers|exactly|specifically)\b",
            r"\bnot (?:sure|certain) (?:about |of )?(?:the )?(?:details|specifics|exact|exactly|numbers)\b",
            r"\b(?:above|beyond) my (?:pay grade|scope|level)\b",
        ),
    ),
    (
        HEDGE_NO_EXPERIENCE,
        _rx(
            r"\bi (?:don'?t|do not) (?:really )?have (?:any |much |direct |hands on |real |practical )*"
            r"(?:experience|exposure)\b",
            r"\bi (?:haven'?t|have not) (?:really |actually )?(?:worked with|used|done|touched|dealt with)\b",
        ),
    ),
    (
        HEDGE_UNSURE,
        _rx(
            r"\bi(?:'m| am) not (?:really |totally |entirely |completely |a hundred percent |100 percent )?"
            r"(?:sure|certain)\b",
            r"\bas far as i (?:know|remember|recall|can tell)\b",
        ),
    ),
)


def hedge_kind(text: str) -> str | None:
    """The kind of hedge in an answer ("someone else handled that", "I wasn't directly involved", "I didn't
    dig into the details", "I'm not sure"), or None. Flow turns (repeat, clarification...) never count."""
    norm = normalize(text)
    if not norm:
        return None
    for kind, pattern in _HEDGES:
        if pattern.search(norm):
            return kind
    return None


def is_backchannel(text: str) -> bool:
    """Backchannel test used for barge-in resume decisions (<= 3 words)."""
    result = classify(text)
    if result.kind in (SpecialKind.BACKCHANNEL, SpecialKind.EMPTY):
        return True
    return result.kind is SpecialKind.WANTS_TIME and len(words(text)) <= 3


__all__ = [
    "HEDGE_NOT_INVOLVED",
    "HEDGE_NO_DETAIL",
    "HEDGE_NO_EXPERIENCE",
    "HEDGE_UNSURE",
    "NONE_RESULT",
    "NON_ANSWER_KINDS",
    "SPECIAL_QUALITY",
    "SpecialKind",
    "SpecialResult",
    "classify",
    "content_words",
    "hedge_kind",
    "is_backchannel",
    "normalize",
    "words",
]
