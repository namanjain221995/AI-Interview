"""Grounding of what the interviewer attributes to the candidate.

* Speech: an attribution ("you mentioned X", "earlier you said X", "as you described, X", "the X you
  mentioned", "your answer about X") must be supported by the candidate's OWN turns - their answers and other
  utterances. The interviewer's questions, the job text and the model-written conversation summary never
  count as something the candidate said. A reference framed as the resume ("your resume says X", "the resume
  mentions X", "on your resume") must be supported by the resume profile or its claims instead.
* Inconsistency records written by a model (turn decision, answer evaluation): every side attributed to the
  candidate must be supported by their turns, a side framed as the resume by the resume, and a record needs at
  least one side in the candidate's own words (:func:`inconsistency_issue`).

Support is decided per window of at most two consecutive sentences of ONE candidate turn, not over everything
said together (harness finding: each word of "Karpenter configuration for cost reduction" occurred somewhere -
in "I wasn't involved in the overall cost-reduction initiatives" and in "the existing Karpenter settings" - so
the old union-of-words check passed an invented attribution). The support must have the same polarity (an
affirmative attribution is not supported by a negated statement), and spellings from speech recognition are
tolerated ("Carpenter" for Karpenter). Pure functions, no I/O; the index is built lazily, once per turn.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.services.interview.state import InterviewState

# ------------------------------------------------------------------ tokens

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Clause boundaries for polarity: punctuation and contrastive / subordinating conjunctions ("and" is not a
# boundary: "we didn't use Helm and Argo" negates both).
_CLAUSE = re.compile(
    r"[,;:()]|\s[-–—]\s|\b(?:but|although|though|whereas|while|because|since|except|however|yet|"
    r"unless|until|so that)\b",
    re.IGNORECASE,
)
_NEGATIONS = frozenset(
    "not never cannot neither nor nothing nobody dont didnt doesnt wasnt werent cant couldnt wouldnt "
    "shouldnt isnt arent havent hasnt hadnt wont".split()
)
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "dozen": 12,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_NUMBER_RUN = re.compile(
    r"\b(?:"
    + "|".join([*_UNITS, *_SCALES])
    + r")(?:(?:\s+|-|\s+and\s+)(?:"
    + "|".join([*_UNITS, *_SCALES])
    + r"))*\b",
    re.IGNORECASE,
)


def _spoken_number(words: list[str]) -> int:
    total = current = 0
    for w in words:
        if w in _UNITS:
            current += _UNITS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        else:
            total += max(current, 1) * _SCALES[w]
            current = 0
    return total + current


def numbers_to_digits(text: str) -> str:
    """ "one hundred and twenty" -> "120", "twelve hundred" -> "1200", "10,000" -> "10000"."""
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)

    def repl(match: re.Match[str]) -> str:
        words = [w for w in re.split(r"[\s-]+", match.group(0).lower()) if w and w != "and"]
        return str(_spoken_number(words)) if words else match.group(0)

    return _NUMBER_RUN.sub(repl, text)


# Function words, framing words of attributions/records and generic verbs: never content.
_STOP = frozenset(
    "a an and are as at be been being but by can could did do does doing done for from had has have having how "
    "i if in into is it its me my of on or our so that the their them then there these they this those to us "
    "was we were what when where which who whom why will with would you your yours yourself he she his her "
    "about also just like some really very much many lot lots any all more most other another each every own "
    "same such than too only even still again ever quite rather pretty bit kind sort thing things stuff "
    "mention mentioned mentions mentioning said say says saying told tell tells note noted notes described "
    "describe describes explained explain explains talked talk brought bring pointed point indicated indicate "
    "stated state states referred refer walked earlier before previously previous now later first then "
    "resume cv profile candidate candidates answer answers claim claims claimed claiming statement statements "
    "turn current conflict conflicts contradict contradicts contradiction possible clearly actually "
    "basically honestly maybe probably perhaps well okay ok yeah yes no use used uses using get got gets make "
    "made makes go going went come came part someone something somebody everyone everything around roughly "
    "approximately approx nearly almost over under".split()
)
FUZZY_MIN_LEN = 5
FUZZY_RATIO = 85.0
#: Share of an attributed phrase's content words a window of the candidate's words must contain.
MIN_COVERAGE = 0.5
#: Resume-framed sides are compared with the resume more leniently (paraphrases of claims are normal).
MIN_RESUME_COVERAGE = 0.4


def _is_negation(word: str) -> bool:
    return word in _NEGATIONS or word.endswith("n't")


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    if len(token) > 4 and token.endswith("ies"):
        token = token[:-3] + "y"
    elif len(token) > 4 and token.endswith(("sses", "ches", "shes", "xes", "zes")):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    return token


def _normalize(text: str) -> str:
    return (text or "").lower().replace("’", "'").replace("‘", "'")


def _token(word: str) -> str | None:
    word = word.split("'")[0]
    if word in _STOP or (len(word) < 3 and not word.isdigit()):
        return None
    return _stem(word)


def content_tokens(text: str) -> list[str]:
    """Stemmed content words (numbers as digits), in order."""
    out: list[str] = []
    for word in _WORD.findall(numbers_to_digits(_normalize(text))):
        if _is_negation(word):
            continue
        token = _token(word)
        if token:
            out.append(token)
    return out


def is_negated(text: str) -> bool:
    return any(_is_negation(w) for w in _WORD.findall(_normalize(text)))


# A clause about someone else's action ("the team handled that", "my manager led it"): the candidate's own
# negation elsewhere ("I didn't design the scaling policies") does not contradict it.
_THIRD_PARTY = re.compile(
    r"^\s*(?:(?:the|my|your|our|their|another|a|an|that|this|other)\s+)?(?:[\w-]+\s+){0,2}?"
    r"(?:team|teams|manager|lead|colleague|colleagues|engineer|engineers|group|department|vendor|someone|"
    r"somebody|others|they)\b",
    re.IGNORECASE,
)


def polar_tokens(text: str) -> list[tuple[str, bool | None]]:
    """(token, negated) per content word. Negation has forward scope inside its clause ("we audited with
    Access Analyzer to ensure we weren't granting excessive privileges": only "granting excessive privileges"
    is negated); words of a clause whose subject is someone else carry no polarity (None)."""
    out: list[tuple[str, bool | None]] = []
    for part in _CLAUSE.split(numbers_to_digits(_normalize(text))):
        if not part or not part.strip():
            continue
        third_party = bool(_THIRD_PARTY.match(part))
        negated = False
        for word in _WORD.findall(part):
            if _is_negation(word):
                negated = True
                continue
            token = _token(word)
            if token:
                out.append((token, None if third_party else negated))
    return out


# ------------------------------------------------------------------ support index


@dataclass(slots=True)
class Support:
    """How well the best window supports a phrase."""

    coverage: float  # share of the phrase's content words found with the same polarity in the window
    same: int  # matched words with the phrase's polarity
    opposite: int  # matched words only found with the opposite polarity
    window: str = ""


class SupportIndex:
    """Texts split into windows of one or two consecutive sentences (never across texts)."""

    def __init__(self, texts: Iterable[str], *, window: int = 2) -> None:
        # window -> {token: polarities seen} (True = negated, False = affirmative, None = third party)
        self._windows: list[tuple[dict[str, set[bool | None]], str]] = []
        vocab: set[str] = set()
        for text in texts:
            sentences = [s.strip() for s in _SENTENCE.split((text or "").strip()) if s.strip()]
            parsed = [polar_tokens(s) for s in sentences]
            for tokens in parsed:
                vocab.update(t for t, _ in tokens)
            for i in range(len(parsed)):
                for size in range(1, window + 1):
                    if i + size > len(parsed):
                        break
                    table: dict[str, set[bool | None]] = {}
                    for part in parsed[i : i + size]:
                        for token, negated in part:
                            table.setdefault(token, set()).add(negated)
                    if table:
                        self._windows.append((table, " ".join(sentences[i : i + size])))
        self._vocab = frozenset(vocab)
        self._fuzzy: dict[str, frozenset[str]] = {}

    @property
    def empty(self) -> bool:
        return not self._windows

    def _matches(self, token: str) -> frozenset[str]:
        """Corpus tokens that count as ``token`` (itself, or a close spelling for longer words)."""
        cached = self._fuzzy.get(token)
        if cached is not None:
            return cached
        found = {token} if token in self._vocab else set()
        if len(token) >= FUZZY_MIN_LEN and not token.isdigit():
            for other in self._vocab:
                if (
                    other != token
                    and len(other) >= FUZZY_MIN_LEN
                    and abs(len(other) - len(token)) <= 3
                    and fuzz.ratio(token, other) >= FUZZY_RATIO
                ):
                    found.add(other)
        result = frozenset(found)
        self._fuzzy[token] = result
        return result

    def support(self, phrase: str, *, polarity: bool = True) -> Support:
        wanted: dict[str, bool | None] = {}
        for token, negated in polar_tokens(phrase):
            if token not in wanted or wanted[token] is None:
                wanted[token] = negated
        if not wanted:
            return Support(1.0, 0, 0)
        polarities = {v for v in wanted.values() if v is not None}
        if len(polarities) > 1:
            # A mixed attribution ("X was an overstatement because you weren't involved") restates a
            # negative answer; polarity is checked for wholly affirmative or wholly negated ones only.
            polarity = False
        matches = {t: self._matches(t) for t in wanted}
        best = Support(0.0, 0, 0)
        for table, text in self._windows:
            same = opposite = 0
            for token, negated in wanted.items():
                seen: set[bool | None] = set()
                for alternative in matches[token]:
                    seen |= table.get(alternative, set())
                if not seen:
                    continue
                if not polarity or negated is None or None in seen or negated in seen:
                    same += 1
                else:
                    opposite += 1
            if not same and not opposite:
                continue
            candidate = Support(same / len(wanted), same, opposite, text)
            if (candidate.coverage, -candidate.opposite) > (best.coverage, -best.opposite):
                best = candidate
        return best

    def supports(self, phrase: str, *, threshold: float = MIN_COVERAGE, polarity: bool = True) -> bool:
        """True when one window contains at least ``threshold`` of the phrase's content words with the
        phrase's own polarity (with ``polarity=False``: with any polarity)."""
        if len(set(content_tokens(phrase))) < 2:
            return True  # too little content to judge
        return self.support(phrase, polarity=polarity).coverage >= threshold


# ------------------------------------------------------------------ attributions in speech

_ATTR_VERB = (
    r"(?:mentioned|said|told me|described|noted|brought up|talked about|explained|pointed out|indicated|"
    r"stated|referred to|walked me through)"
)
_RESUME_WORD = re.compile(r"\b(?:r[eé]sum[eé]|cv|curriculum|linkedin)\b", re.IGNORECASE)
_REFERENCE = re.compile(
    # "you mentioned X", "earlier you said that X", "you've also described X", "you were saying X"
    rf"\b(?:(?:earlier|before|previously|at the start)\s*,?\s+)?you(?:'ve| have)?\s+(?:also\s+|just\s+|"
    rf"earlier\s+|previously\s+)?(?:{_ATTR_VERB}|were saying)(?:\s+(?:earlier|before|previously))?\s*[,:]?\s*"
    rf"(?:that\s+|how\s+)?(?P<a>[^.?!;]{{3,240}})"
    # "as you said, X"
    rf"|\bas you (?:said|mentioned|described|noted|explained)\b\s*,?\s*(?P<b>[^.?!;]{{3,240}})"
    # "your answer about X"
    r"|\byour (?:earlier |previous |last )?(?:answer|point|comment|example|description|story) "
    r"(?:about|on|regarding|of)\s+(?P<c>[^.?!;]{3,240})"
    # "..., but then said X" / "and later mentioned X"
    rf"|\b(?:but|and|yet|while)\s+(?:then|later|now|also)\s+(?:you\s+)?{_ATTR_VERB}\b\s*,?\s*(?:that\s+)?"
    rf"(?P<d>[^.?!;]{{3,240}})"
    # "the Karpenter setup you mentioned"
    rf"|\b(?:the|that|those|these|this)\s+(?P<e>(?:[\w'/-]+\s+){{0,5}}?[\w'/-]+)\s+(?:that\s+)?you(?:'ve)?\s+"
    rf"(?:mentioned|described|talked about|brought up|said|noted)\b"
    # "what you meant by exactly regarding the retries", "what you were about to say regarding the retries" (live
    # finding 2026-10-10: a clarification credited the candidate with the topic of the interviewer's own question)
    r"|\bwhat\s+you\s+(?:meant|mean|were\s+(?:about\s+to\s+say|going\s+to\s+say|saying|getting\s+at)|"
    r"wanted\s+to\s+say)\s+(?:by|about|regarding|on|with|when\s+you\s+said)?\s*(?P<f>[^.?!;]{3,240})"
    r"|\byou\s+were\s+(?:about|going)\s+to\s+(?:say|explain|describe|mention)\s+(?:something\s+)?"
    r"(?:about|regarding|on)\s+(?P<g>[^.?!;]{3,240})",
    re.IGNORECASE,
)
_RESUME_REFERENCE = re.compile(
    r"\b(?:your|the)\s+(?:r[eé]sum[eé]|cv|profile)\s+(?:also\s+)?(?:says|mentions|lists|states|notes|shows|"
    r"describes|claims|includes|has|said|mentioned|listed)\s*(?:that\s+)?(?P<r>[^.?!;]{3,240})",
    re.IGNORECASE,
)
# Where the attributed content ends and the interviewer's own words begin.
_PHRASE_END = re.compile(
    r"\s*(?:[,;:]|\s[-–—]\s)\s*(?:and\s+|so\s+|but\s+|then\s+|yet\s+|while\s+|which\s+)?"
    r"(?=(?:how|what|which|why|when|where|who|could|can|would|will|did|do|does|is|are|was|were|should|"
    r"tell|walk|help|talk|say|imagine|suppose|if|i'd|i|let's|let|so|but|yet|and|then|while|whereas|"
    r"though|although)\b)"
    r"|\s+(?:but|yet|whereas|while|although|though)\s+"
    r"|\s+and\s+(?:then|later|now|also)\s+",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class Reference:
    """Content the interviewer attributes to the candidate (or to their resume)."""

    phrase: str
    resume: bool
    start: int  # span of the whole attribution in the text it was found in
    end: int


def _trim_phrase(raw: str) -> str:
    cut = _PHRASE_END.search(raw)
    phrase = raw[: cut.start()] if cut else raw
    phrase = re.sub(r"^\s*(?:that|how)\s+", "", phrase.strip(), flags=re.IGNORECASE)
    return phrase.strip(" ,;:-–—\"'")


def find_references(text: str) -> list[Reference]:
    """Attributions in ``text`` (in order). A reference is framed as the resume when "resume" / "CV" occurs
    in its own clause ("you mentioned on your resume ...", "your resume lists ...")."""
    refs: list[Reference] = []
    seen: list[tuple[int, int]] = []
    for match in _RESUME_REFERENCE.finditer(text or ""):
        phrase = _trim_phrase(match.group("r"))
        if phrase:
            refs.append(Reference(phrase, True, match.start(), match.end()))
            seen.append((match.start(), match.end()))
    for match in _REFERENCE.finditer(text or ""):
        group = next(g for g in ("a", "b", "c", "d", "e", "f", "g") if match.group(g) is not None)
        raw = match.group(group)
        phrase = raw.strip() if group == "e" else _trim_phrase(raw)
        if not phrase:
            continue
        start = match.start()
        if any(s <= start < e for s, e in seen):
            continue
        head = text[max(0, start - 30) : match.start(group)]
        resume = bool(_RESUME_WORD.search(head) or _RESUME_WORD.search(phrase.split(",")[0][:40]))
        if resume:
            phrase = re.sub(
                r"^(?:on|in)\s+(?:your|the)\s+(?:r[eé]sum[eé]|cv)\s*,?\s*(?:that\s+)?",
                "",
                phrase,
                flags=re.IGNORECASE,
            )
        refs.append(Reference(phrase, resume, start, match.end()))
    refs.sort(key=lambda r: r.start)
    return refs


# ------------------------------------------------------------------ context


def _flatten(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield str(value)


def candidate_texts(state: InterviewState) -> list[str]:
    """Everything the candidate said in this interview (answers and other candidate utterances)."""
    texts = [a.text for a in state.answers if a.text]
    known = set(texts)
    for turn in state.conversation:
        if turn.speaker == "candidate" and turn.text and turn.text not in known:
            texts.append(turn.text)
            known.add(turn.text)
    return texts


def resume_texts(state: InterviewState) -> list[str]:
    texts = [t for t in _flatten(state.candidate_profile or {}) if t]
    texts += [c.claim.claim for c in state.claims.values()]
    return texts


@dataclass(slots=True)
class GroundingContext:
    """Per-turn grounding sources; the indexes are built on first use."""

    candidate: Sequence[str]
    resume: Sequence[str]
    _candidate_index: SupportIndex | None = field(default=None, repr=False)
    _resume_index: SupportIndex | None = field(default=None, repr=False)

    @classmethod
    def from_state(cls, state: InterviewState, *, extra_candidate: Sequence[str] = ()) -> GroundingContext:
        return cls(candidate=[*candidate_texts(state), *extra_candidate], resume=resume_texts(state))

    @property
    def candidate_index(self) -> SupportIndex:
        if self._candidate_index is None:
            self._candidate_index = SupportIndex(self.candidate)
        return self._candidate_index

    @property
    def resume_index(self) -> SupportIndex:
        if self._resume_index is None:
            self._resume_index = SupportIndex(self.resume, window=1)
        return self._resume_index

    def supported(self, ref: Reference) -> bool:
        if ref.resume:
            return self.resume_index.supports(ref.phrase, threshold=MIN_RESUME_COVERAGE, polarity=False)
        return self.candidate_index.supports(ref.phrase)

    def unsupported(self, text: str) -> Reference | None:
        """The first attribution in ``text`` that its source does not support."""
        for ref in find_references(text):
            if not self.supported(ref):
                return ref
        return None


def ungrounded_reference(state: InterviewState, speech: str) -> str | None:
    """The attributed phrase when the candidate's own turns (or, for a reference framed as the resume, the
    resume) do not support it; None when grounded or when there is no attribution."""
    if not find_references(speech or ""):
        return None
    ref = GroundingContext.from_state(state).unsupported(speech)
    return ref.phrase[:160] if ref is not None else None


def coverage(phrase_tokens: list[str], corpus: set[str]) -> float:
    """Share of ``phrase_tokens`` found in ``corpus`` (kept for callers of the old union check)."""
    if not phrase_tokens:
        return 1.0
    return sum(1 for t in phrase_tokens if t in corpus) / len(phrase_tokens)


# ------------------------------------------------------------------ inconsistency records

_SIDE_SPLIT = re.compile(
    r"\s*(?:;|\s/\s|\|)\s*|\s*,?\s+(?:but|whereas|while|however|yet|vs\.?|versus|although|though)\s+"
    r"|\s*,?\s+(?:conflicts?|contradicts?|contrasts?|differs?|disagrees?)\s+with\s+|(?<=[.!?])\s+",
    re.IGNORECASE,
)
_CLAIM_ID = re.compile(r"\bc\d{1,2}\b", re.IGNORECASE)


def split_sides(description: str) -> list[str]:
    return [s.strip(" .\"'") for s in _SIDE_SPLIT.split(description or "") if s and s.strip(" .\"'")]


def _is_resume_side(text: str) -> bool:
    return bool(_RESUME_WORD.search(text) or _CLAIM_ID.search(text))


def inconsistency_issue(ctx: GroundingContext, description: str, *, sides: Sequence[str] = ()) -> str | None:
    """Why a model-written inconsistency record is not grounded in the candidate's own words (None when it is).

    ``sides`` (e.g. the evaluator's statement and the statement it conflicts with) are used when given,
    otherwise the description is split at "; ", " / ", "but", "whereas", "vs" .... A side mentioning the
    resume (or a claim id) is checked against the resume, any other side against the candidate's turns;
    sides with fewer than two content words cannot be judged and are skipped."""
    parts = [s for s in sides if s and s.strip()] or split_sides(description)
    candidate_sides = 0
    for part in parts:
        if _is_resume_side(part):
            if len(set(content_tokens(part))) >= 2 and not ctx.resume_index.supports(
                part, threshold=MIN_RESUME_COVERAGE, polarity=False
            ):
                return f"resume side not in the resume: {part[:120]}"
            continue
        if len(set(content_tokens(part))) < 2:
            continue
        candidate_sides += 1
        if not ctx.candidate_index.supports(part):
            return f"not said by the candidate: {part[:120]}"
    if parts and candidate_sides == 0 and all(_is_resume_side(p) for p in parts):
        return "no side in the candidate's own words"
    return None


__all__ = [
    "GroundingContext",
    "MIN_COVERAGE",
    "Reference",
    "Support",
    "SupportIndex",
    "candidate_texts",
    "content_tokens",
    "coverage",
    "find_references",
    "inconsistency_issue",
    "is_negated",
    "numbers_to_digits",
    "polar_tokens",
    "resume_texts",
    "split_sides",
    "ungrounded_reference",
]
