"""Inconsistency records: de-duplication and neutral wording.

The evaluator and the turn model describe the same discrepancy in different
words every time they see it ("Resume says ~120 nodes; candidate said about
eight" / "Candidate stated 8 nodes, resume claims 120"). Records are therefore
compared by their content words and numbers, not by exact text, so one
discrepancy stays one record: it is probed at most once and counted once in
reports and in the recommendation.

Descriptions are stored in neutral language: an inconsistency is a question
for a human reviewer, never a finding about the candidate.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

_TOKEN = re.compile(r"[a-z0-9]+")
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "hundred": "100", "thousand": "1000",
}  # fmt: skip
_STOP = frozenset(
    "a an and are as at be been but by can could did do does for from had has have how i if in into is it its me "
    "my of on or our so that the their them then there these they this to us was were what when where which who "
    "why will with would you your earlier now previously previous current answer answers candidate candidates "
    "resume claim claims claimed claiming states stated says said mentioned mentions describes described "
    "while whereas however also only just about around approximately roughly than more most some vs versus "
    "turn statement statements".split()
)
SIMILARITY_THRESHOLD = 0.5

# Judgemental phrasing -> neutral phrasing (descriptions are shown to reviewers and to the interviewer).
_NEUTRAL = (
    (re.compile(r"\bignores?\b(?: (?:these|this|the|those))?", re.I), "does not mention"),
    (re.compile(r"\bignored\b", re.I), "did not mention"),
    (re.compile(r"\bfail(?:s|ed)? to\b", re.I), "did not"),
    (re.compile(r"\bfailing to\b", re.I), "not"),
    (re.compile(r"\bfailure to\b", re.I), "not"),
    (re.compile(r"\badmit(?:s|ted)?\b", re.I), "said"),
    (re.compile(r"\b(?:evasive(?:ly)?|dodg\w+)\b", re.I), "indirect"),
    (re.compile(r"\b(?:lie|lies|lied|lying)\b", re.I), "inconsistency"),
    (re.compile(r"\b(?:entirely|completely|clearly|obviously)\b\s*", re.I), ""),
    (re.compile(r"\b(?:exaggerat\w+|inflat\w+|overstat\w+|embellish\w+)\b", re.I), "differs from"),
)


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for token in _TOKEN.findall((text or "").lower()):
        token = _NUMBER_WORDS.get(token, token)
        if token in _STOP or (len(token) < 3 and not token.isdigit()):
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(token) > 4 and token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        out.add(token)
    return out


def similarity(a: str, b: str) -> float:
    """0-1: content-word overlap (relative to the smaller record), boosted when
    both mention the same numbers; fuzzy token-set ratio as a floor."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb) / min(len(ta), len(tb))
    numbers_a = {t for t in ta if t.isdigit()}
    numbers_b = {t for t in tb if t.isdigit()}
    if numbers_a and numbers_a == numbers_b:
        overlap = min(1.0, overlap + 0.2)
    ratio = fuzz.token_set_ratio(" ".join(sorted(ta)), " ".join(sorted(tb))) / 100.0
    return max(overlap, ratio * 0.8)


def is_similar(a: str, b: str, *, threshold: float = SIMILARITY_THRESHOLD) -> bool:
    return similarity(a, b) >= threshold


# A conflict with documentation / best practice / "how it really works" is a correctness question for the
# evaluation, not an inconsistency between the candidate's own statements (harness finding: "Candidate claims
# no extra permissions were needed for Cluster Autoscaler, which may contradict AWS documentation" was
# recorded as a high-severity contradiction and probed as one).
_EXTERNAL_REFERENCE = re.compile(
    r"\b(?:documentation|docs|best practices?|official guidance|industry standard|standard practice|"
    r"technically (?:incorrect|inaccurate|wrong)|factually (?:incorrect|inaccurate|wrong)|is (?:incorrect|"
    r"inaccurate|wrong)|not how \w+ works|how \w+ (?:actually|really) works)\b",
    re.IGNORECASE,
)
_OWN_STATEMENT = re.compile(
    r"\b(?:r[eé]sum[eé]|cv|earlier|previous(?:ly)?|before|prior|initially|first answer|other answer|another "
    r"answer|later|then said|now says?|now said|turn)\b",
    re.IGNORECASE,
)


def is_external_conflict(text: str) -> bool:
    """True when a reported "contradiction" compares an answer with outside knowledge rather than with the
    candidate's resume or another of their answers."""
    return bool(_EXTERNAL_REFERENCE.search(text or "")) and not _OWN_STATEMENT.search(text or "")


def neutral_description(text: str) -> str:
    """Strip judgemental wording from an inconsistency description."""
    out = text or ""
    for pattern, replacement in _NEUTRAL:
        out = pattern.sub(replacement, out)
    return re.sub(r"\s{2,}", " ", out).strip()


__all__ = ["SIMILARITY_THRESHOLD", "is_external_conflict", "is_similar", "neutral_description", "similarity"]
