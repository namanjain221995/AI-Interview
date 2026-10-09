"""Competency catalog: global (seeded, read-only) + organization competencies."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission
from app.core.rbac import Permission, Principal
from app.db.models import Competency
from app.domain.enums import CompetencyCategory
from app.schemas.api.catalog import CompetencyCreate, CompetencyOut, CompetencyUpdate
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.services.jobs.catalog import (
    ensure_competency_key_available,
    ensure_org_owned,
    get_competency,
    org_visible,
)
from app.services.jobs.jobs import escape_like
from app.services.question_bank.embeddings import (
    competency_text,
    embed_rows_best_effort,
    enqueue_embedding,
    with_embedding_flag,
)

router = APIRouter(tags=["catalog"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.TEMPLATES_WRITE))]

_EMBED_FIELDS = {"name", "description", "rubric"}


async def _embed(db: DbSession, row: Competency) -> None:
    if not await embed_rows_best_effort(db, [row], competency_text):
        await enqueue_embedding(db, competency_ids=[row.id])


@router.get(
    "/competencies", response_model=Page[CompetencyOut], summary="List competencies (global + organization)"
)
async def list_competencies(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    category: CompetencyCategory | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    include_inactive: bool = False,
    scope: Literal["all", "global", "organization"] = "all",
) -> Page[CompetencyOut]:
    stmt = select(Competency).where(org_visible(Competency.organization_id, org_id))
    if scope == "global":
        stmt = stmt.where(Competency.organization_id.is_(None))
    elif scope == "organization":
        stmt = stmt.where(Competency.organization_id == org_id)
    if category is not None:
        stmt = stmt.where(Competency.category == category.value)
    if not include_inactive:
        stmt = stmt.where(Competency.is_active.is_(True))
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(
            or_(
                Competency.key.ilike(pattern, escape="\\"),
                Competency.name.ilike(pattern, escape="\\"),
                Competency.description.ilike(pattern, escape="\\"),
            )
        )
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    ordered = stmt.order_by(Competency.category, Competency.name, Competency.id)
    rows = (await db.execute(page.apply(with_embedding_flag(ordered, Competency)))).all()
    return Page[CompetencyOut](
        items=[CompetencyOut.from_row(row, has_embedding=flag) for row, flag in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/competencies",
    response_model=CompetencyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a competency",
)
async def create_competency(
    body: CompetencyCreate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> CompetencyOut:
    await ensure_competency_key_available(db, body.key, org_id)
    row = Competency(
        organization_id=org_id,
        key=body.key,
        name=body.name.strip(),
        category=body.category.value,
        description=body.description,
        rubric=body.rubric.model_dump(exclude_defaults=False),
        is_active=body.is_active,
    )
    db.add(row)
    await db.flush()
    await _embed(db, row)
    await db.refresh(row)
    await audit(
        db,
        principal,
        "competency.create",
        resource_type="competency",
        resource_id=row.id,
        request=request,
        organization_id=org_id,
        details={"key": row.key},
    )
    return CompetencyOut.from_row(row)


@router.patch("/competencies/{competency_id}", response_model=CompetencyOut, summary="Update a competency")
async def update_competency(
    competency_id: uuid.UUID,
    body: CompetencyUpdate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> CompetencyOut:
    row = await get_competency(db, competency_id, org_id)
    ensure_org_owned(row, org_id, what="competency")
    changed: list[str] = []
    for name in sorted(body.model_fields_set):
        value = getattr(body, name)
        if value is None and name != "description":
            continue
        if name == "category":
            value = value.value
        elif name == "rubric":
            value = value.model_dump()
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed.append(name)
    if _EMBED_FIELDS & set(changed):
        row.embedding = None
    await db.flush()
    if row.embedding is None:
        await _embed(db, row)
    if changed:
        await audit(
            db,
            principal,
            "competency.update",
            resource_type="competency",
            resource_id=row.id,
            request=request,
            organization_id=org_id,
            details={"changes": changed},
        )
    await db.refresh(row)
    return CompetencyOut.from_row(row)
