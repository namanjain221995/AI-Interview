"""Deterministic Drill A and Drill B scores (design contract section 8.5).

* **Drill A** (read aloud) = :attr:`ReadingMetrics.delivery_score` from :func:`app.services.intake.reading.align_reading`
  (completeness / word error rate base, out-of-order cap 4, pace and long-pause deductions; pronunciation is not
  assessed and accent never affects it). ``None`` (insufficient evidence) only when the reading could not be
  captured for technical reasons: no capture, a transcript that is ``unavailable`` while speech was detected or
  recognition was degraded, or only disconnect-ended attempts. A candidate who does not read gets 1 plus the
  ``reading_not_completed`` review flag.
* **Drill B** (read and relay) = :func:`relay_score`: each of the passage's hand-written key concepts earns 1
  (``covered`` with a validated quote), 0.5 (``partial`` with a validated quote, or a covered/partial status without a
  valid quote that the deterministic cue coverage confirms) or 0; without a model judgement the cue coverage alone
  decides (basis ``cue_only``). With ``r = credit / concepts``::

      r >= 0.80 -> 5, r >= 0.60 -> 4, r >= 0.40 -> 3, r >= 0.20 -> 2, otherwise 1

  A recited relay (model ``paraphrase = recited`` or a verbatim 4-gram ratio >= 0.5) is capped at 4 (the kit's
  anchor 5 is "relays in own words"); each validated factual misstatement about the passage costs 1 (at most 2);
  never below 1. Silence or "I can't remember" scores 1 (kit anchor 1). ``None`` only when no relay could be
  captured for technical reasons.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Literal

from app.services.intake.evidence import ActivityRecord
from app.services.intake.reading import ReadingMetrics, RelayCoverage, align_reading

RECITED_VERBATIM_RATIO = 0.5
RECITED_MAX = 4
MAX_MISSTATEMENT_DEDUCTION = 2
RELAY_THRESHOLDS: tuple[tuple[Fraction, int], ...] = (
    (Fraction(80, 100), 5),
    (Fraction(60, 100), 4),
    (Fraction(40, 100), 3),
    (Fraction(20, 100), 2),
)
TECHNICAL_END_REASONS = frozenset({"disconnect"})
SCORABLE_END_REASONS = frozenset({"finish_button", "deadline", "silence", "max_audio", "session_end"})
SPEECH_DETECTED_MS = 1500  # voiced audio that should have produced words


@dataclass(slots=True)
class DrillScore:
    """Score + human-readable basis of one drill (stored in evaluator runs, competency ``sub_scores`` and reports)."""

    score: int | None
    basis: list[str] = field(default_factory=list)
    status: Literal["scored", "insufficient"] = "scored"
    reason: str | None = None  # why insufficient (technical)
    coverage: float | None = None
    concepts: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # kit review flags raised by the drill

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "basis": list(self.basis),
            "status": self.status,
            "reason": self.reason,
            "coverage": self.coverage,
            "concepts": list(self.concepts),
            "flags": list(self.flags),
        }


def _insufficient(reason: str, *basis: str) -> DrillScore:
    return DrillScore(score=None, basis=list(basis) or [reason], status="insufficient", reason=reason)


# ============================================================================================== capture state


def capture_problem(activity: ActivityRecord | None) -> str | None:
    """Why an activity has no scorable capture for *technical* reasons (``None`` when it is scorable)."""
    if activity is None:
        return "not_run"
    end_reason = (activity.capture or {}).get("end_reason") or activity.ended_reason
    if not activity.captured:
        if activity.attempts_log and all(
            (entry or {}).get("end_reason") in TECHNICAL_END_REASONS for entry in activity.attempts_log
        ):
            return "interrupted_by_disconnect"
        return "not_captured" if activity.status not in ("skipped",) else "skipped"
    if end_reason in TECHNICAL_END_REASONS:
        return "interrupted_by_disconnect"
    if activity.status in ("interrupted", "failed") and not activity.transcript:
        return "interrupted"
    if not activity.transcript:
        voiced = int((activity.capture or {}).get("voiced_ms") or 0)
        degraded = bool((activity.capture or {}).get("stt_degraded"))
        if activity.transcript_status == "unavailable" and (degraded or voiced >= SPEECH_DETECTED_MS):
            return "transcript_unavailable"
        if degraded and voiced >= SPEECH_DETECTED_MS:
            return "transcript_unavailable"
    return None


# ============================================================================================== Drill A


def reading_metrics(activity: ActivityRecord, passage: Any) -> ReadingMetrics:
    """F0 alignment of the scored attempt: final transcript (live as fallback), captured segments and VAD pauses."""
    capture = activity.capture or {}
    segments = capture.get("segments") or None
    pauses = capture.get("pauses") or None
    return align_reading(passage, activity.transcript, segments=segments, pauses=pauses)


def drill_a_score(activity: ActivityRecord | None, metrics: ReadingMetrics | None) -> DrillScore:
    """Drill A from the reading metrics; ``None`` only for technical capture problems."""
    problem = capture_problem(activity)
    if problem is not None:
        return _insufficient(problem, f"reading could not be assessed ({problem.replace('_', ' ')})")
    if metrics is None:
        return _insufficient("not_aligned", "reading could not be aligned to the passage")
    basis = list(metrics.delivery_basis)
    if activity is not None and activity.transcript_origin == "live_segments":
        basis.append("transcript: live recognition segments (the final pass was not available)")
    return DrillScore(
        score=int(metrics.delivery_score),
        basis=basis,
        coverage=metrics.completeness,
        flags=list(metrics.review_flags),
    )


# ============================================================================================== Drill B


@dataclass(frozen=True, slots=True)
class ConceptEvidence:
    """A model judgement of one relay concept after quote validation."""

    key: str
    status: Literal["covered", "partial", "missing"]
    quote_valid: bool
    answer: str | None = None
    quote: str | None = None


def _base(ratio: Fraction) -> int:
    for minimum, score in RELAY_THRESHOLDS:
        if ratio >= minimum:
            return score
    return 1


def relay_score(
    coverage: RelayCoverage | None,
    *,
    concepts: Sequence[ConceptEvidence] | None,
    paraphrase: str | None = None,
    misstatements: int = 0,
    captured: bool = True,
    problem: str | None = None,
) -> DrillScore:
    """Drill B score from the kit concepts (see the module docstring). ``concepts=None`` = model unavailable
    (cue coverage only); ``captured=False`` = no relay for technical reasons (``problem`` says why)."""
    if not captured or coverage is None:
        reason = problem or "not_captured"
        return _insufficient(reason, f"relay could not be assessed ({reason.replace('_', ' ')})")
    judged = {c.key: c for c in concepts or ()}
    cue_only = concepts is None
    credit = Fraction(0)
    rows: list[dict[str, Any]] = []
    for kit_concept in coverage.concepts:
        judgement = judged.get(kit_concept.key)
        value = Fraction(0)
        status = "missing"
        source = "none"
        if cue_only:
            if kit_concept.covered:
                value, status, source = Fraction(1), "covered", "cue"
        elif judgement is not None and judgement.status != "missing":
            if judgement.quote_valid:
                value = Fraction(1) if judgement.status == "covered" else Fraction(1, 2)
                status, source = judgement.status, "model_quote"
            elif kit_concept.covered:
                value, status, source = Fraction(1, 2), "partial", "model_cue"
        credit += value
        rows.append(
            {
                "key": kit_concept.key,
                "text": kit_concept.text,
                "status": status,
                "credit": float(value),
                "source": source,
                "matched_cue": kit_concept.matched_cue,
                "answer": judgement.answer if judgement is not None and judgement.quote_valid else None,
                "quote": judgement.quote if judgement is not None and judgement.quote_valid else None,
            }
        )
    total = len(coverage.concepts)
    ratio = credit / total if total else Fraction(0)
    base = _base(ratio)
    basis = [
        f"key concepts relayed: {float(credit):g} of {total} (r = {float(ratio):.2f}) -> base {base}"
        + (" [cue coverage only: model judgement unavailable]" if cue_only else "")
    ]
    if cue_only:
        basis.append("cue_only")
    score = base
    recited = paraphrase == "recited" or coverage.verbatim_ratio >= RECITED_VERBATIM_RATIO
    if recited and score > RECITED_MAX:
        score = RECITED_MAX
        basis.append(
            f"relay largely recited the passage (verbatim 4-gram ratio {coverage.verbatim_ratio:.2f}"
            + (", judged recited" if paraphrase == "recited" else "")
            + f") -> at most {RECITED_MAX}"
        )
    deduction = min(max(0, int(misstatements)), MAX_MISSTATEMENT_DEDUCTION)
    if deduction:
        score = max(1, score - deduction)
        basis.append(f"{misstatements} factual misstatement(s) about the passage -> -{deduction}")
    if coverage.relay_words == 0:
        basis.append("no relay speech recognised -> kit anchor 1")
    return DrillScore(score=score, basis=basis, coverage=float(ratio), concepts=rows)


__all__ = [
    "MAX_MISSTATEMENT_DEDUCTION",
    "RECITED_MAX",
    "RECITED_VERBATIM_RATIO",
    "RELAY_THRESHOLDS",
    "ConceptEvidence",
    "DrillScore",
    "capture_problem",
    "drill_a_score",
    "reading_metrics",
    "relay_score",
]
