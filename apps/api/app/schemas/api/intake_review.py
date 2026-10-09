"""Staff review DTOs of the intake module (``/api/v1/intake/...``, design contract sections 10.2 and 15.13).

Decimals (scores, weights, totals) serialise as strings (:data:`app.schemas.intake.DecimalStr`). Documents written
by other tracks (invite settings, onboarding, résumé review, stage items/extra, activity phases) are passed through
as JSON objects so a reader never fails on a field it does not know; the shapes are documented in
``app/schemas/intake.py``. The MQ1 block (``eligibility``) is staff-only data: it is never part of a report, export,
analytics payload or model prompt.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api.intake_admin import IntakeSummaryOut
from app.schemas.intake import (
    ActivityKind,
    DecimalStr,
    DecisionBandValue,
    DecisionOutcome,
    EvaluationReason,
    EvaluationStatus,
    EvidenceRef,
    EvidenceSufficiency,
    ReviewItem,
    ReviewItemResolution,
    ReviewItemResolutionValue,
    TrainingPlan,
)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ============================================================================================== evaluation


class CompetencyScoreOut(_Out):
    key: str
    label: str
    kit_label: str
    weight: DecimalStr
    status: Literal["scored", "insufficient"]
    score: DecimalStr | None = None
    weighted: DecimalStr | None = None
    evaluator: str
    reasoning: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    anchors: dict[str, str] = Field(default_factory=dict)
    sub_scores: dict[str, Any] = Field(default_factory=dict)


class ValidatorIssueOut(_Out):
    code: str
    competency: str | None = None
    detail: str = ""


class ReviewItemOut(ReviewItem):
    """A review item plus the newest decision's resolution of it (``null`` when unresolved)."""

    resolution: ReviewItemResolution | None = None


class IntakeEvaluationOut(_Out):
    id: uuid.UUID
    version: int
    status: EvaluationStatus
    reason: EvaluationReason | str
    created_at: datetime
    finished_at: datetime | None = None
    kit_version: str
    scoring_version: str
    weighted_total: DecimalStr | None = None
    partial_total: DecimalStr | None = None
    partial_label: str | None = None
    normalized_100: DecimalStr | None = None
    normalized_label: str
    band: DecisionBandValue | None = None
    band_label: str | None = None
    evidence_sufficiency: EvidenceSufficiency | None = None
    competencies: list[CompetencyScoreOut] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict, description="Evaluation summary (contract 8.6)")
    validator_issues: list[ValidatorIssueOut] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    training_plan: TrainingPlan | None = None
    error: str | None = None


# ============================================================================================== decisions


class ReviewerOut(_Out):
    id: uuid.UUID
    full_name: str
    email: str | None = None


class IntakeDecisionOut(_Out):
    id: uuid.UUID
    outcome: DecisionOutcome
    outcome_label: str
    rationale: str
    reviewer: ReviewerOut
    ai_band_at_decision: DecisionBandValue | None = None
    ai_total_at_decision: DecimalStr | None = None
    overrides_ai: bool = False
    review_items_resolution: list[dict[str, Any]] = Field(default_factory=list)
    competency_notes: dict[str, str] = Field(default_factory=dict)
    requested_evidence: list[str] = Field(default_factory=list)
    review_decision_id: uuid.UUID
    evaluation_id: uuid.UUID | None = None
    created_at: datetime


class IntakeDecisionListOut(_Out):
    items: list[IntakeDecisionOut]
    total: int


class ReviewItemResolutionIn(_In):
    item_id: str = Field(..., max_length=20)
    resolution: ReviewItemResolutionValue
    note: str | None = Field(None, max_length=1000)


class IntakeDecisionIn(_In):
    """``rationale`` (10-5000 characters) and ``requested_evidence`` (for ``request_evidence``) are checked by the
    service so the errors carry the contract codes ``rationale_required`` / ``requested_evidence_required``."""

    outcome: DecisionOutcome
    rationale: str = Field("", max_length=20000)
    review_items: list[ReviewItemResolutionIn] = Field(default_factory=list, max_length=100)
    competency_notes: dict[str, str] = Field(default_factory=dict)
    requested_evidence: list[str] = Field(default_factory=list, max_length=50)


class IntakeEvaluateResponse(_Out):
    evaluation_version: int
    job_id: uuid.UUID
    status: Literal["queued"] = "queued"
    message: str = "Evaluation queued. A new version appears when it is ready."


# ============================================================================================== detail


class EligibilityOut(_Out):
    """MQ1 answers for human review only (never sent to a model, never scored, never in reports)."""

    enabled: bool
    country: str | None = None
    ask_sponsorship: bool = True
    authorized: bool | None = None
    needs_sponsorship: bool | None = None
    answered_at: datetime | None = None


class ResumeRefOut(_Out):
    id: uuid.UUID
    original_filename: str
    status: str


class StageOut(_Out):
    key: str
    number: int
    title: str
    status: str
    started_s: float | None = None
    ended_s: float | None = None
    budget_s: float | None = None
    items: list[dict[str, Any]] = Field(default_factory=list, description="StageItemResult[] (contract 15.5)")
    extra: dict[str, Any] = Field(default_factory=dict)


class ActivityMaterialOut(_Out):
    key: str
    title: str
    level: str | None = None
    type: str | None = None
    paragraphs: list[str] | None = None
    bullets: list[str] | None = None
    instruction: str | None = None


class ActivityCaptureOut(_Out):
    started_ms: int | None = None
    ended_ms: int | None = None
    voiced_ms: int | None = None
    end_reason: str | None = None
    typed: bool = False
    pauses_count: int = 0
    segments_count: int = 0


class ActivityTranscriptOut(_Out):
    final: str | None = None
    live: str | None = None
    status: str
    source: str | None = None


class ActivityOut(_Out):
    id: uuid.UUID
    kind: ActivityKind
    stage_key: str
    status: str
    attempt: int
    material: ActivityMaterialOut
    phases: list[dict[str, Any]] = Field(default_factory=list)
    reveal: dict[str, Any] = Field(default_factory=dict)
    capture: ActivityCaptureOut
    transcript: ActivityTranscriptOut
    question_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None
    audio_media_id: uuid.UUID | None = None


class ReportRefOut(_Out):
    version: int
    status: str
    artifact_media_id: uuid.UUID | None = None
    generated_at: datetime | None = None


class IntegrityCountsOut(_Out):
    flags: int = 0
    unreviewed: int = 0


class RecordingsOut(_Out):
    parts: int = 0
    ready: bool = False


class IntakeDetailOut(IntakeSummaryOut):
    settings: dict[str, Any] = Field(default_factory=dict, description="IntakeInviteSettings (contract 15.2)")
    onboarding: dict[str, Any] | None = Field(None, description="IntakeOnboarding (contract 15.3)")
    eligibility: EligibilityOut | None = None
    resume_review: dict[str, Any] | None = Field(None, description="ResumeReviewRecord (contract 15.4)")
    resume: ResumeRefOut | None = None
    stages: list[StageOut] = Field(default_factory=list)
    activities: list[ActivityOut] = Field(default_factory=list)
    evaluation: IntakeEvaluationOut | None = None  # type: ignore[assignment]  # full evaluation (newest ready)
    review_items: list[ReviewItemOut] = Field(default_factory=list)
    decisions: list[IntakeDecisionOut] = Field(default_factory=list)
    report: ReportRefOut | None = None
    integrity: IntegrityCountsOut = Field(default_factory=IntegrityCountsOut)
    recordings: RecordingsOut = Field(default_factory=RecordingsOut)
    evaluation_ref: dict[str, Any] | None = Field(
        None, description="The summary's evaluation reference (newest evaluation of any status)"
    )


# ============================================================================================== timeline


class TimelineAnswerOut(_Out):
    answer_id: uuid.UUID
    text: str = ""
    started_ms: int | None = None
    ended_ms: int | None = None
    duration_ms: int | None = None
    typed: bool = False
    audio_media_id: uuid.UUID | None = None


class TimelineExchangeOut(_Out):
    question_id: uuid.UUID
    text: str
    asked_ms: int
    template: bool = False
    item_kind: str
    answer: TimelineAnswerOut | None = None


class TimelineItemOut(_Out):
    item_key: str
    title: str
    mandatory: bool = False
    status: str
    skip_reason: str | None = None
    exchanges: list[TimelineExchangeOut] = Field(default_factory=list)


class TimelineStageOut(_Out):
    key: str
    number: int
    title: str
    status: str
    items: list[TimelineItemOut] = Field(default_factory=list)


class IntakeTimelineOut(_Out):
    intake_id: uuid.UUID
    session_id: uuid.UUID | None = None
    stages: list[TimelineStageOut] = Field(default_factory=list)


# ============================================================================================== analytics


class AnalyticsPeriodOut(_Out):
    since: datetime
    until: datetime
    days: int
    job_id: uuid.UUID | None = None
    target_role: str | None = None
    reviewer_id: uuid.UUID | None = None


class AnalyticsFunnelOut(_Out):
    invitations: int = 0
    onboarded: int = 0
    started: int = 0
    completed: int = 0
    incomplete: int = 0
    evaluated: int = 0
    decided: int = 0


class StageCompletionOut(_Out):
    stage: str
    title: str
    completed: int = 0
    incomplete: int = 0
    rate: float | None = None


class BandCountOut(_Out):
    band: DecisionBandValue
    label: str
    count: int = 0


class OutcomeCountOut(_Out):
    outcome: DecisionOutcome
    label: str
    count: int = 0


class OverridesOut(_Out):
    count: int = 0
    rate: float | None = None


class CompetencyAverageOut(_Out):
    key: str
    label: str
    average: DecimalStr | None = None
    scored: int = 0
    insufficient: int = 0


class KeyCountOut(_Out):
    key: str
    label: str
    count: int = 0


class TrainingNeedOut(_Out):
    module: str
    label: str
    count: int = 0


class ReviewerCountOut(_Out):
    id: uuid.UUID
    full_name: str
    decisions: int = 0


class TargetRoleOut(_Out):
    target_role: str
    label: str | None = None
    count: int = 0
    average_weighted_total: DecimalStr | None = None


class IntakeAnalyticsOut(_Out):
    period: AnalyticsPeriodOut
    funnel: AnalyticsFunnelOut
    average_weighted_total: DecimalStr | None = None
    average_duration_s: float | None = None
    stage_completion: list[StageCompletionOut] = Field(default_factory=list)
    bands: list[BandCountOut] = Field(default_factory=list)
    decisions: list[OutcomeCountOut] = Field(default_factory=list)
    overrides: OverridesOut = Field(default_factory=OverridesOut)
    competency_averages: list[CompetencyAverageOut] = Field(default_factory=list)
    technical_weaknesses: list[KeyCountOut] = Field(default_factory=list)
    training_needs: list[TrainingNeedOut] = Field(default_factory=list)
    reviewers: list[ReviewerCountOut] = Field(default_factory=list)
    by_target_role: list[TargetRoleOut] = Field(default_factory=list)


__all__ = [
    "ActivityOut",
    "CompetencyScoreOut",
    "EligibilityOut",
    "IntakeAnalyticsOut",
    "IntakeDecisionIn",
    "IntakeDecisionListOut",
    "IntakeDecisionOut",
    "IntakeDetailOut",
    "IntakeEvaluateResponse",
    "IntakeEvaluationOut",
    "IntakeTimelineOut",
    "ReviewItemOut",
    "StageOut",
]
