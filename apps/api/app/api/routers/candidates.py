"""Candidates: CRUD, search, latest profile and interviews, archive (soft
delete) / restore, and permanent data-subject deletion (ADMIN)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission, require_role
from app.core.errors import NotFound
from app.core.rbac import Permission, Principal
from app.db.models import Candidate, CandidateProfile, CandidateResume, Interview
from app.domain.enums import InterviewStatus, ResumeStatus, Role
from app.schemas.api.candidates import (
    CandidateCreate,
    CandidateDeletionOut,
    CandidateDeletionPreviewOut,
    CandidateOut,
    CandidateProfileOut,
    CandidateUpdate,
)
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.interviews import InterviewOut
from app.services.candidates.erasure import erase_candidate, erasure_preview
from app.services.candidates.service import (
    ArchivedFilter,
    archive_candidate,
    candidate_extras,
    candidate_list_query,
    create_candidate,
    get_candidate,
    restore_candidate,
    update_candidate,
)
from app.services.jobs.invitations import serialize_interviews
from app.services.resume.service import get_resume_service

router = APIRouter(tags=["candidates"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_WRITE))]
Eraser = Annotated[
    Principal,
    Depends(require_role(Role.ADMIN, Role.SUPER_ADMIN, perms=(Permission.CANDIDATES_WRITE,))),
]


async def _out(db: DbSession, candidate: Candidate) -> CandidateOut:
    extras = (await candidate_extras(db, [candidate.id]))[candidate.id]
    return CandidateOut.from_row(candidate, extras)


@router.get("/candidates", response_model=Page[CandidateOut], summary="List and search candidates")
async def list_candidates(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    q: Annotated[str | None, Query(max_length=100, description="Name, e-mail or skill")] = None,
    job_id: uuid.UUID | None = None,
    status_filter: Annotated[
        InterviewStatus | None, Query(alias="status", description="Has an interview in this status")
    ] = None,
    resume_status: ResumeStatus | None = None,
    skill: Annotated[str | None, Query(max_length=80)] = None,
    include_archived: Annotated[bool, Query(description="Also list archived candidates")] = False,
    archived_only: Annotated[bool, Query(description="List only archived candidates")] = False,
) -> Page[CandidateOut]:
    archived: ArchivedFilter = "only" if archived_only else "include" if include_archived else "exclude"
    stmt = candidate_list_query(
        org_id,
        q=q,
        job_id=job_id,
        status=status_filter,
        resume_status=resume_status,
        skill=skill,
        archived=archived,
    )
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(page.apply(stmt.order_by(Candidate.created_at.desc(), Candidate.id)))).all()
    extras = await candidate_extras(db, [r.id for r in rows])
    return Page[CandidateOut](
        items=[CandidateOut.from_row(r, extras.get(r.id)) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/candidates",
    response_model=CandidateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a candidate",
)
async def create_candidate_endpoint(
    body: CandidateCreate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> CandidateOut:
    candidate = await create_candidate(db, organization_id=org_id, created_by_id=principal.user_id, body=body)
    await audit(
        db,
        principal,
        "candidate.create",
        resource_type="candidate",
        resource_id=candidate.id,
        request=request,
        organization_id=org_id,
    )
    return await _out(db, candidate)


@router.get("/candidates/{candidate_id}", response_model=CandidateOut, summary="Get a candidate")
async def get_candidate_endpoint(
    candidate_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> CandidateOut:
    return await _out(db, await get_candidate(db, candidate_id, org_id))


@router.patch("/candidates/{candidate_id}", response_model=CandidateOut, summary="Update a candidate")
async def update_candidate_endpoint(
    candidate_id: uuid.UUID,
    body: CandidateUpdate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> CandidateOut:
    candidate = await get_candidate(db, candidate_id, org_id)
    changed = await update_candidate(db, candidate, body)
    if changed:
        await audit(
            db,
            principal,
            "candidate.update",
            resource_type="candidate",
            resource_id=candidate.id,
            request=request,
            organization_id=org_id,
            details={"changes": changed},
        )
    await db.refresh(candidate)
    return await _out(db, candidate)


@router.post(
    "/candidates/{candidate_id}/archive",
    response_model=CandidateOut,
    summary="Archive a candidate (soft delete; cancels open invitations)",
)
async def archive_candidate_endpoint(
    candidate_id: uuid.UUID, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> CandidateOut:
    candidate = await get_candidate(db, candidate_id, org_id, for_update=True)
    outcome = await archive_candidate(db, candidate)
    if outcome.changed:
        for interview_id in outcome.cancelled_interview_ids:
            await audit(
                db,
                principal,
                "interview.cancel",
                resource_type="interview",
                resource_id=interview_id,
                request=request,
                organization_id=org_id,
                details={"previous_status": InterviewStatus.INVITED.value, "reason": "candidate_archived"},
            )
        await audit(
            db,
            principal,
            "candidate.archive",
            resource_type="candidate",
            resource_id=candidate.id,
            request=request,
            organization_id=org_id,
            details={"cancelled_interviews": [str(i) for i in outcome.cancelled_interview_ids]},
        )
        await db.refresh(candidate)
    return await _out(db, candidate)


@router.post(
    "/candidates/{candidate_id}/restore", response_model=CandidateOut, summary="Restore an archived candidate"
)
async def restore_candidate_endpoint(
    candidate_id: uuid.UUID, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> CandidateOut:
    candidate = await get_candidate(db, candidate_id, org_id, for_update=True)
    if await restore_candidate(db, candidate):
        await audit(
            db,
            principal,
            "candidate.restore",
            resource_type="candidate",
            resource_id=candidate.id,
            request=request,
            organization_id=org_id,
        )
        await db.refresh(candidate)
    return await _out(db, candidate)


@router.get(
    "/candidates/{candidate_id}/deletion-preview",
    response_model=CandidateDeletionPreviewOut,
    summary="What a permanent deletion would remove (ADMIN; counts only)",
)
async def candidate_deletion_preview(
    candidate_id: uuid.UUID, principal: Eraser, org_id: OrgScope, db: DbSession
) -> CandidateDeletionPreviewOut:
    candidate = await get_candidate(db, candidate_id, org_id)
    return CandidateDeletionPreviewOut.model_validate((await erasure_preview(db, candidate)).as_dict())


@router.delete(
    "/candidates/{candidate_id}",
    response_model=CandidateDeletionOut,
    summary="Permanently delete an archived candidate and all their data (ADMIN, irreversible)",
    description=(
        "Data-subject deletion: removes the candidate, every interview, session, transcript, evaluation, "
        "report, intake interview, open-link registration and stored file (resumes, audio, frames, "
        "recordings, report artifacts). The candidate must be archived first, or pass "
        "`archive_first=true` to archive and delete in one confirmed step. Returns counts only; the "
        "action is audited without personal data."
    ),
)
async def delete_candidate_endpoint(
    candidate_id: uuid.UUID,
    request: Request,
    principal: Eraser,
    org_id: OrgScope,
    db: DbSession,
    request_reference: Annotated[
        str | None,
        Query(
            max_length=64,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:#/-]*$",
            description="Your data-subject request id (recorded in the audit log; no personal data)",
        ),
    ] = None,
    archive_first: Annotated[
        bool, Query(description="Archive a not-yet-archived candidate in the same request")
    ] = False,
) -> CandidateDeletionOut:
    candidate = await get_candidate(db, candidate_id, org_id, for_update=True)
    summary = await erase_candidate(db, candidate, archive_first=archive_first)
    counts = summary.as_dict()
    counts.pop("candidate_id")
    await audit(
        db,
        principal,
        "candidate.delete",
        resource_type="candidate",
        resource_id=candidate_id,
        request=request,
        organization_id=org_id,
        details={**counts, "request_reference": request_reference, "reason": "data_subject_deletion"},
    )
    return CandidateDeletionOut(candidate_id=candidate_id, deleted_at=datetime.now(UTC), **counts)


@router.get(
    "/candidates/{candidate_id}/profile",
    response_model=CandidateProfileOut,
    summary="Latest candidate profile",
)
async def get_profile(
    candidate_id: uuid.UUID,
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    job_id: Annotated[
        uuid.UUID | None, Query(description="Prefer the profile analysed against this job")
    ] = None,
    version: Annotated[int | None, Query(ge=1, description="A specific profile version")] = None,
    include_quick: Annotated[
        bool,
        Query(
            description=(
                "Return the quick profile (status 'quick': deterministic read of a resume whose model profile "
                "is still being generated) when it is newer than the latest full profile"
            )
        ),
    ] = False,
) -> CandidateProfileOut:
    candidate = await get_candidate(db, candidate_id, org_id)
    if version is not None:
        profile = await db.scalar(
            select(CandidateProfile).where(
                CandidateProfile.candidate_id == candidate.id, CandidateProfile.version == version
            )
        )
    elif include_quick:
        profile = await get_resume_service().latest_profile(db, candidate.id, job_id, include_quick=True)
    else:
        profile = await get_resume_service().latest_profile(db, candidate.id, job_id)
    if profile is None:
        raise NotFound("No profile is available for this candidate yet.", code="profile_not_ready")
    out = CandidateProfileOut.model_validate(profile)
    resume = await db.get(CandidateResume, profile.resume_id)
    if resume is not None:
        stored = (resume.scan_result or {}).get("profile") or {}
        out.match_breakdown = stored.get("match")
    return out


@router.get(
    "/candidates/{candidate_id}/interviews",
    response_model=Page[InterviewOut],
    summary="Interviews of a candidate",
)
async def candidate_interviews(
    candidate_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession, page: PaginationParams
) -> Page[InterviewOut]:
    candidate = await get_candidate(db, candidate_id, org_id)
    stmt = select(Interview).where(Interview.candidate_id == candidate.id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(page.apply(stmt.order_by(Interview.created_at.desc(), Interview.id)))).all()
    return Page[InterviewOut](
        items=await serialize_interviews(db, rows), total=total, limit=page.limit, offset=page.offset
    )
