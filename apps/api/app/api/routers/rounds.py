"""Interview rounds of a job, and the default configuration per round type."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, require_permission
from app.core.rbac import Permission, Principal
from app.schemas.api.catalog import RoundCreate, RoundOut, RoundReorder, RoundTypeDefault, RoundUpdate
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.services.jobs.catalog import list_round_type_defaults
from app.services.jobs.jobs import get_job
from app.services.jobs.rounds import (
    create_round,
    delete_round,
    get_round,
    list_rounds,
    reorder_rounds,
    update_round,
)

router = APIRouter(tags=["catalog"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.JOBS_WRITE))]


def _page(rounds: list[RoundOut]) -> Page[RoundOut]:
    return Page[RoundOut](items=rounds, total=len(rounds), limit=max(len(rounds), 1), offset=0)


@router.get(
    "/round-types", response_model=Page[RoundTypeDefault], summary="Default configuration per round type"
)
async def round_types(principal: Reader, db: DbSession) -> Page[RoundTypeDefault]:
    items = [RoundTypeDefault.model_validate(item) for item in await list_round_type_defaults(db)]
    return Page[RoundTypeDefault](items=items, total=len(items), limit=len(items), offset=0)


@router.get("/jobs/{job_id}/rounds", response_model=Page[RoundOut], summary="List the rounds of a job")
async def list_job_rounds(
    job_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> Page[RoundOut]:
    job = await get_job(db, job_id, org_id)
    return _page([RoundOut.model_validate(r) for r in await list_rounds(db, job.id)])


@router.post(
    "/jobs/{job_id}/rounds",
    response_model=RoundOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a round",
)
async def add_round(
    job_id: uuid.UUID, body: RoundCreate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> RoundOut:
    job = await get_job(db, job_id, org_id)
    row = await create_round(db, job, body)
    await audit(
        db,
        principal,
        "round.create",
        resource_type="round",
        resource_id=row.id,
        request=request,
        organization_id=org_id,
        details={"job_id": str(job.id), "round_type": row.round_type, "template_id": body.template_id},
    )
    return RoundOut.model_validate(row)


@router.post(
    "/jobs/{job_id}/rounds/reorder", response_model=Page[RoundOut], summary="Reorder the rounds of a job"
)
async def reorder(
    job_id: uuid.UUID,
    body: RoundReorder,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> Page[RoundOut]:
    job = await get_job(db, job_id, org_id)
    rows = await reorder_rounds(db, job, body.round_ids)
    await audit(
        db,
        principal,
        "round.reorder",
        resource_type="job",
        resource_id=job.id,
        request=request,
        organization_id=org_id,
        details={"round_ids": [str(i) for i in body.round_ids]},
    )
    for row in rows:
        await db.refresh(row)
    return _page([RoundOut.model_validate(r) for r in rows])


@router.get("/rounds/{round_id}", response_model=RoundOut, summary="Get a round")
async def get_round_endpoint(
    round_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> RoundOut:
    row, _ = await get_round(db, round_id, org_id)
    return RoundOut.model_validate(row)


@router.patch("/rounds/{round_id}", response_model=RoundOut, summary="Update a round")
async def patch_round(
    round_id: uuid.UUID,
    body: RoundUpdate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> RoundOut:
    row, job = await get_round(db, round_id, org_id)
    summary = await update_round(db, row, job, body)
    await audit(
        db,
        principal,
        "round.update",
        resource_type="round",
        resource_id=row.id,
        request=request,
        organization_id=org_id,
        details={"job_id": str(job.id), **summary},
    )
    await db.refresh(row)
    return RoundOut.model_validate(row)


@router.delete(
    "/rounds/{round_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a round",
)
async def remove_round(
    round_id: uuid.UUID, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> Response:
    row, job = await get_round(db, round_id, org_id)
    await delete_round(db, row)
    await audit(
        db,
        principal,
        "round.delete",
        resource_type="round",
        resource_id=round_id,
        request=request,
        organization_id=org_id,
        details={"job_id": str(job.id), "name": row.name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
