"""Interview invitations. The invite link and access code are returned exactly
once (create / regenerate-link); list and detail responses never contain
secrets. Every state change is audited."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission
from app.core.rbac import Permission, Principal
from app.db.models import Interview
from app.domain.enums import InterviewStatus
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.interviews import (
    CancelRequest,
    InterviewCreate,
    InterviewCreateResponse,
    InterviewOut,
    RegenerateLinkRequest,
)
from app.services.jobs.invitations import (
    cancel_invitation,
    create_invitation,
    get_interview,
    interview_list_query,
    regenerate_link,
    serialize_interviews,
)

router = APIRouter(prefix="/interviews", tags=["interviews"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_READ))]
Inviter = Annotated[Principal, Depends(require_permission(Permission.INTERVIEWS_INVITE))]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post(
    "",
    response_model=InterviewCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a candidate",
)
async def create_interview(
    body: InterviewCreate,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> InterviewCreateResponse:
    issued = await create_invitation(db, organization_id=org_id, invited_by_id=principal.user_id, body=body)
    interview = issued.interview
    await audit(
        db,
        principal,
        "interview.create",
        resource_type="interview",
        resource_id=interview.id,
        request=request,
        organization_id=org_id,
        details={
            "candidate_id": str(interview.candidate_id),
            "job_id": str(interview.job_id),
            "round_id": str(interview.round_id) if interview.round_id else None,
            "allow_round_selection": interview.allow_round_selection,
            "requires_access_code": issued.access_code is not None,
            "expires_at": interview.invite_expires_at,
            "policy_version": interview.policy_snapshot.get("policy_version"),
        },
    )
    _no_store(response)
    [out] = await serialize_interviews(db, [interview])
    return InterviewCreateResponse(
        interview=out,
        invite_url=issued.invite_url,
        access_code=issued.access_code,
        expires_at=interview.invite_expires_at,
    )


@router.get("", response_model=Page[InterviewOut], summary="List interviews")
async def list_interviews(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    status_filter: Annotated[InterviewStatus | None, Query(alias="status")] = None,
    job_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
) -> Page[InterviewOut]:
    stmt = interview_list_query(org_id, status=status_filter, job_id=job_id, candidate_id=candidate_id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(page.apply(stmt.order_by(Interview.created_at.desc(), Interview.id)))).all()
    return Page[InterviewOut](
        items=await serialize_interviews(db, rows), total=total, limit=page.limit, offset=page.offset
    )


@router.get("/{interview_id}", response_model=InterviewOut, summary="Get an interview")
async def get_interview_endpoint(
    interview_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> InterviewOut:
    [out] = await serialize_interviews(db, [await get_interview(db, interview_id, org_id)])
    return out


@router.post("/{interview_id}/cancel", response_model=InterviewOut, summary="Cancel an interview invitation")
async def cancel_interview(
    interview_id: uuid.UUID,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    body: CancelRequest | None = None,
) -> InterviewOut:
    interview = await get_interview(db, interview_id, org_id)
    previous = interview.status
    revoked = await cancel_invitation(db, interview, reason=body.reason if body else None)
    if previous != interview.status:
        await audit(
            db,
            principal,
            "interview.cancel",
            resource_type="interview",
            resource_id=interview.id,
            request=request,
            organization_id=org_id,
            details={"previous_status": previous, "sessions_revoked": revoked},
        )
    await db.refresh(interview)
    [out] = await serialize_interviews(db, [interview])
    return out


@router.post(
    "/{interview_id}/regenerate-link",
    response_model=InterviewCreateResponse,
    summary="Rotate the invite link",
)
async def regenerate_interview_link(
    interview_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    body: RegenerateLinkRequest | None = None,
) -> InterviewCreateResponse:
    interview = await get_interview(db, interview_id, org_id)
    options = body or RegenerateLinkRequest()
    issued = await regenerate_link(
        db,
        interview,
        expires_in_hours=options.expires_in_hours,
        rotate_access_code=options.rotate_access_code,
    )
    await audit(
        db,
        principal,
        "interview.regenerate_link",
        resource_type="interview",
        resource_id=interview.id,
        request=request,
        organization_id=org_id,
        details={
            "access_code_rotated": issued.access_code is not None,
            "expires_at": interview.invite_expires_at,
        },
    )
    _no_store(response)
    await db.refresh(interview)
    [out] = await serialize_interviews(db, [interview])
    return InterviewCreateResponse(
        interview=out,
        invite_url=issued.invite_url,
        access_code=issued.access_code,
        expires_at=interview.invite_expires_at,
    )
