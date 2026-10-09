"""Property tests of the scoring math.

``hypothesis`` is not a project dependency, so each property is checked on
hundreds of seeded random cases (deterministic and reproducible: a failure
prints the seed and the case index).
"""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from app.domain.enums import Difficulty, EvidenceSignal
from app.services.scoring.compute import (
    DIFFICULTY_CAP,
    assistance_factor,
    calculate,
    compute_competency_states,
    effective_score,
    observations_from_coding,
    observations_from_evaluation,
)
from app.services.scoring.contracts import EvidenceRecord, Observation

from .helpers import ev, evaluation, obs, round_config

CASES = 400
LEVELS = list(Difficulty)


def _random_obs(rng: random.Random, n: int) -> list[Observation]:
    return [
        obs(
            score=rng.uniform(0, 10),
            confidence=rng.uniform(0.05, 1.0),
            difficulty=rng.choice(LEVELS),
            elapsed=rng.uniform(0, 1800),
        )
        for _ in range(n)
    ]


def _random_ev(rng: random.Random, n: int) -> list[EvidenceRecord]:
    return [
        ev(
            signal=rng.choice([EvidenceSignal.POSITIVE, EvidenceSignal.NEGATIVE, EvidenceSignal.NEUTRAL]),
            strength=rng.uniform(0, 1),
            confidence=rng.uniform(0, 1),
            elapsed=rng.uniform(0, 1800),
            difficulty=rng.choice([None, *LEVELS]),
        )
        for _ in range(n)
    ]


def _check(seed: int, prop: Callable[[random.Random, int], None]) -> None:
    rng = random.Random(seed)
    for case in range(CASES):
        try:
            prop(rng, case)
        except AssertionError as exc:  # pragma: no cover - diagnostic path
            raise AssertionError(f"property failed (seed={seed}, case={case}): {exc}") from exc


def test_more_positive_evidence_never_lowers_score() -> None:
    def prop(rng: random.Random, _: int) -> None:
        observations = _random_obs(rng, rng.randint(0, 6))
        evidence = _random_ev(rng, rng.randint(0, 10))
        before = calculate(observations, evidence).score
        extra = [
            ev(signal=EvidenceSignal.POSITIVE, strength=rng.uniform(0, 1), confidence=rng.uniform(0, 1))
            for _ in range(rng.randint(1, 4))
        ]
        after = calculate(observations, evidence + extra).score
        assert after >= before - 1e-9, (before, after)

    _check(101, prop)


def test_more_negative_evidence_never_raises_score() -> None:
    def prop(rng: random.Random, _: int) -> None:
        observations = _random_obs(rng, rng.randint(0, 6))
        evidence = _random_ev(rng, rng.randint(0, 10))
        before = calculate(observations, evidence).score
        extra = [ev(signal=EvidenceSignal.NEGATIVE, strength=rng.uniform(0, 1), confidence=rng.uniform(0, 1))]
        assert calculate(observations, evidence + extra).score <= before + 1e-9

    _check(102, prop)


def test_better_answers_never_lower_score() -> None:
    def prop(rng: random.Random, _: int) -> None:
        observations = _random_obs(rng, rng.randint(1, 6))
        evidence = _random_ev(rng, rng.randint(0, 6))
        before = calculate(observations, evidence).score
        # Raising one observation's score never lowers the competency score.
        index = rng.randrange(len(observations))
        improved = list(observations)
        old = improved[index]
        improved[index] = obs(
            score=min(10.0, old.score + rng.uniform(0, 4)),
            confidence=old.confidence,
            difficulty=old.difficulty,
            elapsed=old.elapsed_s,
        )
        assert calculate(improved, evidence).score >= before - 1e-9
        # A new answer at least as good as everything so far never lowers it.
        best = max([5.0] + [effective_score(o) for o in observations])
        new = obs(
            score=rng.uniform(best, 10.0), confidence=rng.uniform(0.05, 1), difficulty=rng.choice(LEVELS)
        )
        assert calculate([*observations, new], evidence).score >= before - 1e-9

    _check(103, prop)


def test_confidence_grows_with_evidence() -> None:
    def prop(rng: random.Random, _: int) -> None:
        observations = _random_obs(rng, rng.randint(0, 6))
        evidence = _random_ev(rng, rng.randint(0, 8))
        before = calculate(observations, evidence).confidence
        # Any additional evidence record never lowers confidence.
        assert calculate(observations, evidence + _random_ev(rng, 1)).confidence >= before - 1e-9
        # A consistent answer (at the current mean score) never lowers confidence.
        scores = [effective_score(o) for o in observations]
        mean = sum(scores) / len(scores) if scores else rng.uniform(0, 10)
        levels = {o.difficulty for o in observations}
        level = next(iter(levels)) if levels else rng.choice(LEVELS)
        consistent = obs(score=mean, confidence=rng.uniform(0.05, 1), difficulty=level)
        assert calculate([*observations, consistent], evidence).confidence >= before - 1e-9

    _check(104, prop)


def test_confidence_strictly_increases_with_repeated_consistent_answers() -> None:
    previous = 0.0
    observations: list[Observation] = []
    for _ in range(8):
        observations.append(obs(score=7.0, confidence=0.7))
        current = calculate(observations, []).confidence
        assert current > previous
        previous = current


def test_hard_strong_answers_outrank_easy_ones() -> None:
    def prop(rng: random.Random, _: int) -> None:
        raw = rng.uniform(7.0, 10.0)
        conf = rng.uniform(0.2, 1.0)
        doc = evaluation(correctness=raw, depth=raw, specificity=raw, confidence=conf)
        kwargs = {"competency_keys": ["kubernetes"], "answer_id": "a", "question_id": "q", "elapsed_s": 10.0}
        hard = observations_from_evaluation(doc, difficulty=Difficulty.HARD, **kwargs)
        medium = observations_from_evaluation(doc, difficulty=Difficulty.MEDIUM, **kwargs)
        easy = observations_from_evaluation(doc, difficulty=Difficulty.EASY, **kwargs)
        s_hard, s_med, s_easy = (calculate(o, []).score for o in (hard, medium, easy))
        assert s_hard > s_med > s_easy, (raw, conf, s_hard, s_med, s_easy)
        # The same holds inside a mixed history of other answers.
        history = _random_obs(rng, rng.randint(0, 4))
        assert calculate([*history, *hard], []).score > calculate([*history, *easy], []).score
        # Top scores need hard evidence: easy answers alone are capped.
        assert calculate(easy * 30, []).score <= DIFFICULTY_CAP[Difficulty.EASY] + 1e-9

    _check(105, prop)


def test_hints_lower_but_never_zero_coding_scores() -> None:
    def prop(rng: random.Random, _: int) -> None:
        estimate = rng.uniform(0.5, 10.0)
        doc = {
            "correctness_summary": "x",
            "code_quality": 7,
            "problem_solving": 7,
            "edge_case_handling": 7,
            "score_estimate": estimate,
            "confidence": rng.uniform(0.2, 1.0),
        }
        difficulty = rng.choice(LEVELS)
        results = []
        for hints in range(4):
            [o] = observations_from_coding(
                doc,
                competency_keys=["data_structures"],
                difficulty=difficulty,
                coding_session_id="cs",
                question_id=None,
                elapsed_s=100.0,
                hints_used=hints,
            )
            results.append((effective_score(o), calculate([o], []).score))
        effective = [r[0] for r in results]
        competency = [r[1] for r in results]
        assert all(e > 0 for e in effective)
        assert effective == sorted(effective, reverse=True) and effective[3] < effective[0]
        assert effective[3] >= effective[0] * 0.79 - 1e-9
        assert competency == sorted(competency, reverse=True)
        assert all(c > 0 for c in competency)

    _check(106, prop)
    assert assistance_factor(99) > 0


def test_bounds_and_order_independence() -> None:
    config = round_config()

    def prop(rng: random.Random, _: int) -> None:
        observations = _random_obs(rng, rng.randint(0, 8))
        evidence = _random_ev(rng, rng.randint(0, 12))
        for o in observations:
            o.competency_key = rng.choice(["kubernetes", "aws", "communication"])
        for e in evidence:
            e.competency_key = rng.choice(["kubernetes", "aws", "communication", "terraform"])
        states = compute_competency_states(config, observations, evidence)
        shuffled_obs = list(observations)
        shuffled_ev = list(evidence)
        rng.shuffle(shuffled_obs)
        rng.shuffle(shuffled_ev)
        again = compute_competency_states(config, shuffled_obs, shuffled_ev)
        for key, state in states.items():
            assert 0.0 <= state.score_estimate <= 10.0
            assert 0.0 <= state.confidence <= 0.98
            assert state.model_dump() == again[key].model_dump()

    _check(107, prop)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_recompute_is_idempotent(seed: int) -> None:
    rng = random.Random(seed)
    config = round_config()
    observations = _random_obs(rng, 6)
    evidence = _random_ev(rng, 10)
    first = compute_competency_states(config, observations, evidence)
    second = compute_competency_states(config, observations, evidence, previous=first)
    for key in first:
        assert first[key].model_dump() == second[key].model_dump()
