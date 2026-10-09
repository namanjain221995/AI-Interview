"""Short-lived cache of per-auth-session state used by ``get_principal``.

Every authenticated request must know that the access token's auth session is
not revoked, that the user (or candidate interview) is still active, and the
subject's *current* role and organization (role changes apply immediately
instead of after the 15 minute JWT lifetime). That state is cached in Redis
under ``auth:sid:{sid}`` for ``AUTH_CACHE_TTL_SECONDS`` and explicitly
invalidated on logout, revocation, role change, deactivation and password
change, so the cache never extends access beyond a revocation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import AccessTokenClaims
from app.db.models import AuthSession, Candidate, Interview, Organization, User
from app.domain.enums import InterviewStatus, Role, SubjectType

log = get_logger(__name__)

AUTH_CACHE_PREFIX = "auth:sid:"
AUTH_CACHE_TTL_SECONDS = 30


@dataclass(slots=True)
class AuthState:
    ok: bool
    subject_id: str
    reason: str | None = None
    role: str | None = None
    organization_id: str | None = None
    interview_id: str | None = None
    email: str | None = None
    display_name: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> AuthState:
        data = json.loads(raw)
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})  # type: ignore[attr-defined]


def _key(session_id: uuid.UUID | str) -> str:
    return f"{AUTH_CACHE_PREFIX}{session_id}"


async def _query_user_state(db: AsyncSession, claims: AccessTokenClaims) -> AuthState:
    row = (
        await db.execute(
            select(
                AuthSession.revoked_at,
                User.is_active,
                User.role,
                User.organization_id,
                User.email,
                User.full_name,
                Organization.is_active.label("org_active"),
            )
            .join(User, User.id == AuthSession.user_id)
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(AuthSession.id == claims.auth_session_id, AuthSession.user_id == claims.subject_id)
        )
    ).first()
    if row is None:
        return AuthState(ok=False, subject_id=claims.sub, reason="session_not_found")
    if row.revoked_at is not None:
        return AuthState(ok=False, subject_id=claims.sub, reason="session_revoked")
    if not row.is_active:
        return AuthState(ok=False, subject_id=claims.sub, reason="account_disabled")
    if row.organization_id is not None and row.org_active is False:
        return AuthState(ok=False, subject_id=claims.sub, reason="organization_disabled")
    return AuthState(
        ok=True,
        subject_id=claims.sub,
        role=row.role,
        organization_id=str(row.organization_id) if row.organization_id else None,
        email=row.email,
        display_name=row.full_name,
    )


async def _query_candidate_state(db: AsyncSession, claims: AccessTokenClaims) -> AuthState:
    row = (
        await db.execute(
            select(
                AuthSession.revoked_at,
                AuthSession.interview_id,
                Candidate.organization_id,
                Candidate.email,
                Candidate.full_name,
                Interview.status.label("interview_status"),
            )
            .join(Candidate, Candidate.id == AuthSession.candidate_id)
            .outerjoin(Interview, Interview.id == AuthSession.interview_id)
            .where(AuthSession.id == claims.auth_session_id, AuthSession.candidate_id == claims.subject_id)
        )
    ).first()
    if row is None:
        return AuthState(ok=False, subject_id=claims.sub, reason="session_not_found")
    if row.revoked_at is not None:
        return AuthState(ok=False, subject_id=claims.sub, reason="session_revoked")
    if row.interview_id is not None and row.interview_status in (None, InterviewStatus.CANCELLED.value):
        return AuthState(ok=False, subject_id=claims.sub, reason="interview_unavailable")
    return AuthState(
        ok=True,
        subject_id=claims.sub,
        role=Role.CANDIDATE.value,
        organization_id=str(row.organization_id),
        interview_id=str(row.interview_id) if row.interview_id else None,
        email=row.email,
        display_name=row.full_name,
    )


async def load_auth_state(db: AsyncSession, redis: Redis | None, claims: AccessTokenClaims) -> AuthState:
    """Cached auth state for the token's session (Redis errors fall back to the DB)."""
    if redis is not None:
        try:
            raw = await redis.get(_key(claims.sid))
        except (RedisError, OSError) as exc:
            log.warning("auth_cache.read_failed", error=type(exc).__name__)
            raw = None
        if raw:
            try:
                state = AuthState.from_json(raw if isinstance(raw, str) else raw.decode())
                if state.subject_id == claims.sub:
                    return state
            except (ValueError, TypeError):
                pass
    if claims.typ is SubjectType.USER:
        state = await _query_user_state(db, claims)
    else:
        state = await _query_candidate_state(db, claims)
    if redis is not None:
        try:
            await redis.set(_key(claims.sid), state.to_json(), ex=AUTH_CACHE_TTL_SECONDS)
        except (RedisError, OSError) as exc:
            log.warning("auth_cache.write_failed", error=type(exc).__name__)
    return state


async def invalidate_auth_cache(session_ids: Iterable[uuid.UUID | str], redis: Redis | None = None) -> None:
    keys = [_key(sid) for sid in session_ids]
    if not keys:
        return
    if redis is None:
        from app.core.redis import get_redis

        redis = get_redis()
    try:
        for start in range(0, len(keys), 500):
            await redis.delete(*keys[start : start + 500])
    except (RedisError, OSError) as exc:
        log.warning("auth_cache.invalidate_failed", error=type(exc).__name__, count=len(keys))
