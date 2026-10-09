"""Intake Interview scoring: competency weights, weighted total and decision bands.

Pure functions, no I/O. Every number is a :class:`~decimal.Decimal` and nothing is rounded before banding
(display layers format the values; the band always uses the exact total).

Formulas (coordinator decisions, ``docs/design/briefs/intake-common.md``)
-----------------------------------------------------------------------
* **Competency scores** are 1-5 (fractions allowed: ``technical_reading`` is a weighted mix of two drills).
  ``None`` (or a missing key) means *insufficient evidence* for that competency.
* **Weighted total** ``T = sum(score_k * weight_k)`` over the seven competencies. The weights sum to 1.00,
  so ``T`` stays on the 1-5 scale (max 5.0).
* **Decision band** (half-open intervals, no gaps)::

      T >= 4.20          -> strong_accept
      3.30 <= T < 4.20   -> accept_with_notes
      2.50 <= T < 3.30   -> borderline
      T < 2.50           -> reject
      any competency without sufficient evidence -> insufficient_evidence

  The kit printed the bands as "4.2 - 5.0 / 3.3 - 4.1 / 2.5 - 3.2 / below 2.5", which leaves gaps
  (4.15, 3.25); the half-open form closes them without moving a boundary.
* **Partial total** (only when evidence is insufficient): ``sum(score_k * weight_k) / sum(weight_k)`` over the
  scored competencies, i.e. re-normalised to the 1-5 scale. It is labelled as partial and never banded.
* **Technical Reading & Relay** = ``0.4 * Drill A + 0.6 * Drill B`` (each 1-5): Drill A is the read-aloud
  delivery score (:func:`app.services.intake.reading.align_reading`), Drill B the comprehension/relay score.
  Both drill scores are stored and reported separately; relaying the ideas weighs more because reading a
  passage flawlessly without understanding it is the weaker skill.
* **Out of 100** = ``T / 5 * 100``: a display conversion only (labelled as such); the band uses ``T``.

The band is an AI *recommendation*. Human reviewers decide (accept, accept with notes, second interview,
reject, request evidence); review flags never reject automatically.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

ScoreInput = Decimal | int | float | str | None


class DecisionBand(StrEnum):
    STRONG_ACCEPT = "strong_accept"
    ACCEPT_WITH_NOTES = "accept_with_notes"
    BORDERLINE = "borderline"
    REJECT = "reject"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


BAND_LABELS: Mapping[DecisionBand, str] = MappingProxyType(
    {
        DecisionBand.STRONG_ACCEPT: "Strong accept",
        DecisionBand.ACCEPT_WITH_NOTES: "Accept with notes",
        DecisionBand.BORDERLINE: "Borderline",
        DecisionBand.REJECT: "Reject",
        DecisionBand.INSUFFICIENT_EVIDENCE: "Insufficient evidence",
    }
)


@dataclass(frozen=True, slots=True)
class IntakeCompetency:
    key: str
    label: str  # UI label
    kit_label: str  # source wording (metadata only)
    weight: Decimal


INTAKE_COMPETENCIES: tuple[IntakeCompetency, ...] = (
    IntakeCompetency(
        "communication", "Communication & Presentation", "Communication & presentation", Decimal("0.20")
    ),
    IntakeCompetency(
        "technical_reading", "Technical Reading & Relay", "Doc-reading / proxy ability", Decimal("0.20")
    ),
    IntakeCompetency(
        "structured_answer",
        "Structured Answer / Gap-Filling",
        "Gap-filling / structured answer",
        Decimal("0.15"),
    ),
    IntakeCompetency("technical", "Actual Technical Ability", "Technical ability (actual)", Decimal("0.15")),
    IntakeCompetency(
        "trainability",
        "Trainability & Learning Attitude",
        "Trainability / learning attitude",
        Decimal("0.15"),
    ),
    IntakeCompetency("composure", "Confidence & Composure", "Confidence & composure", Decimal("0.10")),
    IntakeCompetency("adaptability", "Niche-Fit & Adaptability", "Niche-fit / adaptability", Decimal("0.05")),
)
INTAKE_COMPETENCY_KEYS: tuple[str, ...] = tuple(c.key for c in INTAKE_COMPETENCIES)
INTAKE_WEIGHTS: Mapping[str, Decimal] = MappingProxyType({c.key: c.weight for c in INTAKE_COMPETENCIES})
INTAKE_LABELS: Mapping[str, str] = MappingProxyType({c.key: c.label for c in INTAKE_COMPETENCIES})

SCORE_MIN = Decimal(1)
SCORE_MAX = Decimal(5)

#: Lower bounds (inclusive) of the bands, highest first; anything below the last one is ``reject``.
STRONG_ACCEPT_MIN = Decimal("4.20")
ACCEPT_WITH_NOTES_MIN = Decimal("3.30")
BORDERLINE_MIN = Decimal("2.50")
BAND_THRESHOLDS: tuple[tuple[Decimal, DecisionBand], ...] = (
    (STRONG_ACCEPT_MIN, DecisionBand.STRONG_ACCEPT),
    (ACCEPT_WITH_NOTES_MIN, DecisionBand.ACCEPT_WITH_NOTES),
    (BORDERLINE_MIN, DecisionBand.BORDERLINE),
)

DRILL_A_WEIGHT = Decimal("0.4")  # reading delivery (read aloud)
DRILL_B_WEIGHT = Decimal("0.6")  # comprehension / relay

NORMALIZED_SCALE = Decimal(100)
TOTAL_LABEL = "Weighted total (1-5)"
NORMALIZED_LABEL = "Out of 100 (conversion of the 1-5 weighted total: total / 5 x 100)"


def to_decimal(value: Decimal | int | float | str, *, name: str = "value") -> Decimal:
    """``value`` as a finite Decimal. Floats go through ``str`` (shortest repr), so 4.1999 stays 4.1999."""
    if isinstance(value, bool):
        raise TypeError(f"{name}: a boolean is not a score")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name}: {value!r} is not a finite number")
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{name}: {value!r} is not a number") from exc
    else:
        raise TypeError(f"{name}: unsupported type {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"{name}: {value!r} is not a finite number")
    return result


def to_score(value: ScoreInput, *, name: str = "score") -> Decimal | None:
    """A 1-5 competency (or drill) score as Decimal; ``None`` stays ``None`` (insufficient evidence)."""
    if value is None:
        return None
    score = to_decimal(value, name=name)
    if score < SCORE_MIN or score > SCORE_MAX:
        raise ValueError(f"{name}: {score} is outside the 1-5 scale")
    return score


def _resolve_weights(weights: Mapping[str, Decimal | int | float | str] | None) -> dict[str, Decimal]:
    if weights is None:
        return dict(INTAKE_WEIGHTS)
    resolved = {key: to_decimal(w, name=f"weight[{key}]") for key, w in weights.items()}
    if any(w <= 0 for w in resolved.values()):
        raise ValueError("weights must be positive")
    if sum(resolved.values(), Decimal(0)) != Decimal(1):
        raise ValueError("weights must sum to exactly 1")
    return resolved


def _scored(
    scores: Mapping[str, ScoreInput], weights: Mapping[str, Decimal]
) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    unknown = sorted(set(scores) - set(weights))
    if unknown:
        raise ValueError(f"unknown competencies: {', '.join(unknown)}")
    scored: dict[str, Decimal] = {}
    missing: list[str] = []
    for key in weights:
        score = to_score(scores.get(key), name=key)
        if score is None:
            missing.append(key)
        else:
            scored[key] = score
    return scored, tuple(missing)


def weighted_total(
    scores: Mapping[str, ScoreInput], *, weights: Mapping[str, Decimal | int | float | str] | None = None
) -> Decimal | None:
    """``sum(score * weight)`` over every competency, or ``None`` when any competency lacks evidence
    (missing key or ``None``). Exact Decimal, never rounded. Unknown keys and scores outside 1-5 raise."""
    resolved = _resolve_weights(weights)
    scored, missing = _scored(scores, resolved)
    if missing:
        return None
    return sum((scored[key] * resolved[key] for key in resolved), Decimal(0))


def partial_weighted_total(
    scores: Mapping[str, ScoreInput], *, weights: Mapping[str, Decimal | int | float | str] | None = None
) -> Decimal | None:
    """Weighted mean over the *scored* competencies only (re-normalised to 1-5), or ``None`` when nothing
    is scored. Equals :func:`weighted_total` when every competency is scored. Never banded."""
    resolved = _resolve_weights(weights)
    scored, _missing = _scored(scores, resolved)
    if not scored:
        return None
    weight = sum((resolved[key] for key in scored), Decimal(0))
    return sum((scored[key] * resolved[key] for key in scored), Decimal(0)) / weight


def decision_band(total: Decimal | int | float | str | None) -> DecisionBand:
    """The band of a weighted total (half-open intervals, see the module docstring). ``None`` means some
    competency lacks evidence -> ``insufficient_evidence``. The exact value is compared: 4.1999 is
    ``accept_with_notes``, 4.2 is ``strong_accept``."""
    if total is None:
        return DecisionBand.INSUFFICIENT_EVIDENCE
    value = to_decimal(total, name="total")
    if value < SCORE_MIN or value > SCORE_MAX:
        raise ValueError(f"total {value} is outside the 1-5 scale")
    for minimum, band in BAND_THRESHOLDS:
        if value >= minimum:
            return band
    return DecisionBand.REJECT


def technical_reading_score(drill_a: ScoreInput, drill_b: ScoreInput) -> Decimal | None:
    """``0.4 * drill_a + 0.6 * drill_b`` (each 1-5). ``None`` when either drill has no score: the
    competency then counts as insufficient evidence instead of silently using one drill."""
    a = to_score(drill_a, name="drill_a")
    b = to_score(drill_b, name="drill_b")
    if a is None or b is None:
        return None
    return DRILL_A_WEIGHT * a + DRILL_B_WEIGHT * b


def normalized_100(total: Decimal | int | float | str | None) -> Decimal | None:
    """``total / 5 * 100`` (display conversion of the 1-5 total; label it as a conversion)."""
    if total is None:
        return None
    value = to_decimal(total, name="total")
    if value < SCORE_MIN or value > SCORE_MAX:
        raise ValueError(f"total {value} is outside the 1-5 scale")
    return value / SCORE_MAX * NORMALIZED_SCALE


class IntakeScoreSummary(BaseModel):
    """Everything a report needs about the weighted score of one intake session."""

    model_config = ConfigDict(frozen=True)

    band: DecisionBand
    band_label: str
    total: Decimal | None  # full weighted total; None when evidence is insufficient
    total_label: str = TOTAL_LABEL
    normalized_100: Decimal | None  # conversion of ``total``
    normalized_label: str = NORMALIZED_LABEL
    partial_total: Decimal | None  # only when evidence is insufficient and something is scored
    partial_label: str | None
    scored: tuple[str, ...]
    missing: tuple[str, ...]
    scored_weight: Decimal
    contributions: dict[str, Decimal]  # score x weight per scored competency


def summarize_scores(
    scores: Mapping[str, ScoreInput], *, weights: Mapping[str, Decimal | int | float | str] | None = None
) -> IntakeScoreSummary:
    """Total, band, out-of-100 conversion and (when evidence is insufficient) the labelled partial total."""
    resolved = _resolve_weights(weights)
    scored, missing = _scored(scores, resolved)
    contributions = {key: scored[key] * resolved[key] for key in scored}
    scored_weight = sum((resolved[key] for key in scored), Decimal(0))
    if not missing:
        total = sum(contributions.values(), Decimal(0))
        band = decision_band(total)
        return IntakeScoreSummary(
            band=band,
            band_label=BAND_LABELS[band],
            total=total,
            normalized_100=normalized_100(total),
            partial_total=None,
            partial_label=None,
            scored=tuple(scored),
            missing=(),
            scored_weight=scored_weight,
            contributions=contributions,
        )
    partial = sum(contributions.values(), Decimal(0)) / scored_weight if scored else None
    label = (
        f"Partial total re-normalised over {len(scored)} of {len(resolved)} competencies "
        f"({scored_weight * 100:.0f}% of the weight); not a decision band"
        if scored
        else None
    )
    band = DecisionBand.INSUFFICIENT_EVIDENCE
    return IntakeScoreSummary(
        band=band,
        band_label=BAND_LABELS[band],
        total=None,
        normalized_100=None,
        partial_total=partial,
        partial_label=label,
        scored=tuple(scored),
        missing=missing,
        scored_weight=scored_weight,
        contributions=contributions,
    )


__all__ = [
    "ACCEPT_WITH_NOTES_MIN",
    "BAND_LABELS",
    "BAND_THRESHOLDS",
    "BORDERLINE_MIN",
    "DRILL_A_WEIGHT",
    "DRILL_B_WEIGHT",
    "INTAKE_COMPETENCIES",
    "INTAKE_COMPETENCY_KEYS",
    "INTAKE_LABELS",
    "INTAKE_WEIGHTS",
    "NORMALIZED_LABEL",
    "SCORE_MAX",
    "SCORE_MIN",
    "STRONG_ACCEPT_MIN",
    "TOTAL_LABEL",
    "DecisionBand",
    "IntakeCompetency",
    "IntakeScoreSummary",
    "decision_band",
    "normalized_100",
    "partial_weighted_total",
    "summarize_scores",
    "technical_reading_score",
    "to_decimal",
    "to_score",
    "weighted_total",
]
