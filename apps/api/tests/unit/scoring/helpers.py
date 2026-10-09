"""Builders shared by the scoring unit tests."""

from __future__ import annotations

import itertools
from typing import Any

from app.domain.enums import AnswerQuality, Difficulty, EvidenceSignal, EvidenceSource
from app.schemas.ai import AnswerEvaluation, DimensionScores, EvidenceItem
from app.schemas.rounds import PassingGuidance, RoundCompetency, RoundConfig, RubricCategory
from app.services.scoring.contracts import EvidenceRecord, Observation

_ids = itertools.count(1)


def round_config(**overrides: Any) -> RoundConfig:
    base: dict[str, Any] = {
        "round_type": "technical_screening",
        "name": "Tech",
        "competencies": [
            RoundCompetency(key="kubernetes", weight=1.5, focus_areas=["availability"]),
            RoundCompetency(key="aws", weight=1.0),
            RoundCompetency(key="communication", weight=0.5, required=False, min_evidence=1),
        ],
        "rubric": [
            RubricCategory(
                key="technical",
                label="Technical",
                weight=60,
                competency_keys=["kubernetes", "aws"],
                dimensions=["correctness"],
            ),
            RubricCategory(
                key="communication", label="Communication", weight=25, competency_keys=["communication"]
            ),
            RubricCategory(key="consistency", label="Consistency", weight=15, dimensions=["consistency"]),
        ],
        "min_evidence_per_competency": 3,
        "confidence_threshold": 0.7,
        "passing_guidance": PassingGuidance(),
    }
    base.update(overrides)
    return RoundConfig(**base)


def obs(
    key: str = "kubernetes",
    score: float = 7.0,
    confidence: float = 0.8,
    difficulty: Difficulty = Difficulty.MEDIUM,
    elapsed: float | None = None,
    answer_id: str | None = None,
    **kwargs: Any,
) -> Observation:
    n = next(_ids)
    return Observation(
        competency_key=key,
        score=score,
        confidence=confidence,
        difficulty=difficulty,
        elapsed_s=float(n * 30 if elapsed is None else elapsed),
        answer_id=answer_id or f"a{n}",
        question_id=f"q{n}",
        **kwargs,
    )


def ev(
    key: str = "kubernetes",
    signal: EvidenceSignal = EvidenceSignal.POSITIVE,
    strength: float = 0.7,
    confidence: float = 0.8,
    dimension: str = "general",
    elapsed: float | None = None,
    answer_id: str | None = None,
    source: EvidenceSource = EvidenceSource.ANSWER,
    text: str = "",
    difficulty: Difficulty | None = None,
) -> EvidenceRecord:
    n = next(_ids)
    return EvidenceRecord(
        id=f"e{n}",
        competency_key=key,
        signal=signal,
        dimension=dimension,
        strength=strength,
        confidence=confidence,
        source=source,
        difficulty=difficulty,
        answer_id=answer_id,
        elapsed_s=float(n * 30 if elapsed is None else elapsed),
        text=text,
    )


def evaluation(
    quality: AnswerQuality = AnswerQuality.STRONG,
    confidence: float = 0.8,
    evidence: list[EvidenceItem] | None = None,
    **scores: float | None,
) -> AnswerEvaluation:
    return AnswerEvaluation(
        overall_quality=quality,
        scores=DimensionScores(**scores),
        summary="s",
        evidence=evidence or [],
        evaluation_confidence=confidence,
    )
