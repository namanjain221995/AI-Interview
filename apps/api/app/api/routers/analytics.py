"""Organization analytics (``analytics:read``).

SUPER_ADMIN may pass ``?organization_id=``; everyone else is pinned to their
own organization. ``days`` sets the period (1-365, default 30); ``job_id``
and ``round_id`` narrow it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, require_permission, resolve_org_id
from app.core.rbac import Permission, Principal
from app.schemas.api.analytics import (
    AnalyticsFunnel,
    AnalyticsOverview,
    CompetencyAnalytics,
    CompletionAnalytics,
    HintUsageAnalytics,
    IntegrityAnalytics,
    ModelUsage,
    ScoreDistributions,
)
from app.schemas.api.common import ERROR_RESPONSES
from app.services.reports import analytics as service
from app.services.reports.analytics import Period, make_period

router = APIRouter(prefix="/analytics", tags=["analytics"], responses=ERROR_RESPONSES)

AnalyticsReader = Annotated[Principal, Depends(require_permission(Permission.ANALYTICS_READ))]


async def analytics_period(
    principal: AnalyticsReader,
    organization_id: Annotated[
        uuid.UUID | None, Query(description="SUPER_ADMIN only: target organization")
    ] = None,
    days: Annotated[int, Query(ge=1, le=365, description="Period length in days")] = 30,
    job_id: uuid.UUID | None = None,
    round_id: uuid.UUID | None = None,
) -> Period:
    return make_period(resolve_org_id(principal, organization_id), days, job_id, round_id)


PeriodDep = Annotated[Period, Depends(analytics_period)]


@router.get("/overview", response_model=AnalyticsOverview, summary="Dashboard KPIs")
async def get_overview(period: PeriodDep, db: DbSession) -> AnalyticsOverview:
    return AnalyticsOverview.model_validate(await service.overview(db, period))


@router.get("/funnel", response_model=AnalyticsFunnel, summary="Invited -> completed -> reviewed funnel")
async def get_funnel(period: PeriodDep, db: DbSession) -> AnalyticsFunnel:
    return AnalyticsFunnel.model_validate(await service.funnel(db, period))


@router.get(
    "/scores", response_model=ScoreDistributions, summary="Overall score distributions by job and round"
)
async def get_score_distributions(period: PeriodDep, db: DbSession) -> ScoreDistributions:
    return ScoreDistributions.model_validate(await service.score_distributions(db, period))


@router.get("/competencies", response_model=CompetencyAnalytics, summary="Competency averages")
async def get_competencies(period: PeriodDep, db: DbSession) -> CompetencyAnalytics:
    return CompetencyAnalytics.model_validate(await service.competency_averages(db, period))


@router.get("/completion", response_model=CompletionAnalytics, summary="Completion rates and durations")
async def get_completion(period: PeriodDep, db: DbSession) -> CompletionAnalytics:
    return CompletionAnalytics.model_validate(await service.completion(db, period))


@router.get(
    "/integrity", response_model=IntegrityAnalytics, summary="Integrity signal rates (observations only)"
)
async def get_integrity(period: PeriodDep, db: DbSession) -> IntegrityAnalytics:
    return IntegrityAnalytics.model_validate(await service.integrity(db, period))


@router.get("/hints", response_model=HintUsageAnalytics, summary="Coding hint usage")
async def get_hints(period: PeriodDep, db: DbSession) -> HintUsageAnalytics:
    return HintUsageAnalytics.model_validate(await service.hint_usage(db, period))


@router.get("/model-usage", response_model=ModelUsage, summary="Model calls, errors, latency and tokens")
async def get_model_usage(period: PeriodDep, db: DbSession) -> ModelUsage:
    return ModelUsage.model_validate(await service.model_usage(db, period))
