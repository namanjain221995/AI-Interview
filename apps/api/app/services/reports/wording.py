"""Neutral, evidence-based wording for report text, and the candidate's voice.

Model-written text (narratives, evaluator evidence and notes, contradiction
descriptions, claim notes, coding reviews) sometimes judges the person:
"failed to", "ignored the question", "admitted". Reports rewrite such wording
into neutral, evidence-based wording ("did not", "did not address", "said")
when they are assembled and when they are rendered, for both audiences.
Stored records (evidence rows, session state, earlier report versions) are
never modified: every function here returns new text or a new document.

Technical uses keep their meaning: code that "ignores the input", a webhook
that "admits pods" or "was ignored" (passive) are left alone, and failures of
systems ("the pod failed to start") become the equivalent "did not start".
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

# --------------------------------------------------------------------------
# Verb forms
# --------------------------------------------------------------------------

_GERUND_IRREGULAR = {
    "be": "being",
    "see": "seeing",
    "agree": "agreeing",
    "flee": "fleeing",
    "free": "freeing",
    "lie": "lying",
    "die": "dying",
    "tie": "tying",
    "dye": "dyeing",
}
# Multi-syllable verbs stressed on the last syllable double their final consonant.
_DOUBLE_FINAL = frozenset(
    {
        "admit", "commit", "submit", "omit", "permit", "emit", "transmit", "refer", "prefer", "occur",
        "recur", "incur", "deter", "control", "patrol", "compel", "propel", "expel", "rebut", "regret",
        "forget", "begin", "upset", "equip", "acquit", "abet", "outrun", "rerun",
    }
)  # fmt: skip
_VOWELS = "aeiou"
# Verbs evaluator text coordinates after "failing to X or Y" (Y gets the same form as X).
KNOWN_VERBS = frozenset(
    {
        "acknowledge", "address", "answer", "apply", "articulate", "assess", "build", "calculate", "check",
        "cite", "clarify", "compare", "compute", "connect", "consider", "count", "cover", "define", "demonstrate",
        "describe", "design", "detail", "detect", "discuss", "distinguish", "document", "engage", "estimate",
        "evaluate", "explain", "explore", "follow", "give", "handle", "identify", "illustrate", "implement",
        "include", "iterate", "justify", "link", "list", "map", "measure", "meet", "mention", "monitor",
        "name", "offer", "outline", "parse", "plan", "process", "produce", "prove", "provide", "quantify",
        "reach", "recognise", "recognize", "reconcile", "relate", "report", "respond", "return", "run",
        "scale", "separate", "share", "show", "solve", "sort", "specify", "split", "state", "substantiate",
        "summarise", "summarize", "support", "test", "track", "use", "validate", "verify", "write",
    }
)  # fmt: skip
_NOT_VERBS = frozenset(
    {
        "the", "a", "an", "this", "that", "these", "those", "its", "their", "his", "her", "my", "our",
        "your", "any", "some", "all", "each", "every", "no", "it", "them", "him", "me", "us",
    }
)  # fmt: skip


def _syllables(word: str) -> int:
    return len(re.findall(r"[aeiouy]+", word.lower())) or 1


def gerund(verb: str) -> str:
    """``explain`` -> ``explaining``, ``parse`` -> ``parsing``, ``run`` -> ``running``."""
    lower = verb.lower()
    if lower in _GERUND_IRREGULAR:
        out = _GERUND_IRREGULAR[lower]
    elif lower.endswith("ie"):
        out = lower[:-2] + "ying"
    elif lower.endswith(("ee", "ye", "oe")):
        out = lower + "ing"
    elif lower.endswith("e") and len(lower) > 2:
        out = lower[:-1] + "ing"
    elif lower in _DOUBLE_FINAL or (
        _syllables(lower) == 1
        and len(lower) >= 3
        and lower[-1] not in _VOWELS + "wxy"
        and lower[-2] in _VOWELS
        and lower[-3] not in _VOWELS
    ):
        out = lower + lower[-1] + "ing"
    else:
        out = lower + "ing"
    return out[:1].upper() + out[1:] if verb[:1].isupper() else out


# --------------------------------------------------------------------------
# Context guards
# --------------------------------------------------------------------------

# Nouns that make "ignores"/"admits"/"was unable to" a technical statement about a system.
_TECHNICAL = re.compile(
    r"\b(?:code|solution|function|implementation|program|script|parser|loop|query|regex|filter|handler|"
    r"tests?|lines?|input|output|method|class|module|logic|pipeline|controllers?|scheduler|webhooks?|"
    r"admission|gatekeeper|kyverno|opa|policy|policies|cluster|pods?|nodes?|service|server|api|requests?|"
    r"ingress|gateway|queue|firewall|load balancer|traffic|packets?|alerts?|monitor|probe|job|cron|"
    r"container|image|deployment|rollout|terraform|plan|provider|system|stub|compiler|linter|cache)\b",
    re.IGNORECASE,
)
# Subjects that make it a statement about the person or their answer.
_PERSONAL = re.compile(
    r"\b(?:candidate|candidate's|they|he|she|you|i|answer|answers|response|responses|reply|explanation|"
    r"interviewee|applicant)\b",
    re.IGNORECASE,
)
_PASSIVE_BEFORE = re.compile(r"\b(?:was|were|is|are|be|been|being|get|gets|got|getting)\s+$", re.IGNORECASE)
_CLAUSE_START = re.compile(r"[.!?;:(\n]")


def _clause_before(text: str, start: int, limit: int = 90) -> str:
    window = text[max(0, start - limit) : start]
    cut = max((m.end() for m in _CLAUSE_START.finditer(window)), default=0)
    return window[cut:]


def _is_technical(text: str, start: int) -> bool:
    """True when the nearest subject-like word before ``start`` (same clause) is a technical noun."""
    before = _clause_before(text, start)
    tech = [m.end() for m in _TECHNICAL.finditer(before)]
    person = [m.end() for m in _PERSONAL.finditer(before)]
    return bool(tech) and (not person or tech[-1] > person[-1])


def _is_passive(text: str, start: int) -> bool:
    return bool(_PASSIVE_BEFORE.search(text[max(0, start - 12) : start]))


# --------------------------------------------------------------------------
# Rewrite rules
# --------------------------------------------------------------------------

_ADV = r"(?:(?:completely|entirely|totally|utterly|clearly|simply)\s+)?"
# "failing to clearly explain" (an -ly adverb that is not itself a verb such as "apply").
_SPLIT_ADVERB = r"(?:(?P<adv>(?!(?:apply|reply|supply|comply|rely|multiply|imply|fly|ally)\b)\w+ly)\s+)?"
# "... explain the trigger or justify the choice": the coordinated verb takes the same form.
_COORD = (
    r"(?:(?P<between>(?:\s+[^\s.,;:!?()]+){0,6}?)(?P<conj>,?\s+(?:or|and)\s+)(?P<verb2>"
    + "|".join(sorted(KNOWN_VERBS, key=len, reverse=True))
    + r")\b)?"
)
_THEN_VERB = r"\s+" + _SPLIT_ADVERB + r"(?P<verb>[A-Za-z][\w-]*)" + _COORD
# "ignores these triggers entirely" -> "does not address these triggers at all".
_IGNORE_TAIL = r"(?:(?P<rest>(?:\s+[^\s.,;:!?()]+){1,8}?)(?P<tail>\s+(?:entirely|completely|altogether))\b)?"


def _capitalise_like(source: str, replacement: str) -> str:
    if source[:1].isupper() and replacement:
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _gerund_phrase(prefix: str) -> Callable[[re.Match[str]], str | None]:
    """Gerund form: "failing to explain X or justify Y" -> "not explaining X or justifying Y"."""

    def build(match: re.Match[str]) -> str | None:
        verb = match.group("verb")
        if verb.lower() in _NOT_VERBS:
            return None
        adv = f"{match.group('adv')} " if match.group("adv") else ""
        out = f"{prefix}{adv}{gerund(verb)}"
        if match.group("verb2"):
            out += match.group("between") + match.group("conj") + gerund(match.group("verb2"))
        return out

    return build


def _fixed(replacement: str) -> Callable[[re.Match[str]], str | None]:
    return lambda match: match.expand(replacement)


def _personal(replacement: str) -> Callable[[re.Match[str]], str | None]:
    """Rewrite only statements about the person (not passives, not technical subjects)."""

    def build(match: re.Match[str]) -> str | None:
        text, start = match.string, match.start()
        if _is_passive(text, start) or _is_technical(text, start):
            return None
        return match.expand(replacement)

    return build


def _ignore(replacement: str) -> Callable[[re.Match[str]], str | None]:
    def build(match: re.Match[str]) -> str | None:
        text, start = match.string, match.start()
        if _is_passive(text, start) or _is_technical(text, start):
            return None
        if match.group("tail"):
            return f"{replacement}{match.group('rest')} at all"
        return replacement

    return build


_Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str | None]]


def _rule(pattern: str, build: Callable[[re.Match[str]], str | None]) -> _Rule:
    return re.compile(pattern, re.IGNORECASE), build


#: Judgemental wording -> neutral, evidence-based wording (both audiences). Applied in order.
NEUTRAL_REWRITES: tuple[_Rule, ...] = (
    _rule(r"\b(may|might|could|would|will|can|should|must)\s+" + _ADV + r"fail to\b", _fixed(r"\1 not")),
    _rule(r"\b" + _ADV + r"failed to\b", _fixed("did not")),
    _rule(r"\b" + _ADV + r"fails to\b", _fixed("does not")),
    _rule(r"\b" + _ADV + r"fail to\b", _fixed("do not")),
    _rule(r"\b" + _ADV + r"failing to" + _THEN_VERB, _gerund_phrase("not ")),
    # "a (notable) failure to address" -> "a (notable) gap in addressing" (the noun phrase stays a noun phrase).
    _rule(r"\bfailures? to" + _THEN_VERB, _gerund_phrase("gap in ")),
    _rule(r"\b(?:was|were) unable to\b", _personal("did not")),
    _rule(r"\bis unable to\b", _personal("does not")),
    _rule(r"\bare unable to\b", _personal("do not")),
    _rule(r"\b(?:was|were|seemed) unwilling to\b", _personal("did not")),
    _rule(r"\b(?:is|seems) unwilling to\b", _personal("does not")),
    _rule(r"\bare unwilling to\b", _personal("do not")),
    _rule(r"\brefused to\b", _fixed("did not")),
    _rule(r"\brefuses to\b", _fixed("does not")),
    _rule(r"\brefusing to" + _THEN_VERB, _gerund_phrase("not ")),
    _rule(r"\bneglected to\b", _fixed("did not")),
    _rule(r"\bneglects to\b", _fixed("does not")),
    _rule(r"\bneglecting to" + _THEN_VERB, _gerund_phrase("not ")),
    _rule(r"\bstruggled to\b", _fixed("did not fully")),
    _rule(r"\bstruggles to\b", _fixed("does not fully")),
    _rule(r"\bstruggle to\b", _fixed("do not fully")),
    _rule(r"\bstruggling to" + _THEN_VERB, _gerund_phrase("not fully ")),
    _rule(r"\bstruggled with\b", _fixed("had difficulty with")),
    _rule(r"\bstruggles with\b", _fixed("has difficulty with")),
    _rule(r"\bstruggle with\b", _fixed("have difficulty with")),
    _rule(r"\bstruggling with\b", _fixed("having difficulty with")),
    _rule(r"\b" + _ADV + r"ignored\b" + _IGNORE_TAIL, _ignore("did not address")),
    _rule(r"\b" + _ADV + r"ignores\b" + _IGNORE_TAIL, _ignore("does not address")),
    _rule(r"\b" + _ADV + r"ignoring\b" + _IGNORE_TAIL, _ignore("not addressing")),
    _rule(r"\badmitted to ((?:not |never )?\w+ing)\b", _personal(r"acknowledged \1")),
    _rule(r"\badmits to ((?:not |never )?\w+ing)\b", _personal(r"acknowledges \1")),
    _rule(r"\badmitted\b", _personal("said")),
    _rule(r"\badmits\b", _personal("says")),
    _rule(r"\badmitting\b", _personal("saying")),
    _rule(r"\b(?:dodged|sidestepped|evaded)(?=\s+(?:\w+\s+){0,2}questions?\b)", _personal("did not directly answer")),
    _rule(r"\b(?:dodges|sidesteps|evades)(?=\s+(?:\w+\s+){0,2}questions?\b)", _personal("does not directly answer")),
    _rule(r"\b(?:dodging|sidestepping|evading)(?=\s+(?:\w+\s+){0,2}questions?\b)", _personal("not directly answering")),
    _rule(r"\bdeflected\b", _personal("redirected")),
    _rule(r"\bdeflects\b", _personal("redirects")),
    _rule(r"\bdeflecting\b", _personal("redirecting")),
    _rule(r"\bevasively\b", _fixed("indirectly")),
    _rule(r"\bevasive\b", _fixed("indirect")),
)  # fmt: skip

# Quoted words (the candidate's or the resume's own) are never rewritten. A single-quoted passage opens
# before a non-space character and closes after one, so apostrophes ("candidate's", "didn't") and empty
# code strings ("return ''") are not mistaken for quotation marks.
_QUOTED = re.compile(
    r"\"[^\"\n]{1,400}\"|\u201c[^\u201d\n]{1,400}\u201d"
    r"|(?<![\w'])'(?=[^\s'])(?:[^'\n]|'(?=\w)){1,400}?(?<=[^\s'])'(?!\w)"
)


def _rewrite_segment(text: str) -> tuple[str, int]:
    count = 0
    for pattern, build in NEUTRAL_REWRITES:

        def _sub(match: re.Match[str], _build: Callable[[re.Match[str]], str | None] = build) -> str:
            nonlocal count
            replacement = _build(match)
            if replacement is None:
                return match.group(0)
            count += 1
            return _capitalise_like(match.group(0), replacement)

        text = pattern.sub(_sub, text)
    return text, count


def neutral_wording(text: str | None) -> tuple[str | None, int]:
    """Rewrite judgemental verbs into neutral, evidence-based wording; returns ``(text, rewrites)``.

    Quoted passages are kept verbatim. Idempotent: no replacement matches a rule again."""
    if not text:
        return text, 0
    out: list[str] = []
    count = 0
    position = 0
    for quoted in _QUOTED.finditer(text):
        segment, n = _rewrite_segment(text[position : quoted.start()])
        out += [segment, quoted.group(0)]
        count += n
        position = quoted.end()
    segment, n = _rewrite_segment(text[position:])
    out.append(segment)
    return "".join(out), count + n


def neutral(text: Any) -> Any:
    """``neutral_wording`` for any value: strings are rewritten, everything else is returned as is."""
    return neutral_wording(text)[0] if isinstance(text, str) else text


# --------------------------------------------------------------------------
# The candidate's voice
# --------------------------------------------------------------------------

_THIRD_PERSON = re.compile(r"\b(the candidate)(?:'s|’s)?(\s+(\w+))?", re.IGNORECASE)
_TO_BE = {"is": "are", "was": "were", "has": "have", "does": "do", "isn't": "aren't", "wasn't": "weren't",
          "hasn't": "haven't", "doesn't": "don't"}  # fmt: skip


def _present_plural(verb: str) -> str:
    lower = verb.lower()
    if lower in _TO_BE:
        return _TO_BE[lower]
    if lower.endswith("ies") and len(lower) > 4:
        return verb[:-3] + "y"
    if lower.endswith(("sses", "shes", "ches", "xes", "zes")):
        return verb[:-2]
    if lower.endswith("s") and not lower.endswith(("ss", "us", "is")):
        return verb[:-1]
    return verb


def second_person(text: str | None) -> tuple[str | None, int]:
    """Candidate text addresses the candidate: "The candidate explains" -> "You explain",
    "the candidate's answer" -> "your answer"."""
    if not text:
        return text, 0
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        possessive = match.group(0)[len(match.group(1)) :].startswith(("'s", "’s"))
        start = match.start() == 0 or text[: match.start()].rstrip()[-1:] in (".", "!", "?", ":")
        word = ("Your" if start else "your") if possessive else ("You" if start else "you")
        rest = match.group(2) or ""
        if not possessive and match.group(3):
            rest = rest[: len(rest) - len(match.group(3))] + _present_plural(match.group(3))
        return word + rest

    return _THIRD_PERSON.sub(_sub, text), count


def candidate_voice(text: Any) -> Any:
    """Neutral wording plus second person, for evaluator text shown to the candidate."""
    if not isinstance(text, str):
        return text
    return second_person(neutral_wording(text)[0])[0]


# --------------------------------------------------------------------------
# Report documents
# --------------------------------------------------------------------------


def _rewrite(container: dict[str, Any] | None, keys: tuple[str, ...], fn: Callable[[Any], Any]) -> None:
    if not isinstance(container, dict):
        return
    for key in keys:
        value = container.get(key)
        if isinstance(value, str):
            container[key] = fn(value)
        elif isinstance(value, list):
            container[key] = [fn(v) if isinstance(v, str) else v for v in value]


def _items(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


_EVALUATION_TEXT = (
    "summary",
    "missing_details",
    "unsupported_claims",
    "technical_evidence",
    "measurable_outcomes",
)
_CODING_TEXT = (
    "correctness_summary",
    "complexity_assessment",
    "hints_impact",
    "strengths",
    "weaknesses",
    "process_observations",
)
_RECRUITER_NARRATIVE_TEXT = (
    "executive_summary",
    "claims_summary",
    "contradictions_summary",
    "coding_summary",
    "recommendation_rationale",
    "areas_for_human_followup",
    "risks",
)
_CANDIDATE_NARRATIVE_TEXT = (
    "overall_summary", "coding_feedback", "problem_solving_feedback", "communication_feedback", "next_steps",
)  # fmt: skip


def neutral_recruiter_document(document: dict[str, Any]) -> dict[str, Any]:
    """A copy of a recruiter report document (``assemble_report_data`` output or stored recruiter
    content) with neutral wording in its model-written text: evidence, contradiction descriptions,
    claim notes, interviewer reasons, evaluator notes (including what a contradiction finding conflicts
    with), coding reviews and their evidence, and the narrative. Quotes, transcripts, questions, the
    candidate's statements, resume claims and human notes are never touched."""
    doc = copy.deepcopy(document)
    for item in _items(doc.get("evidence")):
        _rewrite(item, ("evidence",), neutral)
    for item in _items(doc.get("contradictions")):
        _rewrite(item, ("description",), neutral)
    for item in _items(doc.get("claims")):
        _rewrite(item, ("note",), neutral)
    for question in _items(doc.get("questions")):
        _rewrite(question, ("reason",), neutral)
        for answer in _items(question.get("answers")):
            evaluation = answer.get("evaluation")
            _rewrite(evaluation, _EVALUATION_TEXT, neutral)
            # The finding's "statement" is the candidate's own claim and stays verbatim.
            for finding in _items((evaluation or {}).get("contradictions")):
                _rewrite(finding, ("conflicts_with",), neutral)
    for coding in _items(doc.get("coding")):
        evaluation = coding.get("evaluation")
        _rewrite(evaluation, _CODING_TEXT, neutral)
        for item in _items((evaluation or {}).get("evidence")):
            _rewrite(item, ("evidence",), neutral)
    narrative = doc.get("narrative")
    if isinstance(narrative, dict):
        _rewrite(narrative, _RECRUITER_NARRATIVE_TEXT, neutral)
        for finding in _items(narrative.get("competency_findings")):
            _rewrite(finding, ("summary", "strengths", "weaknesses"), neutral)
    return doc


def neutral_candidate_document(content: dict[str, Any]) -> dict[str, Any]:
    """A copy of candidate report content with evaluator text in neutral, second-person wording
    (feedback lists, coding review, cited evidence, narrative)."""
    doc = copy.deepcopy(content)
    for question in _items(doc.get("questions")):
        _rewrite(
            question.get("feedback"),
            ("summary", "missing_details", "measurable_outcomes", "technical_evidence"),
            candidate_voice,
        )
    for coding in _items(doc.get("coding")):
        _rewrite(coding, ("correctness_summary", "strengths", "weaknesses"), candidate_voice)
    for item in _items(doc.get("evidence")):
        _rewrite(item, ("evidence",), candidate_voice)
    narrative = doc.get("narrative")
    if isinstance(narrative, dict):
        _rewrite(narrative, _CANDIDATE_NARRATIVE_TEXT, candidate_voice)
        for section, keys in (
            ("technical_strengths", ("title", "detail")),
            ("areas_to_improve", ("title", "detail", "learning_guidance")),
            ("strong_answers", ("note",)),
            ("incomplete_answers", ("note",)),
            ("recommended_study_areas", ("why", "next_steps")),
        ):
            for item in _items(narrative.get(section)):
                _rewrite(item, keys, candidate_voice)
    return doc
