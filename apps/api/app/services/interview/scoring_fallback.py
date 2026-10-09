"""Local implementation of the scoring contract (ENGINE_DESIGN §7).

The scoring module (``app.services.scoring``) owns the authoritative
implementation and the ``scores`` table. The engine only needs the pure
competency-state math to adapt live, so this module provides a faithful,
self-contained version of ``observations_from_evaluation`` and
``compute_competency_states`` used (a) by unit tests as the stub, and (b) at
runtime when the scoring package does not expose an implementation yet.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from statistics import pstdev
from typing import Any

from app.domain.enums import ANSWER_QUALITY_VALUE, Difficulty, EvidenceSignal, EvidenceSource
from app.schemas.ai import AnswerEvaluation
from app.schemas.rounds import RoundConfig
from app.services.interview.state import CompetencyState
from app.services.scoring.contracts import EvidenceRecord, Observation

DIMENSION_WEIGHTS: dict[str, float] = {
    "correctness": 0.25,
    "depth": 0.20,
    "practical_experience": 0.15,
    "specificity": 0.15,
    "reasoning": 0.15,
    "completeness": 0.10,
}
DIFFICULTY_CAP = {Difficulty.EASY: 8.0, Difficulty.MEDIUM: 9.3, Difficulty.HARD: 10.0}
DIFFICULTY_WEIGHT = {Difficulty.EASY: 0.85, Difficulty.MEDIUM: 1.0, Difficulty.HARD: 1.2}
PRIOR_MEAN = 5.0
PRIOR_WEIGHT = 1.0
EVIDENCE_BALANCE = 0.8
STRONG_EVIDENCE_BONUS = 0.3
STRONG_EVIDENCE_CAP = 1.2
STRONG_STRENGTH = 0.7
HINT_PENALTY = 0.07


def assistance_factor(hints_used: int) -> float:
    return max(0.0, 1.0 - HINT_PENALTY * max(0, hints_used))


def observation_score(dimensions: dict[str, float | None], fallback: float | None = None) -> float | None:
    weighted = [
        (DIMENSION_WEIGHTS[k], v) for k, v in dimensions.items() if k in DIMENSION_WEIGHTS and v is not None
    ]
    if weighted:
        total = sum(w for w, _ in weighted)
        return sum(w * v for w, v in weighted) / total
    others = [v for v in dimensions.values() if v is not None]
    if others:
        return sum(others) / len(others)
    return fallback


class FallbackScoring:
    """Implements ``app.services.scoring.contracts.ScoringApi`` (state math only)."""

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
        ev = (
            evaluation
            if isinstance(evaluation, AnswerEvaluation)
            else AnswerEvaluation.model_validate(evaluation)
        )
        dims = ev.scores.model_dump()
        quality_value = ANSWER_QUALITY_VALUE.get(ev.overall_quality)
        base = observation_score(dims, fallback=quality_value * 10 if quality_value is not None else None)
        if base is None:
            return []
        difficulty = Difficulty(difficulty)
        score = min(base, DIFFICULTY_CAP[difficulty])
        out: list[Observation] = []
        seen: set[str] = set()
        for key in competency_keys:
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Observation(
                    competency_key=key,
                    score=round(score, 4),
                    confidence=float(ev.evaluation_confidence),
                    difficulty=difficulty,
                    elapsed_s=elapsed_s,
                    answer_id=answer_id,
                    question_id=question_id,
                    dimensions=dims,
                )
            )
        return out

    def compute_competency_states(
        self,
        round_config: RoundConfig,
        observations: list[Observation],
        evidence: list[EvidenceRecord],
        *,
        previous: dict[str, CompetencyState] | None = None,
        expected_topics: dict[str, list[str]] | None = None,
        elapsed_s: float = 0.0,
    ) -> dict[str, CompetencyState]:
        previous = previous or {}
        expected_topics = expected_topics or {}
        states: dict[str, CompetencyState] = {}
        for comp in round_config.competencies:
            key = comp.key
            prev = previous.get(key)
            obs = sorted((o for o in observations if o.competency_key == key), key=lambda o: o.elapsed_s)
            items = [e for e in evidence if e.competency_key == key]
            state = (
                prev.model_copy(deep=True)
                if prev is not None
                else CompetencyState(key=key, name=comp.name, weight=comp.weight, required=comp.required)
            )
            state.weight = comp.weight
            state.required = comp.required
            score, history = self._score(obs, items)
            non_neutral = [e for e in items if e.signal != EvidenceSignal.NEUTRAL]
            positive = [e for e in non_neutral if e.signal == EvidenceSignal.POSITIVE]
            negative = [e for e in non_neutral if e.signal == EvidenceSignal.NEGATIVE]
            confidence = self._confidence(obs, positive)
            state.score_estimate = round(score, 3)
            state.confidence = round(confidence, 4)
            state.evidence_count = len(non_neutral)
            state.positive_evidence = len(positive)
            state.negative_evidence = len(negative)
            state.positive_evidence_ids = [e.id for e in positive]
            state.negative_evidence_ids = [e.id for e in negative]
            state.dimensions_covered = sorted({e.dimension for e in positive if e.dimension})
            state.difficulty_seen = _merge_seen(state.difficulty_seen, obs)
            minimum = round_config.min_evidence_for(key)
            deltas = [abs(history[i] - history[i - 1]) for i in range(1, len(history))]
            stable = len(deltas) >= 2 and all(d < 0.8 for d in deltas[-2:])
            state.sufficient_evidence = bool(
                state.evidence_count >= minimum
                and state.confidence >= round_config.confidence_threshold
                and (stable or state.evidence_count >= minimum + 2)
            )
            state.score_history = [round(h, 3) for h in history][-20:]
            state.unanswered_questions = _unanswered(
                expected_topics.get(key, []), positive, list(comp.focus_areas) + list(state.focus_areas)
            )
            state.last_updated_s = elapsed_s
            states[key] = state
        return states

    # ------------------------------------------------------------- math
    @staticmethod
    def _weight(o: Observation) -> float:
        return max(0.0, o.confidence) * DIFFICULTY_WEIGHT.get(Difficulty(o.difficulty), 1.0)

    def _score(
        self, obs: Sequence[Observation], evidence: Sequence[EvidenceRecord]
    ) -> tuple[float, list[float]]:
        history: list[float] = []
        num = PRIOR_MEAN * PRIOR_WEIGHT
        den = PRIOR_WEIGHT
        for o in obs:
            r = self._weight(o)
            num += r * o.score * o.assistance_factor
            den += r
            history.append(num / den)
        bayes = num / den
        non_neutral = [e for e in evidence if e.signal != EvidenceSignal.NEUTRAL]
        balance = 0.0
        if non_neutral:
            balance = (
                EVIDENCE_BALANCE
                * sum(
                    (1.0 if e.signal == EvidenceSignal.POSITIVE else -1.0) * e.strength * e.confidence
                    for e in non_neutral
                )
                / len(non_neutral)
            )
        score = max(0.0, min(10.0, bayes + balance))
        # Apply the same evidence balance to every point so score deltas
        # reflect new observations, not the balance term appearing at the end.
        history = [max(0.0, min(10.0, h + balance)) for h in history]
        return score, history

    def _confidence(self, obs: Sequence[Observation], positive: Sequence[EvidenceRecord]) -> float:
        if not obs and not positive:
            return 0.0
        r = sum(self._weight(o) for o in obs)
        strong = sum(1 for e in positive if e.strength >= STRONG_STRENGTH)
        r += min(STRONG_EVIDENCE_CAP, STRONG_EVIDENCE_BONUS * strong)
        spread = pstdev([o.score for o in obs]) if len(obs) >= 2 else 0.0
        confidence = (1.0 - math.exp(-r / 2.2)) * (1.0 - min(0.35, 1.2 * spread / 10.0))
        if len({Difficulty(o.difficulty) for o in obs}) >= 2:
            confidence += 0.05
        return min(0.98, max(0.0, confidence))


def _merge_seen(existing: dict[str, int], obs: Iterable[Observation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in obs:
        key = Difficulty(o.difficulty).value
        counts[key] = counts.get(key, 0) + 1
    merged = dict(existing)
    for k, v in counts.items():
        merged[k] = max(merged.get(k, 0), v)
    return merged


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower().replace("_", " ")) if len(t) > 2}


def _unanswered(
    expected: Sequence[str], positive: Sequence[EvidenceRecord], focus: Sequence[str]
) -> list[str]:
    covered_text = " ".join(f"{e.dimension} {e.text}" for e in positive).lower().replace("_", " ")
    covered_tokens = _tokens(covered_text)
    out: list[str] = []
    for topic in [*expected, *focus]:
        topic = topic.strip()
        if not topic or topic in out:
            continue
        toks = _tokens(topic)
        if toks and len(toks & covered_tokens) / len(toks) >= 0.5:
            continue
        out.append(topic)
    return out[:8]


def evidence_record_from_row(row: Any) -> EvidenceRecord:
    """Map a ``competency_evidence`` ORM row to the scoring contract type."""
    difficulty = getattr(row, "difficulty", None)
    return EvidenceRecord(
        id=str(row.id),  # type: ignore[attr-defined]
        competency_key=row.competency_key,  # type: ignore[attr-defined]
        signal=EvidenceSignal(row.signal),  # type: ignore[attr-defined]
        dimension=row.dimension or "general",  # type: ignore[attr-defined]
        strength=float(row.strength),  # type: ignore[attr-defined]
        confidence=float(row.confidence),  # type: ignore[attr-defined]
        source=EvidenceSource(row.source)
        if row.source in EvidenceSource._value2member_map_
        else EvidenceSource.ANSWER,  # type: ignore[attr-defined]
        difficulty=Difficulty(str(difficulty)) if difficulty in Difficulty._value2member_map_ else None,
        question_id=str(row.question_id) if row.question_id else None,  # type: ignore[attr-defined]
        answer_id=str(row.answer_id) if row.answer_id else None,  # type: ignore[attr-defined]
        elapsed_s=float(row.elapsed_seconds or 0.0),  # type: ignore[attr-defined]
        text=row.evidence or "",  # type: ignore[attr-defined]
    )


__all__ = [
    "DIMENSION_WEIGHTS",
    "FallbackScoring",
    "assistance_factor",
    "evidence_record_from_row",
    "observation_score",
]
