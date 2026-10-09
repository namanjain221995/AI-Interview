"""Audit log browsing (``audit:read``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.deps import DbSession, PaginationParams, require_permission, resolve_org_id
from app.core.rbac import Permission, Principal
from app.db.models import AuditLog
from app.schemas.api.common import ERROR_RESPONSES, AuditLogOut, Page

router = APIRouter(prefix="/audit-logs", tags=["audit"], responses=ERROR_RESPONSES)

AuditReader = Annotated[Principal, Depends(require_permission(Permission.AUDIT_READ))]


@router.get("", response_model=Page[AuditLogOut], summary="List audit log entries (newest first)")
async def list_audit_logs(
    principal: AuditReader,
    db: DbSession,
    page: PaginationParams,
    organization_id: Annotated[uuid.UUID | None, Query(description="SUPER_ADMIN: filter by organization")] = None,
    action: Annotated[str | None, Query(max_length=100, description="Exact action, or a prefix ending in '*'")] = None,
    actor_id: uuid.UUID | None = None,
    actor_type: Annotated[str | None, Query(pattern="^(user|candidate|system)$")] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    resource_id: Annotated[str | None, Query(max_length=64)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if principal.is_super_admin:
        if organization_id is not None:
            stmt = stmt.where(AuditLog.organization_id == organization_id)
    else:
        stmt = stmt.where(AuditLog.organization_id == resolve_org_id(principal, organization_id))
    if action:
        if action.endswith("*"):
            prefix = action[:-1].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(AuditLog.action.like(prefix + "%", escape="\\"))
        else:
            stmt = stmt.where(AuditLog.action == action)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if actor_type is not None:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    if resource_type is not None:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(page.apply(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())))).all()
    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )
