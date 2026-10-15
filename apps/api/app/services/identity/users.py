"""Staff accounts: authentication with lockout, creation and updates with
privilege-escalation guards."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.core.logging import get_logger
from app.core.metrics import AUTH_EVENTS
from app.core.ratelimit import hashed_key, hit
from app.core.rbac import STAFF_ROLES, Principal, can_assign_role, can_manage_user
from app.core.security import (
    burn_password_check_async,
    hash_password_async,
    password_needs_rehash,
    validate_password,
    verify_password_async,
)
from app.db.models import Organization, User
from app.domain.enums import Role
from app.services.identity.sessions import invalidate_user_auth_cache, revoke_user_sessions

if TYPE_CHECKING:
    from fastapi import Request

log = get_logger(__name__)

_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class InvalidCredentials(Unauthorized):
    code = "invalid_credentials"
    message = "Invalid email or password."


class AccountLocked(AppError):
    status_code = 423
    code = "account_locked"
    message = "Too many failed sign-in attempts. The account is temporarily locked; try again later."


class AccountDisabled(Unauthorized):
    code = "account_disabled"
    message = "This account is disabled."


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return len(email) <= 320 and bool(_EMAIL.match(email))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _locked(retry_after_seconds: float) -> AccountLocked:
    seconds = max(1, int(retry_after_seconds))
    return AccountLocked(details={"retry_after_seconds": seconds}, headers={"Retry-After": str(seconds)})


async def authenticate(
    db: AsyncSession, *, email: str, password: str, request: Request | None = None
) -> User:
    """Verify credentials, enforcing lockout after ``LOGIN_MAX_ATTEMPTS`` failures.

    Failure bookkeeping (counters, lock, audit rows) is committed before the
    error is raised, because the request transaction is rolled back on errors.
    Unknown emails get the same responses (including the lock) as real ones.
    """
    settings = get_settings()
    normalized = normalize_email(email)
    user = await db.scalar(
        select(User)
        .where(User.email == normalized)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = _utcnow()

    if user is None:
        await burn_password_check_async(password)
        attempt = await hit(
            "login_unknown",
            hashed_key(normalized),
            settings.login_max_attempts,
            settings.login_lockout_seconds,
        )
        await audit(
            db,
            None,
            "auth.login_failed",
            resource_type="user",
            details={"reason": "unknown_email", "email_sha256": hashed_key(normalized)},
            request=request,
        )
        await db.commit()
        AUTH_EVENTS.labels(event="login_failed").inc()
        if not attempt.allowed:
            raise _locked(attempt.retry_after)
        if attempt.remaining <= 0:
            # Same attempt number as a real account (which locks on the
            # login_max_attempts-th failure), so lockout timing cannot reveal
            # whether an email is registered.
            raise _locked(float(settings.login_lockout_seconds))
        raise InvalidCredentials()

    if user.locked_until is not None and user.locked_until > now:
        await burn_password_check_async(password)
        await audit(
            db,
            None,
            "auth.login_blocked",
            resource_type="user",
            resource_id=str(user.id),
            organization_id=user.organization_id,
            details={"reason": "locked"},
            request=request,
        )
        await db.commit()
        AUTH_EVENTS.labels(event="login_failed").inc()
        raise _locked((user.locked_until - now).total_seconds())

    if not await verify_password_async(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        lock_now = user.failed_login_count >= settings.login_max_attempts
        await audit(
            db,
            None,
            "auth.login_failed",
            resource_type="user",
            resource_id=str(user.id),
            organization_id=user.organization_id,
            details={"reason": "bad_password", "failed_attempts": user.failed_login_count},
            request=request,
        )
        if lock_now:
            user.locked_until = now + timedelta(seconds=settings.login_lockout_seconds)
            user.failed_login_count = 0
            await audit(
                db,
                None,
                "auth.account_locked",
                resource_type="user",
                resource_id=str(user.id),
                organization_id=user.organization_id,
                details={"lockout_seconds": settings.login_lockout_seconds},
                request=request,
            )
            AUTH_EVENTS.labels(event="account_locked").inc()
        await db.commit()
        AUTH_EVENTS.labels(event="login_failed").inc()
        log.info("auth.login_failed", user_id=str(user.id), locked=lock_now)
        if lock_now:
            raise _locked(settings.login_lockout_seconds)
        raise InvalidCredentials()

    org_active = True
    if user.organization_id is not None:
        org_active = bool(
            await db.scalar(select(Organization.is_active).where(Organization.id == user.organization_id))
        )
    if not user.is_active or not org_active or Role(user.role) not in STAFF_ROLES:
        await audit(
            db,
            None,
            "auth.login_denied",
            resource_type="user",
            resource_id=str(user.id),
            organization_id=user.organization_id,
            details={"reason": "account_disabled" if not user.is_active else "organization_disabled"},
            request=request,
        )
        await db.commit()
        AUTH_EVENTS.labels(event="login_failed").inc()
        raise AccountDisabled()

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(password)
        log.info("auth.password_rehashed", user_id=str(user.id))
    return user


# ------------------------------------------------------------- management


@dataclass(slots=True)
class UserChanges:
    full_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = None
    fields: set[str] = field(default_factory=set)


async def get_user_for_actor(db: AsyncSession, actor: Principal, user_id: uuid.UUID) -> User:
    """Load a user the actor may see (same organization unless SUPER_ADMIN)."""
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    if not actor.is_super_admin and user.organization_id != actor.organization_id:
        raise NotFound("User not found.")
    return user


async def _count_other_active_admins(
    db: AsyncSession, organization_id: uuid.UUID | None, exclude: uuid.UUID
) -> int:
    if organization_id is None:
        return 1  # platform-level super admins are not bound to an organization
    # Lock the organization row so concurrent demotions/deactivations of the
    # last admins are serialized (the second one sees the first one's change).
    await db.execute(select(Organization.id).where(Organization.id == organization_id).with_for_update())
    return int(
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == organization_id,
                User.role == Role.ADMIN.value,
                User.is_active.is_(True),
                User.id != exclude,
            )
        )
        or 0
    )


async def create_user(
    db: AsyncSession,
    actor: Principal,
    *,
    email: str,
    full_name: str,
    role: Role,
    password: str,
    organization_id: uuid.UUID | None,
) -> User:
    role = Role(role)
    if not can_assign_role(actor.role, role):
        raise Forbidden("You are not allowed to create users with this role.", code="role_not_allowed")
    if actor.is_super_admin:
        target_org = organization_id if organization_id is not None else actor.organization_id
    else:
        if organization_id is not None and organization_id != actor.organization_id:
            raise Forbidden("You can only create users in your own organization.")
        target_org = actor.organization_id
    if target_org is None and role is not Role.SUPER_ADMIN:
        raise ValidationFailed("organization_id is required.", details={"field": "organization_id"})
    if target_org is not None:
        org = await db.get(Organization, target_org)
        if org is None:
            raise NotFound("Organization not found.")
        if not org.is_active:
            raise Conflict("The organization is disabled.")
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise ValidationFailed("Invalid email address.", details={"field": "email"})
    if await db.scalar(select(User.id).where(User.email == normalized)) is not None:
        raise Conflict("A user with this email already exists.", details={"field": "email"})
    name = full_name.strip()
    if not name:
        raise ValidationFailed("Full name is required.", details={"field": "full_name"})
    validate_password(password, email=normalized, full_name=name)
    user = User(
        organization_id=target_org,
        email=normalized,
        full_name=name,
        role=role.value,
        password_hash=await hash_password_async(password),
        is_active=True,
        password_changed_at=_utcnow(),
        must_change_password=True,  # an administrator chose it: the user picks their own at first sign-in
    )
    db.add(user)
    await db.flush()
    return user


async def update_user(db: AsyncSession, actor: Principal, user: User, changes: UserChanges) -> dict[str, Any]:
    """Apply ``changes`` with privilege checks; returns an audit-safe change summary."""
    current_role = Role(user.role)
    if not can_manage_user(actor.role, current_role):
        raise Forbidden("You are not allowed to modify this user.", code="role_not_allowed")
    summary: dict[str, Any] = {}
    is_self = actor.user_id == user.id

    if "role" in changes.fields and changes.role is not None and changes.role is not current_role:
        if is_self:
            raise Forbidden("You cannot change your own role.", code="self_modification")
        if not can_assign_role(actor.role, changes.role):
            raise Forbidden("You are not allowed to assign this role.", code="role_not_allowed")
        if current_role is Role.ADMIN and user.is_active:
            if await _count_other_active_admins(db, user.organization_id, user.id) == 0:
                raise Conflict("An organization must keep at least one active administrator.")
        summary["role"] = {"from": current_role.value, "to": changes.role.value}
        user.role = changes.role.value

    if (
        "is_active" in changes.fields
        and changes.is_active is not None
        and changes.is_active != user.is_active
    ):
        if is_self and not changes.is_active:
            raise Forbidden("You cannot deactivate your own account.", code="self_modification")
        if not changes.is_active and Role(user.role) is Role.ADMIN:
            if await _count_other_active_admins(db, user.organization_id, user.id) == 0:
                raise Conflict("An organization must keep at least one active administrator.")
        summary["is_active"] = {"from": user.is_active, "to": changes.is_active}
        user.is_active = changes.is_active
        if changes.is_active:
            user.failed_login_count = 0
            user.locked_until = None

    if "full_name" in changes.fields and changes.full_name is not None:
        name = changes.full_name.strip()
        if not name:
            raise ValidationFailed("Full name is required.", details={"field": "full_name"})
        if name != user.full_name:
            summary["full_name"] = True
            user.full_name = name

    password_changed = False
    if "password" in changes.fields and changes.password is not None:
        if is_self:
            # Your own password changes only through POST /auth/password, which checks the current one.
            raise Forbidden("Change your own password from your account page.", code="self_password_change")
        validate_password(changes.password, email=user.email, full_name=user.full_name)
        user.password_hash = await hash_password_async(changes.password)
        user.password_changed_at = _utcnow()
        user.must_change_password = True  # an admin reset: the user chooses their own at next sign-in
        user.failed_login_count = 0
        user.locked_until = None
        password_changed = True
        summary["password"] = "changed"  # noqa: S105  (audit marker, not a secret)

    await db.flush()
    if user.is_active is False and "is_active" in summary:
        await revoke_user_sessions(db, user.id, reason="deactivated")
    elif password_changed:
        await revoke_user_sessions(db, user.id, reason="password_changed")
    elif summary:
        await invalidate_user_auth_cache(db, user.id)
    await db.refresh(user)  # reload server-side onupdate columns (updated_at)
    return summary


async def deactivate_user(db: AsyncSession, actor: Principal, user: User) -> dict[str, Any]:
    return await update_user(db, actor, user, UserChanges(is_active=False, fields={"is_active"}))


async def change_own_password(
    db: AsyncSession,
    user: User,
    *,
    current_password: str,
    new_password: str,
    keep_family_id: uuid.UUID | None,
) -> None:
    """Self-service password change; other sessions of the user are revoked."""
    if not await verify_password_async(current_password, user.password_hash):
        # 422, not 401: a 401 would make the SPA try to refresh its session.
        raise ValidationFailed(
            "The current password is incorrect.",
            code="invalid_current_password",
            details={"field": "current_password"},
        )
    if current_password == new_password:
        raise ValidationFailed(
            "The new password must differ from the current one.", details={"field": "new_password"}
        )
    validate_password(new_password, email=user.email, full_name=user.full_name)
    user.password_hash = await hash_password_async(new_password)
    user.password_changed_at = _utcnow()
    user.must_change_password = False
    await db.flush()
    await revoke_user_sessions(db, user.id, reason="password_changed", keep_family_id=keep_family_id)
    # The kept session's cached auth state still says "must change": refresh it now, not in 30 s.
    await invalidate_user_auth_cache(db, user.id)
