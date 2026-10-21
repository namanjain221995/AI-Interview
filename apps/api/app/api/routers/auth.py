"""Staff authentication: login, refresh (rotation + reuse detection), logout, me.

Tokens travel only in cookies (see ``app.services.identity.cookies``). Every
response that issues a session also returns the CSRF token the SPA must echo
in ``X-CSRF-Token`` on unsafe requests.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.audit import audit
from app.core.deps import CurrentPrincipal, DbSession, OptionalPrincipal, StaffPrincipal, verify_csrf
from app.core.errors import AppError, Unauthorized, current_request_id, error_payload
from app.core.logging import get_logger
from app.core.metrics import AUTH_EVENTS
from app.core.ratelimit import client_ip, rate_limit
from app.core.rbac import Principal
from app.db.models import AuthSession, Organization, User
from app.domain.enums import Role, SubjectType
from app.schemas.api.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    OrganizationBrief,
    SessionResponse,
)
from app.schemas.api.common import ERROR_RESPONSES
from app.services.identity.cookies import (
    clear_auth_cookies,
    cookie_names,
    refresh_cookie,
    request_audience,
    set_auth_cookies,
)
from app.services.identity.sessions import (
    IssuedTokens,
    RefreshTokenReuse,
    find_session_by_refresh_token,
    revoke_family,
    revoke_session_family_by_id,
    rotate_refresh_token,
    start_user_session,
)
from app.services.identity.users import authenticate, change_own_password

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)

LOGIN_IP_LIMIT = 30  # attempts per minute per client address (account lockout is separate)
REFRESH_IP_LIMIT = 120


async def build_me(db: DbSession, principal: Principal) -> MeResponse:
    org = await db.get(Organization, principal.organization_id) if principal.organization_id else None
    subject_id = principal.id
    assert subject_id is not None
    must_change = False
    if principal.user_id is not None:
        must_change = bool(
            await db.scalar(select(User.must_change_password).where(User.id == principal.user_id))
        )
    return MeResponse(
        subject_type=principal.subject_type,
        id=subject_id,
        role=principal.role,
        email=principal.email,
        display_name=principal.display_name,
        organization_id=principal.organization_id,
        organization=OrganizationBrief.model_validate(org) if org is not None else None,
        interview_id=principal.interview_id,
        permissions=sorted(p.value for p in principal.permissions),
        must_change_password=must_change,
    )


def principal_from_tokens(tokens: IssuedTokens, *, email: str | None, display_name: str | None) -> Principal:
    is_user = tokens.subject_type is SubjectType.USER
    return Principal(
        subject_type=tokens.subject_type,
        user_id=tokens.subject_id if is_user else None,
        candidate_id=None if is_user else tokens.subject_id,
        organization_id=tokens.organization_id,
        role=tokens.role,
        auth_session_id=tokens.auth_session_id,
        interview_id=tokens.interview_id,
        email=email,
        display_name=display_name,
    )


def _session_response(me: MeResponse, tokens: IssuedTokens) -> SessionResponse:
    return SessionResponse(
        principal=me,
        csrf_token=tokens.csrf_token,
        access_token_expires_at=tokens.access_expires_at,
        session_expires_at=tokens.session_expires_at,
    )


def _error_clearing_cookies(request: Request, exc: AppError) -> JSONResponse:
    response = JSONResponse(
        error_payload(exc.code, exc.message, current_request_id(request), exc.details),
        status_code=exc.status_code,
        headers=exc.headers,
    )
    clear_auth_cookies(response, request_audience(request))
    return response


@router.post(
    "/login",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit("login_ip", LOGIN_IP_LIMIT, 60, client_ip))],
    summary="Sign in with email and password",
)
async def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> SessionResponse:
    user = await authenticate(db, email=body.email, password=body.password, request=request)
    # A browser that signs in again supersedes its previous session family.
    previous = await find_session_by_refresh_token(db, request.cookies.get(cookie_names("staff").refresh))
    if previous is not None and previous.user_id == user.id and previous.revoked_at is None:
        await revoke_family(db, previous.family_id, reason="superseded")
    tokens = await start_user_session(db, user, request=request)
    principal = principal_from_tokens(tokens, email=user.email, display_name=user.full_name)
    await audit(db, principal, "auth.login", resource_type="user", resource_id=str(user.id), request=request)
    set_auth_cookies(response, tokens)
    AUTH_EVENTS.labels(event="login_succeeded").inc()
    log.info("auth.login", user_id=str(user.id))
    return _session_response(await build_me(db, principal), tokens)


@router.post(
    "/refresh",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit("refresh_ip", REFRESH_IP_LIMIT, 60, client_ip))],
    summary="Rotate the refresh token and issue a new access token",
)
async def refresh(request: Request, response: Response, db: DbSession) -> SessionResponse | JSONResponse:
    audience = request_audience(request)
    token, legacy = refresh_cookie(request)  # staff or candidate set, by audience (G2)
    row = await find_session_by_refresh_token(db, token)
    if row is not None and audience == "candidate" and row.subject_type != SubjectType.CANDIDATE:
        row = None  # the legacy fallback reached the staff session: a candidate page never rotates it
    if row is None:
        return _error_clearing_cookies(
            request,
            Unauthorized("Your session has expired. Please sign in again.", code="invalid_refresh_token"),
        )
    verify_csrf(request, row.id)
    try:
        tokens = await rotate_refresh_token(db, token, request=request)
    except RefreshTokenReuse as exc:
        AUTH_EVENTS.labels(event="refresh_reuse_detected").inc()
        await audit(
            db,
            None,
            "auth.refresh_reuse_detected",
            resource_type="auth_session",
            resource_id=str(row.family_id),
            organization_id=None,
            details={"subject_type": row.subject_type},
            request=request,
        )
        await db.commit()
        return _error_clearing_cookies(request, exc)
    except Unauthorized as exc:
        return _error_clearing_cookies(request, exc)
    email: str | None = None
    name: str | None = None
    if tokens.subject_type is SubjectType.USER:
        user = await db.get(User, tokens.subject_id)
        if user is not None:
            email, name = user.email, user.full_name
    principal = principal_from_tokens(tokens, email=email, display_name=name)
    if legacy and tokens.subject_type is SubjectType.CANDIDATE:
        clear_auth_cookies(response, "staff")  # a pre-G2 candidate session moves to the candidate names
        set_auth_cookies(response, tokens)
    elif audience == "staff" and tokens.subject_type is SubjectType.CANDIDATE:
        # A tab still running the pre-G2 bundle (no audience header) holds its candidate session under the staff
        # names and sends the CSRF token it reads there: keep them in place until that tab reloads.
        set_auth_cookies(response, tokens, names=cookie_names("staff"))
    else:
        set_auth_cookies(response, tokens)
    AUTH_EVENTS.labels(event="refresh").inc()
    return _session_response(await build_me(db, principal), tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End the current session")
async def logout(request: Request, db: DbSession, principal: OptionalPrincipal) -> Response:
    revoked: list[object] = []
    audience = request_audience(request)
    token, legacy = refresh_cookie(request)
    row = await find_session_by_refresh_token(db, token)
    if row is not None and audience == "candidate" and row.subject_type != SubjectType.CANDIDATE:
        row = None  # the legacy fallback reached the staff session: a candidate sign-out never ends it
    if principal is None and row is not None:
        verify_csrf(request, row.id)  # access token expired: bind CSRF to the refresh session
    if principal is not None and principal.auth_session_id is not None:
        revoked += await revoke_session_family_by_id(db, principal.auth_session_id, reason="logout")
    if row is not None and row.revoked_at is None:
        revoked += await revoke_family(db, row.family_id, reason="logout")
    if principal is not None or row is not None:
        await audit(
            db,
            principal,
            "auth.logout",
            resource_type="auth_session",
            resource_id=str(principal.auth_session_id if principal else row.id if row else ""),
            request=request,
            organization_id=principal.organization_id if principal else None,
        )
        AUTH_EVENTS.labels(event="logout").inc()
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(
        result, audience
    )  # only this audience: a candidate signing out keeps the staff session
    if legacy and row is not None:
        clear_auth_cookies(result, "staff")  # a pre-G2 candidate session lived under the staff names
    return result


@router.get("/me", response_model=MeResponse, summary="Current principal")
async def me(principal: CurrentPrincipal, db: DbSession) -> MeResponse:
    return await build_me(db, principal)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, summary="Change your own password")
async def change_password(
    body: ChangePasswordRequest, request: Request, principal: StaffPrincipal, db: DbSession
) -> Response:
    user = await db.get(User, principal.user_id)
    if user is None or Role(user.role) is Role.CANDIDATE:
        raise Unauthorized()
    family_id = None
    if principal.auth_session_id is not None:
        current = await db.get(AuthSession, principal.auth_session_id)
        family_id = current.family_id if current is not None else None
    await change_own_password(
        db,
        user,
        current_password=body.current_password,
        new_password=body.new_password,
        keep_family_id=family_id,
    )
    await audit(
        db, principal, "user.password_change", resource_type="user", resource_id=str(user.id), request=request
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
