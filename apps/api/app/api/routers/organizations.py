"""Organizations (``organizations:manage`` = SUPER_ADMIN only)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select

from app.core.audit import audit
from app.core.deps import DbSession, PaginationParams, require_permission
from app.core.rbac import Permission, Principal
from app.db.models import Organization
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.organizations import OrganizationCreate, OrganizationOut, OrganizationUpdate
from app.services.identity.organizations import (
    create_organization,
    ensure_slug_available,
    get_organization,
    normalize_slug,
)

router = APIRouter(prefix="/organizations", tags=["organizations"], responses=ERROR_RESPONSES)

OrgManager = Annotated[Principal, Depends(require_permission(Permission.ORGANIZATIONS_MANAGE))]


@router.get("", response_model=Page[OrganizationOut], summary="List organizations")
async def list_organizations(
    principal: OrgManager,
    db: DbSession,
    page: PaginationParams,
    q: Annotated[str | None, Query(max_length=100)] = None,
    is_active: bool | None = None,
) -> Page[OrganizationOut]:
    stmt = select(Organization)
    if q:
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(Organization.name.ilike(pattern, escape="\\"), Organization.slug.ilike(pattern, escape="\\"))
        )
    if is_active is not None:
        stmt = stmt.where(Organization.is_active.is_(is_active))
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(page.apply(stmt.order_by(Organization.name, Organization.id)))).all()
    return Page[OrganizationOut](
        items=[OrganizationOut.model_validate(o) for o in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED, summary="Create an organization"
)
async def create(
    body: OrganizationCreate, request: Request, principal: OrgManager, db: DbSession
) -> OrganizationOut:
    org = await create_organization(db, name=body.name, slug=body.slug, settings=body.settings)
    await audit(
        db,
        principal,
        "organization.create",
        resource_type="organization",
        resource_id=str(org.id),
        organization_id=org.id,
        request=request,
        details={"slug": org.slug, "name": org.name},
    )
    return OrganizationOut.model_validate(org)


@router.get("/{organization_id}", response_model=OrganizationOut, summary="Get an organization")
async def get_one(organization_id: uuid.UUID, principal: OrgManager, db: DbSession) -> OrganizationOut:
    return OrganizationOut.model_validate(await get_organization(db, organization_id))


@router.patch("/{organization_id}", response_model=OrganizationOut, summary="Update an organization")
async def update(
    organization_id: uuid.UUID,
    body: OrganizationUpdate,
    request: Request,
    principal: OrgManager,
    db: DbSession,
) -> OrganizationOut:
    org = await get_organization(db, organization_id)
    changes: dict[str, object] = {}
    if body.name is not None and body.name.strip() != org.name:
        changes["name"] = {"from": org.name, "to": body.name.strip()}
        org.name = body.name.strip()
    if body.slug is not None:
        slug = normalize_slug(body.slug)
        if slug != org.slug:
            await ensure_slug_available(db, slug, exclude_id=org.id)
            changes["slug"] = {"from": org.slug, "to": slug}
            org.slug = slug
    if body.is_active is not None and body.is_active != org.is_active:
        changes["is_active"] = {"from": org.is_active, "to": body.is_active}
        org.is_active = body.is_active
    if body.features is not None:
        merged = {**(org.features or {}), **body.features}
        if merged != (org.features or {}):
            changes["features"] = {"from": dict(org.features or {}), "to": merged}
            org.features = merged
    if changes:
        await db.flush()
        await db.refresh(org)
        await audit(
            db,
            principal,
            "organization.update",
            resource_type="organization",
            resource_id=str(org.id),
            organization_id=org.id,
            request=request,
            details={"changes": changes},
        )
    return OrganizationOut.model_validate(org)
