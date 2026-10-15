"""Interview templates: reusable RoundConfig documents.

Global default templates (one per round type, seeded) are read-only; an
organization creates its own (optionally marking one default per round type).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission
from app.core.rbac import Permission, Principal
from app.db.models import InterviewTemplate
from app.domain.enums import RoundType
from app.schemas.api.catalog import TemplateCreate, TemplateOut, TemplateUpdate
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.services.jobs.catalog import (
    build_template_config,
    clear_other_defaults,
    ensure_org_owned,
    get_template,
    org_visible,
    parse_round_type,
)

router = APIRouter(tags=["catalog"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.TEMPLATES_WRITE))]


@router.get("/templates", response_model=Page[TemplateOut], summary="List interview templates")
async def list_templates(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    round_type: RoundType | None = None,
    scope: Literal["all", "global", "organization"] = "all",
) -> Page[TemplateOut]:
    stmt = select(InterviewTemplate).where(org_visible(InterviewTemplate.organization_id, org_id))
    if scope == "global":
        stmt = stmt.where(InterviewTemplate.organization_id.is_(None))
    elif scope == "organization":
        stmt = stmt.where(InterviewTemplate.organization_id == org_id)
    if round_type is not None:
        stmt = stmt.where(InterviewTemplate.round_type == round_type.value)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    order = (
        InterviewTemplate.organization_id.is_(None),  # organization templates first
        InterviewTemplate.round_type,
        InterviewTemplate.name,
        InterviewTemplate.id,
    )
    rows = (await db.scalars(page.apply(stmt.order_by(*order)))).all()
    return Page[TemplateOut](
        items=[TemplateOut.from_row(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.post(
    "/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED, summary="Create a template"
)
async def create_template(
    body: TemplateCreate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> TemplateOut:
    config = await build_template_config(
        db, round_type=body.round_type, name=body.name, base=None, patch=body.config, organization_id=org_id
    )
    row = InterviewTemplate(
        organization_id=org_id,
        name=body.name.strip(),
        description=body.description,
        round_type=body.round_type.value,
        config=config.model_dump(mode="json"),
        is_default=body.is_default,
        created_by_id=principal.user_id,
    )
    db.add(row)
    await db.flush()
    if body.is_default:
        await clear_other_defaults(db, org_id, row.round_type, row.id)
    await audit(
        db,
        principal,
        "template.create",
        resource_type="interview_template",
        resource_id=row.id,
        request=request,
        organization_id=org_id,
        details={"round_type": row.round_type, "name": row.name},
    )
    return TemplateOut.from_row(row)


@router.get("/templates/{template_id}", response_model=TemplateOut, summary="Get a template")
async def get_template_endpoint(
    template_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> TemplateOut:
    return TemplateOut.from_row(await get_template(db, template_id, org_id))


@router.patch(
    "/templates/{template_id}", response_model=TemplateOut, summary="Update an organization template"
)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> TemplateOut:
    row = await get_template(db, template_id, org_id)
    ensure_org_owned(row, org_id, what="template")
    fields = set(body.model_fields_set)
    round_type = body.round_type or parse_round_type(row.round_type)
    name = body.name.strip() if body.name else row.name
    if fields & {"config", "round_type", "name"}:
        patch = dict(body.config or {})
        if "name" in fields and body.name and "name" not in patch:
            patch["name"] = name
        config = await build_template_config(
            db,
            round_type=round_type,
            name=name,
            base=dict(row.config or {}),
            patch=patch,
            organization_id=org_id,
        )
        row.config = config.model_dump(mode="json")
    row.name = name
    row.round_type = round_type.value
    if "description" in fields:
        row.description = body.description
    if body.is_default is not None:
        row.is_default = body.is_default
    await db.flush()
    if row.is_default:
        await clear_other_defaults(db, org_id, row.round_type, row.id)
    await audit(
        db,
        principal,
        "template.update",
        resource_type="interview_template",
        resource_id=row.id,
        request=request,
        organization_id=org_id,
        details={"fields": sorted(fields)},
    )
    await db.refresh(row)
    return TemplateOut.from_row(row)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete an organization template",
)
async def delete_template(
    template_id: uuid.UUID, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> Response:
    row = await get_template(db, template_id, org_id)
    ensure_org_owned(row, org_id, what="template")
    await db.delete(row)
    await audit(
        db,
        principal,
        "template.delete",
        resource_type="interview_template",
        resource_id=template_id,
        request=request,
        organization_id=org_id,
        details={"name": row.name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
