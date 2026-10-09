"""Round configuration — the contract between recruiters' setup and the
interview engine. Stored in ``rounds.config`` / ``interview_templates.config``
and frozen into ``interview_sessions.round_config`` when a session starts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import CodeLanguage, Difficulty, RoundType


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class RoundCompetency(_Model):
    key: str = Field(..., max_length=80, description="Competency key, e.g. 'kubernetes'")
    name: str | None = None
    weight: float = Field(1.0, ge=0, le=10)
    required: bool = True
    min_evidence: int | None = Field(None, ge=1, le=20, description="Overrides round default")
    target_difficulty: Difficulty | None = None
    focus_areas: list[str] = Field(default_factory=list, description="Dimensions to probe, e.g. availability")


class RubricCategory(_Model):
    """Weighted scoring category, e.g. 'Technical Knowledge: 30%'.

    ``competency_keys`` lists competencies whose scores roll into this
    category; ``dimensions`` lists answer-evaluation dimensions that do
    (e.g. communication_clarity, consistency)."""

    key: str
    label: str
    weight: float = Field(..., ge=0, le=100, description="Percentage weight; categories sum to 100")
    competency_keys: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    description: str | None = None


class DifficultyDistribution(_Model):
    easy: float = 0.3
    medium: float = 0.4
    hard: float = 0.3

    @model_validator(mode="after")
    def _normalise(self) -> DifficultyDistribution:
        total = self.easy + self.medium + self.hard
        if total <= 0:
            raise ValueError("difficulty distribution must have a positive total")
        self.easy, self.medium, self.hard = self.easy / total, self.medium / total, self.hard / total
        return self


class QuestionStrategy(_Model):
    style: list[str] = Field(
        default_factory=lambda: ["experience_deep_dive", "scenario", "conceptual"],
        description="Preferred question styles in priority order",
    )
    max_followup_depth: int = Field(4, ge=0, le=8, description="Follow-ups allowed on one topic while uncertain")
    prefer_curated_ratio: float = Field(0.4, ge=0, le=1, description="Target share of curated bank questions")
    allow_generated_questions: bool = True
    adaptive_difficulty: bool = True
    allow_candidate_questions_at_end: bool = True


class StoppingConditions(_Model):
    stop_on_sufficient_evidence: bool = True
    min_duration_minutes: float = Field(8, ge=0)
    max_questions: int | None = Field(None, ge=1, le=200)
    min_questions: int = Field(4, ge=0, le=100)


class CodingSection(_Model):
    enabled: bool = False
    duration_minutes: int = Field(20, ge=5, le=120)
    languages: list[CodeLanguage] = Field(
        default_factory=lambda: [
            CodeLanguage.PYTHON,
            CodeLanguage.JAVASCRIPT,
            CodeLanguage.TYPESCRIPT,
            CodeLanguage.JAVA,
            CodeLanguage.GO,
            CodeLanguage.CPP,
        ]
    )
    difficulty: Difficulty = Difficulty.MEDIUM
    question_ids: list[str] = Field(default_factory=list, description="Pinned coding_questions ids (optional)")
    allow_generated: bool = False
    max_problems: int = Field(1, ge=1, le=5)
    hints_enabled: bool = True
    max_hints: int = Field(3, ge=0, le=3)
    hint3_may_reveal_solution: bool = False
    restrict_paste: bool = True
    large_paste_threshold_chars: int = Field(200, ge=20)
    proactive_comments: bool = True
    min_seconds_between_comments: int = Field(75, ge=20)
    start_after_minutes: float | None = Field(None, ge=0, description="Earliest start; None = engine decides")


class ResumeValidation(_Model):
    enabled: bool = True
    max_claims: int = Field(4, ge=0, le=20)
    min_priority: int = Field(2, ge=1, le=5, description="Only verify claims at or above this priority (1=low)")


class PassingGuidance(_Model):
    """Thresholds the recommendation engine uses. Assistive only."""

    strong_proceed_min_score: float = 80
    proceed_min_score: float = 65
    borderline_min_score: float = 50
    min_confidence: float = Field(0.6, ge=0, le=1)
    min_required_coverage: float = Field(0.75, ge=0, le=1, description="Share of required competencies with sufficient evidence")
    reviewer_notes: str = "Recommendations are advisory. A human reviewer owns the hiring decision."


class RoundConfig(_Model):
    round_type: RoundType
    name: str
    duration_minutes: int = Field(30, ge=5, le=240)
    max_duration_minutes: int = Field(40, ge=5, le=300)
    expected_difficulty: Difficulty = Difficulty.MEDIUM
    difficulty_distribution: DifficultyDistribution = Field(default_factory=DifficultyDistribution)
    competencies: list[RoundCompetency] = Field(default_factory=list)
    rubric: list[RubricCategory] = Field(default_factory=list)
    question_strategy: QuestionStrategy = Field(default_factory=QuestionStrategy)
    min_evidence_per_competency: int = Field(3, ge=1, le=20)
    confidence_threshold: float = Field(0.7, ge=0, le=1)
    stopping: StoppingConditions = Field(default_factory=StoppingConditions)
    coding: CodingSection = Field(default_factory=CodingSection)
    resume_validation: ResumeValidation = Field(default_factory=ResumeValidation)
    passing_guidance: PassingGuidance = Field(default_factory=PassingGuidance)
    instructions: str = Field(
        "Answer naturally, as you would with a human interviewer. Take a moment to think when you need it.",
        description="Shown to the candidate before the round starts",
    )
    interviewer_notes: str | None = Field(None, description="Private guidance for the AI interviewer")
    candidate_selectable: bool = False

    @field_validator("rubric")
    @classmethod
    def _rubric_sums(cls, value: list[RubricCategory]) -> list[RubricCategory]:
        if value:
            total = sum(c.weight for c in value)
            if abs(total - 100) > 0.5:
                raise ValueError(f"rubric weights must sum to 100 (got {total:g})")
        return value

    @model_validator(mode="after")
    def _durations(self) -> RoundConfig:
        if self.max_duration_minutes < self.duration_minutes:
            self.max_duration_minutes = self.duration_minutes
        return self

    def competency_keys(self) -> list[str]:
        return [c.key for c in self.competencies]

    def min_evidence_for(self, key: str) -> int:
        for c in self.competencies:
            if c.key == key and c.min_evidence:
                return c.min_evidence
        return self.min_evidence_per_competency
