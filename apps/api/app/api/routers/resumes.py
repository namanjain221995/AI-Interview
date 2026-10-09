"""Resumes: recruiter upload (same pipeline as the candidate portal), status /
progress, reprocessing."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import DbSession, OrgScope, PaginationParams, RedisClient, require_permission
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.core.rbac import Permission, Principal
from app.db.models import CandidateProfile, CandidateResume
from app.db.session import session_scope
from app.domain.enums import ResumeStatus
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.resumes import ReprocessRequest, ResumeOut
from app.services.candidates.service import ensure_active, get_candidate
from app.services.resume.service import extraction_summary, get_resume_service, media_id_of, resume_media
from app.services.resume.validation import ResumeRejected
from app.services.storage.signing import signed_url_with_expiry

log = get_logger(__name__)

router = APIRouter(tags=["resumes"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_WRITE))]

MEDIA_VIEW_AUDIT_WINDOW_SECONDS = 600


def resume_out(resume: CandidateResume, *, profile: CandidateProfile | None = None) -> ResumeOut:
    out = ResumeOut.model_validate(resume)
    scan = resume.scan_result or {}
    media_id = media_id_of(resume)
    out.media_id = uuid.UUID(media_id) if media_id else None
    out.warnings = list(scan.get("warnings") or [])
    out.scan = scan.get("malware")
    out.profile_ready = resume.status == ResumeStatus.READY.value
    out.extraction_summary = extraction_summary(resume, profile)
    return out


async def _get_resume(db: DbSession, resume_id: uuid.UUID, org_id: uuid.UUID) -> CandidateResume:
    resume = await db.get(CandidateResume, resume_id)
    if resume is None or resume.organization_id != org_id:
        raise NotFound("Resume not found.")
    return resume


async def _profile_for(db: DbSession, resume: CandidateResume) -> CandidateProfile | None:
    return await db.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.resume_id == resume.id)
        .order_by(CandidateProfile.version.desc())
        .limit(1)
    )


@router.post(
    "/candidates/{candidate_id}/resumes",
    response_model=ResumeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a resume for a candidate (multipart)",
)
async def upload_resume(
    candidate_id: uuid.UUID,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
    file: Annotated[UploadFile, File(description="PDF, DOCX, TXT, PNG, JPEG, WebP or TIFF")],
    job_id: Annotated[uuid.UUID | None, Form(description="Analyse against this job")] = None,
    interview_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ResumeOut:
    candidate = await get_candidate(db, candidate_id, org_id)
    ensure_active(candidate, action="upload a resume")
    data = await file.read(get_settings().max_resume_bytes + 1)
    try:
        resume = await get_resume_service().accept_upload(
            db,
            organization_id=org_id,
            candidate_id=candidate.id,
            interview_id=interview_id,
            job_id=job_id,
            filename=file.filename or "resume",
            declared_content_type=file.content_type,
            data=data,
            uploaded_by_candidate=False,
        )
    except ResumeRejected as exc:
        # The request transaction rolls back; keep the security-relevant trail.
        async with session_scope() as audit_db:
            await audit(
                audit_db,
                principal,
                "resume.rejected",
                resource_type="candidate",
                resource_id=candidate.id,
                request=request,
                organization_id=org_id,
                details={"reason": exc.code, "size_bytes": len(data), "found": exc.details.get("found")},
            )
        raise
    await audit(
        db,
        principal,
        "resume.upload",
        resource_type="resume",
        resource_id=resume.id,
        request=request,
        organization_id=org_id,
        details={
            "candidate_id": str(candidate.id),
            "content_type": resume.content_type,
            "size_bytes": resume.size_bytes,
        },
    )
    return resume_out(resume)


@router.get(
    "/candidates/{candidate_id}/resumes", response_model=Page[ResumeOut], summary="Resumes of a candidate"
)
async def list_resumes(
    candidate_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession, page: PaginationParams
) -> Page[ResumeOut]:
    candidate = await get_candidate(db, candidate_id, org_id)
    stmt = select(CandidateResume).where(CandidateResume.candidate_id == candidate.id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(page.apply(stmt.order_by(CandidateResume.created_at.desc(), CandidateResume.id)))
    ).all()
    return Page[ResumeOut](
        items=[resume_out(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get(
    "/resumes/{resume_id}", response_model=ResumeOut, summary="Resume status, progress and extraction summary"
)
async def get_resume(
    resume_id: uuid.UUID,
    request: Request,
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    redis: RedisClient,
    include_media_url: Annotated[bool, Query(description="Include a short-lived signed download URL")] = True,
) -> ResumeOut:
    resume = await _get_resume(db, resume_id, org_id)
    out = resume_out(resume, profile=await _profile_for(db, resume))
    media = await resume_media(db, resume) if include_media_url else None
    if media is not None and media.deleted_at is None:
        url, expires_at = signed_url_with_expiry(media.id)
        out.media_id, out.media_url, out.media_url_expires_at = media.id, url, expires_at
        if await _first_view_in_window(redis, principal, media.id):
            await audit(
                db,
                principal,
                "media.view",
                resource_type="media",
                resource_id=media.id,
                request=request,
                organization_id=org_id,
                details={"kind": "resume", "resume_id": str(resume.id)},
            )
    return out


async def _first_view_in_window(redis: Redis, principal: Principal, media_id: uuid.UUID) -> bool:
    """The status page polls this endpoint every second or two: record one
    ``media.view`` per viewer and file per window instead of one per poll."""
    key = f"audit:media_view:{principal.user_id}:{media_id}"
    try:
        return bool(await redis.set(key, "1", nx=True, ex=MEDIA_VIEW_AUDIT_WINDOW_SECONDS))
    except (RedisError, OSError):
        return True  # never lose an audit entry because Redis is unavailable


@router.post(
    "/resumes/{resume_id}/reprocess",
    response_model=ResumeOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run extraction and analysis again",
)
async def reprocess_resume(
    resume_id: uuid.UUID,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
    body: ReprocessRequest | None = None,
) -> ResumeOut:
    resume = await _get_resume(db, resume_id, org_id)
    options = body or ReprocessRequest()
    await get_resume_service().reprocess(db, resume, job_id=options.job_id, force=options.force)
    await audit(
        db,
        principal,
        "resume.reprocess",
        resource_type="resume",
        resource_id=resume.id,
        request=request,
        organization_id=org_id,
        details={"job_id": str(options.job_id) if options.job_id else None, "force": options.force},
    )
    await db.refresh(resume)
    return resume_out(resume)
