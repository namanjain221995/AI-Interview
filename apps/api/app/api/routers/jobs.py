"""Jobs and job requirements."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission
from app.core.rbac import Permission, Principal
from app.db.models import Job
from app.domain.enums import JobStatus
from app.schemas.api.catalog import RoundOut
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.jobs import (
    JobCreate,
    JobDetail,
    JobOut,
    JobUpdate,
    RequirementCreate,
    RequirementOut,
    RequirementUpdate,
)
from app.services.identity import get_organization_settings
from app.services.jobs.jobs import (
    add_requirement,
    archive_job,
    create_job,
    delete_requirement,
    get_job,
    get_requirement,
    job_counts,
    job_list_query,
    list_requirements,
    update_job,
    update_requirement,
)
from app.services.jobs.policy import build_policy_snapshot
from app.services.jobs.rounds import list_rounds
from app.services.question_bank.embeddings import with_embedding_flag

router = APIRouter(tags=["jobs"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.JOBS_WRITE))]


async def _detail(db: DbSession, job: Job) -> JobDetail:
    await db.refresh(job)  # embeddings may have been written after the insert (expired timestamps)
    counts = (await job_counts(db, [job.id]))[job.id]
    detail = JobDetail.model_validate(JobOut.from_row(job, counts).model_dump())
    detail.requirements = [RequirementOut.from_row(r) for r in await list_requirements(db, job.id)]
    detail.rounds = [RoundOut.model_validate(r) for r in await list_rounds(db, job.id)]
    org_settings = await get_organization_settings(db, job.organization_id)
    detail.effective_policy = build_policy_snapshot(org_settings, job).model_dump(mode="json")
    return detail


@router.get("/jobs", response_model=Page[JobOut], summary="List jobs")
async def list_jobs(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    status_filter: Annotated[
        JobStatus | None, Query(alias="status", description="Default: everything but archived")
    ] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[JobOut]:
    stmt = job_list_query(org_id, status=status_filter, q=q)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    ordered = stmt.order_by(Job.updated_at.desc(), Job.id)
    rows = (await db.execute(page.apply(with_embedding_flag(ordered, Job)))).all()
    counts = await job_counts(db, [job.id for job, _ in rows])
    return Page[JobOut](
        items=[JobOut.from_row(job, counts.get(job.id), has_embedding=flag) for job, flag in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/jobs", response_model=JobDetail, status_code=status.HTTP_201_CREATED, summary="Create a job")
async def create_job_endpoint(
    body: JobCreate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> JobDetail:
    job, requirements = await create_job(
        db, organization_id=org_id, created_by_id=principal.user_id, body=body
    )
    await audit(
        db,
        principal,
        "job.create",
        resource_type="job",
        resource_id=job.id,
        request=request,
        organization_id=org_id,
        details={"title": job.title, "status": job.status, "requirements": len(requirements)},
    )
    return await _detail(db, job)


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="Get a job with requirements and rounds")
async def get_job_endpoint(
    job_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> JobDetail:
    return await _detail(db, await get_job(db, job_id, org_id))


@router.patch("/jobs/{job_id}", response_model=JobDetail, summary="Update a job")
async def update_job_endpoint(
    job_id: uuid.UUID, body: JobUpdate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> JobDetail:
    job = await get_job(db, job_id, org_id)
    changed = await update_job(db, job, body)
    if changed:
        await audit(
            db,
            principal,
            "job.update",
            resource_type="job",
            resource_id=job.id,
            request=request,
            organization_id=org_id,
            details={"changes": sorted(changed)},
        )
    await db.refresh(job)
    return await _detail(db, job)


@router.post("/jobs/{job_id}/archive", response_model=JobOut, summary="Archive a job")
async def archive_job_endpoint(
    job_id: uuid.UUID, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> JobOut:
    job = await get_job(db, job_id, org_id)
    if await archive_job(db, job):
        await audit(
            db,
            principal,
            "job.archive",
            resource_type="job",
            resource_id=job.id,
            request=request,
            organization_id=org_id,
        )
        await db.refresh(job)
    counts = (await job_counts(db, [job.id]))[job.id]
    return JobOut.from_row(job, counts)


@router.get(
    "/jobs/{job_id}/requirements", response_model=Page[RequirementOut], summary="List job requirements"
)
async def list_job_requirements(
    job_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> Page[RequirementOut]:
    job = await get_job(db, job_id, org_id)
    items = [RequirementOut.from_row(r) for r in await list_requirements(db, job.id)]
    return Page[RequirementOut](items=items, total=len(items), limit=max(len(items), 1), offset=0)


@router.post(
    "/jobs/{job_id}/requirements",
    response_model=RequirementOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a requirement",
)
async def add_job_requirement(
    job_id: uuid.UUID,
    body: RequirementCreate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> RequirementOut:
    job = await get_job(db, job_id, org_id)
    requirement = await add_requirement(db, job, body)
    await db.refresh(requirement)
    await audit(
        db,
        principal,
        "job.requirement.create",
        resource_type="job_requirement",
        resource_id=requirement.id,
        request=request,
        organization_id=org_id,
        details={"job_id": str(job.id), "name": requirement.name},
    )
    return RequirementOut.from_row(requirement)


@router.patch("/requirements/{requirement_id}", response_model=RequirementOut, summary="Update a requirement")
async def update_job_requirement(
    requirement_id: uuid.UUID,
    body: RequirementUpdate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> RequirementOut:
    requirement, job = await get_requirement(db, requirement_id, org_id)
    changed = await update_requirement(db, requirement, job, body)
    if changed:
        await audit(
            db,
            principal,
            "job.requirement.update",
            resource_type="job_requirement",
            resource_id=requirement.id,
            request=request,
            organization_id=org_id,
            details={"job_id": str(job.id), "changes": sorted(changed)},
        )
    await db.refresh(requirement)
    return RequirementOut.from_row(requirement)


@router.delete(
    "/requirements/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a requirement",
)
async def delete_job_requirement(
    requirement_id: uuid.UUID, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> Response:
    requirement, job = await get_requirement(db, requirement_id, org_id)
    name = requirement.name
    await delete_requirement(db, requirement, job)
    await audit(
        db,
        principal,
        "job.requirement.delete",
        resource_type="job_requirement",
        resource_id=requirement_id,
        request=request,
        organization_id=org_id,
        details={"job_id": str(job.id), "name": name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
