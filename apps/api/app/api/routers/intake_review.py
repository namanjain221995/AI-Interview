"""Intake Interview staff review API (``/api/v1/intake``; INTAKE_INTERVIEW.md 10.2).

Detail, timeline, evaluation, re-evaluation, human decisions and intake analytics. Admin routes (create, list,
stats, settings) live in ``intake.py`` under the same prefix. Every query is scoped to the caller's organisation
(SUPER_ADMIN may pass ``?organization_id=``); another organisation's id answers 404 ``intake_not_found``.
``INTAKE_ENABLED=false`` answers 404 ``intake_disabled`` on every route. State changes and sensitive reads are
audited (``intake.view``, ``transcript.view``, ``intake.evaluate``, ``intake.decision.create`` + ``review.create``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.audit import audit
from app.core.deps import DbSession, resolve_org_id
from app.core.deps import require_permission as permission
from app.core.rbac import Permission, Principal
from app.db.models import User
from app.db.models.intake import IntakeInterview
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.intake_review import (
    IntakeAnalyticsOut,
    IntakeDecisionIn,
    IntakeDecisionListOut,
    IntakeDecisionOut,
    IntakeDetailOut,
    IntakeEvaluateResponse,
    IntakeEvaluationOut,
    IntakeTimelineOut,
)
from app.schemas.intake import IntakeBucket, TargetRole
from app.services.intake import analytics as analytics_service
from app.services.intake import review_views as views
from app.services.intake.access import (
    IntakeRoute,
    get_intake,
    require_intake_enabled,
    require_intake_entitled,
)
from app.services.intake.decisions import create_decision
from app.services.intake.pipeline import decision_out, decisions_out

router = APIRouter(
    prefix="/intake",
    tags=["intake"],
    responses=ERROR_RESPONSES,
    route_class=IntakeRoute,
    dependencies=[Depends(require_intake_enabled), Depends(require_intake_entitled)],
)

Reader = Annotated[Principal, Depends(permission(Permission.SESSIONS_READ))]
TimelineReader = Annotated[
    Principal, Depends(permission(Permission.SESSIONS_READ, Permission.TRANSCRIPTS_READ))
]
EvaluationReader = Annotated[
    Principal, Depends(permission(Permission.SESSIONS_READ, Permission.REPORTS_READ))
]
Reviewer = Annotated[Principal, Depends(permission(Permission.SESSIONS_READ, Permission.REVIEWS_WRITE))]
AnalyticsReader = Annotated[Principal, Depends(permission(Permission.ANALYTICS_READ))]
OrganizationParam = Annotated[uuid.UUID | None, Query(description="SUPER_ADMIN only: target organization")]


async def _intake(
    db: DbSession, principal: Principal, intake_id: uuid.UUID, organization_id: uuid.UUID | None
) -> IntakeInterview:
    return await get_intake(db, intake_id, resolve_org_id(principal, organization_id))


@router.get(
    "/interviews/{intake_id}",
    response_model=IntakeDetailOut,
    summary="Intake detail: summary, onboarding, stages, activities, evaluation, review items, decisions (audited)",
)
async def get_intake_detail(
    intake_id: uuid.UUID,
    principal: Reader,
    db: DbSession,
    request: Request,
    organization_id: OrganizationParam = None,
) -> IntakeDetailOut:
    intake = await _intake(db, principal, intake_id, organization_id)
    payload, eligibility_included = await views.detail_view(db, intake)
    await audit(
        db,
        principal,
        "intake.view",
        resource_type="intake_interview",
        resource_id=str(intake.id),
        organization_id=intake.organization_id,
        request=request,
        details={"eligibility_included": eligibility_included},
    )
    return IntakeDetailOut.model_validate(payload)


@router.get(
    "/interviews/{intake_id}/timeline",
    response_model=IntakeTimelineOut,
    summary="Stages -> items -> questions and answers with timestamps and audio ids (audited)",
)
async def get_intake_timeline(
    intake_id: uuid.UUID,
    principal: TimelineReader,
    db: DbSession,
    request: Request,
    organization_id: OrganizationParam = None,
) -> IntakeTimelineOut:
    intake = await _intake(db, principal, intake_id, organization_id)
    payload = await views.timeline_view(db, intake)
    if payload.get("session_id"):
        await audit(
            db,
            principal,
            "transcript.view",
            resource_type="session",
            resource_id=str(payload["session_id"]),
            organization_id=intake.organization_id,
            request=request,
            details={"view": "intake_timeline", "intake_id": str(intake.id)},
        )
    return IntakeTimelineOut.model_validate(payload)


@router.get(
    "/interviews/{intake_id}/evaluation",
    response_model=IntakeEvaluationOut,
    summary="The newest ready evaluation (or a given version) with the seven competency scores",
)
async def get_intake_evaluation(
    intake_id: uuid.UUID,
    principal: EvaluationReader,
    db: DbSession,
    version: Annotated[int | None, Query(ge=1)] = None,
    organization_id: OrganizationParam = None,
) -> IntakeEvaluationOut:
    intake = await _intake(db, principal, intake_id, organization_id)
    session = await views.session_for_intake(db, intake)
    evaluation = await views.evaluation_for(db, session, version=version)
    return IntakeEvaluationOut.model_validate(await views.evaluation_view(db, evaluation))


@router.post(
    "/interviews/{intake_id}/evaluate",
    response_model=IntakeEvaluateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a new evaluation version",
)
async def reevaluate_intake(
    intake_id: uuid.UUID,
    principal: Reviewer,
    db: DbSession,
    request: Request,
    organization_id: OrganizationParam = None,
) -> IntakeEvaluateResponse:
    intake = await _intake(db, principal, intake_id, organization_id)
    result = await views.request_reevaluation(db, principal, intake, request=request)
    return IntakeEvaluateResponse(**result)


@router.get(
    "/interviews/{intake_id}/decisions",
    response_model=IntakeDecisionListOut,
    summary="Human decisions, newest first",
)
async def list_intake_decisions(
    intake_id: uuid.UUID,
    principal: Reader,
    db: DbSession,
    organization_id: OrganizationParam = None,
) -> IntakeDecisionListOut:
    intake = await _intake(db, principal, intake_id, organization_id)
    items = await decisions_out(db, intake.id)
    return IntakeDecisionListOut(items=[IntakeDecisionOut.model_validate(i) for i in items], total=len(items))


@router.post(
    "/interviews/{intake_id}/decisions",
    response_model=IntakeDecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a human decision (mirrored into review decisions; audited)",
)
async def create_intake_decision(
    intake_id: uuid.UUID,
    body: IntakeDecisionIn,
    principal: Reviewer,
    db: DbSession,
    request: Request,
    organization_id: OrganizationParam = None,
) -> IntakeDecisionOut:
    intake = await _intake(db, principal, intake_id, organization_id)
    session = await views.session_for_intake(db, intake)
    decision = await create_decision(
        db,
        principal,
        intake=intake,
        session=session,
        outcome=body.outcome,
        rationale=body.rationale,
        review_items=[item.model_dump() for item in body.review_items],
        competency_notes=body.competency_notes,
        requested_evidence=body.requested_evidence,
        request=request,
    )
    await db.refresh(decision)
    reviewer = await db.get(User, decision.reviewer_id)
    return IntakeDecisionOut.model_validate(decision_out(decision, reviewer))


@router.get(
    "/analytics", response_model=IntakeAnalyticsOut, summary="Intake analytics (funnel, bands, decisions)"
)
async def get_intake_analytics(
    principal: AnalyticsReader,
    db: DbSession,
    organization_id: OrganizationParam = None,
    days: Annotated[int, Query(ge=1, le=365, description="Period length in days")] = 30,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    job_id: uuid.UUID | None = None,
    target_role: TargetRole | None = None,
    reviewer_id: uuid.UUID | None = None,
    bucket: IntakeBucket = "all",
) -> IntakeAnalyticsOut:
    period = analytics_service.make_period(
        resolve_org_id(principal, organization_id),
        days=days,
        created_from=created_from,
        created_to=created_to,
        job_id=job_id,
        target_role=target_role,
        reviewer_id=reviewer_id,
        bucket=bucket,
    )
    return IntakeAnalyticsOut.model_validate(await analytics_service.intake_analytics(db, period))
