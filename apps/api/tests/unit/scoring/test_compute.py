"""Exact-value tests of the scoring formulae (ENGINE_DESIGN.md section 7)."""

from __future__ import annotations

import math

import pytest

from app.domain.enums import AnswerQuality, Difficulty, EvidenceSignal, EvidenceSource, Recommendation
from app.schemas.ai import CodingEvaluation, EvidenceItem
from app.schemas.rounds import RoundCompetency
from app.services.interview.state import CompetencyState
from app.services.scoring import ScoringApi, get_scoring
from app.services.scoring.compute import (
    aggregate_rubric,
    assistance_factor,
    calculate,
    compute_competency_states,
    dimension_mean,
    evidence_balance,
    is_stable,
    observations_from_coding,
    observations_from_evaluation,
    recommend,
    replay,
    unanswered_topics,
)
from app.services.scoring.contracts import RubricResult

from .helpers import ev, evaluation, obs, round_config

# ---------------------------------------------------------------- observations


def test_service_implements_protocol() -> None:
    assert isinstance(get_scoring(), ScoringApi)


def test_dimension_weights() -> None:
    # correctness .25 x 10 + depth .20 x 0 over .45
    assert dimension_mean({"correctness": 10, "depth": 0}) == pytest.approx(2.5 / 0.45)
    full = {
        "correctness": 8,
        "depth": 6,
        "practical_experience": 7,
        "specificity": 5,
        "reasoning": 9,
        "completeness": 4,
    }
    expected = 0.25 * 8 + 0.20 * 6 + 0.15 * 7 + 0.15 * 5 + 0.15 * 9 + 0.10 * 4
    assert dimension_mean(full) == pytest.approx(expected)
    # Secondary dimensions only when no primary one applies.
    assert dimension_mean({"relevance": 6, "consistency": 8}) == pytest.approx(7.0)
    assert dimension_mean({"relevance": 2, "correctness": 9}) == pytest.approx(9.0)
    assert dimension_mean({}) is None


@pytest.mark.parametrize(
    ("difficulty", "expected"), [(Difficulty.EASY, 8.0), (Difficulty.MEDIUM, 9.3), (Difficulty.HARD, 9.8)]
)
def test_difficulty_caps(difficulty: Difficulty, expected: float) -> None:
    result = observations_from_evaluation(
        evaluation(correctness=9.8, depth=9.8),
        competency_keys=["kubernetes"],
        difficulty=difficulty,
        answer_id="a",
        question_id="q",
        elapsed_s=10,
    )
    assert [o.score for o in result] == [pytest.approx(expected)]


def test_observation_per_question_competency_and_fallback_to_evidence_keys() -> None:
    items = [
        EvidenceItem(competency_key="aws", dimension="iam", signal=EvidenceSignal.POSITIVE, evidence="x"),
        EvidenceItem(competency_key="aws", dimension="vpc", signal=EvidenceSignal.POSITIVE, evidence="y"),
        EvidenceItem(
            competency_key="terraform", dimension="state", signal=EvidenceSignal.NEGATIVE, evidence="z"
        ),
    ]
    with_keys = observations_from_evaluation(
        evaluation(correctness=7, evidence=items),
        competency_keys=["kubernetes", "aws", "kubernetes"],
        difficulty=Difficulty.MEDIUM,
        answer_id="a",
        question_id="q",
        elapsed_s=5,
    )
    assert [o.competency_key for o in with_keys] == ["kubernetes", "aws"]
    fallback = observations_from_evaluation(
        evaluation(correctness=7, evidence=items),
        competency_keys=[],
        difficulty=Difficulty.MEDIUM,
        answer_id="a",
        question_id=None,
        elapsed_s=5,
    )
    assert [o.competency_key for o in fallback] == ["aws", "terraform"]
    assert fallback[0].confidence == 0.8 and fallback[0].dimensions["correctness"] == 7


@pytest.mark.parametrize(
    "quality",
    [
        AnswerQuality.CLARIFICATION_REQUEST,
        AnswerQuality.CANDIDATE_QUESTION,
        AnswerQuality.WANTS_TIME,
        AnswerQuality.WANTS_HINT,
    ],
)
def test_flow_turns_produce_no_observation(quality: AnswerQuality) -> None:
    assert (
        observations_from_evaluation(
            evaluation(quality, correctness=9),
            competency_keys=["kubernetes"],
            difficulty=Difficulty.HARD,
            answer_id="a",
            question_id="q",
            elapsed_s=0,
        )
        == []
    )


def test_quality_value_used_when_no_dimension_applies() -> None:
    result = observations_from_evaluation(
        evaluation(AnswerQuality.NO_ANSWER),
        competency_keys=["kubernetes"],
        difficulty=Difficulty.MEDIUM,
        answer_id="a",
        question_id="q",
        elapsed_s=0,
    )
    assert result[0].score == 0.0
    vague = observations_from_evaluation(
        evaluation(AnswerQuality.VAGUE),
        competency_keys=["kubernetes"],
        difficulty=Difficulty.MEDIUM,
        answer_id="a",
        question_id="q",
        elapsed_s=0,
    )
    assert vague[0].score == pytest.approx(3.5)


def test_accepts_dict_evaluation() -> None:
    doc = evaluation(correctness=6).model_dump(mode="json")
    result = get_scoring().observations_from_evaluation(
        doc, competency_keys=["aws"], difficulty=Difficulty.EASY, answer_id="a", question_id=None, elapsed_s=1
    )
    assert result[0].score == pytest.approx(6.0)


@pytest.mark.parametrize(
    ("hints", "factor"), [(0, 1.0), (1, 0.93), (2, 0.86), (3, 0.79), (7, 0.79), (-1, 1.0)]
)
def test_assistance_factor(hints: int, factor: float) -> None:
    assert assistance_factor(hints) == pytest.approx(factor)


def test_coding_observation_applies_cap_and_assistance() -> None:
    coding = CodingEvaluation(
        correctness_summary="ok",
        code_quality=8,
        problem_solving=8,
        edge_case_handling=7,
        score_estimate=9.0,
        confidence=0.9,
    )
    [o] = observations_from_coding(
        coding,
        competency_keys=["data_structures"],
        difficulty="easy",
        coding_session_id="cs1",
        question_id=None,
        elapsed_s=600,
        hints_used=3,
    )
    assert o.score == pytest.approx(8.0)  # easy cap
    assert o.assistance_factor == pytest.approx(0.79)
    calc = calculate([o], [])
    weight = 0.9 * 0.85
    assert calc.bayesian_mean == pytest.approx((5.0 + weight * 8.0 * 0.79) / (1 + weight))


# ------------------------------------------------------------- competency math


def test_bayesian_mean_with_prior() -> None:
    calc = calculate([obs(score=8.0, confidence=1.0)], [])
    assert calc.bayesian_mean == pytest.approx(6.5)
    assert calc.score == pytest.approx(6.5)
    hard = calculate([obs(score=8.0, confidence=1.0, difficulty=Difficulty.HARD)], [])
    assert hard.bayesian_mean == pytest.approx((5 + 1.2 * 8) / 2.2)


def test_evidence_balance() -> None:
    balance, pos, neg = evidence_balance([ev(strength=1.0, confidence=1.0)])
    assert (balance, pos, neg) == (pytest.approx(0.4), pytest.approx(1.0), 0.0)
    balance, _, _ = evidence_balance(
        [ev(strength=1.0, confidence=1.0), ev(signal=EvidenceSignal.NEGATIVE, strength=1.0, confidence=1.0)]
    )
    assert balance == pytest.approx(0.0)
    neutral_only, _, _ = evidence_balance([ev(signal=EvidenceSignal.NEUTRAL)])
    assert neutral_only == 0.0
    # Hint usage is informational, even when recorded as negative.
    hint, _, _ = evidence_balance([ev(signal=EvidenceSignal.NEGATIVE, source=EvidenceSource.HINT_USAGE)])
    assert hint == 0.0
    many, _, _ = evidence_balance([ev(strength=1.0, confidence=1.0) for _ in range(50)])
    assert 0.78 < many < 0.8


def test_score_clamped() -> None:
    calc = calculate(
        [obs(score=10, confidence=1, difficulty=Difficulty.HARD) for _ in range(30)],
        [ev(strength=1, confidence=1) for _ in range(30)],
    )
    assert calc.score == 10.0
    low = calculate(
        [obs(score=0, confidence=1) for _ in range(30)],
        [ev(signal=EvidenceSignal.NEGATIVE, strength=1, confidence=1) for _ in range(30)],
    )
    assert low.score == pytest.approx(0.0, abs=0.5)
    assert low.score >= 0.0


def test_confidence_formula() -> None:
    single = calculate([obs(score=7, confidence=1.0)], [])
    assert single.confidence == pytest.approx(1 - math.exp(-1 / 2.2))
    strong = calculate([obs(score=7, confidence=1.0)], [ev(strength=0.8, confidence=0.9)])
    assert strong.confidence == pytest.approx(1 - math.exp(-1.3 / 2.2))
    weak_item = calculate([obs(score=7, confidence=1.0)], [ev(strength=0.5, confidence=0.9)])
    assert weak_item.confidence == pytest.approx(single.confidence)
    capped_bonus = calculate([], [ev(strength=0.9, confidence=0.9) for _ in range(20)])
    assert capped_bonus.total_weight == pytest.approx(1.5)


def test_consistency_penalty_and_difficulty_bonus() -> None:
    spread = calculate([obs(score=2, confidence=1), obs(score=10, confidence=1)], [])
    assert spread.score_std == pytest.approx(4.0)
    assert spread.consistency_penalty == pytest.approx(0.65)  # capped at .35
    assert spread.confidence == pytest.approx((1 - math.exp(-2 / 2.2)) * 0.65)
    mixed = calculate(
        [obs(score=7, confidence=1), obs(score=7, confidence=1, difficulty=Difficulty.HARD)], []
    )
    assert mixed.confidence == pytest.approx(1 - math.exp(-2.2 / 2.2) + 0.05)
    assert calculate([], []).confidence == 0.0


def test_confidence_cap() -> None:
    calc = calculate(
        [
            obs(score=8, confidence=1, difficulty=d)
            for d in (Difficulty.EASY, Difficulty.HARD)
            for _ in range(20)
        ],
        [],
    )
    assert calc.confidence == pytest.approx(0.98)


def test_replay_groups_answers_and_orders_by_time() -> None:
    a = obs(score=8, confidence=1, elapsed=100, answer_id="A")
    b = obs(score=6, confidence=1, elapsed=50, answer_id="B")
    e1 = ev(answer_id="A", elapsed=100)
    points = replay([a, b], [e1])
    assert [p.elapsed_s for p in points] == [50, 100]
    assert points[0].score == pytest.approx(round((5 + 6) / 2, 3))
    assert points[-1].evidence_count == 1


def test_stability() -> None:
    assert not is_stable([6.0, 6.2])
    assert is_stable([6.0, 6.5, 6.9])
    assert not is_stable([6.0, 6.9, 7.0])


def test_unanswered_topics() -> None:
    evidence = [
        ev(dimension="autoscaling", signal=EvidenceSignal.POSITIVE),
        ev(dimension="node_count", signal=EvidenceSignal.NEGATIVE),
        ev(dimension="general", text="explained ingress controllers and TLS termination"),
    ]
    missing = unanswered_topics(
        ["autoscaling", "node count", "ingress", "high availability"], ["node_count", "monitoring"], evidence
    )
    # node count: only negative evidence -> unanswered; touched as a focus area.
    assert missing == ["node count", "high availability", "monitoring"]


# ------------------------------------------------------------------ states


def test_states_cover_round_and_extra_competencies() -> None:
    config = round_config()
    states = compute_competency_states(config, [obs("kubernetes"), obs("terraform")], [ev("aws")])
    assert set(states) == {"kubernetes", "aws", "communication", "terraform"}
    assert states["terraform"].required is False
    assert states["kubernetes"].weight == 1.5
    assert states["communication"].score_estimate == 5.0 and states["communication"].confidence == 0.0
    assert states["aws"].positive_evidence_ids and states["aws"].evidence_count == 1


def test_sufficiency_rules() -> None:
    config = round_config()
    # 3 consistent answers with strong evidence -> sufficient (stable history)
    observations = [obs(score=7.5, confidence=0.95, answer_id=f"s{i}") for i in range(4)]
    evidence = [ev(strength=0.8, confidence=0.9, answer_id=f"s{i}") for i in range(4)]
    states = compute_competency_states(config, observations, evidence)
    k8s = states["kubernetes"]
    assert k8s.evidence_count == 4
    assert k8s.confidence >= 0.7
    assert k8s.sufficient_evidence
    assert len(k8s.score_history) == 4
    # Not enough evidence items
    few = compute_competency_states(config, observations, evidence[:2])
    assert not few["kubernetes"].sufficient_evidence
    # Optional competency with min_evidence 1 still needs the confidence threshold
    comm = compute_competency_states(config, [obs("communication", confidence=0.3)], [ev("communication")])
    assert not comm["communication"].sufficient_evidence


def test_sufficiency_unstable_needs_extra_evidence() -> None:
    config = round_config(confidence_threshold=0.3)
    swings = [obs(score=s, confidence=1.0, answer_id=f"w{i}") for i, s in enumerate([2, 9, 3])]
    evidence = [ev(strength=0.9, confidence=0.9, answer_id=f"w{i}") for i in range(3)]
    states = compute_competency_states(config, swings, evidence)
    assert not states["kubernetes"].sufficient_evidence  # deltas >= 0.8 and count < min + 2
    more = evidence + [ev(strength=0.9, confidence=0.9, answer_id=f"w{i}") for i in range(2)]
    assert compute_competency_states(config, swings, more)["kubernetes"].sufficient_evidence


def test_previous_engine_fields_carried_over() -> None:
    config = round_config()
    previous = {
        "kubernetes": CompetencyState(
            key="kubernetes",
            current_difficulty=Difficulty.HARD,
            questions_asked=4,
            followups_on_current_topic=2,
            last_quality=AnswerQuality.STRONG,
            score_estimate=9.9,
            difficulty_seen={"hard": 3},
        ),
        "legacy": CompetencyState(key="legacy", questions_asked=1),
    }
    states = compute_competency_states(
        config, [obs(score=6, difficulty=Difficulty.MEDIUM)], [], previous=previous
    )
    k8s = states["kubernetes"]
    assert k8s.current_difficulty == Difficulty.HARD and k8s.questions_asked == 4
    assert k8s.followups_on_current_topic == 2 and k8s.last_quality == AnswerQuality.STRONG
    assert k8s.score_estimate != 9.9  # recomputed from evidence
    assert k8s.difficulty_seen == {"medium": 1, "hard": 3}
    assert "legacy" in states


def test_unanswered_includes_round_focus_areas_and_expected() -> None:
    config = round_config()
    states = compute_competency_states(
        config,
        [],
        [ev(dimension="availability")],
        expected_topics={"kubernetes": ["rolling updates"]},
        focus_areas={"kubernetes": ["networking"]},
    )
    assert states["kubernetes"].unanswered_questions == ["rolling updates", "networking"]
    assert states["kubernetes"].dimensions_covered == ["availability"]


# ------------------------------------------------------------------ rubric


def _state(
    key: str,
    score: float,
    conf: float,
    *,
    weight: float = 1.0,
    required: bool = True,
    sufficient: bool = True,
    count: int = 3,
) -> CompetencyState:
    return CompetencyState(
        key=key,
        weight=weight,
        required=required,
        score_estimate=score,
        confidence=conf,
        evidence_count=count,
        sufficient_evidence=sufficient,
    )


def test_aggregate_rubric() -> None:
    config = round_config()
    states = {
        "kubernetes": _state("kubernetes", 8.0, 0.9, weight=1.5),
        "aws": _state("aws", 6.0, 0.8),
        "communication": _state("communication", 7.0, 0.5, weight=0.5, required=False, sufficient=False),
    }
    rubric = aggregate_rubric(config, states, {"correctness": 9.0, "consistency": 6.0})
    tech, comm, cons = rubric.categories
    assert tech.score == pytest.approx((80 * 1.5 + 60 * 1.0 + 90 * 1.0) / 3.5, abs=0.01)
    assert tech.confidence == pytest.approx((0.9 * 1.5 + 0.8) / 2.5, abs=0.001)
    assert tech.contributing_competencies == ["kubernetes", "aws"]
    assert comm.score == pytest.approx(70.0)
    assert cons.score == pytest.approx(60.0)
    assert rubric.overall_score == pytest.approx((0.60 * tech.score + 0.25 * 70 + 0.15 * 60), abs=0.01)
    assert rubric.required_coverage == 1.0
    mean_conf = (1.5 * 0.9 + 0.8 + 0.25 * 0.5) / 2.75  # optional weighs half
    assert rubric.overall_confidence == pytest.approx(mean_conf, abs=0.001)


def test_aggregate_rubric_empty_category_and_coverage() -> None:
    config = round_config()
    states = {
        "kubernetes": _state("kubernetes", 8.0, 0.9, weight=1.5),
        "aws": _state("aws", 5.0, 0.0, sufficient=False, count=0),
        "communication": _state(
            "communication", 5.0, 0.0, weight=0.5, required=False, sufficient=False, count=0
        ),
    }
    rubric = aggregate_rubric(config, states, {})
    assert rubric.categories[2].score == 50.0 and rubric.categories[2].confidence == 0.0
    assert rubric.required_coverage == 0.5
    weights = 1.5 + 1.0 + 0.25
    # mean over assessed competencies (only kubernetes) x effective-weight coverage
    assert rubric.overall_confidence == pytest.approx(0.9 * (1.5 / weights), abs=0.001)


def test_aggregate_without_rubric() -> None:
    config = round_config(rubric=[])
    states = {"kubernetes": _state("kubernetes", 8.0, 0.9, weight=2), "aws": _state("aws", 6.0, 0.9)}
    rubric = aggregate_rubric(config, states, {})
    assert rubric.categories == []
    assert rubric.overall_score == pytest.approx((2 * 80 + 60) / 3, abs=0.01)


# ---------------------------------------------------------------- recommend


def _rubric(score: float, conf: float, coverage: float) -> RubricResult:
    return RubricResult(
        categories=[], overall_score=score, overall_confidence=conf, required_coverage=coverage
    )


@pytest.mark.parametrize(
    ("score", "conf", "coverage", "flags", "contradictions", "end", "expected"),
    [
        (85, 0.8, 1.0, 0, 0, None, Recommendation.STRONG_PROCEED),
        (70, 0.8, 1.0, 0, 0, None, Recommendation.PROCEED),
        (55, 0.8, 1.0, 0, 0, None, Recommendation.BORDERLINE_EVIDENCE),
        (30, 0.8, 1.0, 0, 0, None, Recommendation.HUMAN_REVIEW),
        (85, 0.8, 1.0, 2, 0, None, Recommendation.HUMAN_REVIEW),
        (85, 0.8, 1.0, 0, 1, None, Recommendation.HUMAN_REVIEW),
        (85, 0.4, 1.0, 0, 0, None, Recommendation.INSUFFICIENT_EVIDENCE),
        (85, 0.8, 0.5, 0, 0, None, Recommendation.INSUFFICIENT_EVIDENCE),
        (85, 0.4, 0.5, 0, 0, "time_limit", Recommendation.BORDERLINE_EVIDENCE),
        (85, 0.1, 0.0, 0, 0, "time_limit", Recommendation.INSUFFICIENT_EVIDENCE),
        (85, 0.4, 0.5, 3, 0, None, Recommendation.INSUFFICIENT_EVIDENCE),
    ],
)
def test_recommendation_matrix(
    score: float,
    conf: float,
    coverage: float,
    flags: int,
    contradictions: int,
    end: str | None,
    expected: Recommendation,
) -> None:
    config = round_config()
    states = {
        "kubernetes": _state("kubernetes", 8, 0.9),
        "aws": _state("aws", 8, 0.9, sufficient=coverage >= 1),
    }
    result = recommend(
        config,
        _rubric(score, conf, coverage),
        states,
        unreviewed_integrity_flags=flags,
        open_high_contradictions=contradictions,
        end_reason=end,
    )
    assert result.recommendation is expected
    assert result.rationale
    assert "human reviewer" in result.human_decision_note.lower()
    if flags:
        assert "integrity" in result.rationale.lower() and "not determinations" in result.rationale
    assert result.factors["overall_score"] == score


def test_recommendation_never_rejects() -> None:
    config = round_config()
    for score in range(0, 101, 5):
        rec = recommend(
            config,
            _rubric(score, 0.9, 1.0),
            {},
            unreviewed_integrity_flags=0,
            open_high_contradictions=0,
            end_reason=None,
        )
        assert rec.recommendation in set(Recommendation)
        assert "reject" not in rec.recommendation.value


def test_required_flags_respect_round_config() -> None:
    config = round_config(
        competencies=[RoundCompetency(key="a", required=False), RoundCompetency(key="b", required=False)],
        rubric=[],
    )
    states = compute_competency_states(config, [], [])
    rubric = aggregate_rubric(config, states, {})
    assert rubric.required_coverage == 0.0  # nothing sufficient; all competencies count when none required
