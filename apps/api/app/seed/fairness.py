"""Fairness lint for catalog content.

Interview content is job-related only: questions, problems and rubrics never
ask about, mention or evaluate protected or prohibited topics (age, date of
birth, marital/family status, pregnancy, children, health, disability,
religion, race, ethnicity, national origin, citizenship/visa status, sexual
orientation, gender identity, salary history, appearance, accent) and never
judge emotion or personality.

The patterns are deliberately specific so ordinary technical vocabulary does
not trip them: ``race condition``, ``health check``, ``child node``,
``max-age``, ``accent color``, ``instance family``, ``first-class citizen``,
``confidence interval``, ``kernel panic``, ``user-friendly``, ``stable
marriage problem``, ``birthday attack``, ``your mental model``... Rules marked
``evaluation_only`` (demeanour words such as "confident" or "calm") apply to
evaluator-facing text only: rubrics, expected concepts, common errors and
competency anchors, where they would turn into a judgement of the candidate.

Use :func:`fairness_hits` for one text, or :func:`lint_fields` for a record's
``(field, text, evaluation)`` triples; :func:`strip_code` removes fenced and
inline code from Markdown before linting (identifiers and data are not prose).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FairnessRule:
    topic: str
    pattern: re.Pattern[str]
    evaluation_only: bool = False


@dataclass(frozen=True, slots=True)
class FairnessHit:
    field: str
    topic: str
    match: str
    context: str

    def describe(self) -> str:
        return f"{self.field}: mentions {self.topic} ({self.match!r} in {self.context!r})"


def _rx(*patterns: str) -> re.Pattern[str]:
    # Patterns are lowercase and run on lowercased text: much faster than
    # re.IGNORECASE on large catalogs.
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_POSSESSIVE = r"(?:your|candidate'?s|applicant'?s|interviewee'?s|his|her)"

RULES: tuple[FairnessRule, ...] = (
    FairnessRule(
        "age",
        _rx(
            r"\bhow old (?:are|is|were|was) (?:you|they|he|she|the (?:candidate|applicant|interviewee))\b",
            rf"\b{_POSSESSIVE} age\b",
            r"\bage of (?:the )?(?:candidate|applicant|interviewee)\b",
            r"\b(?:older|younger) (?:candidates?|applicants?|workers?|employees?|colleagues?|people|engineers?|developers?|hires?)\b",
            r"\bage (?:groups?|brackets?|discrimination)\b",
            r"\b(?:you(?:'re| are)|they(?:'re| are)|i'?m) too (?:old|young)\b",
            r"\bretire(?:ment)? (?:age|plans?)\b",
            r"\b(?:when|what year) did you graduate\b",
            r"\bgraduation year\b",
            r"\b(?:millennials?|baby boomers?|boomers|gen ?z|generation z)\b",
        ),
    ),
    FairnessRule(
        "date of birth",
        _rx(
            r"\bdates? of birth\b",
            r"\bbirth ?dates?\b",
            r"\bbirthdays?\b(?!\s+(?:paradox|problem|attacks?|bounds?))",
            r"\b(?:year|place|country) of birth\b",
            r"\bbirthplace\b",
            r"\b(?:when|where) were you born\b",
        ),
    ),
    FairnessRule(
        "marital or family status",
        _rx(
            r"\bmarital\b",
            r"\bmarried\b",
            r"\bmarriage\b(?<!stable marriage)(?!\s+problems?)",
            r"\bmarriages\b(?<!stable marriages)",
            r"\bspouses?\b",
            r"\bhusbands?\b",
            r"\bwi(?:fe|ves)\b",
            r"\bdivorc\w*",
            r"\bwidow(?:ed|er|ers)\b",
            r"\bfianc[eé]{1,2}s?\b",
            r"\b(?:boy|girl)friends?\b",
            r"\bfamily (?:status|plans?|situation|commitments?|obligations?|responsibilities|life|background)\b",
            r"\bsingle (?:mother|father|parent)s?\b",
            r"\bstart(?:ing)? a family\b",
            r"\bcaregiv(?:er|ers|ing)\b",
            r"\bmaiden name\b",
        ),
    ),
    FairnessRule(
        "pregnancy",
        _rx(
            r"\bpregnan\w*",
            r"\bmaternity\b",
            r"\bpaternity\b",
            r"\bparental leave\b",
            r"\bexpecting a (?:baby|child)\b",
        ),
    ),
    FairnessRule(
        "children",
        _rx(
            r"\bkids\b",
            r"\b(?:your|my|their) kid\b",
            r"\b(?:your|my) (?:children|child|sons?|daughters?)\b(?!\s+(?:process(?:es)?|nodes?|elements?|components?|threads?|tasks?|spans?|class(?:es)?|widgets?|routes?|pids?|windows?|tables?|records?|rows?))",
            r"\b(?:do|did|will) you (?:have|want|plan to have) (?:any )?(?:children|kids)\b",
            r"\bchild ?care\b",
        ),
    ),
    FairnessRule(
        "health",
        _rx(
            rf"\b{_POSSESSIVE} (?:health|medical)\b(?![- ]?(?:checks?|probes?|endpoints?|status|monitor\w*|scores?|dashboards?|signals?|metrics?))",
            r"\bmental (?:health|illness(?:es)?|conditions?)\b",
            r"\bphysical (?:health|fitness|condition|abilit(?:y|ies))\b",
            r"\b(?:any|chronic|pre-?existing|underlying) (?:health|medical) (?:conditions?|issues?|problems?)\b",
            r"\bmedical (?:conditions?|leave|history)\b",
            r"\bsick (?:leave|days?)\b",
            r"\b(?:chronic|serious|long-term) (?:illness(?:es)?|conditions?)\b",
            r"\billness(?:es)?\b",
            r"\bdiagnos(?:ed|is) with\b",
        ),
    ),
    FairnessRule(
        "disability",
        _rx(
            r"\bdisabilit(?:y|ies)\b",
            r"\bdisabled (?:people|persons?|candidates?|applicants?|colleagues?|workers?)\b",
            r"\bhandicap\w*",
            r"\b(?:hearing|visual|cognitive|physical|mobility|speech) impairments?\b",
            r"\bwheelchairs?\b",
            r"\bneurodiver\w*|\bneurodivergen\w*",
            r"\b(?:autism|autistic|adhd|dyslexi\w*)\b",
        ),
    ),
    FairnessRule(
        "religion",
        _rx(
            r"\breligio\w*",
            r"\b(?:church|churches|mosques?|synagogues?)\b",
            r"\b(?:christians?|muslims?|jewish|jews|hindus?|buddhists?|sikhs?|atheists?|catholics?|protestants?)\b",
            rf"\b{_POSSESSIVE} faith\b",
            r"\bholidays? (?:do )?you (?:observe|celebrate)\b",
        ),
    ),
    FairnessRule(
        "race or ethnicity",
        _rx(
            r"\bracial\w*",
            r"\bracis[mt]s?\b",
            rf"\b(?:{_POSSESSIVE[3:-1]}|their) race\b(?!\s+conditions?)",
            r"\brace(?:\s*/\s*|\s*,\s*|\s+(?:or|and)\s+)(?:ethnicity|religion|gender|colou?r|national origin|sex)\b",
            r"\b(?:ethnicity|religion|gender|colou?r|sex)(?:\s*/\s*|\s*,\s*|\s+(?:or|and)\s+)race\b",
            r"\bethnic\w*",
            r"\bskin colou?r\b",
        ),
    ),
    FairnessRule(
        "national origin",
        _rx(
            r"\bnational origin\b",
            r"\bnationalit(?:y|ies)\b",
            r"\bwhere (?:are|were) you (?:originally |really )?from\b",
            rf"\b{_POSSESSIVE} (?:country|place) of origin\b",
            r"\b(?:native|non-native) (?:english )?speakers?\b",
            r"\b(?:mother|native) tongue\b",
        ),
    ),
    FairnessRule(
        "citizenship or visa status",
        _rx(
            r"\bcitizenship\b",
            r"\b(?:are you|is the candidate|is the applicant) an? (?:\w+ )?citizen\b",
            r"\bvisas? (?:status|sponsorship|types?|holders?|requirements?)\b",
            r"\b(?:work|residence|residency) (?:permits?|visas?)\b",
            r"\b(?:residency|immigration) status\b",
            r"\bgreen card\b",
            r"\bh-?1b\b",
            r"\bimmigra(?:nt|nts|tion)\b",
            r"\bwork authori[sz]ation\b",
            r"\b(?:legally )?(?:authori[sz]ed|eligible) to work\b",
            r"\b(?:require|requires|need|needs) (?:visa )?sponsorship\b",
            r"\bsponsor(?:ship)? (?:for )?(?:a |your )?(?:visa|work permit)\b",
        ),
    ),
    FairnessRule(
        "sexual orientation",
        _rx(
            r"\bsexual\w*",
            r"\b(?:gay|lesbian|bisexual|transgender|queer|lgbt\w*|heterosexual|homosexual)\b",
            r"\bsex life\b",
        ),
    ),
    FairnessRule(
        "gender identity",
        _rx(
            r"\bgender\w*",
            r"\b(?:non-?binary|cisgender)\b",
            r"\b(?:women|woman|men|males?|females?)\b",
            r"\bsex\b",
        ),
    ),
    FairnessRule(
        "salary history",
        _rx(
            r"\bsalary history\b",
            r"\bpay history\b",
            rf"\b{_POSSESSIVE} (?:(?:current|previous|past|last|prior|present) )?(?:salary|salaries|ctc|pay|earnings|income|compensation)\b(?!\s+(?:expectations?|requirements?|logic|steps?|actions?|transactions?|handlers?|flows?|events?))",
            r"\bsalary (?:at|in|from) your (?:current|last|previous|prior|present) (?:job|role|company|employer|position)\b",
            r"\bhow much (?:do|did) you (?:currently )?(?:make|earn|get paid)\b",
            r"\bwhat (?:do|did) you (?:currently )?(?:make|earn)\b",
        ),
    ),
    FairnessRule(
        "appearance",
        _rx(
            r"\bphysical appearance\b",
            rf"\b{_POSSESSIVE} (?:appearance|looks|attire|clothing|clothes|outfit|hairstyle|hair|skin|body|tattoos?)\b",
            r"\b(?:your|candidate'?s|applicant'?s) (?:height|weight)\b(?!\s+(?:decay|initiali[sz]ation|matrix|matrices|updates?|sharing))",
            r"\b(?:good|nice|great)[- ]looking\b",
            r"\bphysically attractive\b",
            r"\bwell[- ]dressed\b",
            r"\bdress code\b",
            r"\b(?:tattoos?|piercings?)\b",
            r"\b(?:overweight|obese|obesity)\b",
        ),
    ),
    FairnessRule(
        "accent",
        _rx(
            r"\b(?:your|candidate'?s|applicant'?s|his|her|their|a|an|the|thick|strong|heavy|foreign|native|regional|non-native|british|american|indian|english) accents?\b(?![- ]?(?:colou?rs?|marks?|characters?|letters?)|\s+on\b)",
            r"\baccent (?:reduction|neutrali[sz]ation)\b",
            r"\bspeaks? with an? (?:\w+ )?accent\b",
        ),
    ),
    FairnessRule(
        "emotion or personality",
        _rx(
            r"\bpersonalit(?:y|ies)\b",
            r"\btemperament\w*",
            r"\b(?:introvert|extrovert|extravert|ambivert)\w*",
            r"\b(?:likeable|likable|likeability|charisma\w*)\b",
            r"\bemotion(?:s|al|ally)?\b",
            r"\b(?:nervous|nervousness|anxious|anxiety|insecure|insecurity)\b",
            r"\bbody language\b",
            r"\beye contact\b",
            r"\btone of voice\b",
            r"\b(?:intelligent|smart|brilliant|gifted) (?:candidates?|people|persons?|engineers?|developers?|hires?)\b",
            r"\biq\b",
            r"\bpolitical (?:views?|affiliations?|part(?:y|ies)|opinions?|beliefs?)\b",
        ),
    ),
    FairnessRule(
        "emotion or personality judgement",
        _rx(
            r"\b(?:confident|confidently|self-confiden\w*|overconfident|underconfident)\b",
            r"\b(?:with|shows?|showing|displays?|lacks?|lacking|projects?) (?:self-)?confidence\b(?!\s+(?:intervals?|levels?|scores?|bounds?|thresholds?|estimates?))",
            r"\b(?:calm|calmly|composure)\b",
            r"\b(?:enthusias(?:m|tic|tically)|passion|passionate|passionately|energetic)\b",
            r"\b(?:shy|timid|arrogant|arrogance|humble|humility|charming|cheerful)\b",
            r"\b(?:honest|honestly|honesty|sincere|sincerely|sincerity)\b",
            r"\b(?:positive )?attitude\b",
            r"\bdemeanou?r\b",
            r"\bsmil(?:e|es|ed|ing)\b",
            r"\bculture fit\b|\bfits? (?:in|into) (?:the|our) (?:team|culture)\b",
            r"\b(?:intelligent|intelligence|brilliant|gifted)\b(?<!artificial intelligence)(?<!business intelligence)(?<!threat intelligence)",
        ),
        evaluation_only=True,
    ),
)

_FENCED = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")


def strip_code(markdown: str) -> str:
    """Markdown without fenced code blocks and inline code spans."""
    return _INLINE.sub(" ", _FENCED.sub(" ", markdown))


def _context(text: str, start: int, end: int, width: int = 40) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    snippet = " ".join(text[left:right].split())
    return ("..." if left else "") + snippet + ("..." if right < len(text) else "")


def fairness_hits(text: str, *, evaluation: bool = False, field: str = "text") -> list[FairnessHit]:
    """Protected or prohibited topics mentioned in ``text`` (one hit per
    topic). ``evaluation=True`` also applies the demeanour/personality
    judgement rules meant for rubric-like text."""
    hits: list[FairnessHit] = []
    if not text:
        return hits
    lowered = text.lower()
    # str.lower() keeps the length for virtually all text; otherwise show the lowered text.
    shown = text if len(lowered) == len(text) else lowered
    for rule in RULES:
        if rule.evaluation_only and not evaluation:
            continue
        match = rule.pattern.search(lowered)
        if match:
            start, end = match.span()
            hits.append(FairnessHit(field, rule.topic, shown[start:end], _context(shown, start, end)))
    return hits


def lint_fields(fields: Iterable[tuple[str, str | None, bool]]) -> list[FairnessHit]:
    """Lint ``(field, text, evaluation)`` triples."""
    hits: list[FairnessHit] = []
    for name, text, evaluation in fields:
        if text:
            hits.extend(fairness_hits(text, evaluation=evaluation, field=name))
    return hits


__all__ = ["RULES", "FairnessHit", "FairnessRule", "fairness_hits", "lint_fields", "strip_code"]
