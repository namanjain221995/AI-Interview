"""Intake Interview staff admin API (``/api/v1/intake``; INTAKE_INTERVIEW.md 10.1, 10.3).

Config, organisation intake settings, single and batch create (drafts included), list with status buckets,
stats, update, send, cancel and link rotation. Review, evaluation, decisions and analytics live in
``intake_review.py`` (same prefix). Every query is scoped to the caller's organisation (other organisations'
ids answer 404); every state change is audited; invite links and access codes are returned exactly once.
``INTAKE_ENABLED=false`` answers 404 ``intake_disabled`` on every route.
"""

from __future__ import annotations

import functools
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission, require_role
from app.core.errors import ServiceUnavailable
from app.core.lifecycle import run_after_commit
from app.core.metrics import INTAKE_INVITATIONS
from app.core.ratelimit import enforce_rate_limit
from app.core.rbac import Permission, Principal
from app.db.models import Job, Round
from app.domain.enums import Role
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.deletion import IntakeDeletionOut, SessionDeletionCounts
from app.schemas.api.intake_admin import (
    IntakeBatchCreate,
    IntakeBatchResponse,
    IntakeCancelRequest,
    IntakeConfigOut,
    IntakeCreate,
    IntakeCreateResponse,
    IntakeJobOption,
    IntakeKitCardOut,
    IntakeKitDuration,
    IntakeKitInfo,
    IntakeKitPassageOut,
    IntakeKitStageOut,
    IntakeOrgSettingsIn,
    IntakeOrgSettingsOut,
    IntakeRegenerateLinkRequest,
    IntakeSendRequest,
    IntakeStatsOut,
    IntakeSummaryOut,
    IntakeUpdate,
    MinutesRange,
)
from app.schemas.intake import (
    INTAKE_CANDIDATE_STAGE_TITLES,
    DecisionBandValue,
    DecisionOutcome,
    IntakeBucket,
    TargetRole,
)
from app.services.data_deletion.intake import delete_intake
from app.services.intake import invitations as service
from app.services.intake.access import IntakeRoute, get_intake, require_intake_enabled
from app.services.intake.kit import IntakeKitError, load_intake_kit
from app.services.intake.listing import IntakeFilters, get_summary, intake_stats, list_intakes
from app.services.intake.org_settings import get_org_settings, save_org_settings, settings_out
from app.services.jobs.invitations import CLOSED_JOB_STATUSES, IssuedInvite

router = APIRouter(
    prefix="/intake",
    tags=["intake"],
    responses=ERROR_RESPONSES,
    route_class=IntakeRoute,
    dependencies=[Depends(require_intake_enabled)],
)

ConfigReader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
SettingsManager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
Creator = Annotated[
    Principal, Depends(require_permission(Permission.INTERVIEWS_INVITE, Permission.CANDIDATES_WRITE))
]
Inviter = Annotated[Principal, Depends(require_permission(Permission.INTERVIEWS_INVITE))]
Reader = Annotated[Principal, Depends(require_permission(Permission.SESSIONS_READ))]
Deleter = Annotated[
    Principal,
    Depends(require_role(Role.ADMIN, Role.SUPER_ADMIN, perms=(Permission.SESSIONS_READ,))),
]

BATCH_RATE_LIMIT = 10  # per minute per user
SortKey = Literal["-created_at", "created_at", "-completed_at", "-score", "score", "candidate"]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def _count_invitations(mode: str, count: int) -> None:
    if count:
        INTAKE_INVITATIONS.labels(mode=mode).inc(count)


def _count_after_commit(db: AsyncSession, mode: str, count: int) -> None:
    if count:
        run_after_commit(db, functools.partial(_count_invitations, mode, count))


def _create_response(summary: IntakeSummaryOut, issued: IssuedInvite | None) -> IntakeCreateResponse:
    return IntakeCreateResponse(
        intake=summary,
        invite_url=issued.invite_url if issued else None,
        access_code=issued.access_code if issued else None,
        expires_at=issued.interview.invite_expires_at if issued else None,
    )


# ======================================================================================= config


@router.get("/config", response_model=IntakeConfigOut, summary="Intake kit index, defaults and intake jobs")
async def intake_config(principal: ConfigReader, org_id: OrgScope, db: DbSession) -> IntakeConfigOut:
    try:
        kit = load_intake_kit()
    except IntakeKitError as exc:
        raise ServiceUnavailable(
            "The intake interview kit is not available. Please contact support.",
            code="intake_kit_unavailable",
        ) from exc
    org = await get_org_settings(db, org_id)
    rows = (
        await db.execute(
            select(Job.id, Job.title, Job.status, Round.id, Round.name)
            .join(Round, Round.job_id == Job.id)
            .where(
                Job.organization_id == org_id,
                Job.status.notin_(tuple(CLOSED_JOB_STATUSES)),
                Round.is_active.is_(True),
                Round.round_type == "intake",
            )
            .order_by(Job.title, Job.id, Round.sequence)
        )
    ).all()
    return IntakeConfigOut(
        kit=IntakeKitInfo(
            version=kit.version,
            sha256=kit.source.sha256,
            title=kit.source.title,
            duration=IntakeKitDuration(
                min=kit.duration.min_minutes, max=kit.duration.max_minutes, target=kit.duration.target_minutes
            ),
            stages=[
                IntakeKitStageOut(
                    key=stage.key,
                    number=stage.number,
                    title=stage.title,
                    candidate_title=INTAKE_CANDIDATE_STAGE_TITLES[stage.key],
                    minutes=MinutesRange(min=stage.minutes.min, max=stage.minutes.max),
                )
                for stage in kit.stages
            ],
            passages=[
                IntakeKitPassageOut(key=p.key, title=p.title, level=p.level, drills=list(p.drills))
                for p in kit.passages
            ],
            topic_cards=[
                IntakeKitCardOut(number=c.number, title=c.title, type=c.type, type_label=c.type_label)
                for c in kit.topic_cards
            ],
        ),
        defaults=org.defaults,
        work_authorization_default=org.work_authorization,
        jobs=[
            IntakeJobOption(
                job_id=job_id, title=title, status=job_status, round_id=round_id, round_name=round_name
            )
            for job_id, title, job_status, round_id, round_name in rows
        ],
    )


# ===================================================================================== settings


@router.get("/settings", response_model=IntakeOrgSettingsOut, summary="Organisation intake settings")
async def get_intake_settings(
    principal: ConfigReader, org_id: OrgScope, db: DbSession
) -> IntakeOrgSettingsOut:
    return await settings_out(db, await get_org_settings(db, org_id))


@router.put(
    "/settings", response_model=IntakeOrgSettingsOut, summary="Replace the organisation intake settings"
)
async def put_intake_settings(
    body: IntakeOrgSettingsIn, request: Request, principal: SettingsManager, org_id: OrgScope, db: DbSession
) -> IntakeOrgSettingsOut:
    saved, changed = await save_org_settings(db, org_id, body, user_id=principal.user_id)
    await audit(
        db,
        principal,
        "intake.settings.update",
        resource_type="intake_settings",
        resource_id=org_id,
        request=request,
        organization_id=org_id,
        details={
            "changed": changed,
            "approved_info_count": len(saved.approved_info),
            "work_authorization_enabled": saved.work_authorization.enabled,
        },
    )
    return await settings_out(db, saved)


# ======================================================================================== create


@router.post(
    "/interviews",
    response_model=IntakeCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an intake interview (draft or sent)",
)
async def create_intake_interview(
    body: IntakeCreate,
    request: Request,
    response: Response,
    principal: Creator,
    org_id: OrgScope,
    db: DbSession,
) -> IntakeCreateResponse:
    created = await service.create_intake(db, organization_id=org_id, user_id=principal.user_id, body=body)
    intake = created.intake
    await audit(
        db,
        principal,
        "intake.create",
        resource_type="intake",
        resource_id=intake.id,
        request=request,
        organization_id=org_id,
        details={
            "candidate_id": str(intake.candidate_id),
            "candidate_reused": created.candidate_reused,
            "job_id": str(intake.job_id),
            "round_id": str(intake.round_id) if intake.round_id else None,
            "interview_id": str(intake.interview_id) if intake.interview_id else None,
            "sent": created.issued is not None,
            "access_code_issued": bool(created.issued and created.issued.access_code),
        },
    )
    _count_after_commit(db, "single", 1 if created.issued else 0)
    _no_store(response)
    return _create_response(await get_summary(db, org_id, intake.id), created.issued)


@router.post(
    "/interviews/batch",
    response_model=IntakeBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create intake interviews for many candidates",
)
async def create_intake_batch(
    body: IntakeBatchCreate,
    request: Request,
    response: Response,
    principal: Creator,
    org_id: OrgScope,
    db: DbSession,
) -> IntakeBatchResponse:
    await enforce_rate_limit(
        "intake_batch_create", str(principal.user_id or principal.id), limit=BATCH_RATE_LIMIT, window_s=60
    )
    result = await service.create_batch(db, organization_id=org_id, user_id=principal.user_id, body=body)
    await audit(
        db,
        principal,
        "intake.batch_create",
        resource_type="intake_batch",
        resource_id=result.batch_id,
        request=request,
        organization_id=org_id,
        details={
            "batch_id": str(result.batch_id),
            "job_id": str(body.job_id),
            "rows": len(body.rows),
            "created": len(result.created),
            "failed": len(result.failed),
            "sent": result.sent,
            "failed_codes": sorted({f.code for f in result.failed}),
        },
    )
    _count_after_commit(db, "batch", result.sent)
    _no_store(response)
    return IntakeBatchResponse(
        batch_id=result.batch_id, created=[item for item, _row in result.created], failed=result.failed
    )


# ========================================================================================= list


def _filters(
    q: str | None,
    job_id: uuid.UUID | None,
    target_role: str | None,
    band: str | None,
    outcome: str | None,
    reviewer_id: uuid.UUID | None,
    batch_id: uuid.UUID | None,
    created_from: datetime | None,
    created_to: datetime | None,
    session_id: uuid.UUID | None = None,
) -> IntakeFilters:
    return IntakeFilters(
        q=q,
        job_id=job_id,
        target_role=target_role,
        band=band,
        outcome=outcome,
        reviewer_id=reviewer_id,
        batch_id=batch_id,
        created_from=created_from,
        created_to=created_to,
        session_id=session_id,
    )


@router.get("/interviews", response_model=Page[IntakeSummaryOut], summary="List intake interviews")
async def list_intake_interviews(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    bucket: IntakeBucket = "all",
    q: Annotated[str | None, Query(max_length=200, description="Candidate name or e-mail")] = None,
    job_id: uuid.UUID | None = None,
    target_role: TargetRole | None = None,
    band: DecisionBandValue | None = None,
    outcome: DecisionOutcome | None = None,
    reviewer_id: Annotated[uuid.UUID | None, Query(description="Assigned or deciding reviewer")] = None,
    batch_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    session_id: Annotated[
        uuid.UUID | None, Query(description="Exact lookup: the intake whose interview owns this session")
    ] = None,
    sort: SortKey = "-created_at",
) -> Page[IntakeSummaryOut]:
    filters = _filters(
        q, job_id, target_role, band, outcome, reviewer_id, batch_id, created_from, created_to, session_id
    )
    items, total = await list_intakes(
        db, org_id, filters, bucket=bucket, sort=sort, limit=page.limit, offset=page.offset
    )
    return Page[IntakeSummaryOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/stats", response_model=IntakeStatsOut, summary="Intake bucket counts and totals")
async def intake_stats_endpoint(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    job_id: uuid.UUID | None = None,
    target_role: TargetRole | None = None,
    band: DecisionBandValue | None = None,
    outcome: DecisionOutcome | None = None,
    reviewer_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> IntakeStatsOut:
    filters = _filters(q, job_id, target_role, band, outcome, reviewer_id, batch_id, created_from, created_to)
    return await intake_stats(db, org_id, filters)


# ================================================================================ update / actions


@router.patch(
    "/interviews/{intake_id}", response_model=IntakeSummaryOut, summary="Update an intake interview"
)
async def update_intake_interview(
    intake_id: uuid.UUID,
    body: IntakeUpdate,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> IntakeSummaryOut:
    intake = await get_intake(db, intake_id, org_id, lock=True)
    changed = await service.update_intake(db, intake, body, user_id=principal.user_id)
    if changed:
        await audit(
            db,
            principal,
            "intake.update",
            resource_type="intake",
            resource_id=intake.id,
            request=request,
            organization_id=org_id,
            details={"changed": changed, "status": intake.status},
        )
    return await get_summary(db, org_id, intake.id)


@router.post("/interviews/{intake_id}/send", response_model=IntakeCreateResponse, summary="Send a draft")
async def send_intake_interview(
    intake_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    body: IntakeSendRequest | None = None,
) -> IntakeCreateResponse:
    intake = await get_intake(db, intake_id, org_id, lock=True)
    issued = await service.send_draft(
        db, intake, invited_by_id=principal.user_id, expires_in_hours=body.expires_in_hours if body else None
    )
    await audit(
        db,
        principal,
        "intake.send",
        resource_type="intake",
        resource_id=intake.id,
        request=request,
        organization_id=org_id,
        details={
            "interview_id": str(issued.interview.id),
            "access_code_issued": issued.access_code is not None,
            "expires_at": issued.interview.invite_expires_at,
        },
    )
    _count_after_commit(db, "draft_sent", 1)
    _no_store(response)
    return _create_response(await get_summary(db, org_id, intake.id), issued)


@router.post(
    "/interviews/{intake_id}/cancel", response_model=IntakeSummaryOut, summary="Cancel an intake interview"
)
async def cancel_intake_interview(
    intake_id: uuid.UUID,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    body: IntakeCancelRequest | None = None,
) -> IntakeSummaryOut:
    intake = await get_intake(db, intake_id, org_id, lock=True)
    previous = await service.cancel_intake(db, intake, reason=body.reason if body else None)
    if previous is not None:
        await audit(
            db,
            principal,
            "intake.cancel",
            resource_type="intake",
            resource_id=intake.id,
            request=request,
            organization_id=org_id,
            details={
                "previous_status": previous,
                "interview_id": str(intake.interview_id) if intake.interview_id else None,
                "reason": body.reason if body else None,
            },
        )
    return await get_summary(db, org_id, intake.id)


@router.post(
    "/interviews/{intake_id}/regenerate-link",
    response_model=IntakeCreateResponse,
    summary="Issue a new invite link (shown once)",
)
async def regenerate_intake_link(
    intake_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    body: IntakeRegenerateLinkRequest | None = None,
) -> IntakeCreateResponse:
    intake = await get_intake(db, intake_id, org_id, lock=True)
    issued = await service.regenerate_intake_link(
        db, intake, expires_in_hours=body.expires_in_hours if body else None
    )
    await audit(
        db,
        principal,
        "intake.regenerate_link",
        resource_type="intake",
        resource_id=intake.id,
        request=request,
        organization_id=org_id,
        details={
            "interview_id": str(issued.interview.id),
            "access_code_issued": issued.access_code is not None,
            "expires_at": issued.interview.invite_expires_at,
        },
    )
    _no_store(response)
    return _create_response(await get_summary(db, org_id, intake.id), issued)


@router.delete(
    "/interviews/{intake_id}",
    response_model=IntakeDeletionOut,
    summary="Permanently delete an intake interview and its sessions, results and files (ADMIN)",
    description=(
        "Deletes the intake interview (drafts too), its invitation, every session with its stage and "
        "activity results, captures, evaluations, scores, decisions, reports (and artifacts), recordings, "
        "integrity events and stored files. The candidate and their résumés stay. 409 `intake_busy` while "
        "a session is live or still being evaluated. Audited with counts only."
    ),
)
async def delete_intake_interview(
    intake_id: uuid.UUID, request: Request, principal: Deleter, org_id: OrgScope, db: DbSession
) -> IntakeDeletionOut:
    intake = await get_intake(db, intake_id, org_id, lock=True)
    candidate_id = intake.candidate_id
    summary = await delete_intake(db, intake)
    counts = summary.as_dict()
    counts.pop("intake_id")
    await audit(
        db,
        principal,
        "intake.delete",
        resource_type="intake",
        resource_id=intake_id,
        request=request,
        organization_id=org_id,
        details={**counts, "candidate_id": str(candidate_id)},
    )
    return IntakeDeletionOut(
        intake_id=intake_id,
        candidate_id=candidate_id,
        deleted_at=datetime.now(UTC),
        was_draft=summary.was_draft,
        interviews=summary.interviews,
        evaluations=summary.evaluations,
        decisions=summary.decisions,
        auth_sessions_revoked=summary.auth_sessions_revoked,
        sessions=SessionDeletionCounts.model_validate(summary.sessions),
    )
