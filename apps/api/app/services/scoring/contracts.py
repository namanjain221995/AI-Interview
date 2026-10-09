"""Scoring/evidence engine contract (implemented in ``app.services.scoring``).

Competency state is a pure function of evidence: the live engine calls
``compute_competency_states`` after every applied evaluation, and the final
pipeline calls ``recompute_session_scores(final=True)``. Neither ever needs
Redis, so scores survive any cache loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.domain.enums import Difficulty, EvidenceSignal, EvidenceSource, Recommendation
from app.schemas.rounds import RoundConfig
from app.services.interview.state import CompetencyState


@dataclass(slots=True)
class Observation:
    """One evaluated answer's (or coding problem's) score for one competency."""

    competency_key: str
    score: float  # 0-10, already combining the relevant answer dimensions
    confidence: float  # evaluator confidence 0-1
    difficulty: Difficulty
    elapsed_s: float
    answer_id: str | None = None
    question_id: str | None = None
    coding_session_id: str | None = None
    dimensions: dict[str, float | None] = field(default_factory=dict)
    assistance_factor: float = 1.0  # < 1 when coding hints were used (transparent)


@dataclass(slots=True)
class EvidenceRecord:
    id: str
    competency_key: str
    signal: EvidenceSignal
    dimension: str
    strength: float
    confidence: float
    source: EvidenceSource
    difficulty: Difficulty | None = None
    question_id: str | None = None
    answer_id: str | None = None
    elapsed_s: float = 0.0
    text: str = ""
    coding_session_id: str | None = None  # additive: groups coding evidence with its coding observation


@dataclass(slots=True)
class RubricCategoryResult:
    key: str
    label: str
    weight: float  # percent
    score: float  # 0-100
    confidence: float
    contributing_competencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RubricResult:
    categories: list[RubricCategoryResult]
    overall_score: float  # 0-100
    overall_confidence: float  # 0-1
    required_coverage: float  # share of required competencies with sufficient evidence


@dataclass(slots=True)
class RecommendationResult:
    recommendation: Recommendation
    rationale: str
    factors: dict[str, float | int | str] = field(default_factory=dict)
    human_decision_note: str = "This recommendation is assistive. A human reviewer owns the hiring decision."


@dataclass(slots=True)
class ScoringResult:
    competencies: dict[str, CompetencyState]
    rubric: RubricResult
    recommendation: RecommendationResult
    snapshot_written: bool = False


@runtime_checkable
class ScoringApi(Protocol):
    def observations_from_evaluation(
        self,
        evaluation: object,  # app.schemas.ai.AnswerEvaluation
        *,
        competency_keys: list[str],
        difficulty: Difficulty,
        answer_id: str,
        question_id: str | None,
        elapsed_s: float,
    ) -> list[Observation]: ...

    def compute_competency_states(
        self,
        round_config: RoundConfig,
        observations: list[Observation],
        evidence: list[EvidenceRecord],
        *,
        previous: dict[str, CompetencyState] | None = None,
        expected_topics: dict[str, list[str]] | None = None,
        elapsed_s: float = 0.0,
        focus_areas: dict[str, list[str]] | None = None,  # additive: plan focus areas per competency
    ) -> dict[str, CompetencyState]: ...

    def aggregate_rubric(
        self,
        round_config: RoundConfig,
        states: dict[str, CompetencyState],
        dimension_averages: dict[str, float],
    ) -> RubricResult: ...

    def recommend(
        self,
        round_config: RoundConfig,
        rubric: RubricResult,
        states: dict[str, CompetencyState],
        *,
        unreviewed_integrity_flags: int,
        open_high_contradictions: int,
        end_reason: str | None,
    ) -> RecommendationResult: ...
