"""Auth sessions: creation, refresh-token rotation with reuse detection, revocation.

One ``auth_sessions`` row per refresh token. Rotation marks the presented row
``used_at`` and inserts a successor in the same ``family_id``. Presenting an
already-rotated token outside a short grace window revokes the whole family
(token theft signal). The grace window (``REFRESH_REUSE_GRACE_SECONDS``)
tolerates two browser tabs refreshing concurrently: the late request receives
a fresh access token for the successor session and no new refresh cookie (the
browser already stored the successor's cookie from the first response).

Staff refresh tokens slide (each rotation extends ``REFRESH_TOKEN_TTL_SECONDS``);
candidate sessions have an absolute lifetime of
``CANDIDATE_SESSION_TTL_SECONDS`` from verification.
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.core.security import create_access_token, create_csrf_token, generate_refresh_token, hash_token
from app.db.models import AuthSession, Candidate, Interview, Organization, User
from app.domain.enums import InterviewStatus, Role, SubjectType
from app.services.identity.auth_cache import invalidate_auth_cache

if TYPE_CHECKING:
    from fastapi import Request

log = get_logger(__name__)

REFRESH_REUSE_GRACE_SECONDS = 10
MAX_REFRESH_TOKEN_LENGTH = 256


class RefreshTokenReuse(Unauthorized):
    code = "refresh_token_reused"
    message = "This session is no longer valid. Please sign in again."


@dataclass(slots=True)
class IssuedTokens:
    subject_type: SubjectType
    subject_id: uuid.UUID
    role: Role
    organization_id: uuid.UUID | None
    interview_id: uuid.UUID | None
    auth_session_id: uuid.UUID
    family_id: uuid.UUID
    session_expires_at: datetime
    access_token: str
    access_expires_at: datetime
    csrf_token: str
    refresh_token: str | None
    refresh_expires_at: datetime | None


@dataclass(slots=True)
class _Subject:
    role: Role
    organization_id: uuid.UUID | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _request_meta(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ip: str | None = None
    if request.client is not None:
        try:
            ip = str(ipaddress.ip_address(request.client.host))
        except ValueError:
            ip = None
    agent = request.headers.get("user-agent")
    return ip, agent[:512] if agent else None


async def _insert_session(
    db: AsyncSession,
    *,
    subject_type: SubjectType,
    family_id: uuid.UUID,
    expires_at: datetime,
    user_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    interview_id: uuid.UUID | None = None,
    rotated_from_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> tuple[AuthSession, str]:
    token = generate_refresh_token()
    ip, agent = _request_meta(request)
    row = AuthSession(
        id=uuid.uuid4(),
        subject_type=subject_type.value,
        user_id=user_id,
        candidate_id=candidate_id,
        interview_id=interview_id,
        family_id=family_id,
        refresh_token_hash=hash_token(token),
        rotated_from_id=rotated_from_id,
        expires_at=expires_at,
        ip_address=ip,
        user_agent=agent,
    )
    db.add(row)
    await db.flush()
    return row, token


def _issue(row: AuthSession, subject: _Subject, *, refresh_token: str | None) -> IssuedTokens:
    subject_type = SubjectType(row.subject_type)
    subject_id = row.user_id if subject_type is SubjectType.USER else row.candidate_id
    assert subject_id is not None
    access, access_exp = create_access_token(
        subject_id=subject_id,
        subject_type=subject_type,
        role=subject.role,
        organization_id=subject.organization_id,
        auth_session_id=row.id,
        interview_id=row.interview_id,
    )
    if access_exp > row.expires_at:  # never outlive the session itself
        access, access_exp = create_access_token(
            subject_id=subject_id,
            subject_type=subject_type,
            role=subject.role,
            organization_id=subject.organization_id,
            auth_session_id=row.id,
            interview_id=row.interview_id,
            ttl_seconds=max(1, int((row.expires_at - _utcnow()).total_seconds())),
        )
    return IssuedTokens(
        subject_type=subject_type,
        subject_id=subject_id,
        role=subject.role,
        organization_id=subject.organization_id,
        interview_id=row.interview_id,
        auth_session_id=row.id,
        family_id=row.family_id,
        session_expires_at=row.expires_at,
        access_token=access,
        access_expires_at=access_exp,
        csrf_token=create_csrf_token(row.id),
        refresh_token=refresh_token,
        refresh_expires_at=row.expires_at if refresh_token is not None else None,
    )


async def start_user_session(db: AsyncSession, user: User, *, request: Request | None = None) -> IssuedTokens:
    """New session family for a staff user that just authenticated."""
    settings = get_settings()
    row, token = await _insert_session(
        db,
        subject_type=SubjectType.USER,
        family_id=uuid.uuid4(),
        expires_at=_utcnow() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_id=user.id,
        request=request,
    )
    return _issue(row, _Subject(role=Role(user.role), organization_id=user.organization_id), refresh_token=token)


async def start_candidate_session(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    interview_id: uuid.UUID,
    organization_id: uuid.UUID,
    request: Request | None = None,
    ttl_seconds: int | None = None,
) -> IssuedTokens:
    """New session family for a candidate verified for one interview.

    Used by the candidate-access flow; set the cookies with
    :func:`app.services.identity.cookies.set_auth_cookies`.
    """
    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.candidate_session_ttl_seconds
    row, token = await _insert_session(
        db,
        subject_type=SubjectType.CANDIDATE,
        family_id=uuid.uuid4(),
        expires_at=_utcnow() + timedelta(seconds=ttl),
        candidate_id=candidate_id,
        interview_id=interview_id,
        request=request,
    )
    return _issue(row, _Subject(role=Role.CANDIDATE, organization_id=organization_id), refresh_token=token)


async def _load_subject(db: AsyncSession, row: AuthSession) -> _Subject:
    """Current role/org of the session's subject, or raise if access must end."""
    if row.subject_type == SubjectType.USER.value:
        user = await db.get(User, row.user_id) if row.user_id else None
        if user is None or not user.is_active:
            raise Unauthorized("This account is disabled.", code="account_disabled")
        if user.organization_id is not None:
            org_active = await db.scalar(select(Organization.is_active).where(Organization.id == user.organization_id))
            if org_active is False:
                raise Unauthorized("This organization is disabled.", code="organization_disabled")
        return _Subject(role=Role(user.role), organization_id=user.organization_id)
    candidate = await db.get(Candidate, row.candidate_id) if row.candidate_id else None
    if candidate is None:
        raise Unauthorized("This session is no longer valid.", code="session_revoked")
    if row.interview_id is not None:
        status = await db.scalar(select(Interview.status).where(Interview.id == row.interview_id))
        if status is None or status == InterviewStatus.CANCELLED.value:
            raise Unauthorized("This interview is no longer available.", code="interview_unavailable")
    return _Subject(role=Role.CANDIDATE, organization_id=candidate.organization_id)


async def find_session_by_refresh_token(db: AsyncSession, refresh_token: str | None, *, for_update: bool = False) -> AuthSession | None:
    if not refresh_token or len(refresh_token) > MAX_REFRESH_TOKEN_LENGTH:
        return None
    stmt = select(AuthSession).where(AuthSession.refresh_token_hash == hash_token(refresh_token))
    if for_update:
        # populate_existing: the row may already sit in the identity map with
        # values read before the lock was granted (concurrent rotation).
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(stmt)


async def rotate_refresh_token(db: AsyncSession, refresh_token: str | None, *, request: Request | None = None) -> IssuedTokens:
    """Exchange a refresh token for new tokens (see module docstring).

    Raises :class:`RefreshTokenReuse` after revoking the family (already
    committed, so the revocation survives the error response) when a rotated
    token is replayed.
    """
    settings = get_settings()
    row = await find_session_by_refresh_token(db, refresh_token, for_update=True)
    if row is None:
        raise Unauthorized("Your session has expired. Please sign in again.", code="invalid_refresh_token")
    if row.revoked_at is not None:
        raise Unauthorized("Your session has ended. Please sign in again.", code="session_revoked")
    now = _utcnow()
    if row.used_at is not None:
        successor = await db.scalar(
            select(AuthSession)
            .where(AuthSession.family_id == row.family_id, AuthSession.rotated_from_id == row.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        within_grace = (now - row.used_at).total_seconds() <= REFRESH_REUSE_GRACE_SECONDS
        if successor is not None and successor.revoked_at is None and within_grace and successor.expires_at > now:
            subject = await _load_subject(db, successor)
            log.info("auth.refresh_grace_reissue", auth_session_id=str(successor.id))
            return _issue(successor, subject, refresh_token=None)
        revoked = await revoke_family(db, row.family_id, reason="reuse_detected")
        await db.commit()
        log.warning("auth.refresh_reuse_detected", family_id=str(row.family_id), revoked=len(revoked))
        raise RefreshTokenReuse(details={"family_id": str(row.family_id)})
    if row.expires_at <= now:
        raise Unauthorized("Your session has expired. Please sign in again.", code="session_expired")
    try:
        subject = await _load_subject(db, row)
    except Unauthorized:
        await revoke_family(db, row.family_id, reason="subject_disabled")
        await db.commit()
        raise
    row.used_at = now
    if row.subject_type == SubjectType.USER.value:
        expires_at = now + timedelta(seconds=settings.refresh_token_ttl_seconds)
    else:
        expires_at = row.expires_at  # absolute candidate lifetime
    new_row, token = await _insert_session(
        db,
        subject_type=SubjectType(row.subject_type),
        family_id=row.family_id,
        expires_at=expires_at,
        user_id=row.user_id,
        candidate_id=row.candidate_id,
        interview_id=row.interview_id,
        rotated_from_id=row.id,
        request=request,
    )
    return _issue(new_row, subject, refresh_token=token)


def _schedule_invalidation(db: AsyncSession, session_ids: list[uuid.UUID]) -> None:
    if not session_ids:
        return
    ids = list(session_ids)

    async def _invalidate() -> None:
        await invalidate_auth_cache(ids)

    run_after_commit(db, _invalidate)


async def revoke_family(db: AsyncSession, family_id: uuid.UUID, *, reason: str) -> list[uuid.UUID]:
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_utcnow(), revoked_reason=reason[:64])
        .returning(AuthSession.id)
        .execution_options(synchronize_session=False)
    )
    ids = list(result.scalars().all())
    await invalidate_auth_cache(ids)
    _schedule_invalidation(db, ids)
    return ids


async def revoke_session_family_by_id(db: AsyncSession, auth_session_id: uuid.UUID, *, reason: str) -> list[uuid.UUID]:
    family_id = await db.scalar(select(AuthSession.family_id).where(AuthSession.id == auth_session_id))
    if family_id is None:
        return []
    return await revoke_family(db, family_id, reason=reason)


async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, reason: str, keep_family_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep_family_id is not None:
        stmt = stmt.where(AuthSession.family_id != keep_family_id)
    result = await db.execute(
        stmt.values(revoked_at=_utcnow(), revoked_reason=reason[:64])
        .returning(AuthSession.id)
        .execution_options(synchronize_session=False)
    )
    ids = list(result.scalars().all())
    await invalidate_auth_cache(ids)
    _schedule_invalidation(db, ids)
    return ids


async def invalidate_user_auth_cache(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Drop cached auth state of a user's live sessions (role/active changed)."""
    ids = list(
        (
            await db.scalars(
                select(AuthSession.id).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            )
        ).all()
    )
    await invalidate_auth_cache(ids)
    _schedule_invalidation(db, ids)
