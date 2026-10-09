"""Difficulty adaptation and target-distribution balancing (ENGINE_DESIGN §6).

Pure functions:

* :func:`starting_difficulty` - a competency's first question starts at the
  plan's ``starting_difficulty``, nudged one level toward the level with the
  largest deficit against the round's target distribution (30/40/30 by
  default), but never above what the candidate's running average supports
  (avg < 5 -> easy/medium only; avg >= 7 -> medium/hard) and never more than
  one level away from the plan.
* :func:`adapt` - step up one level after a strong answer; step down after
  two weak answers at a level; never jump two levels.
* :func:`clamp_step` - post-check helper: a model-reported difficulty more
  than one level away from the current one is clamped to one step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.enums import ANSWER_QUALITY_VALUE, DIFFICULTY_ORDER, AnswerQuality, Difficulty
from app.schemas.rounds import DifficultyDistribution

STRONG_QUALITIES: frozenset[AnswerQuality] = frozenset({AnswerQuality.STRONG})
WEAK_QUALITIES: frozenset[AnswerQuality] = frozenset(
    {AnswerQuality.INCORRECT, AnswerQuality.NO_ANSWER, AnswerQuality.VAGUE, AnswerQuality.OFF_TOPIC}
)
SCORED_QUALITIES: frozenset[AnswerQuality] = frozenset(ANSWER_QUALITY_VALUE)


def level(difficulty: Difficulty) -> int:
    return DIFFICULTY_ORDER.index(Difficulty(difficulty))


def from_level(value: int) -> Difficulty:
    return DIFFICULTY_ORDER[max(0, min(len(DIFFICULTY_ORDER) - 1, value))]


def step(difficulty: Difficulty, delta: int) -> Difficulty:
    return from_level(level(difficulty) + (1 if delta > 0 else -1 if delta < 0 else 0))


def clamp_step(current: Difficulty, proposed: Difficulty) -> Difficulty:
    """Never jump two levels: clamp ``proposed`` to one step from ``current``."""
    diff = level(proposed) - level(current)
    if abs(diff) <= 1:
        return Difficulty(proposed)
    return step(current, diff)


def allowed_by_average(running_average: float | None) -> tuple[Difficulty, ...]:
    """Levels the candidate's running average (0-10) supports."""
    if running_average is None:
        return DIFFICULTY_ORDER
    if running_average < 5.0:
        return (Difficulty.EASY, Difficulty.MEDIUM)
    if running_average >= 7.0:
        return (Difficulty.MEDIUM, Difficulty.HARD)
    return DIFFICULTY_ORDER


def deficits(distribution: DifficultyDistribution, counts: Mapping[str, int]) -> dict[Difficulty, float]:
    """How far each level is below its target share if one more question is asked."""
    total = sum(int(counts.get(d.value, 0)) for d in DIFFICULTY_ORDER) + 1
    shares = {
        Difficulty.EASY: distribution.easy,
        Difficulty.MEDIUM: distribution.medium,
        Difficulty.HARD: distribution.hard,
    }
    return {d: shares[d] * total - int(counts.get(d.value, 0)) for d in DIFFICULTY_ORDER}


def largest_deficit(
    distribution: DifficultyDistribution,
    counts: Mapping[str, int],
    *,
    among: Iterable[Difficulty] | None = None,
    prefer_harder: bool = False,
) -> Difficulty:
    candidates = tuple(among) if among is not None else DIFFICULTY_ORDER
    gaps = deficits(distribution, counts)
    # Ties resolve toward medium, then easy (gentler) - or toward hard for a candidate whose
    # evidence so far is good (prefer_harder).
    if prefer_harder:
        preference = {Difficulty.MEDIUM: 0, Difficulty.HARD: 1, Difficulty.EASY: 2}
    else:
        preference = {Difficulty.MEDIUM: 0, Difficulty.EASY: 1, Difficulty.HARD: 2}
    return max(candidates, key=lambda d: (round(gaps[d], 6), -preference[d]))


#: Running average (0-10) from which ties between easy and hard resolve toward hard.
PREFER_HARDER_AVERAGE = 6.0


def _clamp_to(allowed: tuple[Difficulty, ...], difficulty: Difficulty) -> Difficulty:
    if difficulty in allowed:
        return difficulty
    lv = level(difficulty)
    return min(allowed, key=lambda d: abs(level(d) - lv))


def starting_difficulty(
    plan_start: Difficulty,
    distribution: DifficultyDistribution,
    counts: Mapping[str, int],
    running_average: float | None,
    *,
    adaptive: bool = True,
) -> Difficulty:
    """First difficulty for a competency not asked yet."""
    plan_start = Difficulty(plan_start)
    if not adaptive:
        return plan_start
    allowed = allowed_by_average(running_average)
    # The nudge goes toward the most under-represented level the candidate's average supports
    # (otherwise an easy/hard tie resolves to "easy", the average clamps it back to medium, and a
    # strong candidate never gets a hard question).
    target = largest_deficit(
        distribution,
        counts,
        among=allowed,
        prefer_harder=running_average is not None and running_average >= PREFER_HARDER_AVERAGE,
    )
    nudged = plan_start
    if (
        target != plan_start
        and deficits(distribution, counts)[target] > deficits(distribution, counts)[plan_start]
    ):
        nudged = step(plan_start, level(target) - level(plan_start))
    result = _clamp_to(allowed, nudged)
    return clamp_step(plan_start, result)


@dataclass(slots=True, frozen=True)
class Adaptation:
    difficulty: Difficulty
    weak_at_level: int
    changed: bool
    reason: str


def adapt(
    current: Difficulty,
    quality: AnswerQuality | None,
    weak_at_level: int,
    *,
    adaptive: bool = True,
    running_average: float | None = None,
) -> Adaptation:
    """Next difficulty for the same competency after an answer of ``quality``."""
    current = Difficulty(current)
    if not adaptive or quality is None or quality not in SCORED_QUALITIES:
        return Adaptation(current, weak_at_level, False, "no_change")
    if quality in STRONG_QUALITIES:
        nxt = step(current, +1)
        if running_average is not None and nxt not in allowed_by_average(running_average):
            return Adaptation(current, 0, False, "capped_by_average")
        return Adaptation(nxt, 0, nxt != current, "strong_answer" if nxt != current else "at_max")
    if quality in WEAK_QUALITIES:
        weak = weak_at_level + 1
        if weak >= 2:
            nxt = step(current, -1)
            return Adaptation(nxt, 0, nxt != current, "two_weak_answers" if nxt != current else "at_min")
        return Adaptation(current, weak, False, "weak_answer")
    # adequate / partial: hold the level
    return Adaptation(current, weak_at_level, False, "hold")


def running_average(
    qualities: Iterable[AnswerQuality | None], competency_scores: Iterable[tuple[float, float]] = ()
) -> float | None:
    """Candidate's running average on a 0-10 scale.

    Evaluated competency scores (``(score, confidence)``) dominate when
    present; otherwise the reported answer qualities are used."""
    weighted = [(s, c) for s, c in competency_scores if c > 0]
    if weighted:
        total = sum(c for _, c in weighted)
        return sum(s * c for s, c in weighted) / total
    values = [ANSWER_QUALITY_VALUE[q] * 10 for q in qualities if q is not None and q in ANSWER_QUALITY_VALUE]
    if not values:
        return None
    return sum(values) / len(values)


__all__ = [
    "Adaptation",
    "STRONG_QUALITIES",
    "WEAK_QUALITIES",
    "adapt",
    "allowed_by_average",
    "clamp_step",
    "deficits",
    "from_level",
    "largest_deficit",
    "level",
    "running_average",
    "starting_difficulty",
    "step",
]
