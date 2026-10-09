"""Analytics DTOs (``/api/v1/analytics``). All figures are organization-scoped."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalyticsPeriod(BaseModel):
    days: int
    since: datetime
    until: datetime
    job_id: str | None = None
    round_id: str | None = None


class AnalyticsOverview(BaseModel):
    period: AnalyticsPeriod
    period_days: int
    interviews_invited: int
    interviews_completed: int
    active_interviews: int = Field(..., description="Invited or in progress, not expired")
    sessions_live: int
    sessions_started: int
    sessions_completed: int
    completed_this_week: int
    awaiting_review: int
    average_overall_score: float | None
    average_confidence: float | None
    completion_rate: float | None = Field(None, description="Completed sessions / started sessions")
    integrity_unreviewed: int
    integrity_events: int
    average_duration_seconds: float | None
    recommendation_distribution: dict[str, int]
    review_decision_distribution: dict[str, int]
    ai_override_rate: float | None = Field(None, description="Share of human decisions that overrode the AI")
    sessions_by_day: list[dict[str, Any]]


class FunnelStage(BaseModel):
    stage: str
    label: str
    count: int
    rate_from_previous: float | None = None
    rate_from_start: float | None = None


class AnalyticsFunnel(BaseModel):
    period: AnalyticsPeriod
    stages: list[FunnelStage]


class ScoreDistributionGroup(BaseModel):
    key: str
    label: str
    count: int
    mean: float | None
    median: float | None
    min: float | None
    max: float | None
    buckets: list[int] = Field(..., description="Counts per 10-point bucket: 0-9, 10-19, ..., 90-100")


class ScoreDistributions(BaseModel):
    period: AnalyticsPeriod
    bucket_labels: list[str]
    overall: ScoreDistributionGroup
    by_job: list[ScoreDistributionGroup]
    by_round: list[ScoreDistributionGroup]


class CompetencyAnalyticsItem(BaseModel):
    competency_key: str
    name: str | None = None
    # None when no session produced evidence for the competency.
    average_score: float | None = None
    average_confidence: float | None = None
    sessions: int
    evidence_count: int
    sufficient_rate: float


class CompetencyAnalytics(BaseModel):
    period: AnalyticsPeriod
    items: list[CompetencyAnalyticsItem]


class CompletionGroup(BaseModel):
    key: str
    label: str
    started: int
    completed: int
    completion_rate: float | None
    average_duration_seconds: float | None
    median_duration_seconds: float | None
    p90_duration_seconds: float | None


class CompletionAnalytics(BaseModel):
    period: AnalyticsPeriod
    overall: CompletionGroup
    by_job: list[CompletionGroup]
    by_round: list[CompletionGroup]
    end_reasons: dict[str, int]
    status_counts: dict[str, int]


class IntegrityAnalytics(BaseModel):
    period: AnalyticsPeriod
    note: str
    sessions: int
    sessions_with_events: int
    sessions_with_flags: int
    flag_rate: float | None
    events_total: int
    events_per_session: float | None
    by_type: dict[str, int]
    by_severity: dict[str, int]
    by_source: dict[str, int]
    review_status: dict[str, int]


class HintUsageAnalytics(BaseModel):
    period: AnalyticsPeriod
    coding_sessions: int
    sessions_with_hints: int
    hint_rate: float | None
    average_hints: float | None
    distribution: dict[str, int]
    by_level: dict[str, int]
    by_problem: list[dict[str, Any]]


class ModelUsageItem(BaseModel):
    task: str
    endpoint: str
    calls: int
    errors: int
    error_rate: float | None
    fallbacks: int
    repaired: int
    p50_ms: float | None
    p95_ms: float | None
    avg_ms: float | None
    avg_ttft_ms: float | None
    prompt_tokens: int
    completion_tokens: int


class ModelUsage(BaseModel):
    period: AnalyticsPeriod
    items: list[ModelUsageItem]
    totals: dict[str, Any]
