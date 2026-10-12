"""Gap-targeted follow-ups (Tier 2): what a substantive answer left out, found deterministically before the turn.

Quick assessment is off in production, so the turn model judged each answer while already speaking and its own
``missing_specifics`` arrived after the follow-up was asked (or never: the tail was cut). ``answer_gaps`` reads
the answer and the question it answered and names what a strong answer would still add; ``gap_instruction``
turns the most important one into a single directive line ("if you follow up, ask for exactly this"). No model
call, so no time on the critical path.

Conservative on purpose: a gap is only reported for an on-topic answer of some length, and each rule needs a
clear signal (e.g. "we" throughout and no "I"), because a false gap makes the interviewer ask for something the
candidate already gave - the "I already told you" failure this module exists to prevent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.enums import TurnAction
from app.services.interview.prompts import fence

#: Answers shorter than this are handled by the policy's brief/clarify paths, not by gap probes.
MIN_WORDS = 15

_WORD = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
_FIRST_PERSON = re.compile(r"\b(?:i|i'd|i've|i'm|i'll|me|my|myself|mine)\b")
_TEAM = re.compile(r"\b(?:we|we'd|we've|we're|we'll|our|ours|us|the team|my team)\b")
_NUMBER = re.compile(
    r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    r"hundred|hundreds|thousand|thousands|million|millions|billion|percent|half|double|twice|triple|dozen|"
    r"lakh|lakhs|crore|crores)\b"
)
_OUTCOME_CLAIM = re.compile(
    r"\b(?:improv\w*|reduc\w*|increas\w*|decreas\w*|faster|slower|cut|cutting|saved?|saving|scal\w*|grew|grow\w*|"
    r"dropp\w*|optimi[sz]\w*|speed\w* up|lower\w*|boost\w*|doubl\w*|halv\w*|shrank|minimi[sz]\w*|maximi[sz]\w*)\b"
)
_METRIC_ASK = re.compile(
    r"\b(?:how (?:many|much|big|large|long|fast|often)|scale|size|latency|throughput|cost|budget|percent\w*|"
    r"metric\w*|measur\w*|numbers?|volume|traffic|load|qps|rps|uptime|sla|slo)\b"
)
_REASONING = re.compile(
    r"\b(?:because|since|so that|reason|why|trade-?offs?|instead|rather than|chose|choose|chosen|decided|decision|"
    r"picked|prefer\w*|versus|vs|compared|alternative\w*|pros?|cons?|downside\w*|upside\w*|benefit\w*|cost of|"
    r"which meant|that meant|otherwise|risk\w*)\b"
)
#: Questions that ask for a decision (a "how did you measure / what happened" question does not need a why).
_DESIGN_ASK = re.compile(
    r"\b(?:design\w*|architect\w*|approach|choose|chose|choice|decide|decision|why|trade-?off\w*|structur\w*)\b"
)
_RESULT = re.compile(
    r"\b(?:result\w*|outcome\w*|impact|in the end|ended up|led to|which meant|finally|eventually|as a result|"
    r"learn\w*|lesson\w*|feedback|success\w*|worked|shipped|deliver\w*|resolv\w*|fixed|achiev\w*)\b"
)
_STOP = frozenset(
    "a an and are as at be by for from how in into is it its of on or that the their them they this to was were "
    "what when where which who why with your you about over under using use used per via vs than then there these "
    "those have has had can could would should will just also more most some any each other such only very".split()
)
#: Moves after which a follow-up on the same answer is possible.
STAY_MOVES: frozenset[TurnAction] = frozenset({TurnAction.ASK_FOLLOWUP, TurnAction.REQUEST_SPECIFICITY})


@dataclass(frozen=True, slots=True)
class Gap:
    kind: str  # expected_point | own_role | number | reasoning | outcome
    ask: str  # what to ask for, phrased for the interviewer (derived text: fenced when rendered)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _words(text) if len(w) >= 4 and w not in _STOP}


def uncovered_points(answer: str, expected: Sequence[str]) -> tuple[list[str], int]:
    """Expected-evidence phrases with no content word in the answer, and how many phrases it did touch."""
    said = _stems(answer)
    missing: list[str] = []
    covered = 0
    for point in expected:
        stems = _stems(point)
        if not stems:
            continue
        if stems & said:
            covered += 1
        else:
            missing.append(point.strip())
    return missing, covered


def answer_gaps(
    answer: str,
    *,
    question: str | None,
    expected_evidence: Sequence[str] = (),
    technical: bool = True,
) -> list[Gap]:
    """What the answer left out, most important first (empty for short or off-topic answers)."""
    text = (answer or "").strip()
    words = _words(text)
    if len(words) < MIN_WORDS:
        return []
    lowered = " ".join(words)
    asked = (question or "").lower()
    gaps: list[Gap] = []

    missing, covered = uncovered_points(text, expected_evidence)
    if missing and covered:
        # On topic (it touched part of what a full answer covers) but silent on a specific point.
        gaps.append(Gap("expected_point", f"the point they did not address: {missing[0][:120]}"))

    team = len(_TEAM.findall(lowered))
    if team >= 3 and not _FIRST_PERSON.search(lowered):
        gaps.append(Gap("own_role", "what they personally did, as opposed to the team (they said \"we\" throughout)"))

    # Stories (non-technical competencies) need an outcome, not a figure, unless the question asked for one.
    wants_number = _METRIC_ASK.search(asked) or (technical and _OUTCOME_CLAIM.search(lowered))
    if wants_number and not _NUMBER.search(lowered):
        gaps.append(Gap("number", "a concrete number for the outcome or the scale they described"))

    if technical:
        if len(words) >= 25 and _DESIGN_ASK.search(asked) and not _REASONING.search(lowered):
            gaps.append(Gap("reasoning", "why they chose that approach and what they gave up for it"))
    elif not _RESULT.search(lowered):
        gaps.append(Gap("outcome", "the outcome: what happened as a result and how they knew"))
    return gaps


def gap_instruction(
    gaps: Sequence[Gap],
    *,
    recommended: TurnAction,
    alternatives: Sequence[TurnAction] = (),
    allowed: Sequence[TurnAction] | None = None,
) -> str | None:
    """One directive line for the turn model when the move may stay on the answer (else None)."""
    stay = [a for a in (recommended, *alternatives) if a in STAY_MOVES and (allowed is None or a in allowed)]
    if not gaps or not stay:
        return None
    return (
        "If you follow up on this answer, ask for exactly one thing it left out, anchored on what they said, and "
        "never for something they already gave: " + fence(gaps[0].ask, "derived_answer_gap")
    )


__all__ = ["MIN_WORDS", "STAY_MOVES", "Gap", "answer_gaps", "gap_instruction", "uncovered_points"]
