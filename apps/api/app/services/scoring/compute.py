"""Pure scoring math (ENGINE_DESIGN.md section 7).

Everything here is a deterministic function of its inputs: no I/O, no clock,
no randomness. The live engine calls :func:`compute_competency_states` after
every applied evaluation; :mod:`app.services.scoring.persistence` calls the
same functions to write ``scores`` / ``score_snapshots``.

Formulae
--------
Observation (one evaluated answer, one competency)
    raw   = weighted mean of the applicable answer dimensions
            (correctness .25, depth .20, practical_experience .15,
            specificity .15, reasoning .15, completeness .10); when none of
            those apply, the remaining dimensions with equal weight; when no
            dimension applies, the answer-quality value x 10.
    score = min(raw, cap[difficulty])          caps: easy 8.0, medium 9.3, hard 10
    weight r = evaluation_confidence x {easy .85, medium 1.0, hard 1.2}
    Coding: score = min(score_estimate, cap) x assistance_factor where
    assistance_factor = 1 - 0.07 x hints (at most 3 hints count: >= 0.79).

Competency score
    mean    = sum(r_i x s_i) / sum(r_i)                      (weighted observation mean)
    bayes   = (5.0 x 1.0 + sum(r_i x s_i)) / (1.0 + sum(r_i))  when mean > 5.0, else mean
              The neutral prior only tempers HIGH scores from thin evidence;
              it never lifts weak or empty answers towards the midpoint
              (owner rule: low/empty answers = low scores). Without any
              observation the prior 5.0 is the starting point of the evidence
              balance below.
    balance = 0.8 x (P - N) / (P + N + 1.0)
              where P / N = sum(strength x confidence) of positive / negative
              evidence. This is the strength-and-confidence-weighted mean of
              the evidence signals (+1 / -1) shrunk by one neutral
              pseudo-item; unlike a plain average it is monotone (adding a
              weak positive item can never lower the score).
    score   = clamp(bayes + balance, 0, 10)

Confidence
    R = sum(r_i) + min(1.5, 0.3 x strong evidence items)     (strong: strength >= .7, confidence >= .5)
    c = (1 - exp(-R / 2.2)) x (1 - min(.35, 1.2 x std(scores) / 10))
        + .05 when >= 2 difficulty levels were seen;  capped at .98

Sufficiency
    evidence_count >= min_evidence AND confidence >= confidence target AND
    (last two score deltas < 0.8 OR evidence_count >= min_evidence + extra)
    The score history is replayed from the evidence in time order, so the
    result never depends on how often the function was called.

Round scope (short-round calibration, :func:`round_scope`)
    The configured targets assume a full-length round. A round's evidence
    budget is what its planned duration can hold:
        conversation = duration - coding minutes - 2 (intro, candidate questions, close), >= 1
        answers      = min(0.8 x conversation, stopping.max_questions) + coding problems
        load         = required competencies + 0.5 x optional ones
        per_comp     = answers / load            (scored answers each competency can expect)
        scope        = clamp(per_comp / 4, 0, 1) (1 = full-length round: configured targets)
        relief       = 0.25 x (1 - scope)
    confidence target = max(min(threshold, .5), threshold x (1 - relief))
    overall target    = min(max(min(min_confidence, .45), min_confidence x (1 - relief)),
                            floor3(confidence target x required weight share - .001))
    extra             = 2, or 1 when scope < .75 (under 3 answers per
                        competency a 3-point stable history cannot exist)
    The cap makes sufficient evidence on every required competency (the
    engine's stop condition) always meet the overall target. Confidence
    values themselves are never changed, and scores never move.

No evidence = no score
    A competency with neither an observation nor non-neutral evidence is
    *unassessed*: its internal estimate stays the neutral starting point the
    engine needs, but it is never shown or stored as a score (persistence
    writes ``score = NULL``), never enters a rubric category or the overall
    score, and a session where nothing was assessed has no overall score at
    all and the recommendation ``insufficient_evidence``. Rubric categories
    and the overall score are re-normalised over what was assessed
    (``RubricResult.assessed_weight_share`` says how much that is).

Hint-usage evidence (``source=hint_usage``) is recorded for transparency but
treated as neutral here: assistance is already priced in through the coding
assistance factor, and hints must never fail a candidate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import (
    ANSWER_QUALITY_VALUE,
    DIFFICULTY_ORDER,
    AnswerQuality,
    Difficulty,
    EvidenceSignal,
    EvidenceSource,
    Recommendation,
    SessionEndReason,
)
from app.schemas.ai import AnswerEvaluation, CodingEvaluation, DimensionScores
from app.schemas.rounds import RoundCompetency, RoundConfig
from app.services.interview.state import CompetencyState
from app.services.scoring.contracts import (
    EvidenceRecord,
    Observation,
    RecommendationResult,
    RubricCategoryResult,
    RubricResult,
)
from app.services.scoring.topics import dedupe_topics, topic_covered

# --------------------------------------------------------------------------
# Constants (ENGINE_DESIGN.md section 7)
# --------------------------------------------------------------------------

DIMENSION_WEIGHTS: dict[str, float] = {
    "correctness": 0.25,
    "depth": 0.20,
    "practical_experience": 0.15,
    "specificity": 0.15,
    "reasoning": 0.15,
    "completeness": 0.10,
}
SECONDARY_DIMENSIONS: tuple[str, ...] = (
    "relevance",
    "architecture_awareness",
    "tradeoff_understanding",
    "consistency",
    "communication_clarity",
)
DIFFICULTY_CAP: dict[Difficulty, float] = {
    Difficulty.EASY: 8.0,
    Difficulty.MEDIUM: 9.3,
    Difficulty.HARD: 10.0,
}
DIFFICULTY_WEIGHT: dict[Difficulty, float] = {
    Difficulty.EASY: 0.85,
    Difficulty.MEDIUM: 1.0,
    Difficulty.HARD: 1.2,
}

PRIOR_SCORE = 5.0
PRIOR_WEIGHT = 1.0
EVIDENCE_BALANCE_SCALE = 0.8
EVIDENCE_PSEUDO_WEIGHT = 1.0

CONFIDENCE_RATE = 2.2
STD_PENALTY_FACTOR = 1.2
STD_PENALTY_CAP = 0.35
MULTI_DIFFICULTY_BONUS = 0.05
CONFIDENCE_CAP = 0.98
STRONG_EVIDENCE_STRENGTH = 0.7
STRONG_EVIDENCE_MIN_CONFIDENCE = 0.5
STRONG_EVIDENCE_BONUS = 0.3
STRONG_EVIDENCE_BONUS_CAP = 1.5

STABILITY_DELTA = 0.8
SUFFICIENCY_EXTRA_EVIDENCE = 2

# Short-round calibration (round_scope). Measured on the 10-minute quality harness: 7-8 scored
# answers per interview, 1-4 per required competency; spoken answers run ~45-75 s each.
ANSWERS_PER_MINUTE = 0.8
ROUND_OVERHEAD_MINUTES = 2.0  # introduction, candidate questions and closing
MIN_CONVERSATION_MINUTES = 1.0
OPTIONAL_COMPETENCY_LOAD = 0.5  # optional competencies weigh half, as in coverage and target ranking
FULL_SCOPE_ANSWERS = 4.0  # scored answers per competency the configured targets assume
MAX_TARGET_RELIEF = 0.25
COMPETENCY_TARGET_FLOOR = 0.5
OVERALL_TARGET_FLOOR = 0.45
SHORT_SCOPE = 0.75  # below: fewer than 3 expected answers per competency
SHORT_SCOPE_EXTRA_EVIDENCE = 1
CAP_ROUNDING_MARGIN = 0.001

HINT_PENALTY = 0.07
MAX_PENALIZED_HINTS = 3

SCORE_HISTORY_LIMIT = 50

# Turns that change the flow, not the score.
NON_SCORING_QUALITIES = frozenset(
    {
        AnswerQuality.CLARIFICATION_REQUEST,
        AnswerQuality.CANDIDATE_QUESTION,
        AnswerQuality.WANTS_TIME,
        AnswerQuality.WANTS_HINT,
    }
)

POSITIVE_RECOMMENDATIONS = frozenset({Recommendation.STRONG_PROCEED, Recommendation.PROCEED})
TIME_END_REASONS = frozenset({SessionEndReason.TIME_LIMIT.value})

HUMAN_DECISION_NOTE = "This recommendation is assistive. A human reviewer owns the hiring decision."
NO_EVIDENCE_REASON = (
    "No answer produced scorable evidence (the interview was too short or the candidate gave no answers), "
    "so no score is given."
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _difficulty(value: Difficulty | str | None, default: Difficulty = Difficulty.MEDIUM) -> Difficulty:
    if value is None:
        return default
    try:
        return Difficulty(value)
    except ValueError:
        return default


def _signal(value: EvidenceSignal | str) -> EvidenceSignal:
    try:
        return EvidenceSignal(value)
    except ValueError:
        return EvidenceSignal.NEUTRAL


def assistance_factor(hints_used: int) -> float:
    """1 - 0.07 per hint used (at most 3 count): 0 hints 1.0, 3 hints 0.79. Never 0."""
    hints = max(0, min(int(hints_used or 0), MAX_PENALIZED_HINTS))
    return round(1.0 - HINT_PENALTY * hints, 4)


def observation_weight(obs: Observation) -> float:
    """r = evaluation confidence x difficulty multiplier."""
    return clamp(float(obs.confidence), 0.0, 1.0) * DIFFICULTY_WEIGHT[_difficulty(obs.difficulty)]


def effective_score(obs: Observation) -> float:
    """Observation score after the transparent coding assistance factor."""
    return clamp(float(obs.score), 0.0, 10.0) * clamp(float(obs.assistance_factor), 0.0, 1.0)


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------


def _as_answer_evaluation(evaluation: object) -> AnswerEvaluation:
    if isinstance(evaluation, AnswerEvaluation):
        return evaluation
    if isinstance(evaluation, Mapping):
        return AnswerEvaluation.model_validate(dict(evaluation))
    if hasattr(evaluation, "model_dump"):
        return AnswerEvaluation.model_validate(evaluation.model_dump())
    raise TypeError(f"Unsupported evaluation type {type(evaluation).__name__}")


def dimension_mean(scores: DimensionScores | Mapping[str, float | None]) -> float | None:
    """Weighted mean of the applicable dimensions (see module docstring)."""
    values = scores.model_dump() if isinstance(scores, DimensionScores) else dict(scores)
    primary = [
        (DIMENSION_WEIGHTS[k], float(v))
        for k, v in values.items()
        if k in DIMENSION_WEIGHTS and v is not None
    ]
    if primary:
        total = sum(w for w, _ in primary)
        return sum(w * v for w, v in primary) / total
    secondary = [float(v) for v in (values.get(k) for k in SECONDARY_DIMENSIONS) if v is not None]
    if secondary:
        return sum(secondary) / len(secondary)
    return None


def raw_answer_score(evaluation: AnswerEvaluation) -> float | None:
    """Uncapped 0-10 score of one evaluation, ``None`` for non-scoring turns."""
    if evaluation.overall_quality in NON_SCORING_QUALITIES:
        return None
    mean = dimension_mean(evaluation.scores)
    if mean is not None:
        return clamp(mean, 0.0, 10.0)
    quality_value = ANSWER_QUALITY_VALUE.get(evaluation.overall_quality)
    return None if quality_value is None else quality_value * 10.0


def observations_from_evaluation(
    evaluation: object,
    *,
    competency_keys: list[str],
    difficulty: Difficulty,
    answer_id: str,
    question_id: str | None,
    elapsed_s: float,
) -> list[Observation]:
    """One observation per competency the question targeted (or, when the
    question had none, per competency the evaluator attached evidence to)."""
    ev = _as_answer_evaluation(evaluation)
    raw = raw_answer_score(ev)
    if raw is None:
        return []
    level = _difficulty(difficulty)
    score = min(raw, DIFFICULTY_CAP[level])
    keys = list(dict.fromkeys(k for k in competency_keys if k))
    if not keys:
        keys = list(dict.fromkeys(item.competency_key for item in ev.evidence if item.competency_key))
    dims = ev.scores.model_dump()
    return [
        Observation(
            competency_key=key,
            score=round(score, 4),
            confidence=float(ev.evaluation_confidence),
            difficulty=level,
            elapsed_s=float(elapsed_s),
            answer_id=answer_id,
            question_id=question_id,
            dimensions=dict(dims),
        )
        for key in keys
    ]


def observations_from_coding(
    evaluation: CodingEvaluation | Mapping[str, Any],
    *,
    competency_keys: list[str],
    difficulty: Difficulty | str,
    coding_session_id: str,
    question_id: str | None,
    elapsed_s: float,
    hints_used: int,
) -> list[Observation]:
    """Coding observation: ``min(score_estimate, cap) x assistance_factor``."""
    ev = (
        evaluation
        if isinstance(evaluation, CodingEvaluation)
        else CodingEvaluation.model_validate(dict(evaluation))
    )
    level = _difficulty(difficulty)
    score = min(float(ev.score_estimate), DIFFICULTY_CAP[level])
    factor = assistance_factor(hints_used)
    dims: dict[str, float | None] = {
        "code_quality": ev.code_quality,
        "problem_solving": ev.problem_solving,
        "edge_case_handling": ev.edge_case_handling,
        "testing_approach": ev.testing_approach,
        "communication": ev.communication,
    }
    return [
        Observation(
            competency_key=key,
            score=round(score, 4),
            confidence=float(ev.confidence),
            difficulty=level,
            elapsed_s=float(elapsed_s),
            coding_session_id=coding_session_id,
            question_id=question_id,
            dimensions=dict(dims),
            assistance_factor=factor,
        )
        for key in dict.fromkeys(k for k in competency_keys if k)
    ]


# --------------------------------------------------------------------------
# Competency state
# --------------------------------------------------------------------------


def scoring_evidence(evidence: Iterable[EvidenceRecord]) -> list[EvidenceRecord]:
    """Non-neutral evidence that moves scores (hint usage is informational)."""
    out = []
    for item in evidence:
        if _signal(item.signal) is EvidenceSignal.NEUTRAL:
            continue
        if str(item.source) == EvidenceSource.HINT_USAGE.value:
            continue
        out.append(item)
    return out


def evidence_balance(evidence: Iterable[EvidenceRecord]) -> tuple[float, float, float]:
    """``(balance, P, N)`` over scoring evidence (see module docstring)."""
    pos = neg = 0.0
    for item in scoring_evidence(evidence):
        weight = clamp(float(item.strength), 0.0, 1.0) * clamp(float(item.confidence), 0.0, 1.0)
        if _signal(item.signal) is EvidenceSignal.POSITIVE:
            pos += weight
        else:
            neg += weight
    balance = EVIDENCE_BALANCE_SCALE * (pos - neg) / (pos + neg + EVIDENCE_PSEUDO_WEIGHT)
    return balance, pos, neg


def _pstdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


@dataclass(slots=True)
class CompetencyCalculation:
    """Intermediate values of one competency computation (audit trail)."""

    score: float
    bayesian_mean: float
    evidence_balance: float
    positive_weight: float
    negative_weight: float
    confidence: float
    total_weight: float
    strong_evidence: int
    score_std: float
    consistency_penalty: float
    difficulty_levels: int
    evidence_count: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "score": round(self.score, 3),
            "bayesian_mean": round(self.bayesian_mean, 3),
            "evidence_balance": round(self.evidence_balance, 3),
            "positive_weight": round(self.positive_weight, 3),
            "negative_weight": round(self.negative_weight, 3),
            "confidence": round(self.confidence, 3),
            "total_weight": round(self.total_weight, 3),
            "strong_evidence": self.strong_evidence,
            "score_std": round(self.score_std, 3),
            "consistency_penalty": round(self.consistency_penalty, 3),
            "difficulty_levels": self.difficulty_levels,
            "evidence_count": self.evidence_count,
        }


def calculate(
    observations: Sequence[Observation], evidence: Sequence[EvidenceRecord]
) -> CompetencyCalculation:
    """Score and confidence of one competency from its observations/evidence."""
    numerator = PRIOR_SCORE * PRIOR_WEIGHT
    denominator = PRIOR_WEIGHT
    total_weight = 0.0
    scores: list[float] = []
    for obs in observations:
        r = observation_weight(obs)
        s = effective_score(obs)
        numerator += r * s
        denominator += r
        total_weight += r
        scores.append(s)
    if total_weight > 0 and (numerator - PRIOR_SCORE * PRIOR_WEIGHT) / total_weight <= PRIOR_SCORE:
        # Weak answers keep their own (low) mean: the prior never pulls them up to the midpoint.
        bayes = (numerator - PRIOR_SCORE * PRIOR_WEIGHT) / total_weight
    else:
        bayes = numerator / denominator
    balance, pos, neg = evidence_balance(evidence)
    score = clamp(bayes + balance, 0.0, 10.0)

    counted = scoring_evidence(evidence)
    strong = sum(
        1
        for e in counted
        if float(e.strength) >= STRONG_EVIDENCE_STRENGTH
        and float(e.confidence) >= STRONG_EVIDENCE_MIN_CONFIDENCE
    )
    r_total = total_weight + min(STRONG_EVIDENCE_BONUS_CAP, STRONG_EVIDENCE_BONUS * strong)
    std = _pstdev(scores)
    penalty = 1.0 - min(STD_PENALTY_CAP, STD_PENALTY_FACTOR * std / 10.0)
    levels = {_difficulty(o.difficulty) for o in observations}
    levels |= {_difficulty(e.difficulty) for e in counted if e.difficulty}
    confidence = (1.0 - math.exp(-r_total / CONFIDENCE_RATE)) * penalty
    if r_total > 0 and len(levels) >= 2:
        confidence += MULTI_DIFFICULTY_BONUS
    confidence = clamp(confidence, 0.0, CONFIDENCE_CAP)
    return CompetencyCalculation(
        score=score,
        bayesian_mean=bayes,
        evidence_balance=balance,
        positive_weight=pos,
        negative_weight=neg,
        confidence=confidence,
        total_weight=r_total,
        strong_evidence=strong,
        score_std=std,
        consistency_penalty=penalty,
        difficulty_levels=len(levels),
        evidence_count=len(counted),
    )


def _group_key(item: Observation | EvidenceRecord) -> str:
    if item.answer_id:
        return f"a:{item.answer_id}"
    if item.coding_session_id:
        return f"c:{item.coding_session_id}"
    if isinstance(item, EvidenceRecord):
        return f"e:{item.id}"
    return f"t:{item.elapsed_s:.3f}:{item.question_id or ''}"


@dataclass(slots=True)
class ReplayPoint:
    """State of one competency after one update (an answer, a coding problem...)."""

    elapsed_s: float
    score: float
    confidence: float
    evidence_count: int


def replay(observations: Sequence[Observation], evidence: Sequence[EvidenceRecord]) -> list[ReplayPoint]:
    """Competency state after each update group, in time order.

    An update group is everything produced by one answer (or one coding
    problem): its observation(s) plus its evidence items.
    """
    groups: dict[str, list[Observation | EvidenceRecord]] = {}
    items: list[Observation | EvidenceRecord] = [*observations, *evidence]
    for item in items:
        groups.setdefault(_group_key(item), []).append(item)
    ordered = sorted(groups.items(), key=lambda kv: (min(float(i.elapsed_s or 0.0) for i in kv[1]), kv[0]))
    points: list[ReplayPoint] = []
    obs_acc: list[Observation] = []
    ev_acc: list[EvidenceRecord] = []
    for _, items in ordered:
        for item in items:
            if isinstance(item, Observation):
                obs_acc.append(item)
            else:
                ev_acc.append(item)
        calc = calculate(obs_acc, ev_acc)
        points.append(
            ReplayPoint(
                elapsed_s=max(float(i.elapsed_s or 0.0) for i in items),
                score=round(calc.score, 3),
                confidence=round(calc.confidence, 3),
                evidence_count=calc.evidence_count,
            )
        )
    return points


def is_stable(history: Sequence[float]) -> bool:
    """Last two score deltas both below 0.8 (needs three points)."""
    if len(history) < 3:
        return False
    return (
        abs(history[-1] - history[-2]) < STABILITY_DELTA and abs(history[-2] - history[-3]) < STABILITY_DELTA
    )


def is_sufficient(
    *,
    evidence_count: int,
    confidence: float,
    history: Sequence[float],
    min_evidence: int,
    threshold: float,
    extra_evidence: int = SUFFICIENCY_EXTRA_EVIDENCE,
) -> bool:
    if evidence_count < min_evidence or confidence < threshold:
        return False
    return is_stable(history) or evidence_count >= min_evidence + extra_evidence


# --------------------------------------------------------------------------
# Round scope (short-round calibration)
# --------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class RoundScope:
    """Evidence budget of a round and the targets scaled to it (module docstring)."""

    planned_minutes: float
    conversation_minutes: float
    expected_answers: float
    required_competencies: int
    optional_competencies: int
    competency_load: float
    answers_per_competency: float
    scope: float  # 1.0 = full-length round (configured targets apply unchanged)
    relief: float  # share by which the confidence targets were lowered (0 .. MAX_TARGET_RELIEF)
    required_weight_share: float  # effective-weight share of the competencies coverage requires
    confidence_threshold: float  # configured, per competency
    confidence_target: float  # applied, per competency (sufficiency)
    extra_evidence: int  # evidence items that stand in for a stable score history
    min_confidence: float  # configured, overall (recommendation)
    min_confidence_target: float  # applied, overall (recommendation)
    overall_capped: bool = False  # the overall target is the consistency cap, not the scaled minimum

    @property
    def calibrated(self) -> bool:
        """True when the round's targets differ from the configured ones."""
        return (
            self.confidence_target < self.confidence_threshold
            or self.min_confidence_target < self.min_confidence
            or self.extra_evidence != SUFFICIENCY_EXTRA_EVIDENCE
        )

    def factors(self) -> dict[str, float | int]:
        return {
            "round_scope": round(self.scope, 3),
            "expected_answers_per_competency": round(self.answers_per_competency, 2),
            "competency_confidence_target": round(self.confidence_target, 3),
            "min_confidence_target": round(self.min_confidence_target, 3),
            "required_weight_share": round(self.required_weight_share, 3),
        }

    def as_dict(self) -> dict[str, float | int]:
        return {
            "planned_minutes": round(self.planned_minutes, 1),
            "conversation_minutes": round(self.conversation_minutes, 1),
            "expected_answers": round(self.expected_answers, 2),
            "required_competencies": self.required_competencies,
            "optional_competencies": self.optional_competencies,
            "answers_per_competency": round(self.answers_per_competency, 2),
            "scope": round(self.scope, 3),
            "relief": round(self.relief, 3),
            "required_weight_share": round(self.required_weight_share, 3),
            "confidence_threshold": self.confidence_threshold,
            "confidence_target": round(self.confidence_target, 3),
            "extra_evidence": self.extra_evidence,
            "min_confidence": self.min_confidence,
            "min_confidence_target": round(self.min_confidence_target, 3),
        }

    def describe(self) -> str:
        """Rationale sentence about the round's targets (empty when the configured ones apply)."""
        if not self.calibrated:
            return ""
        overall = f"{self.min_confidence_target:.0%} overall (configured {self.min_confidence:.0%}"
        overall += (
            "; capped so that sufficient evidence on every required competency meets it)"
            if self.overall_capped
            else ")"
        )
        if self.relief <= 0:
            return f"Overall confidence target: {overall}."
        if not self.required_competencies:
            mix = f"{self.optional_competencies} competencies"
        elif self.optional_competencies:
            mix = f"{self.required_competencies} required and {self.optional_competencies} optional competencies"
        else:
            mix = f"{self.required_competencies} required competencies"
        return (
            f"Round-scope calibration: a {self.planned_minutes:g}-minute round with {mix} allows about "
            f"{self.answers_per_competency:.1f} scored answers per competency, so the evidence targets are "
            f"{self.confidence_target:.0%} confidence per competency (configured {self.confidence_threshold:.0%}) "
            f"and {overall}."
        )


def _scaled(configured: float, relief: float, floor: float) -> float:
    """``configured x (1 - relief)``, never below ``min(configured, floor)``."""
    return max(min(configured, floor), configured * (1.0 - relief))


def _required_weight_share(round_config: RoundConfig) -> float:
    """Effective-weight share (optional competencies weigh half) of the competencies that required
    coverage counts: the required ones, or all of them when none is marked required."""
    comps = round_config.competencies
    weights = [max(float(c.weight), 0.0) * (1.0 if c.required else OPTIONAL_COMPETENCY_LOAD) for c in comps]
    total = sum(weights)
    if total <= 0 or not any(c.required for c in comps):
        return 1.0
    return sum(w for w, c in zip(weights, comps, strict=True) if c.required) / total


def round_scope(round_config: RoundConfig) -> RoundScope:
    """The round's evidence budget and the sufficiency/confidence targets scaled to it.

    Full-length rounds (at least ``FULL_SCOPE_ANSWERS`` expected scored
    answers per competency) keep the configured per-competency target
    exactly; shorter or more crowded rounds get proportionally lower targets
    (by at most ``MAX_TARGET_RELIEF``, never below the floors). The overall
    target is also capped at ``confidence_target x required_weight_share``:
    overall confidence is the effective-weight mean over *all* round
    competencies, so with that cap sufficient evidence on every required
    competency (the engine's stop condition) always meets it. Pure and
    cheap: the live engine calls it after every evaluation.
    """
    planned = float(round_config.duration_minutes)
    coding = round_config.coding
    problems = coding.max_problems if coding.enabled else 0
    coding_minutes = float(coding.duration_minutes * problems) if coding.enabled else 0.0
    conversation = max(MIN_CONVERSATION_MINUTES, planned - coding_minutes - ROUND_OVERHEAD_MINUTES)
    answers = ANSWERS_PER_MINUTE * conversation
    if round_config.stopping.max_questions:
        answers = min(answers, float(round_config.stopping.max_questions))
    answers += problems
    required = sum(1 for c in round_config.competencies if c.required)
    optional = len(round_config.competencies) - required
    # When no competency is marked required, all of them are (as in required coverage).
    load = required + OPTIONAL_COMPETENCY_LOAD * optional if required else float(optional)
    per_competency = answers / max(load, 1.0)
    scope = clamp(per_competency / FULL_SCOPE_ANSWERS, 0.0, 1.0)
    relief = round(MAX_TARGET_RELIEF * (1.0 - scope), 4)
    threshold = float(round_config.confidence_threshold)
    min_confidence = float(round_config.passing_guidance.min_confidence)
    confidence_target = round(_scaled(threshold, relief, COMPETENCY_TARGET_FLOOR), 4)
    share = _required_weight_share(round_config)
    scaled_overall = _scaled(min_confidence, relief, OVERALL_TARGET_FLOOR)
    # Competency and overall confidences are stored rounded to 3 decimals: keep the cap 0.001 below
    # target x share so the guarantee survives rounding.
    cap = max(0.0, math.floor((confidence_target * share - CAP_ROUNDING_MARGIN) * 1000.0) / 1000.0)
    return RoundScope(
        planned_minutes=planned,
        conversation_minutes=conversation,
        expected_answers=answers,
        required_competencies=required,
        optional_competencies=optional,
        competency_load=load,
        answers_per_competency=per_competency,
        scope=scope,
        relief=relief,
        required_weight_share=share,
        confidence_threshold=threshold,
        confidence_target=confidence_target,
        extra_evidence=SHORT_SCOPE_EXTRA_EVIDENCE if scope < SHORT_SCOPE else SUFFICIENCY_EXTRA_EVIDENCE,
        min_confidence=min_confidence,
        min_confidence_target=round(min(scaled_overall, cap), 4),
        overall_capped=cap < scaled_overall,
    )


def unanswered_topics(
    expected: Iterable[str],
    focus_areas: Iterable[str],
    evidence: Sequence[EvidenceRecord],
) -> list[str]:
    """Expected topics without positive coverage + focus areas never touched."""
    counted = scoring_evidence(evidence)
    positive = [(e.dimension, e.text) for e in counted if _signal(e.signal) is EvidenceSignal.POSITIVE]
    touched = [(e.dimension, e.text) for e in counted]
    missing = [t for t in dedupe_topics(expected) if not topic_covered(t, positive)]
    missing += [t for t in dedupe_topics(focus_areas) if not topic_covered(t, touched)]
    return dedupe_topics(missing)


@dataclass(slots=True)
class _CompetencyMeta:
    key: str
    name: str | None
    weight: float
    required: bool
    min_evidence: int
    focus_areas: list[str] = field(default_factory=list)


def _metas(
    round_config: RoundConfig,
    keys: Iterable[str],
) -> dict[str, _CompetencyMeta]:
    configured: dict[str, RoundCompetency] = {c.key: c for c in round_config.competencies}
    metas: dict[str, _CompetencyMeta] = {}
    for comp in round_config.competencies:
        metas[comp.key] = _CompetencyMeta(
            key=comp.key,
            name=comp.name,
            weight=float(comp.weight),
            required=bool(comp.required),
            min_evidence=round_config.min_evidence_for(comp.key),
            focus_areas=list(comp.focus_areas),
        )
    for key in keys:
        if key and key not in configured and key not in metas:
            # Evidence about a competency the round did not list: reported,
            # never required, so it cannot block or inflate coverage.
            metas[key] = _CompetencyMeta(
                key=key,
                name=None,
                weight=1.0,
                required=False,
                min_evidence=round_config.min_evidence_per_competency,
            )
    return metas


def compute_competency_states(
    round_config: RoundConfig,
    observations: list[Observation],
    evidence: list[EvidenceRecord],
    *,
    previous: dict[str, CompetencyState] | None = None,
    expected_topics: dict[str, list[str]] | None = None,
    elapsed_s: float = 0.0,
    focus_areas: dict[str, list[str]] | None = None,
) -> dict[str, CompetencyState]:
    """Competency states derived purely from observations and evidence.

    ``previous`` only contributes engine-owned bookkeeping (current
    difficulty, questions asked, follow-up counters, last quality); every
    score-related field is recomputed. ``expected_topics`` are the
    expected-evidence topics of questions asked per competency (positive
    evidence must cover them); ``focus_areas`` are plan focus areas per
    competency (any evidence touching them counts). The round's own
    competency focus areas are always checked.
    """
    previous = previous or {}
    expected_topics = expected_topics or {}
    focus_areas = focus_areas or {}
    scope = round_scope(round_config)
    keys_seen = (
        [o.competency_key for o in observations] + [e.competency_key for e in evidence] + list(previous)
    )
    metas = _metas(round_config, keys_seen)

    obs_by: dict[str, list[Observation]] = {}
    for obs in observations:
        obs_by.setdefault(obs.competency_key, []).append(obs)
    ev_by: dict[str, list[EvidenceRecord]] = {}
    for item in evidence:
        ev_by.setdefault(item.competency_key, []).append(item)

    states: dict[str, CompetencyState] = {}
    for key, meta in metas.items():
        comp_obs = sorted(
            obs_by.get(key, []), key=lambda o: (o.elapsed_s, o.answer_id or o.coding_session_id or "")
        )
        ev = sorted(ev_by.get(key, []), key=lambda e: (e.elapsed_s, e.id))
        prev = previous.get(key)
        calc = calculate(comp_obs, ev)
        history = [p.score for p in replay(comp_obs, ev)][-SCORE_HISTORY_LIMIT:]
        counted = scoring_evidence(ev)
        positive = [e for e in counted if _signal(e.signal) is EvidenceSignal.POSITIVE]
        negative = [e for e in counted if _signal(e.signal) is EvidenceSignal.NEGATIVE]

        seen: dict[str, int] = {}
        for o in comp_obs:
            level = _difficulty(o.difficulty).value
            seen[level] = seen.get(level, 0) + 1
        if prev is not None:
            for level, count in prev.difficulty_seen.items():
                seen[level] = max(seen.get(level, 0), int(count))
        seen = {d.value: seen[d.value] for d in DIFFICULTY_ORDER if d.value in seen}

        input_times = [float(o.elapsed_s or 0.0) for o in comp_obs] + [float(e.elapsed_s or 0.0) for e in ev]
        if input_times:
            last_updated = max(max(input_times), 0.0) or float(elapsed_s)
        else:
            last_updated = prev.last_updated_s if prev is not None else 0.0

        plan_focus = list(focus_areas.get(key, [])) + list(meta.focus_areas)
        unanswered = unanswered_topics(expected_topics.get(key, []), plan_focus, ev)

        state = CompetencyState(
            key=key,
            name=meta.name or (prev.name if prev is not None else None),
            weight=meta.weight,
            required=meta.required,
            score_estimate=round(calc.score, 3),
            confidence=round(calc.confidence, 3),
            evidence_count=calc.evidence_count,
            positive_evidence=len(positive),
            negative_evidence=len(negative),
            positive_evidence_ids=[e.id for e in positive],
            negative_evidence_ids=[e.id for e in negative],
            dimensions_covered=list(dict.fromkeys(e.dimension for e in positive if e.dimension)),
            unanswered_questions=unanswered,
            difficulty_seen=seen,
            sufficient_evidence=is_sufficient(
                evidence_count=calc.evidence_count,
                confidence=calc.confidence,
                history=history,
                min_evidence=meta.min_evidence,
                threshold=scope.confidence_target,
                extra_evidence=scope.extra_evidence,
            ),
            score_history=history,
            last_updated_s=float(last_updated),
        )
        if prev is not None:
            state.current_difficulty = prev.current_difficulty
            state.questions_asked = prev.questions_asked
            state.followups_on_current_topic = prev.followups_on_current_topic
            state.last_quality = prev.last_quality
        states[key] = state
    return states


def competency_calculations(
    observations: Sequence[Observation], evidence: Sequence[EvidenceRecord]
) -> dict[str, CompetencyCalculation]:
    """Per-competency intermediate values (for the "why this score" view)."""
    keys = list(
        dict.fromkeys([o.competency_key for o in observations] + [e.competency_key for e in evidence])
    )
    return {
        key: calculate(
            [o for o in observations if o.competency_key == key],
            [e for e in evidence if e.competency_key == key],
        )
        for key in keys
    }


# --------------------------------------------------------------------------
# Rubric and overall
# --------------------------------------------------------------------------


def _coverage_keys(round_config: RoundConfig, states: Mapping[str, CompetencyState]) -> list[str]:
    configured = [c.key for c in round_config.competencies if c.key in states]
    return configured or list(states)


def required_coverage(round_config: RoundConfig, states: Mapping[str, CompetencyState]) -> float:
    """Share of required competencies with sufficient evidence (all
    competencies when none is marked required)."""
    keys = _coverage_keys(round_config, states)
    required = [k for k in keys if states[k].required] or keys
    if not required:
        return 0.0
    return sum(1 for k in required if states[k].sufficient_evidence) / len(required)


def _effective_weight(state: CompetencyState) -> float:
    """Competency weight, halved for optional competencies (as in the
    engine's target ranking: weight x (required ? 1 : 0.5))."""
    return max(float(state.weight), 0.0) * (1.0 if state.required else 0.5)


def _touched(state: CompetencyState) -> bool:
    return state.evidence_count > 0 or state.confidence > 0


def is_assessed(state: CompetencyState) -> bool:
    """True when the competency has any observation or non-neutral evidence. An unassessed competency
    has no score: its ``score_estimate`` is only the engine's neutral starting point."""
    return _touched(state)


def evidence_coverage(round_config: RoundConfig, states: Mapping[str, CompetencyState]) -> float:
    """Effective-weight share of the round's competencies that have any
    evidence (an untouched nice-to-have costs half of an untouched requirement)."""
    keys = _coverage_keys(round_config, states)
    total = sum(_effective_weight(states[k]) for k in keys)
    covered = sum(_effective_weight(states[k]) for k in keys if _touched(states[k]))
    return covered / total if total > 0 else 0.0


def _weighted_confidence(round_config: RoundConfig, states: Mapping[str, CompetencyState]) -> float:
    """Effective-weight average confidence of the competencies that were
    assessed at all (what was not assessed is priced in by the coverage)."""
    keys = [k for k in _coverage_keys(round_config, states) if _touched(states[k])]
    total = sum(_effective_weight(states[k]) for k in keys)
    if total <= 0:
        return 0.0
    return sum(_effective_weight(states[k]) * states[k].confidence for k in keys) / total


def aggregate_rubric(
    round_config: RoundConfig, states: dict[str, CompetencyState], dimension_averages: dict[str, float]
) -> RubricResult:
    """Rubric categories (0-100), overall score and confidence.

    Category score = weighted average of its *assessed* competencies' scores
    x 10 (weight = competency weight) and its dimension averages x 10
    (weight 1 each, only dimensions that were actually scored). A category
    with nothing assessed has no score (``None``, confidence 0): an untouched
    competency is never counted as a neutral 50. Overall = category-weighted
    mean of the categories that have a score (re-normalised; without a
    rubric, the weight-averaged score of the assessed competencies x 10);
    ``None`` when nothing was assessed. Overall confidence = weight-averaged
    confidence of the assessed competencies x evidence coverage (optional
    competencies weigh half in both).
    """
    mean_confidence = _weighted_confidence(round_config, states)
    categories: list[RubricCategoryResult] = []
    for category in round_config.rubric:
        items: list[tuple[float, float]] = []
        conf_items: list[tuple[float, float]] = []
        contributing: list[str] = []
        for key in category.competency_keys:
            state = states.get(key)
            if state is None or not is_assessed(state):
                continue
            weight = max(float(state.weight), 0.0) or 1.0
            items.append((state.score_estimate * 10.0, weight))
            conf_items.append((state.confidence, weight))
            contributing.append(key)
        dims_used = [d for d in category.dimensions if dimension_averages.get(d) is not None]
        for dim in dims_used:
            items.append((clamp(float(dimension_averages[dim]), 0.0, 10.0) * 10.0, 1.0))
        score: float | None = None
        if items:
            total = sum(w for _, w in items)
            score = round(clamp(sum(v * w for v, w in items) / total, 0.0, 100.0), 2)
        if conf_items:
            ctotal = sum(w for _, w in conf_items)
            confidence = sum(c * w for c, w in conf_items) / ctotal
        elif dims_used:
            confidence = mean_confidence
        else:
            confidence = 0.0
        categories.append(
            RubricCategoryResult(
                key=category.key,
                label=category.label,
                weight=float(category.weight),
                score=score,
                confidence=round(clamp(confidence, 0.0, 1.0), 3),
                contributing_competencies=contributing,
            )
        )

    overall: float | None = None
    share = 0.0
    if categories:
        total_weight = sum(max(c.weight, 0.0) for c in categories)
        scored = [c for c in categories if c.score is not None]
        scored_weight = sum(max(c.weight, 0.0) for c in scored)
        if scored and scored_weight > 0:
            overall = sum(max(c.weight, 0.0) * (c.score or 0.0) for c in scored) / scored_weight
            share = scored_weight / total_weight if total_weight > 0 else 1.0
        elif scored:
            overall = sum(c.score or 0.0 for c in scored) / len(scored)
            share = len(scored) / len(categories)
    else:
        keys = _coverage_keys(round_config, states)
        total = sum(max(float(states[k].weight), 0.0) for k in keys)
        assessed = [k for k in keys if is_assessed(states[k])]
        assessed_weight = sum(max(float(states[k].weight), 0.0) for k in assessed)
        if assessed and assessed_weight > 0:
            overall = (
                sum(max(float(states[k].weight), 0.0) * states[k].score_estimate for k in assessed)
                / assessed_weight
                * 10.0
            )
            share = assessed_weight / total if total > 0 else 1.0
        elif assessed:
            overall = sum(states[k].score_estimate for k in assessed) / len(assessed) * 10.0
            share = len(assessed) / len(keys)
    coverage = required_coverage(round_config, states)
    overall_confidence = mean_confidence * evidence_coverage(round_config, states)
    return RubricResult(
        categories=categories,
        overall_score=round(clamp(overall, 0.0, 100.0), 2) if overall is not None else None,
        overall_confidence=round(clamp(overall_confidence, 0.0, 1.0), 3),
        required_coverage=round(coverage, 3),
        assessed_weight_share=round(clamp(share, 0.0, 1.0), 3),
    )


# --------------------------------------------------------------------------
# Recommendation
# --------------------------------------------------------------------------

_LABELS = {
    Recommendation.STRONG_PROCEED: "strong evidence to proceed",
    Recommendation.PROCEED: "evidence supports proceeding",
    Recommendation.HUMAN_REVIEW: "human review needed",
    Recommendation.BORDERLINE_EVIDENCE: "borderline evidence",
    Recommendation.INSUFFICIENT_EVIDENCE: "insufficient evidence",
}


def _competency_label(state: CompetencyState) -> str:
    """Human-readable competency name for rationale text (never a raw key)."""
    if state.name:
        return state.name
    from app.services.scoring.persistence import humanize_key

    return humanize_key(state.key)


def recommend(
    round_config: RoundConfig,
    rubric: RubricResult,
    states: dict[str, CompetencyState],
    *,
    unreviewed_integrity_flags: int,
    open_high_contradictions: int,
    end_reason: str | None,
) -> RecommendationResult:
    """Assistive recommendation from ``PassingGuidance`` (never a rejection).

    The confidence gate is the round-scope target (:func:`round_scope`):
    the configured minimum scaled to the round's evidence budget and capped
    so that sufficient evidence on every required competency (the engine's
    own stop condition) always meets it. Scores and confidence values are
    never adjusted; the rationale states the targets whenever they differ
    from the configured ones.
    """
    guidance = round_config.passing_guidance
    scope = round_scope(round_config)
    min_confidence = scope.min_confidence_target
    score = rubric.overall_score
    confidence = rubric.overall_confidence
    coverage = rubric.required_coverage
    keys = _coverage_keys(round_config, states)
    required = [k for k in keys if states[k].required] or keys
    insufficient = [k for k in required if not states[k].sufficient_evidence]
    time_ran_out = (end_reason or "") in TIME_END_REASONS
    nothing_assessed = score is None or not any(is_assessed(states[k]) for k in keys)
    factors: dict[str, float | int | str | None] = {
        "overall_score": round(score, 2) if score is not None else None,
        "assessed_weight_share": rubric.assessed_weight_share,
        "overall_confidence": round(confidence, 3),
        "required_coverage": round(coverage, 3),
        "min_confidence": guidance.min_confidence,
        "min_required_coverage": guidance.min_required_coverage,
        "strong_proceed_min_score": guidance.strong_proceed_min_score,
        "proceed_min_score": guidance.proceed_min_score,
        "borderline_min_score": guidance.borderline_min_score,
        "unreviewed_integrity_flags": int(unreviewed_integrity_flags),
        "open_high_contradictions": int(open_high_contradictions),
        "end_reason": end_reason or "",
        "competencies_without_sufficient_evidence": ", ".join(insufficient),
        **scope.factors(),
    }
    notes: list[str] = []
    if unreviewed_integrity_flags:
        notes.append(
            f"{unreviewed_integrity_flags} integrity signal(s) are awaiting human review "
            "(observations only, not determinations)."
        )
    if open_high_contradictions:
        notes.append(
            f"{open_high_contradictions} high-severity inconsistency(ies) remain open for a reviewer to discuss."
        )

    low_confidence = confidence < min_confidence
    low_coverage = coverage < guidance.min_required_coverage
    if nothing_assessed or score is None:
        rec = Recommendation.INSUFFICIENT_EVIDENCE
        reason = NO_EVIDENCE_REASON
    elif low_confidence or low_coverage:
        partial = coverage > 0 or confidence >= min_confidence / 2
        if time_ran_out and partial:
            rec = Recommendation.BORDERLINE_EVIDENCE
            reason = (
                "The interview reached its time limit with partial coverage: "
                f"confidence {confidence:.0%} (minimum {min_confidence:.0%}), "
                f"required coverage {coverage:.0%} (minimum {guidance.min_required_coverage:.0%})."
            )
        else:
            rec = Recommendation.INSUFFICIENT_EVIDENCE
            reason = (
                "There is not enough evidence for a reliable assessment: "
                f"confidence {confidence:.0%} (minimum {min_confidence:.0%}), "
                f"required coverage {coverage:.0%} (minimum {guidance.min_required_coverage:.0%})."
            )
        if insufficient:
            reason += (
                " Competencies still lacking evidence: "
                + ", ".join(_competency_label(states[k]) for k in insufficient)
                + "."
            )
    elif unreviewed_integrity_flags or open_high_contradictions:
        rec = Recommendation.HUMAN_REVIEW
        reason = (
            f"Overall score {score:.0f}/100 with confidence {confidence:.0%}, but items need a human reviewer "
            "before any recommendation."
        )
    elif score >= guidance.strong_proceed_min_score:
        rec = Recommendation.STRONG_PROCEED
        reason = f"Overall score {score:.0f}/100 is at or above {guidance.strong_proceed_min_score:.0f} with confidence {confidence:.0%}."
    elif score >= guidance.proceed_min_score:
        rec = Recommendation.PROCEED
        reason = f"Overall score {score:.0f}/100 is at or above {guidance.proceed_min_score:.0f} with confidence {confidence:.0%}."
    elif score >= guidance.borderline_min_score:
        rec = Recommendation.BORDERLINE_EVIDENCE
        reason = (
            f"Overall score {score:.0f}/100 is between {guidance.borderline_min_score:.0f} and "
            f"{guidance.proceed_min_score:.0f}; the evidence is mixed."
        )
    else:
        rec = Recommendation.HUMAN_REVIEW
        reason = (
            f"Overall score {score:.0f}/100 is below {guidance.borderline_min_score:.0f}. "
            "The platform never rejects automatically; a reviewer should look at the evidence."
        )
    if scope.calibrated and not nothing_assessed:
        notes.append(scope.describe())
    rationale = " ".join([f"{_LABELS[rec].capitalize()}.", reason, *notes])
    return RecommendationResult(
        recommendation=rec,
        rationale=rationale,
        factors=factors,
        human_decision_note=HUMAN_DECISION_NOTE,
    )


# --------------------------------------------------------------------------
# ScoringApi implementation
# --------------------------------------------------------------------------


class ScoringService:
    """Stateless implementation of :class:`app.services.scoring.contracts.ScoringApi`."""

    def observations_from_evaluation(
        self,
        evaluation: object,
        *,
        competency_keys: list[str],
        difficulty: Difficulty,
        answer_id: str,
        question_id: str | None,
        elapsed_s: float,
    ) -> list[Observation]:
        return observations_from_evaluation(
            evaluation,
            competency_keys=competency_keys,
            difficulty=difficulty,
            answer_id=answer_id,
            question_id=question_id,
            elapsed_s=elapsed_s,
        )

    def observations_from_coding(
        self,
        evaluation: CodingEvaluation | Mapping[str, Any],
        *,
        competency_keys: list[str],
        difficulty: Difficulty | str,
        coding_session_id: str,
        question_id: str | None,
        elapsed_s: float,
        hints_used: int,
    ) -> list[Observation]:
        return observations_from_coding(
            evaluation,
            competency_keys=competency_keys,
            difficulty=difficulty,
            coding_session_id=coding_session_id,
            question_id=question_id,
            elapsed_s=elapsed_s,
            hints_used=hints_used,
        )

    def compute_competency_states(
        self,
        round_config: RoundConfig,
        observations: list[Observation],
        evidence: list[EvidenceRecord],
        *,
        previous: dict[str, CompetencyState] | None = None,
        expected_topics: dict[str, list[str]] | None = None,
        elapsed_s: float = 0.0,
        focus_areas: dict[str, list[str]] | None = None,
    ) -> dict[str, CompetencyState]:
        return compute_competency_states(
            round_config,
            observations,
            evidence,
            previous=previous,
            expected_topics=expected_topics,
            elapsed_s=elapsed_s,
            focus_areas=focus_areas,
        )

    def aggregate_rubric(
        self,
        round_config: RoundConfig,
        states: dict[str, CompetencyState],
        dimension_averages: dict[str, float],
    ) -> RubricResult:
        return aggregate_rubric(round_config, states, dimension_averages)

    def recommend(
        self,
        round_config: RoundConfig,
        rubric: RubricResult,
        states: dict[str, CompetencyState],
        *,
        unreviewed_integrity_flags: int,
        open_high_contradictions: int,
        end_reason: str | None,
    ) -> RecommendationResult:
        return recommend(
            round_config,
            rubric,
            states,
            unreviewed_integrity_flags=unreviewed_integrity_flags,
            open_high_contradictions=open_high_contradictions,
            end_reason=end_reason,
        )


_SCORING = ScoringService()


def get_scoring() -> ScoringService:
    return _SCORING
