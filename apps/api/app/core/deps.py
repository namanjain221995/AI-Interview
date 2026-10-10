"""FastAPI dependencies shared by every router.

Database session
    ``Depends(get_db)`` (or the ``DbSession`` alias) yields one ``AsyncSession``
    per request. It commits after the endpoint returns *and before the response
    is sent* (a commit failure becomes an error response instead of a false
    200), rolls back if the endpoint raised, and then awaits callbacks queued
    with :func:`app.core.lifecycle.run_after_commit` (e.g. job dispatch).

    Implementation note: FastAPI runs the exit code of a generator dependency
    after the response is sent unless the dependency is declared with
    ``scope="function"``. ``get_db`` is therefore a plain coroutine wrapping a
    function-scoped generator, so callers get the safe behaviour from a plain
    ``Depends(get_db)``. Consequence: the session is closed before a
    ``StreamingResponse``/SSE body starts; generators that need the database
    while streaming must open their own ``app.db.session.session_scope()``.

Authentication
    ``get_principal`` accepts the ``access_token`` cookie or an
    ``Authorization: Bearer`` header, verifies the JWT, checks the auth session
    is not revoked and the subject still active (Redis-cached, explicitly
    invalidated), and enforces CSRF (header ``X-CSRF-Token`` == cookie
    ``csrf_token``, HMAC-bound to the session) on unsafe methods of
    cookie-authenticated requests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized, ValidationFailed
from app.core.lifecycle import drain_after_commit
from app.core.logging import bind_context
from app.core.rbac import Permission, Principal, permission
from app.core.redis import get_redis as _shared_redis
from app.core.security import AccessTokenClaims, constant_time_equals, decode_access_token, verify_csrf_token
from app.db.session import get_sessionmaker
from app.domain.enums import Role, SubjectType
from app.services.identity.auth_cache import load_auth_state
from app.services.identity.cookies import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CSRF_EXEMPT_PATHS = frozenset({"/api/v1/auth/login"})
# What a staff user whose password was issued by an administrator may call before choosing their own (S13).
PASSWORD_CHANGE_PATHS = frozenset({"/api/v1/auth/me", "/api/v1/auth/password", "/api/v1/auth/logout"})
CSRF_EXEMPT_PREFIXES = ("/api/v1/candidate-access/",)

# ----------------------------------------------------------------- database


async def _request_session() -> AsyncIterator[AsyncSession]:
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await drain_after_commit(session)
        await session.close()


async def get_db(
    session: Annotated[AsyncSession, Depends(_request_session, scope="function")],
) -> AsyncSession:
    return session


DbSession = Annotated[AsyncSession, Depends(get_db)]


# -------------------------------------------------------------------- redis


async def get_redis() -> Redis:
    """Shared ``str`` Redis client (async so FastAPI does not use a threadpool)."""
    return _shared_redis()


RedisClient = Annotated[Redis, Depends(get_redis)]


# ------------------------------------------------------------ authentication


def _extract_token(request: Request) -> tuple[str | None, bool]:
    """Return ``(token, from_cookie)``. An Authorization header wins over the cookie."""
    header = request.headers.get("authorization")
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise Unauthorized("Unsupported authorization scheme.", code="invalid_token")
        return value.strip(), False
    cookie = request.cookies.get(ACCESS_COOKIE)
    if cookie:
        return cookie, True
    return None, False


def csrf_exempt(path: str) -> bool:
    return path in CSRF_EXEMPT_PATHS or path.startswith(CSRF_EXEMPT_PREFIXES)


def verify_csrf(request: Request, auth_session_id: uuid.UUID | str) -> None:
    """Double-submit check bound to the auth session; raises 403 on failure."""
    if not get_settings().csrf_enabled:
        return
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(CSRF_COOKIE)
    if (
        not header
        or not cookie
        or not constant_time_equals(header, cookie)
        or not verify_csrf_token(auth_session_id, header)
    ):
        raise Forbidden("Missing or invalid CSRF token.", code="csrf_failed")


def _principal_from_state(claims: AccessTokenClaims, state: Any) -> Principal:
    org = uuid.UUID(state.organization_id) if state.organization_id else None
    if claims.typ is SubjectType.USER:
        return Principal(
            subject_type=SubjectType.USER,
            user_id=claims.subject_id,
            organization_id=org,
            role=Role(state.role),
            auth_session_id=claims.auth_session_id,
            email=state.email,
            display_name=state.display_name,
        )
    interview_id = uuid.UUID(state.interview_id) if state.interview_id else claims.interview_id
    return Principal(
        subject_type=SubjectType.CANDIDATE,
        candidate_id=claims.subject_id,
        organization_id=org,
        role=Role.CANDIDATE,
        auth_session_id=claims.auth_session_id,
        interview_id=interview_id,
        email=state.email,
        display_name=state.display_name,
    )


async def _authenticate(
    request: Request, db: AsyncSession, redis: Redis, *, required: bool
) -> Principal | None:
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached
    token, from_cookie = _extract_token(request)
    if token is None:
        if required:
            raise Unauthorized()
        return None
    claims = decode_access_token(token)
    state = await load_auth_state(db, redis, claims)
    if not state.ok:
        raise Unauthorized(
            "Your session is no longer valid. Please sign in again.", code=state.reason or "session_revoked"
        )
    principal = _principal_from_state(claims, state)
    if from_cookie and request.method.upper() not in SAFE_METHODS and not csrf_exempt(request.url.path):
        verify_csrf(request, claims.auth_session_id)
    if getattr(state, "must_change_password", False) and request.url.path not in PASSWORD_CHANGE_PATHS:
        raise Forbidden("Please choose a new password before continuing.", code="password_change_required")
    request.state.principal = principal
    bind_context(
        user_id=str(principal.user_id) if principal.user_id else None,
        candidate_id=str(principal.candidate_id) if principal.candidate_id else None,
        organization_id=str(principal.organization_id) if principal.organization_id else None,
    )
    return principal


async def get_principal(request: Request, db: DbSession, redis: RedisClient) -> Principal:
    principal = await _authenticate(request, db, redis, required=True)
    assert principal is not None
    return principal


async def get_optional_principal(request: Request, db: DbSession, redis: RedisClient) -> Principal | None:
    """Principal when valid credentials are present, else ``None`` (never raises 401)."""
    try:
        return await _authenticate(request, db, redis, required=False)
    except Unauthorized:
        return None


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]


async def get_staff_principal(principal: CurrentPrincipal) -> Principal:
    if not principal.is_staff:
        raise Forbidden("This endpoint is only available to staff accounts.")
    return principal


async def get_candidate_principal(principal: CurrentPrincipal) -> Principal:
    """Candidate principal; ``principal.interview_id`` is the only interview it may access."""
    if not principal.is_candidate or principal.interview_id is None:
        raise Forbidden("This endpoint is only available to candidates.")
    return principal


StaffPrincipal = Annotated[Principal, Depends(get_staff_principal)]
CandidatePrincipal = Annotated[Principal, Depends(get_candidate_principal)]


def require_permission(*perms: str | Permission) -> Callable[..., Awaitable[Principal]]:
    """Dependency requiring *all* ``perms``; returns the principal.

    ``principal: Annotated[Principal, Depends(require_permission("jobs:write"))]``
    """
    if not perms:
        raise ValueError("require_permission() needs at least one permission")
    required = tuple(permission(p) for p in perms)  # unknown names fail at import time

    async def _dependency(principal: CurrentPrincipal) -> Principal:
        missing = [p.value for p in required if not principal.has(p)]
        if missing:
            raise Forbidden(details={"missing_permissions": missing})
        return principal

    _dependency.__name__ = "require_" + "_".join(p.value.replace(":", "_") for p in required)
    return _dependency


def require_role(
    *roles: Role | str, perms: tuple[str | Permission, ...] = ()
) -> Callable[..., Awaitable[Principal]]:
    """Dependency requiring one of ``roles`` (and *all* ``perms``); returns the principal.

    For the few actions the permission matrix deliberately keeps above a
    permission, e.g. irreversible data-subject deletion (ADMIN, SUPER_ADMIN):
    ``Annotated[Principal, Depends(require_role(Role.ADMIN, Role.SUPER_ADMIN))]``.
    """
    if not roles:
        raise ValueError("require_role() needs at least one role")
    allowed = frozenset(Role(r) for r in roles)  # unknown names fail at import time
    required = tuple(permission(p) for p in perms)

    async def _dependency(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise Forbidden(
                "Your role does not allow this action.",
                details={"required_roles": sorted(r.value for r in allowed)},
            )
        missing = [p.value for p in required if not principal.has(p)]
        if missing:
            raise Forbidden(details={"missing_permissions": missing})
        return principal

    _dependency.__name__ = "require_role_" + "_".join(sorted(r.value.lower() for r in allowed))
    return _dependency


def resolve_org_id(principal: Principal, requested: uuid.UUID | None = None) -> uuid.UUID:
    """Organization a staff request operates on.

    SUPER_ADMIN may target any organization (``requested``) and defaults to
    their own; everyone else is pinned to their organization.
    """
    if principal.is_super_admin:
        org = requested or principal.organization_id
        if org is None:
            raise ValidationFailed("organization_id is required.", details={"field": "organization_id"})
        return org
    if principal.organization_id is None:
        raise Forbidden("No organization is associated with this account.")
    if requested is not None and requested != principal.organization_id:
        raise Forbidden("You cannot access another organization.")
    return principal.organization_id


async def get_org_scope(
    principal: StaffPrincipal,
    organization_id: Annotated[
        uuid.UUID | None, Query(description="SUPER_ADMIN only: target organization")
    ] = None,
) -> uuid.UUID:
    return resolve_org_id(principal, organization_id)


OrgScope = Annotated[uuid.UUID, Depends(get_org_scope)]


# ---------------------------------------------------------------- pagination

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class Pagination:
    """``?limit=&offset=``; list endpoints return ``{"items": [...], "total": n}``."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = DEFAULT_PAGE_SIZE,
        offset: Annotated[int, Query(ge=0, le=1_000_000, description="Items to skip")] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset

    def apply[S: Select](self, stmt: S) -> S:  # bare bound: Select is variadic (Select[*Ts])
        """``stmt`` with this page's LIMIT/OFFSET; keeps the select's row type."""
        return stmt.limit(self.limit).offset(self.offset)

    def __repr__(self) -> str:
        return f"Pagination(limit={self.limit}, offset={self.offset})"


PaginationParams = Annotated[Pagination, Depends(Pagination)]
