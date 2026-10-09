"""Staff user administration (``users:manage``).

ADMINs manage users of their own organization and can never grant
SUPER_ADMIN; SUPER_ADMINs manage users across organizations. Users cannot
change their own role or deactivate themselves, and an organization always
keeps at least one active ADMIN.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select

from app.core.audit import audit
from app.core.deps import DbSession, PaginationParams, require_permission, resolve_org_id
from app.core.errors import Forbidden
from app.core.rbac import Permission, Principal, can_manage_user
from app.db.models import User
from app.domain.enums import Role
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.users import UserCreate, UserOut, UserUpdate
from app.services.identity.sessions import invalidate_user_auth_cache
from app.services.identity.users import (
    UserChanges,
    create_user,
    deactivate_user,
    get_user_for_actor,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"], responses=ERROR_RESPONSES)

UserManager = Annotated[Principal, Depends(require_permission(Permission.USERS_MANAGE))]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=Page[UserOut], summary="List users")
async def list_users(
    principal: UserManager,
    db: DbSession,
    page: PaginationParams,
    organization_id: Annotated[uuid.UUID | None, Query(description="SUPER_ADMIN: filter by organization")] = None,
    role: Role | None = None,
    is_active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search email or name")] = None,
) -> Page[UserOut]:
    stmt = select(User)
    if principal.is_super_admin:
        if organization_id is not None:
            stmt = stmt.where(User.organization_id == organization_id)
    else:
        stmt = stmt.where(User.organization_id == resolve_org_id(principal, organization_id))
    if role is not None:
        stmt = stmt.where(User.role == role.value)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if q:
        pattern = f"%{_escape_like(q.strip().lower())}%"
        stmt = stmt.where(or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\")))
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(page.apply(stmt.order_by(User.created_at.desc(), User.id)))).all()
    return Page[UserOut](items=[UserOut.from_user(u) for u in rows], total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Create a user")
async def create_user_endpoint(body: UserCreate, request: Request, principal: UserManager, db: DbSession) -> UserOut:
    user = await create_user(
        db,
        principal,
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        password=body.password,
        organization_id=body.organization_id,
    )
    await audit(
        db, principal, "user.create", resource_type="user", resource_id=str(user.id), request=request,
        organization_id=user.organization_id, details={"role": user.role, "email": user.email},
    )
    return UserOut.from_user(user)


@router.get("/{user_id}", response_model=UserOut, summary="Get a user")
async def get_user(user_id: uuid.UUID, principal: UserManager, db: DbSession) -> UserOut:
    return UserOut.from_user(await get_user_for_actor(db, principal, user_id))


@router.patch("/{user_id}", response_model=UserOut, summary="Update a user")
async def patch_user(
    user_id: uuid.UUID, body: UserUpdate, request: Request, principal: UserManager, db: DbSession
) -> UserOut:
    user = await get_user_for_actor(db, principal, user_id)
    fields = set(body.model_fields_set)
    changes = UserChanges(
        full_name=body.full_name, role=body.role, is_active=body.is_active, password=body.password, fields=fields
    )
    summary = await update_user(db, principal, user, changes)
    if summary:
        await audit(
            db, principal, "user.update", resource_type="user", resource_id=str(user.id), request=request,
            organization_id=user.organization_id, details={"changes": summary},
        )
    return UserOut.from_user(user)


@router.post("/{user_id}/deactivate", response_model=UserOut, summary="Deactivate a user and end their sessions")
async def deactivate(user_id: uuid.UUID, request: Request, principal: UserManager, db: DbSession) -> UserOut:
    user = await get_user_for_actor(db, principal, user_id)
    summary = await deactivate_user(db, principal, user)
    if summary:
        await audit(
            db, principal, "user.deactivate", resource_type="user", resource_id=str(user.id), request=request,
            organization_id=user.organization_id,
        )
    return UserOut.from_user(user)


@router.post("/{user_id}/unlock", response_model=UserOut, summary="Clear a sign-in lockout")
async def unlock(user_id: uuid.UUID, request: Request, principal: UserManager, db: DbSession) -> UserOut:
    user = await get_user_for_actor(db, principal, user_id)
    if not can_manage_user(principal.role, Role(user.role)):
        raise Forbidden("You are not allowed to modify this user.", code="role_not_allowed")
    if user.locked_until is not None or user.failed_login_count:
        user.locked_until = None
        user.failed_login_count = 0
        await db.flush()
        await db.refresh(user)
        await invalidate_user_auth_cache(db, user.id)
        await audit(
            db, principal, "user.unlock", resource_type="user", resource_id=str(user.id), request=request,
            organization_id=user.organization_id,
        )
    return UserOut.from_user(user)
