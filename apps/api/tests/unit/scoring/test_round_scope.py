"""Round-scope (short-round) calibration of the sufficiency and confidence targets.

Calibration data: the 10-minute quality-harness round and the persona outcomes of the Wave C
after-pass (strong 67.4, mid 50.0, vague 35.8, weak 29.8 overall). Scores and confidence values
must never move; only the targets they are compared with scale with the round's evidence budget.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from app.domain.enums import Difficulty, Recommendation
from app.schemas.rounds import RoundConfig
from app.services.interview.state import CompetencyState
from app.services.scoring.compute import (
    COMPETENCY_TARGET_FLOOR,
    MAX_TARGET_RELIEF,
    SUFFICIENCY_EXTRA_EVIDENCE,
    aggregate_rubric,
    compute_competency_states,
    is_sufficient,
    recommend,
    required_coverage,
    round_scope,
)
from app.services.scoring.contracts import RubricResult

from .helpers import ev, obs, round_config

CASES = 400


def harness_round(**overrides: Any) -> RoundConfig:
    """The 10-minute technical screening used by the quality harness (tests/quality)."""
    base: dict[str, Any] = {
        "round_type": "technical_screening",
        "name": "Technical Screening",
        "duration_minutes": 10,
        "max_duration_minutes": 12,
        "competencies": [
            {"key": "kubernetes", "weight": 1.5, "focus_areas": ["availability", "scaling"]},
            {"key": "aws", "weight": 1.2, "focus_areas": ["networking", "iam"]},
            {"key": "ci_cd", "weight": 0.8, "focus_areas": ["deployment_strategy", "rollback"]},
            {"key": "ownership", "weight": 0.6, "required": False, "min_evidence": 1},
            {"key": "communication", "weight": 0.4, "required": False, "min_evidence": 1},
        ],
        "rubric": [
            {
                "key": "technical_knowledge",
                "label": "Technical Knowledge",
                "weight": 50,
                "competency_keys": ["kubernetes", "aws", "ci_cd"],
                "dimensions": ["correctness", "depth"],
            },
            {
                "key": "practical_experience",
                "label": "Practical Experience",
                "weight": 15,
                "dimensions": ["practical_experience", "specificity"],
            },
            {"key": "problem_solving", "label": "Problem Solving", "weight": 10, "dimensions": ["reasoning"]},
            {
                "key": "ownership_leadership",
                "label": "Ownership",
                "weight": 10,
                "competency_keys": ["ownership"],
            },
            {
                "key": "communication_content",
                "label": "Communication",
                "weight": 10,
                "competency_keys": ["communication"],
                "dimensions": ["communication_clarity"],
            },
            {
                "key": "resume_consistency",
                "label": "Resume Consistency",
                "weight": 5,
                "dimensions": ["consistency"],
            },
        ],
        "min_evidence_per_competency": 2,
        "confidence_threshold": 0.65,
        "stopping": {"min_duration_minutes": 5, "max_questions": 8, "min_questions": 4},
        "passing_guidance": {"min_confidence": 0.6, "min_required_coverage": 0.75},
    }
    base.update(overrides)
    return RoundConfig.model_validate(base)


# ------------------------------------------------------------------ budget


def test_full_length_round_keeps_the_configured_targets() -> None:
    scope = round_scope(round_config())  # 30 minutes, 2 required + 1 optional competency
    assert scope.scope == 1.0 and scope.relief == 0.0
    assert scope.confidence_target == 0.7 and scope.min_confidence_target == 0.6
    assert scope.extra_evidence == SUFFICIENCY_EXTRA_EVIDENCE
    assert not scope.calibrated and scope.describe() == ""


def test_harness_round_budget_and_targets() -> None:
    scope = round_scope(harness_round())
    # 10 min - 2 min overhead = 8 conversational minutes x 0.8 = 6.4 answers (max_questions 8 does not bind),
    # load 3 required + 0.5 x 2 optional = 4 -> 1.6 answers per competency, scope 0.4, relief 0.15.
    assert scope.conversation_minutes == 8.0
    assert scope.expected_answers == pytest.approx(6.4)
    assert scope.competency_load == 4.0 and scope.answers_per_competency == pytest.approx(1.6)
    assert scope.scope == pytest.approx(0.4) and scope.relief == pytest.approx(0.15)
    assert scope.confidence_target == pytest.approx(0.5525)
    assert scope.extra_evidence == 1
    # Required share 3.5 / 4.0: the overall target is capped at 0.5525 x 0.875 - 0.001 (floored to 3 decimals).
    assert scope.required_weight_share == pytest.approx(0.875)
    assert scope.overall_capped and scope.min_confidence_target == pytest.approx(0.482)
    text = scope.describe()
    assert text.startswith("Round-scope calibration: a 10-minute round with 3 required and 2 optional")
    assert "55% confidence per competency (configured 65%)" in text and "48% overall (configured 60%" in text


def test_coding_time_question_cap_and_problems_shape_the_budget() -> None:
    long = round_scope(
        harness_round(duration_minutes=40, max_duration_minutes=45, stopping={"max_questions": 30})
    )
    coding = round_scope(
        harness_round(
            duration_minutes=40,
            max_duration_minutes=45,
            stopping={"max_questions": 30},
            coding={"enabled": True, "duration_minutes": 20, "max_problems": 1},
        )
    )
    assert long.conversation_minutes == 38.0 and coding.conversation_minutes == 18.0
    assert coding.expected_answers == pytest.approx(0.8 * 18 + 1)  # the problem counts as one scored item
    capped = round_scope(
        harness_round(duration_minutes=40, max_duration_minutes=45, stopping={"max_questions": 6})
    )
    assert capped.expected_answers == 6.0
    tiny = round_scope(harness_round(duration_minutes=5, coding={"enabled": True, "duration_minutes": 10}))
    assert tiny.conversation_minutes == 1.0  # never below one minute
    assert tiny.relief <= MAX_TARGET_RELIEF and tiny.confidence_target >= COMPETENCY_TARGET_FLOOR


def test_all_optional_competencies_count_as_required() -> None:
    comps = [{"key": k, "weight": 1.0, "required": False} for k in ("a", "b", "c", "d")]
    scope = round_scope(harness_round(competencies=comps, rubric=[]))
    assert scope.required_competencies == 0 and scope.competency_load == 4.0
    assert scope.required_weight_share == 1.0
    assert "with 4 competencies" in scope.describe()


@pytest.mark.parametrize("threshold", [0.5, 0.65, 0.7, 0.9])
def test_targets_scale_monotonically_with_duration_and_load(threshold: float) -> None:
    previous = 0.0
    for minutes in range(5, 91, 5):
        scope = round_scope(
            harness_round(
                duration_minutes=minutes, max_duration_minutes=minutes, confidence_threshold=threshold
            )
        )
        assert scope.confidence_target >= previous - 1e-9  # longer rounds never get a lower target
        assert min(threshold, COMPETENCY_TARGET_FLOOR) - 1e-9 <= scope.confidence_target <= threshold
        assert threshold * (1 - MAX_TARGET_RELIEF) - 1e-9 <= scope.confidence_target  # relief is bounded
        previous = scope.confidence_target
    previous = 1.0
    for count in range(1, 12):
        comps = [{"key": f"c{i}", "weight": 1.0} for i in range(count)]
        scope = round_scope(harness_round(competencies=comps, rubric=[], confidence_threshold=threshold))
        assert scope.confidence_target <= previous + 1e-9  # more required competencies never raise it
        previous = scope.confidence_target


def test_overall_target_is_met_whenever_every_required_competency_is_sufficient() -> None:
    """The engine stops on "every required competency sufficient": with the cap, the recommendation's
    overall-confidence gate always agrees (including the 3-decimal rounding of stored confidences)."""
    rng = random.Random(211)
    for case in range(CASES):
        count = rng.randint(1, 10)
        comps = [
            {
                "key": f"c{i}",
                "weight": round(rng.uniform(0.0, 3.0), 2),
                "required": rng.random() < 0.6,
                "min_evidence": 1,
            }
            for i in range(count)
        ]
        minutes = rng.randint(5, 90)
        config = harness_round(
            competencies=comps,
            rubric=[],
            duration_minutes=minutes,
            max_duration_minutes=minutes,
            confidence_threshold=round(rng.uniform(0.3, 0.95), 2),
            passing_guidance={
                "min_confidence": round(rng.uniform(0.3, 0.95), 2),
                "min_required_coverage": 1.0,
            },
            stopping={"max_questions": rng.choice([None, rng.randint(3, 30)])},
        )
        scope = round_scope(config)
        states: dict[str, CompetencyState] = {}
        for c in config.competencies:
            counts_for_coverage = c.required or not any(x.required for x in config.competencies)
            if counts_for_coverage:
                raw = (
                    scope.confidence_target
                    if rng.random() < 0.3
                    else rng.uniform(scope.confidence_target, 0.98)
                )
            else:
                raw = rng.choice([0.0, rng.uniform(0.0, 0.98)])
            states[c.key] = CompetencyState(
                key=c.key,
                weight=c.weight,
                required=c.required,
                confidence=round(raw, 3),
                evidence_count=3 if raw > 0 else 0,
                sufficient_evidence=counts_for_coverage,
            )
        rubric = aggregate_rubric(config, states, {})
        assert rubric.required_coverage == 1.0
        assert rubric.overall_confidence >= scope.min_confidence_target, (case, scope.as_dict())
        rec = recommend(
            config, rubric, states, unreviewed_integrity_flags=0, open_high_contradictions=0, end_reason=None
        )
        assert rec.recommendation is not Recommendation.INSUFFICIENT_EVIDENCE, (case, rec.rationale)


# ------------------------------------------------------------------ sufficiency


def _two_consistent_answers(key: str = "kubernetes") -> tuple[list, list]:
    observations = [
        obs(key, score=7.2, confidence=0.8, answer_id="s1", elapsed=60),
        obs(key, score=7.6, confidence=0.8, answer_id="s2", elapsed=160),
    ]
    evidence = [
        ev(key, strength=0.6, confidence=0.8, answer_id="s1", elapsed=60),
        ev(key, strength=0.6, confidence=0.8, answer_id="s2", elapsed=160),
        ev(key, strength=0.75, confidence=0.8, answer_id="s2", elapsed=160),
    ]
    return observations, evidence


def test_two_consistent_answers_are_enough_for_a_short_round_only() -> None:
    observations, evidence = _two_consistent_answers()
    short = compute_competency_states(harness_round(), observations, evidence)["kubernetes"]
    full = compute_competency_states(
        harness_round(duration_minutes=40, max_duration_minutes=45, stopping={"max_questions": 30}),
        observations,
        evidence,
    )["kubernetes"]
    # Identical evidence -> identical score and confidence (calibration never moves them) ...
    assert (short.score_estimate, short.confidence, short.score_history) == (
        full.score_estimate,
        full.confidence,
        full.score_history,
    )
    assert 0.5525 <= short.confidence < 0.65 and short.evidence_count == 3 and len(short.score_history) == 2
    # ... but only the 10-minute round accepts it: 3 items >= 2 + 1 extra and confidence >= 0.5525.
    assert short.sufficient_evidence and not full.sufficient_evidence


def test_short_round_still_needs_the_minimum_evidence_and_target() -> None:
    config = harness_round()
    single = compute_competency_states(config, [obs(score=8.0, confidence=0.9)], [ev(strength=0.8)])
    assert not single["kubernetes"].sufficient_evidence  # one answer: confidence ~0.47
    assert not is_sufficient(
        evidence_count=1,
        confidence=0.9,
        history=[7.0, 7.1, 7.2],
        min_evidence=2,
        threshold=0.55,
        extra_evidence=1,
    )
    assert not is_sufficient(
        evidence_count=5,
        confidence=0.54,
        history=[7.0, 7.1, 7.2],
        min_evidence=2,
        threshold=0.55,
        extra_evidence=1,
    )
    assert is_sufficient(
        evidence_count=3, confidence=0.56, history=[6.1], min_evidence=2, threshold=0.55, extra_evidence=1
    )
    assert not is_sufficient(evidence_count=3, confidence=0.56, history=[6.1], min_evidence=2, threshold=0.55)


def test_calibration_never_moves_scores_and_only_relaxes_sufficiency() -> None:
    rng = random.Random(307)
    short = harness_round()
    full = harness_round(duration_minutes=40, max_duration_minutes=45, stopping={"max_questions": 30})
    levels = list(Difficulty)
    for case in range(CASES):
        observations = [
            obs(
                rng.choice(["kubernetes", "aws", "ci_cd", "communication"]),
                score=rng.uniform(0, 10),
                confidence=rng.uniform(0.2, 1.0),
                difficulty=rng.choice(levels),
                elapsed=rng.uniform(0, 600),
            )
            for _ in range(rng.randint(0, 9))
        ]
        evidence = [
            ev(
                rng.choice(["kubernetes", "aws", "ci_cd", "ownership"]),
                strength=rng.uniform(0, 1),
                confidence=rng.uniform(0, 1),
                elapsed=rng.uniform(0, 600),
            )
            for _ in range(rng.randint(0, 14))
        ]
        a = compute_competency_states(short, observations, evidence)
        b = compute_competency_states(full, observations, evidence)
        for key in a:
            assert (a[key].score_estimate, a[key].confidence) == (b[key].score_estimate, b[key].confidence), (
                case
            )
            assert a[key].sufficient_evidence or not b[key].sufficient_evidence, case  # short >= full


# ------------------------------------------------------------------ recommendation


def _rubric(score: float, conf: float, coverage: float) -> RubricResult:
    return RubricResult(
        categories=[], overall_score=score, overall_confidence=conf, required_coverage=coverage
    )


def test_rationale_and_factors_state_the_round_targets() -> None:
    config = harness_round()
    states = {
        k: CompetencyState(
            key=k, weight=w, required=True, confidence=0.6, evidence_count=4, sufficient_evidence=True
        )
        for k, w in (("kubernetes", 1.5), ("aws", 1.2), ("ci_cd", 0.8))
    }
    rec = recommend(
        config,
        _rubric(68, 0.55, 1.0),
        states,
        unreviewed_integrity_flags=0,
        open_high_contradictions=0,
        end_reason="time_limit",
    )
    assert rec.recommendation is Recommendation.PROCEED
    assert rec.factors["min_confidence"] == 0.6  # configured values stay visible
    assert rec.factors["min_confidence_target"] == pytest.approx(0.482)
    assert rec.factors["competency_confidence_target"] == pytest.approx(0.552, abs=1e-3)
    assert rec.factors["round_scope"] == pytest.approx(0.4)
    assert "with confidence 55%" in rec.rationale and "Round-scope calibration" in rec.rationale
    low = recommend(
        config,
        _rubric(68, 0.4, 0.667),
        states,
        unreviewed_integrity_flags=0,
        open_high_contradictions=0,
        end_reason=None,
    )
    assert low.recommendation is Recommendation.INSUFFICIENT_EVIDENCE
    assert "confidence 40% (minimum 48%)" in low.rationale


def test_low_scores_never_reach_borderline_or_proceed() -> None:
    """Weak and vague candidates must not rise: below the borderline score no target relief can lead to
    a positive or borderline recommendation (the time-limit partial-coverage rule is unchanged)."""
    rng = random.Random(409)
    config = harness_round()
    for case in range(CASES):
        states = {
            k: CompetencyState(
                key=k,
                weight=w,
                required=True,
                confidence=rng.uniform(0, 0.98),
                evidence_count=rng.randint(0, 9),
                sufficient_evidence=rng.random() < 0.7,
            )
            for k, w in (("kubernetes", 1.5), ("aws", 1.2), ("ci_cd", 0.8))
        }
        rubric = _rubric(rng.uniform(0, 49.9), rng.uniform(0, 0.98), required_coverage(config, states))
        end = rng.choice([None, "stopping_condition", "sufficient_evidence", "candidate_ended"])
        rec = recommend(
            config, rubric, states, unreviewed_integrity_flags=0, open_high_contradictions=0, end_reason=end
        )
        assert rec.recommendation in (Recommendation.HUMAN_REVIEW, Recommendation.INSUFFICIENT_EVIDENCE), case


# Recorded after-pass competency states (score, confidence, evidence items), the recorded recommendation
# (configured targets) and the expected one with the round-scope targets. The score history is unknown,
# so sufficiency is recomputed through the evidence-count path only (the conservative bound).
PERSONAS: dict[str, dict[str, Any]] = {
    "strong": {
        "overall": 67.36, "confidence": 0.646, "end": "time_limit", "recorded": "borderline_evidence",
        "comps": {"kubernetes": (6.75, 0.731, 5), "aws": (6.49, 0.591, 4), "ci_cd": (7.21, 0.876, 8),
                  "ownership": (5.18, 0.0, 1), "communication": (5.56, 0.386, 3)},
        "configured_coverage": 2 / 3, "expected": Recommendation.PROCEED,
    },
    "mid": {
        "overall": 50.04, "confidence": 0.618, "end": "stopping_condition", "recorded": "insufficient_evidence",
        "comps": {"kubernetes": (5.58, 0.666, 7), "aws": (4.02, 0.782, 9), "ci_cd": (4.0, 0.637, 6),
                  "ownership": (5.0, 0.0, 0), "communication": (5.37, 0.127, 1)},
        "configured_coverage": 2 / 3, "expected": Recommendation.BORDERLINE_EVIDENCE,
    },
    "vague": {
        "overall": 35.78, "confidence": 0.666, "end": "sufficient_evidence", "recorded": "human_review",
        "comps": {"kubernetes": (2.02, 0.743, 5), "aws": (3.85, 0.76, 5), "ci_cd": (1.93, 0.711, 4),
                  "ownership": (5.0, 0.0, 0), "communication": (5.49, 0.351, 0)},
        "configured_coverage": 1.0, "expected": Recommendation.HUMAN_REVIEW,
    },
    "weak": {
        "overall": 29.82, "confidence": 0.676, "end": "stopping_condition", "recorded": "insufficient_evidence",
        "comps": {"kubernetes": (1.53, 0.846, 8), "aws": (0.79, 0.836, 6), "ci_cd": (2.6, 0.506, 2),
                  "ownership": (5.0, 0.0, 0), "communication": (5.33, 0.127, 1)},
        "configured_coverage": 2 / 3, "expected": Recommendation.INSUFFICIENT_EVIDENCE,
    },
}  # fmt: skip


def _persona_states(
    config: RoundConfig, comps: dict[str, tuple[float, float, int]], threshold: float, extra: int
) -> dict[str, CompetencyState]:
    meta = {c.key: c for c in config.competencies}
    return {
        key: CompetencyState(
            key=key,
            weight=meta[key].weight,
            required=meta[key].required,
            score_estimate=score,
            confidence=conf,
            evidence_count=count,
            sufficient_evidence=is_sufficient(
                evidence_count=count,
                confidence=conf,
                history=[],
                min_evidence=config.min_evidence_for(key),
                threshold=threshold,
                extra_evidence=extra,
            ),
        )
        for key, (score, conf, count) in comps.items()
    }


@pytest.mark.parametrize("persona", list(PERSONAS))
def test_recorded_persona_outcomes(persona: str) -> None:
    data = PERSONAS[persona]
    config = harness_round()
    scope = round_scope(config)
    configured = _persona_states(
        config, data["comps"], config.confidence_threshold, SUFFICIENCY_EXTRA_EVIDENCE
    )
    assert required_coverage(config, configured) == pytest.approx(data["configured_coverage"])
    scaled = _persona_states(config, data["comps"], scope.confidence_target, scope.extra_evidence)
    rubric = _rubric(data["overall"], data["confidence"], required_coverage(config, scaled))
    rec = recommend(
        config,
        rubric,
        scaled,
        unreviewed_integrity_flags=0,
        open_high_contradictions=0,
        end_reason=data["end"],
    )
    assert rec.recommendation is data["expected"], rec.rationale
