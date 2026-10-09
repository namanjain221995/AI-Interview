"""Drill A reading engine: align a read-aloud transcript to the passage.

``align_reading(reference, hypothesis, *, segments=None, pauses=None)`` aligns the final speech-recognition
transcript to the passage and measures what was read. There is no pronunciation model, so pronunciation is
always ``not_assessed`` and nothing here can penalise an accent: only *which words* were read (from the
transcript) and *timing* (from segment timestamps or VAD pauses) are used, and near-miss recognitions of
the right word count as read.

Normalisation (both sides, before alignment)
--------------------------------------------
Unicode NFKC, lower case, punctuation removed; hyphenated words split ("self-attention" -> self attention);
curly apostrophes straightened; contractions expanded ("don't" -> do not, "can't"/"cannot" -> can not; the
ambiguous 's / 'd accept is|has / would|had); possessive 's dropped (accepting the plural spelling, which
sounds the same); British spellings mapped to American (optimise/optimize, regularisation, defence,
behaviour, centre, modelling, catalogue...); numbers spelled out (2 -> two, 1,000 -> one thousand,
15% -> fifteen percent, 3rd -> third); "&" -> and. Hesitation fillers in the transcript (um, uh, erm...)
are dropped and counted, never treated as reading errors.

Alignment
---------
Dynamic programming over the normalised tokens (Levenshtein with weighted operations): match 0, ASR
variant 0.4, substitution 1.5, omission 1, insertion 1, plus merge operations that join up to three tokens
on either side when their concatenation matches ("back propagation" = backpropagation, "multihead" =
multi-head, "G P T" = GPT). A substitution costs more than one but less than an omission plus an insertion,
so an isolated misread word is a substitution while a skipped-and-moved sentence becomes an omission run
plus an insertion run (which reorder recovery then finds).

* **ASR variant**: a substitution whose words are near-misses of each other counts as read, not as an
  error: technical terms (the passage's ``terms``, acronyms, words of 10+ letters) at rapidfuzz
  ``ratio >= 72`` or an equal consonant-skeleton key ("Carpenter" for Karpenter, "epics" for epochs, "get"
  for Git); other words of 4+ letters at ``ratio >= 85`` (descent/decent, models/model). A merge only
  tolerates a near-miss when the joined strings are within 2 characters and ``ratio >= 85`` (so a merge
  can never swallow a neighbouring omitted or repeated word).
* **Reorder recovery**: an omission run of 3+ tokens whose words appear (60%+ of them) in a run of inserted
  tokens elsewhere was read out of order: those words count as read (``reordered``), not as errors.
* **Repetition**: an inserted token that repeats a word read just before or after it, or is the start of
  one (a false start, "optim- optimisation"), is a ``repetition`` (a fluency signal) rather than an
  insertion.

Metrics
-------
* ``wer`` = (substitutions + omissions + insertions + repetitions) / reference tokens, after ASR tolerance
  and reorder recovery; ``strict_wer`` = plain Levenshtein word error rate over the normalised tokens (no
  ASR tolerance, merges or reorder recovery), for comparison with standard WER figures.
* ``completeness`` = share of the passage's *content words* (function words such as "the" and "of"
  excluded, because recognisers drop them inconsistently) that were read (match, ASR variant or
  reordered); ``token_coverage`` = the same over every token.
* Per sentence: tokens, covered, omitted, substituted, coverage and status (``read`` >= 0.75,
  ``skipped`` < 0.25, otherwise ``partial``).
* Timing: duration = first segment start to last segment end (or ``duration_seconds``); words per minute =
  spoken words (fillers excluded) per minute; pauses = VAD pause list when given, otherwise the gaps
  between consecutive segments; a pause is >= 0.5 s, a long pause >= 2.0 s.

Delivery score (1-5), computed from the rounded (4 dp) metrics so it can be recomputed from stored values
-------------------------------------------------------------------------------------------------------
Base, from completeness ``C`` and ``wer`` ``W``::

    5  C >= 0.95 and W <= 0.10
    4  C >= 0.85 and W <= 0.20
    3  C >= 0.70 and W <= 0.35
    2  C >= 0.40
    1  otherwise

A passage read partly out of order (reordered words) is complete but not read as written: base at most 4.
Timing deductions (only when timing is known; at most 2 in total; never below 1): pace below 80 or above
220 words per minute -> -1; long pauses beyond the first -> -1 for 2-3 long pauses, -2 for 4 or more.
Pronunciation is never scored. ``review_flags`` contains ``reading_not_completed`` when nothing was read or
``C < 0.50`` (a human-review item, never an automatic decision).

``relay_concepts(passage)`` returns the passage's hand-written key concepts (5-8 each, from the kit) for
Drill B; ``relay_coverage(passage, transcript)`` is a deterministic cue-based estimate of which concepts a
relay covered, plus how much of it was parroted verbatim.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

if TYPE_CHECKING:  # pragma: no cover
    from app.services.intake.kit import IntakeKit, Passage, RelayConcept

METHOD = "intake-reading-v1"

# Alignment costs
MATCH_COST = 0.0
MERGE_COST = 0.05  # tiny, so a 1:1 match wins ties
ASR_COST = 0.4
SUBSTITUTION_COST = 1.5
OMISSION_COST = 1.0
INSERTION_COST = 1.0
MAX_MERGE = 3

# ASR near-miss tolerance
ASR_TERM_RATIO = 72.0
ASR_WORD_RATIO = 85.0
ASR_MIN_CHARS = 4
TERM_MIN_CHARS = 10  # words this long count as technical terms even when not listed
PHONETIC_MIN_KEY = 2
MERGE_RATIO = 85.0  # fuzzy merges (split/joined compounds misrecognised) need a close match...
MERGE_MAX_LENGTH_DIFFERENCE = 2  # ...and nearly the same length

# Reorder recovery / repetition
REORDER_MIN_TOKENS = 3
REORDER_MIN_SHARE = 0.6
REORDERED_MAX_BASE = 4  # a passage read out of order is complete but not read as written
FALSE_START_MIN_CHARS = 3

# Sentences
SENTENCE_READ_FROM = 0.75
SENTENCE_SKIPPED_BELOW = 0.25

# Timing
PAUSE_MIN_SECONDS = 0.5
LONG_PAUSE_SECONDS = 2.0
LONG_PAUSE_ALLOWANCE = 1
SLOW_WPM = 80.0
FAST_WPM = 220.0
MAX_TIMING_DEDUCTION = 2

# Review
INCOMPLETE_BELOW = 0.5
REVIEW_FLAG_INCOMPLETE = "reading_not_completed"

# Relay
VERBATIM_NGRAM = 4

FILLERS: frozenset[str] = frozenset(
    {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "err", "ah", "ahh", "hmm", "hm", "mm", "mmm", "mhm", "eh"}
)

FUNCTION_WORDS: frozenset[str] = frozenset(
    """a an the and or but nor so yet if then than as of to in on at by for with from into onto over under
    up down out off about above below between through during before after again also just only very too
    is are was were be been being am do does did doing done have has had having will would shall should can
    could may might must not no yes it its this that these those there here what which who whom whose how
    when where why i me my mine we us our ours you your yours he him his she her hers they them their theirs
    one ones all any each every some such own same other others more most much many few less least
    let lets s t""".split()
)

_PRONOUN_S = frozenset(
    "it he she that there here what where who how when why this everyone everybody someone somebody "
    "nobody everything something nothing".split()
)
_SPECIAL_CONTRACTIONS: Mapping[str, tuple[str, ...]] = {
    "can't": ("can", "not"),
    "won't": ("will", "not"),
    "shan't": ("shall", "not"),
    "ain't": ("is", "not"),
    "let's": ("let", "us"),
    "cannot": ("can", "not"),
    "y'all": ("you", "all"),
}
_CONTRACTION_SUFFIXES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("n't", "not", frozenset()),
    ("'re", "are", frozenset()),
    ("'ve", "have", frozenset()),
    ("'ll", "will", frozenset()),
    ("'m", "am", frozenset()),
    ("'d", "would", frozenset({"had"})),
)

_SPELLING: Mapping[str, str] = {
    "practise": "practice", "practised": "practiced", "practises": "practices", "practising": "practicing",
    "licence": "license", "licences": "licenses", "defence": "defense", "defences": "defenses",
    "offence": "offense", "offences": "offenses", "pretence": "pretense",
    "programme": "program", "programmes": "programs", "grey": "gray", "aluminium": "aluminum",
    "tyre": "tire", "tyres": "tires", "cheque": "check", "cheques": "checks", "plough": "plow",
    "draught": "draft", "mould": "mold", "sceptical": "skeptical", "sceptic": "skeptic",
    "learnt": "learned", "spelt": "spelled", "dreamt": "dreamed", "burnt": "burned", "leapt": "leaped",
    "spilt": "spilled", "towards": "toward", "afterwards": "afterward", "amongst": "among",
    "whilst": "while", "upwards": "upward", "downwards": "downward", "backwards": "backward",
    "forwards": "forward", "judgement": "judgment", "judgements": "judgments", "ageing": "aging",
    "acknowledgement": "acknowledgment", "enrol": "enroll", "enrolment": "enrollment", "fulfil": "fulfill",
    "fulfilment": "fulfillment", "skilful": "skillful", "wilful": "willful", "instalment": "installment",
    "catalogue": "catalog", "catalogues": "catalogs", "dialogue": "dialog", "dialogues": "dialogs",
    "analogue": "analog", "analogues": "analogs", "monologue": "monolog",
    "centre": "center", "centres": "centers", "centred": "centered", "metre": "meter", "metres": "meters",
    "litre": "liter", "litres": "liters", "theatre": "theater", "theatres": "theaters", "fibre": "fiber",
    "fibres": "fibers", "calibre": "caliber", "spectre": "specter", "lustre": "luster", "meagre": "meager",
    "sombre": "somber", "neurone": "neuron", "neurones": "neurons", "maths": "math", "okay": "ok",
}  # fmt: skip
_OUR_STEMS = (
    "behavio|colo|favo|hono|labo|neighbo|flavo|humo|rumo|harbo|vapo|armo|savo|endeavo|odo|rigo|vigo|tumo|"
    "parlo|splendo|valo|clamo|ardo|cando|fervo"
)
_RX_OUR = re.compile(rf"^({_OUR_STEMS})ur(s|ed|ing|al|ally|ite|ites|able|ably|er|ers|ful|less)?$")
_RX_ISATION = re.compile(r"isation(s?)$")
_RX_ISE = re.compile(r"(\w{2,})is(e|ed|es|ing|er|ers)$")
_RX_YSE = re.compile(r"(\w+)ys(e|ed|es|ing)$")
_RX_DOUBLE_L = re.compile(r"(\w*[aeiou])ll(ing|ed|er|ers)$")

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand"))
_ORDINALS = {
    "one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
    "twelve": "twelfth",
}  # fmt: skip

_WORD_RX = re.compile(
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"  # 1,000 / 50,000.5 / 1,000%
    r"|\d+(?:\.\d+)?(?:st|nd|rd|th)?%?"  # 99 / 3.5 / 3rd / 99%
    r"|[a-z0-9]+(?:'[a-z0-9]+)*"  # words, keeping inner apostrophes
    r"|(?<=\d)[–—-](?=\d)"  # 8–10 -> "to"
    r"|[&%]",
    re.IGNORECASE,
)
_RANGE_DASHES = frozenset("–—-")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n\s*\n")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "prof.", "inc.", "no.")

EventKind = Literal["omission", "substitution", "insertion", "repetition", "asr_variant", "reordered"]
OpKind = Literal["match", "asr_variant", "substitution", "omission", "insertion", "repetition"]


# ============================================================================================ tokens


@dataclass(slots=True)
class Token:
    norm: str
    surface: str
    word_index: int
    start: int
    end: int
    alts: frozenset[str] = frozenset()
    joined: str = ""  # apostrophe-free original word ("its" for "it's"), for contraction groups
    group_size: int = 1  # tokens produced by the same original word
    sentence: int = -1
    technical: bool = False

    @property
    def content(self) -> bool:
        return self.norm not in FUNCTION_WORDS


def _number_words(value: int) -> list[str]:
    if value < 20:
        return [_ONES[value]]
    if value < 100:
        tens, ones = divmod(value, 10)
        return [_TENS[tens]] + ([_ONES[ones]] if ones else [])
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        return [_ONES[hundreds], "hundred"] + (_number_words(rest) if rest else [])
    for scale, name in _SCALES:
        if value >= scale:
            high, rest = divmod(value, scale)
            return _number_words(high) + [name] + (_number_words(rest) if rest else [])
    return [str(value)]  # pragma: no cover - unreachable


def _ordinal(words: list[str]) -> list[str]:
    last = words[-1]
    if last in _ORDINALS:
        last = _ORDINALS[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last = last + "th"
    return words[:-1] + [last]


def _spell_number(raw: str) -> list[str]:
    percent = raw.endswith("%")
    text = raw.rstrip("%").replace(",", "")
    ordinal = text[-2:] in ("st", "nd", "rd", "th") and text[:-2].isdigit()
    if ordinal:
        text = text[:-2]
    whole, _, fraction = text.partition(".")
    if not whole.isdigit() or len(whole) > 15:
        return [raw.lower()]
    words = _number_words(int(whole))
    if ordinal:
        words = _ordinal(words)
    if fraction:
        words = words + ["point"] + [_ONES[int(d)] for d in fraction if d.isdigit()]
    if percent:
        words.append("percent")
    return words


def canonical_spelling(word: str) -> str:
    """American spelling for the common British patterns (applied to both sides, so over-application is
    harmless): dictionary first, then -our, -isation, -ise, -yse and doubled -ll- endings."""
    mapped = _SPELLING.get(word)
    if mapped is not None:
        return mapped
    word = _RX_OUR.sub(lambda m: f"{m.group(1)}r{m.group(2) or ''}", word)
    word = _RX_ISATION.sub(r"ization\1", word)
    if len(word) >= 5:
        word = _RX_ISE.sub(r"\1iz\2", word)
        word = _RX_YSE.sub(r"\1yz\2", word)
    return _RX_DOUBLE_L.sub(r"\1l\2", word)


def _expand_word(word: str) -> list[tuple[str, frozenset[str]]]:
    """Normalised tokens (with accepted alternatives) for one lower-cased word."""
    if word == "&":
        return [("and", frozenset())]
    if word == "%":
        return [("percent", frozenset())]
    if word in _RANGE_DASHES:
        return [("to", frozenset())]
    if word[0].isdigit():
        return [(w, frozenset()) for w in _spell_number(word)]
    special = _SPECIAL_CONTRACTIONS.get(word)
    if special is not None:
        return [(w, frozenset()) for w in special]
    if "'" in word:
        for suffix, expansion, alts in _CONTRACTION_SUFFIXES:
            if word.endswith(suffix) and len(word) > len(suffix):
                stem = word[: -len(suffix)]
                return [(canonical_spelling(stem), frozenset()), (expansion, alts)]
        if word.endswith("'s") and len(word) > 2:
            stem = word[:-2]
            if stem in _PRONOUN_S:
                return [(stem, frozenset()), ("is", frozenset({"has"}))]
            base = canonical_spelling(stem)
            return [(base, frozenset({canonical_spelling(stem + "s")}))]
        word = word.replace("'", "")
    return [(canonical_spelling(word), frozenset())]


_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})


def _prepare_text(text: str) -> str:
    """NFKC plus one-for-one apostrophe replacements: offsets into the result are stable (they equal the
    input's offsets for ordinary text) and the original casing is kept for display."""
    return unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)


def tokenize(text: str, *, drop_fillers: bool = False) -> tuple[list[Token], int]:
    """Normalised tokens of ``text`` and the number of fillers dropped. Character offsets refer to the
    NFKC-normalised text (identical to the input for ordinary text)."""
    prepared = _prepare_text(text)
    tokens: list[Token] = []
    fillers = 0
    for word_index, match in enumerate(_WORD_RX.finditer(prepared)):
        word = match.group(0).lower()
        if not word:  # pragma: no cover - the pattern never matches empty text
            continue
        if drop_fillers and word in FILLERS:
            fillers += 1
            continue
        expanded = _expand_word(word)
        joined = word.replace("'", "")
        for norm, alts in expanded:
            if not norm:
                continue
            tokens.append(
                Token(
                    norm=norm,
                    surface=match.group(0),
                    word_index=word_index,
                    start=match.start(),
                    end=match.end(),
                    alts=alts,
                    joined=joined,
                    group_size=len(expanded),
                )
            )
    return tokens, fillers


def normalize_tokens(text: str) -> list[str]:
    """The normalised token strings of ``text`` (handy for tests and cue matching)."""
    return [t.norm for t in tokenize(text)[0]]


# ========================================================================================= phonetics


def phonetic_key(word: str) -> str:
    """A crude consonant skeleton: digraphs folded, voicing merged (b/p, d/t, g/k, v/f, z/s), vowels
    dropped (a leading vowel kept as "a"), repeats collapsed. "Carpenter" and "Karpenter" share a key."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    for prefix, repl in (("kn", "n"), ("wr", "r"), ("ps", "s"), ("gn", "n"), ("pn", "n"), ("x", "s")):
        if w.startswith(prefix):
            w = repl + w[len(prefix) :]
            break
    for src, dst in (("sch", "sk"), ("ph", "f"), ("gh", "g"), ("ck", "k"), ("ch", "k"), ("sh", "s"),
                     ("th", "t"), ("wh", "w"), ("qu", "kw"), ("x", "ks")):  # fmt: skip
        w = w.replace(src, dst)
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    w = w.translate(str.maketrans("bdgvzqj", "ptkfskk"))
    head = "a" if w[0] in "aeiouy" else ""
    body = re.sub(r"[aeiouywh]", "", w)
    body = re.sub(r"(.)\1+", r"\1", body)
    return head + body


# ========================================================================================= relations


def _equal(a: Token, b: Token) -> bool:
    return a.norm == b.norm or a.norm in b.alts or b.norm in a.alts


def _near_miss(a: str, b: str, *, technical: bool) -> bool:
    """``b`` is a plausible speech-recognition near-miss of ``a``."""
    if not a or not b or a == b:
        return a == b and bool(a)
    if technical:
        if min(len(a), len(b)) >= 3 and fuzz.ratio(a, b) >= ASR_TERM_RATIO:
            return True
        key_a, key_b = phonetic_key(a), phonetic_key(b)
        return len(key_a) >= PHONETIC_MIN_KEY and key_a == key_b
    if len(a) >= ASR_MIN_CHARS and len(b) >= ASR_MIN_CHARS - 1:
        return fuzz.ratio(a, b) >= ASR_WORD_RATIO
    return False


class _Relations:
    """Cached token relations for one alignment."""

    def __init__(self) -> None:
        self._cache: dict[tuple[Any, ...], tuple[OpKind, float]] = {}

    def pair(self, r: Token, h: Token) -> tuple[OpKind, float]:
        key = (r.norm, r.alts, h.norm, h.alts, r.technical)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result: tuple[OpKind, float]
        if _equal(r, h):
            result = ("match", MATCH_COST)
        elif _near_miss(r.norm, h.norm, technical=r.technical):
            result = ("asr_variant", ASR_COST)
        else:
            result = ("substitution", SUBSTITUTION_COST)
        self._cache[key] = result
        return result

    def merged(self, whole: str, parts: tuple[str, ...]) -> tuple[OpKind, float] | None:
        """Relation between one token (``whole``) and several tokens on the other side (``parts``): a match
        when they concatenate to it ("back" + "propagation"), an ASR variant when the concatenation is a
        close near-miss, otherwise ``None``.

        The fuzzy case is strict on purpose: the strings must be within 2 characters and ``ratio >= 85``,
        every part must have 2+ characters, and no part may match ``whole`` on its own; otherwise a merge
        would swallow a neighbouring omitted or repeated word ("standing" + "on" ~ "standing")."""
        key = ("merge", whole, parts)
        if key in self._cache:
            hit = self._cache[key]
            return None if hit[0] == "substitution" else hit
        joined = "".join(parts)
        if joined == whole:
            hit = ("match", MERGE_COST)
        elif (
            abs(len(joined) - len(whole)) <= MERGE_MAX_LENGTH_DIFFERENCE
            and all(len(part) >= 2 for part in parts)
            and fuzz.ratio(joined, whole) >= MERGE_RATIO
            and not any(part == whole or _near_miss(whole, part, technical=False) for part in parts)
        ):
            hit = ("asr_variant", ASR_COST + MERGE_COST)
        else:
            hit = ("substitution", math.inf)
        self._cache[key] = hit
        return None if hit[0] == "substitution" else hit


@dataclass(slots=True)
class _Op:
    kind: OpKind
    i0: int  # reference span [i0, i1)
    i1: int
    j0: int  # hypothesis span [j0, j1)
    j1: int
    reordered: bool = False


def _merge_joined(tokens: Sequence[Token], start: int, count: int) -> str | None:
    """The apostrophe-free original word when ``count`` tokens from ``start`` are exactly the tokens of one
    contracted word ("it's" -> it is -> "its")."""
    first = tokens[start]
    if first.group_size != count:
        return None
    if all(tokens[start + k].word_index == first.word_index for k in range(count)):
        return first.joined
    return None


def _align(ref: Sequence[Token], hyp: Sequence[Token], rel: _Relations) -> list[_Op]:
    """Minimum-cost alignment (weighted Levenshtein with 1:k and k:1 merges), in reference order."""
    n, m = len(ref), len(hyp)
    inf = math.inf
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[str, int, int, OpKind] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    ref_parts = {
        k: [tuple(t.norm for t in ref[i : i + k]) for i in range(n)] for k in range(2, MAX_MERGE + 1)
    }
    hyp_parts = {
        k: [tuple(t.norm for t in hyp[j : j + k]) for j in range(m)] for k in range(2, MAX_MERGE + 1)
    }
    for i in range(n + 1):
        row = cost[i]
        brow = back[i]
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best = inf
            choice: tuple[str, int, int, OpKind] | None = None
            if i and j:
                kind, c = rel.pair(ref[i - 1], hyp[j - 1])
                total = cost[i - 1][j - 1] + c
                if total < best:
                    best, choice = total, ("diag", 1, 1, kind)
                for k in range(2, MAX_MERGE + 1):
                    if j >= k:  # one reference token = k hypothesis tokens
                        prev = cost[i - 1][j - k]
                        if prev + MERGE_COST < best:
                            r = ref[i - 1]
                            if _merge_joined(hyp, j - k, k) == r.norm:
                                hit: tuple[OpKind, float] | None = ("match", MERGE_COST)
                            else:
                                hit = rel.merged(r.norm, hyp_parts[k][j - k])
                            if hit is not None and prev + hit[1] < best:
                                best, choice = prev + hit[1], ("merge", 1, k, hit[0])
                    if i >= k:  # k reference tokens = one hypothesis token
                        prev = cost[i - k][j - 1]
                        if prev + MERGE_COST < best:
                            h = hyp[j - 1]
                            if _merge_joined(ref, i - k, k) == h.norm:
                                hit = ("match", MERGE_COST)
                            else:
                                hit = rel.merged(h.norm, ref_parts[k][i - k])
                            if hit is not None and prev + hit[1] < best:
                                best, choice = prev + hit[1], ("merge", k, 1, hit[0])
            if i and cost[i - 1][j] + OMISSION_COST < best:
                best, choice = cost[i - 1][j] + OMISSION_COST, ("del", 1, 0, "omission")
            if j and row[j - 1] + INSERTION_COST < best:
                best, choice = row[j - 1] + INSERTION_COST, ("ins", 0, 1, "insertion")
            row[j] = best
            brow[j] = choice
    ops: list[_Op] = []
    i, j = n, m
    while i or j:
        step = back[i][j]
        if step is None:  # pragma: no cover - defensive
            raise RuntimeError("alignment backtrace failed")
        _name, di, dj, kind = step
        ops.append(_Op(kind, i - di, i, j - dj, j))
        i, j = i - di, j - dj
    ops.reverse()
    return ops


def _levenshtein(ref: Sequence[Token], hyp: Sequence[Token]) -> int:
    """Plain word-level edit distance (normalisation-equal tokens match; no ASR tolerance or merges)."""
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (0 if _equal(r, h) else 1),
            )
        previous = current
    return previous[-1]


# ==================================================================================== post-processing


def _runs(ops: Sequence[_Op], kind: OpKind) -> list[list[int]]:
    """Indices into ``ops`` of maximal runs of consecutive ops of ``kind``."""
    runs: list[list[int]] = []
    current: list[int] = []
    for index, op in enumerate(ops):
        if op.kind == kind:
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _recover_reordering(
    ops: list[_Op], ref: Sequence[Token], hyp: Sequence[Token], rel: _Relations
) -> list[_Op]:
    """Re-label omission runs that were read elsewhere (an insertion run with the same words)."""
    omission_runs = [r for r in _runs(ops, "omission") if len(r) >= REORDER_MIN_TOKENS]
    insertion_runs = [r for r in _runs(ops, "insertion") if len(r) >= REORDER_MIN_TOKENS]
    if not omission_runs or not insertion_runs:
        return ops
    replacements: dict[int, list[_Op]] = {}  # op index of a run start -> new ops
    removed: set[int] = set()
    used: set[int] = set()
    for o_run in sorted(omission_runs, key=len, reverse=True):
        i0, i1 = ops[o_run[0]].i0, ops[o_run[-1]].i1
        best: tuple[int, int, list[_Op]] | None = None
        for index, h_run in enumerate(insertion_runs):
            if index in used:
                continue
            j0, j1 = ops[h_run[0]].j0, ops[h_run[-1]].j1
            sub = _align(ref[i0:i1], hyp[j0:j1], rel)
            read = sum(op.i1 - op.i0 for op in sub if op.kind in ("match", "asr_variant"))
            needed = max(REORDER_MIN_TOKENS, math.ceil(REORDER_MIN_SHARE * (i1 - i0)))
            if read >= needed and (best is None or read > best[0]):
                for op in sub:
                    op.i0, op.i1, op.j0, op.j1 = op.i0 + i0, op.i1 + i0, op.j0 + j0, op.j1 + j0
                best = (read, index, sub)
        if best is None:
            continue
        _read, index, sub = best
        used.add(index)
        for op in sub:
            op.reordered = op.kind in ("match", "asr_variant")
        replacements[o_run[0]] = sub
        removed.update(o_run)
        removed.update(insertion_runs[index])
    if not replacements:
        return ops
    result: list[_Op] = []
    for index, op in enumerate(ops):
        if index in replacements:
            result.extend(replacements[index])
        elif index not in removed:
            result.append(op)
    return result


def _is_repeat(token: Token, others: Iterable[Token]) -> bool:
    for other in others:
        if _equal(token, other) or _near_miss(other.norm, token.norm, technical=False):
            return True
        if len(token.norm) >= FALSE_START_MIN_CHARS and other.norm.startswith(token.norm):
            return True
    return False


def _classify_repetitions(ops: list[_Op], ref: Sequence[Token], hyp: Sequence[Token]) -> None:
    """Insertions that repeat a nearby word that was actually read (or start one) become repetitions, in
    place. A word omitted in its place and said elsewhere is not a repetition: it stays an insertion."""
    aligned_hyp: set[int] = set()
    read_ref: set[int] = set()
    for op in ops:
        if op.kind not in ("insertion", "omission", "repetition"):
            aligned_hyp.update(range(op.j0, op.j1))
            read_ref.update(range(op.i0, op.i1))
    # Group insertions by contiguous transcript position (after reorder recovery, neighbours in the op list
    # are not necessarily neighbours in the transcript).
    groups: list[list[_Op]] = []
    for op in sorted((op for op in ops if op.kind == "insertion"), key=lambda op: op.j0):
        if groups and groups[-1][-1].j1 == op.j0:
            groups[-1].append(op)
        else:
            groups.append([op])
    for group in groups:
        j0, j1 = group[0].j0, group[-1].j1
        span = (j1 - j0) + 3
        neighbours = [hyp[j] for j in range(max(0, j0 - span), min(len(hyp), j1 + span)) if j in aligned_hyp]
        position = group[0].i0  # reference position of the insertion
        neighbours += [
            ref[i] for i in range(max(0, position - span), min(len(ref), position + span)) if i in read_ref
        ]
        for op in group:
            if _is_repeat(hyp[op.j0], neighbours):
                op.kind = "repetition"


# ============================================================================================ timing


def _get(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _segment_bounds(segment: Any) -> tuple[float, float, str] | None:
    """(start s, end s, text) of a timed segment: mapping/object with ``start_ms``/``end_ms`` or
    ``start``/``end`` (seconds), or a ``(start, end[, text])`` tuple in seconds."""
    if isinstance(segment, (tuple, list)):
        if len(segment) < 2:
            return None
        text = str(segment[2]) if len(segment) > 2 and segment[2] is not None else ""
        return float(segment[0]), float(segment[1]), text
    text = _get(segment, "text") or ""
    start_ms, end_ms = _get(segment, "start_ms"), _get(segment, "end_ms")
    if start_ms is not None and end_ms is not None:
        return float(start_ms) / 1000.0, float(end_ms) / 1000.0, str(text)
    start, end = _get(segment, "start"), _get(segment, "end")
    if start is not None and end is not None:
        return float(start), float(end), str(text)
    return None


def _pause_seconds(pause: Any) -> float | None:
    """A pause length in seconds: a number (seconds), a ``(start, end)`` tuple (seconds), or a
    mapping/object with ``duration_ms``, ``duration``/``seconds``, ``start_ms``/``end_ms`` or ``start``/``end``."""
    if isinstance(pause, bool):
        return None
    if isinstance(pause, (int, float)):
        return float(pause)
    if isinstance(pause, (tuple, list)):
        return float(pause[1]) - float(pause[0]) if len(pause) >= 2 else None
    duration_ms = _get(pause, "duration_ms")
    if duration_ms is not None:
        return float(duration_ms) / 1000.0
    for name in ("duration", "seconds"):
        value = _get(pause, name)
        if value is not None:
            return float(value)
    start_ms, end_ms = _get(pause, "start_ms"), _get(pause, "end_ms")
    if start_ms is not None and end_ms is not None:
        return (float(end_ms) - float(start_ms)) / 1000.0
    start, end = _get(pause, "start"), _get(pause, "end")
    if start is not None and end is not None:
        return float(end) - float(start)
    return None


@dataclass(slots=True)
class _Timing:
    duration: float | None = None
    source: Literal["vad", "segments", "none"] = "none"
    pauses: list[float] = field(default_factory=list)


def _timing(
    segments: Sequence[Any] | None, pauses: Sequence[Any] | None, duration_seconds: float | None
) -> _Timing:
    timing = _Timing()
    bounds = sorted(b for b in (_segment_bounds(s) for s in segments or ()) if b is not None and b[1] >= b[0])
    if bounds:
        timing.duration = max(b[1] for b in bounds) - bounds[0][0]
    if duration_seconds is not None:
        timing.duration = float(duration_seconds)
    if timing.duration is not None and (not math.isfinite(timing.duration) or timing.duration <= 0):
        timing.duration = None
    if pauses is not None:
        timing.source = "vad"
        timing.pauses = [
            p for p in (_pause_seconds(x) for x in pauses) if p is not None and p >= PAUSE_MIN_SECONDS
        ]
    elif len(bounds) >= 2:
        timing.source = "segments"
        end = bounds[0][1]
        for start, stop, _text in bounds[1:]:
            gap = start - end
            if gap >= PAUSE_MIN_SECONDS:
                timing.pauses.append(gap)
            end = max(end, stop)
    elif len(bounds) == 1:
        timing.source = "segments"  # one segment: no gap was observed
    return timing


# =========================================================================================== results


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ReadingEvent(_Frozen):
    """One non-matching alignment step (positions are token indices; ``ref_word_index`` indexes the words of
    the reference text, ``ref_start``/``ref_end`` are character offsets for highlighting)."""

    kind: EventKind
    ref_index: int | None = None
    ref_word_index: int | None = None
    ref_start: int | None = None
    ref_end: int | None = None
    ref_word: str | None = None
    hyp_index: int | None = None
    hyp_word: str | None = None
    sentence: int | None = None


class SentenceCoverage(_Frozen):
    index: int
    text: str
    tokens: int
    covered: int
    omitted: int
    substituted: int
    asr_variants: int
    coverage: float
    status: Literal["read", "partial", "skipped"]
    reordered: bool


class ReadingMetrics(_Frozen):
    method: str = METHOD
    reference_words: int
    reference_tokens: int
    content_tokens: int
    hypothesis_words: int
    hypothesis_tokens: int
    matched: int
    asr_variants: int
    substitutions: int
    omissions: int
    insertions: int
    repetitions: int
    reordered: int
    fillers: int
    wer: float
    strict_wer: float
    completeness: float
    token_coverage: float
    sentences: tuple[SentenceCoverage, ...]
    sentences_read: int
    sentences_partial: int
    sentences_skipped: int
    duration_seconds: float | None
    words_per_minute: float | None
    correct_words_per_minute: float | None
    pause_source: Literal["vad", "segments", "none"]
    pause_count: int | None
    long_pause_count: int | None
    longest_pause_seconds: float | None
    total_pause_seconds: float | None
    delivery_score: int
    delivery_basis: tuple[str, ...]
    pronunciation: Literal["not_assessed"] = "not_assessed"
    review_flags: tuple[str, ...] = ()
    events: tuple[ReadingEvent, ...] = ()

    def _positions(self, kind: EventKind, attr: str) -> list[int]:
        return [getattr(e, attr) for e in self.events if e.kind == kind and getattr(e, attr) is not None]

    @property
    def omitted_positions(self) -> list[int]:
        """Reference token indices that were not read."""
        return self._positions("omission", "ref_index")

    @property
    def substituted_positions(self) -> list[int]:
        return self._positions("substitution", "ref_index")

    @property
    def inserted_positions(self) -> list[int]:
        """Hypothesis token indices of words that are not in the passage (repetitions excluded)."""
        return self._positions("insertion", "hyp_index")

    @property
    def repeated_positions(self) -> list[int]:
        return self._positions("repetition", "hyp_index")


def _r4(value: float) -> float:
    return round(value, 4)


def delivery_score(
    *,
    completeness: float,
    wer: float,
    words_per_minute: float | None,
    long_pause_count: int | None,
    reordered: int = 0,
) -> tuple[int, tuple[str, ...]]:
    """The documented 1-5 mapping (module docstring) and a human-readable basis for it."""
    basis: list[str] = []
    if completeness >= 0.95 and wer <= 0.10:
        base = 5
    elif completeness >= 0.85 and wer <= 0.20:
        base = 4
    elif completeness >= 0.70 and wer <= 0.35:
        base = 3
    elif completeness >= 0.40:
        base = 2
    else:
        base = 1
    basis.append(f"completeness {completeness:.2f}, word error rate {wer:.2f} -> base {base}")
    if reordered and base > REORDERED_MAX_BASE:
        base = REORDERED_MAX_BASE
        basis.append(f"{reordered} word(s) read out of order -> base at most {REORDERED_MAX_BASE}")
    deduction = 0
    if words_per_minute is None:
        basis.append("pace not measured (no timing)")
    elif words_per_minute < SLOW_WPM or words_per_minute > FAST_WPM:
        deduction += 1
        side = "slow" if words_per_minute < SLOW_WPM else "fast"
        basis.append(
            f"pace {words_per_minute:.0f} wpm ({side}; natural is {SLOW_WPM:.0f}-{FAST_WPM:.0f}) -> -1"
        )
    else:
        basis.append(f"pace {words_per_minute:.0f} wpm (within {SLOW_WPM:.0f}-{FAST_WPM:.0f})")
    if long_pause_count is None:
        basis.append("pauses not measured (no timing)")
    else:
        extra = max(0, long_pause_count - LONG_PAUSE_ALLOWANCE)
        pause_deduction = 0 if extra == 0 else (1 if extra <= 2 else 2)
        deduction += pause_deduction
        note = f" -> -{pause_deduction}" if pause_deduction else ""
        basis.append(
            f"{long_pause_count} long pause(s) of {LONG_PAUSE_SECONDS:.0f}+ s ({LONG_PAUSE_ALLOWANCE} allowed){note}"
        )
    deduction = min(deduction, MAX_TIMING_DEDUCTION)
    score = max(1, base - deduction)
    if deduction:
        basis.append(f"timing deductions {deduction} (max {MAX_TIMING_DEDUCTION}) -> {score}")
    basis.append("pronunciation not assessed; accent never affects the score")
    return score, tuple(basis)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of the sentences of ``text`` (paragraph breaks always end a sentence)."""
    protected = text
    for abbreviation in _ABBREVIATIONS:
        protected = re.sub(
            re.escape(abbreviation), lambda m: m.group(0).replace(".", "\x00"), protected, flags=re.I
        )
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(protected):
        if text[start : match.start()].strip():
            spans.append((start, match.start()))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _term_sequences(terms: Iterable[str]) -> list[list[str]]:
    sequences = []
    for term in terms:
        tokens = normalize_tokens(term)
        if tokens:
            sequences.append(tokens)
    return sequences


def _mark_technical(ref: list[Token], terms: Iterable[str] | None) -> None:
    for token in ref:
        surface = token.surface.strip("'")
        if (
            len(token.norm) >= TERM_MIN_CHARS
            or any(ch.isdigit() for ch in token.norm)
            or (len(surface) >= 2 and surface.isupper())
        ):
            token.technical = True
    norms = [t.norm for t in ref]
    for sequence in _term_sequences(terms or ()):
        size = len(sequence)
        for start in range(len(norms) - size + 1):
            if norms[start : start + size] == sequence:
                for k in range(start, start + size):
                    ref[k].technical = True


def _reference_parts(reference: Any) -> tuple[str, tuple[str, ...]]:
    paragraphs = getattr(reference, "paragraphs", None)
    if paragraphs is not None:
        return "\n\n".join(paragraphs), tuple(getattr(reference, "terms", ()) or ())
    if isinstance(reference, str):
        return reference, ()
    if isinstance(reference, Sequence):
        return "\n\n".join(str(p) for p in reference), ()
    raise TypeError("reference must be a string, a sequence of paragraphs or a Passage")


def align_reading(
    reference: str | Sequence[str] | Passage,
    hypothesis: str | None,
    *,
    segments: Sequence[Any] | None = None,
    pauses: Sequence[Any] | None = None,
    duration_seconds: float | None = None,
    terms: Iterable[str] | None = None,
) -> ReadingMetrics:
    """Align a read-aloud transcript to its passage (see the module docstring for every rule).

    ``reference``: passage text, its paragraphs, or a kit :class:`~app.services.intake.kit.Passage` (whose
    ``terms`` are then used for ASR tolerance unless ``terms`` is given). ``hypothesis``: the final transcript
    (``None`` joins the segment texts). ``segments``: timed STT segments (``start_ms``/``end_ms`` or
    ``start``/``end`` seconds). ``pauses``: VAD pauses (seconds, ``(start, end)`` or mappings with
    ``duration_ms`` ...); preferred over segment gaps. ``duration_seconds`` overrides the segment span.
    """
    text, passage_terms = _reference_parts(reference)
    ref, _ = tokenize(text)
    if not ref:
        raise ValueError("the reference passage has no words")
    if hypothesis is None:
        texts = [b[2] for b in (_segment_bounds(s) for s in segments or ()) if b is not None]
        hypothesis = " ".join(t for t in texts if t)
    _mark_technical(ref, passage_terms if terms is None else terms)
    spans = _sentence_spans(_prepare_text(text))
    for token in ref:
        token.sentence = next((k for k, (s, e) in enumerate(spans) if s <= token.start < e), len(spans) - 1)
    hyp_all, fillers = tokenize(hypothesis or "", drop_fillers=False)
    ref_norms = {t.norm for t in ref}
    hyp = [t for t in hyp_all if not (t.norm in FILLERS and t.norm not in ref_norms)]
    fillers += len(hyp_all) - len(hyp)

    rel = _Relations()
    ops = _align(ref, hyp, rel) if hyp else [_Op("omission", i, i + 1, 0, 0) for i in range(len(ref))]
    ops = _recover_reordering(ops, ref, hyp, rel)
    _classify_repetitions(ops, ref, hyp)

    counts = {"match": 0, "asr_variant": 0, "substitution": 0, "omission": 0, "insertion": 0, "repetition": 0}
    covered = [False] * len(ref)
    status_by_ref: dict[int, OpKind] = {}
    reordered_refs: set[int] = set()
    events: list[tuple[tuple[int, int], ReadingEvent]] = []
    for op in ops:
        if op.kind in ("insertion", "repetition"):
            counts[op.kind] += op.j1 - op.j0
            for j in range(op.j0, op.j1):
                events.append(
                    (
                        (op.i0, j),
                        ReadingEvent(kind=op.kind, hyp_index=j, hyp_word=hyp[j].surface, ref_index=None),
                    )
                )
            continue
        hyp_words = " ".join(dict.fromkeys(hyp[j].surface for j in range(op.j0, op.j1))) or None
        for i in range(op.i0, op.i1):
            counts[op.kind] += 1
            status_by_ref[i] = op.kind
            if op.kind in ("match", "asr_variant"):
                covered[i] = True
                if op.reordered:
                    reordered_refs.add(i)
            event_kind: EventKind | None
            if op.kind == "match":
                event_kind = "reordered" if op.reordered else None
            else:
                event_kind = op.kind  # type: ignore[assignment]
            if event_kind is None:
                continue
            token = ref[i]
            events.append(
                (
                    (i, op.j0 if op.j1 > op.j0 else -1),
                    ReadingEvent(
                        kind=event_kind,
                        ref_index=i,
                        ref_word_index=token.word_index,
                        ref_start=token.start,
                        ref_end=token.end,
                        ref_word=token.surface,
                        hyp_index=op.j0 if op.j1 > op.j0 else None,
                        hyp_word=hyp_words if op.kind != "omission" else None,
                        sentence=token.sentence,
                    ),
                )
            )
    events.sort(key=lambda item: item[0])

    n = len(ref)
    content = [i for i, t in enumerate(ref) if t.content] or list(range(n))
    errors = counts["substitution"] + counts["omission"] + counts["insertion"] + counts["repetition"]
    wer = _r4(errors / n)
    strict_wer = _r4(_levenshtein(ref, hyp) / n)
    completeness = _r4(sum(covered[i] for i in content) / len(content))
    token_coverage = _r4(sum(covered) / n)

    sentences: list[SentenceCoverage] = []
    prepared = _prepare_text(text)
    for index, (start, end) in enumerate(spans):
        members = [i for i, t in enumerate(ref) if t.sentence == index]
        if not members:
            continue
        got = sum(covered[i] for i in members)
        ratio = _r4(got / len(members))
        status: Literal["read", "partial", "skipped"] = (
            "read"
            if ratio >= SENTENCE_READ_FROM
            else "skipped"
            if ratio < SENTENCE_SKIPPED_BELOW
            else "partial"
        )
        sentences.append(
            SentenceCoverage(
                index=index,
                text=" ".join(prepared[start:end].split()),
                tokens=len(members),
                covered=got,
                omitted=sum(status_by_ref.get(i) == "omission" for i in members),
                substituted=sum(status_by_ref.get(i) == "substitution" for i in members),
                asr_variants=sum(status_by_ref.get(i) == "asr_variant" for i in members),
                coverage=ratio,
                status=status,
                reordered=any(i in reordered_refs for i in members),
            )
        )

    timing = _timing(segments, pauses, duration_seconds)
    spoken_words = len({t.word_index for t in hyp})
    wpm = cwpm = None
    if timing.duration is not None and spoken_words:
        minutes = timing.duration / 60.0
        wpm = _r4(spoken_words / minutes)
        cwpm = _r4(sum(covered) / minutes)
    timed = timing.source != "none"
    long_pauses = [p for p in timing.pauses if p >= LONG_PAUSE_SECONDS]
    pause_count = len(timing.pauses) if timed else None
    long_count = len(long_pauses) if timed else None

    basis: tuple[str, ...]
    if not hyp:
        score = 1
        basis = ("no reading captured -> 1", "pronunciation not assessed; accent never affects the score")
    else:
        score, basis = delivery_score(
            completeness=completeness,
            wer=wer,
            words_per_minute=wpm,
            long_pause_count=long_count,
            reordered=len(reordered_refs),
        )
    flags = (REVIEW_FLAG_INCOMPLETE,) if (not hyp or completeness < INCOMPLETE_BELOW) else ()

    return ReadingMetrics(
        reference_words=len({t.word_index for t in ref}),
        reference_tokens=n,
        content_tokens=len(content),
        hypothesis_words=spoken_words,
        hypothesis_tokens=len(hyp),
        matched=counts["match"],
        asr_variants=counts["asr_variant"],
        substitutions=counts["substitution"],
        omissions=counts["omission"],
        insertions=counts["insertion"],
        repetitions=counts["repetition"],
        reordered=len(reordered_refs),
        fillers=fillers,
        wer=wer,
        strict_wer=strict_wer,
        completeness=completeness,
        token_coverage=token_coverage,
        sentences=tuple(sentences),
        sentences_read=sum(s.status == "read" for s in sentences),
        sentences_partial=sum(s.status == "partial" for s in sentences),
        sentences_skipped=sum(s.status == "skipped" for s in sentences),
        duration_seconds=_r4(timing.duration) if timing.duration is not None else None,
        words_per_minute=wpm,
        correct_words_per_minute=cwpm,
        pause_source=timing.source,
        pause_count=pause_count,
        long_pause_count=long_count,
        longest_pause_seconds=_r4(max(timing.pauses))
        if timed and timing.pauses
        else (0.0 if timed else None),
        total_pause_seconds=_r4(sum(timing.pauses)) if timed else None,
        delivery_score=score,
        delivery_basis=basis,
        review_flags=flags,
        events=tuple(event for _key, event in events),
    )


# ============================================================================================== relay


class ConceptCoverage(_Frozen):
    key: str
    text: str
    covered: bool
    matched_cue: str | None = None


class RelayCoverage(_Frozen):
    passage: str
    concepts: tuple[ConceptCoverage, ...]
    covered: int
    total: int
    coverage: float
    verbatim_ratio: float  # share of the relay's word 4-grams copied from the passage (parroting signal)
    relay_words: int


def relay_concepts(passage: Passage | str, *, kit: IntakeKit | None = None) -> tuple[RelayConcept, ...]:
    """The hand-written key concepts (5-8) of a passage, for Drill B coverage scoring."""
    if isinstance(passage, str):
        if kit is None:
            from app.services.intake.kit import load_intake_kit

            kit = load_intake_kit()
        passage = kit.passage(passage)
    return passage.relay_concepts


def _cue_tokens(cue: str) -> list[tuple[str, bool]]:
    """Normalised cue tokens with a prefix flag (a cue word ending in ``*`` matches any word it starts)."""
    parts: list[tuple[str, bool]] = []
    for word in cue.split():
        prefix = word.endswith("*")
        tokens = normalize_tokens(word.rstrip("*"))
        parts.extend((token, prefix and k == len(tokens) - 1) for k, token in enumerate(tokens))
    return parts


def _token_matches(cue: str, token: str, *, prefix: bool) -> bool:
    if token == cue or (prefix and token.startswith(cue)):
        return True
    return len(cue) >= 5 and fuzz.ratio(cue, token) >= ASR_WORD_RATIO


def _find_cue(cue: str, words: Sequence[str]) -> bool:
    tokens = _cue_tokens(cue)
    if not tokens:
        return False
    size = len(tokens)
    for start in range(len(words) - size + 1):
        if all(_token_matches(tokens[k][0], words[start + k], prefix=tokens[k][1]) for k in range(size)):
            return True
    return False


def relay_coverage(
    passage: Passage | str, transcript: str | None, *, kit: IntakeKit | None = None
) -> RelayCoverage:
    """Deterministic estimate of which key concepts a Drill B relay covered (any cue of a concept found in the
    normalised transcript; ``*`` marks a prefix cue), and how much of the relay repeats the passage verbatim.
    A baseline for tests and degraded mode; the model-based comprehension score remains the Drill B score."""
    if isinstance(passage, str):
        if kit is None:
            from app.services.intake.kit import load_intake_kit

            kit = load_intake_kit()
        passage = kit.passage(passage)
    words = [t.norm for t in tokenize(transcript or "", drop_fillers=True)[0]]
    results = []
    for concept in passage.relay_concepts:
        cue = next((c for c in concept.cues if _find_cue(c, words)), None)
        results.append(
            ConceptCoverage(key=concept.key, text=concept.text, covered=cue is not None, matched_cue=cue)
        )
    covered = sum(r.covered for r in results)
    passage_words = normalize_tokens(passage.text)
    grams = {
        tuple(passage_words[k : k + VERBATIM_NGRAM]) for k in range(len(passage_words) - VERBATIM_NGRAM + 1)
    }
    relay_grams = [tuple(words[k : k + VERBATIM_NGRAM]) for k in range(len(words) - VERBATIM_NGRAM + 1)]
    verbatim = sum(g in grams for g in relay_grams) / len(relay_grams) if relay_grams else 0.0
    return RelayCoverage(
        passage=passage.key,
        concepts=tuple(results),
        covered=covered,
        total=len(results),
        coverage=_r4(covered / len(results)) if results else 0.0,
        verbatim_ratio=_r4(verbatim),
        relay_words=len({t.word_index for t in tokenize(transcript or "", drop_fillers=True)[0]}),
    )


__all__ = [
    "ASR_TERM_RATIO",
    "ASR_WORD_RATIO",
    "FAST_WPM",
    "LONG_PAUSE_SECONDS",
    "METHOD",
    "PAUSE_MIN_SECONDS",
    "SLOW_WPM",
    "ConceptCoverage",
    "ReadingEvent",
    "ReadingMetrics",
    "RelayCoverage",
    "SentenceCoverage",
    "Token",
    "align_reading",
    "canonical_spelling",
    "delivery_score",
    "normalize_tokens",
    "phonetic_key",
    "relay_concepts",
    "relay_coverage",
    "tokenize",
]
