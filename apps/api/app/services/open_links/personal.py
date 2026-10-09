"""Personal "continue later" links ``/r/<token>`` (OPEN_LINKS.md section 3.7).

32 random bytes; only the SHA-256 is stored. Verification is an e-mail check
like invite verification: rate limited per IP and per registration, locked for
``LOGIN_LOCKOUT_SECONDS`` after every ``LOCKOUT_AFTER`` consecutive failures and
permanently after ``HARD_LOCK_AFTER`` (a recruiter reissues the link).
"""

from __future__ import annotations

import math
import secrets
import uuid
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import hash_token
from app.db.models import LinkRegistration
from app.services.open_links.links import personal_url, valid_token

log = get_logger(__name__)

LOCKOUT_AFTER = 5
HARD_LOCK_AFTER = 20


def lock_key(registration_id: uuid.UUID) -> str:
    return f"openlink:personal:lock:{registration_id}"


def issue_personal_token(registration: LinkRegistration) -> str:
    """Rotate the personal token; returns the full personal URL (shown once)."""
    token = secrets.token_urlsafe(32)
    registration.personal_token_hash = hash_token(token)
    registration.personal_token_issued_at = datetime.now(UTC)
    registration.personal_failed_count = 0
    return personal_url(token)


async def find_by_personal_token(db: AsyncSession, token: str) -> LinkRegistration | None:
    if not valid_token(token):
        return None
    return await db.scalar(
        select(LinkRegistration).where(LinkRegistration.personal_token_hash == hash_token(token))
    )


async def lock_remaining(registration_id: uuid.UUID) -> int:
    try:
        ttl = await get_redis().pttl(lock_key(registration_id))
    except (RedisError, OSError) as exc:
        log.warning("open_link.personal_lock_check_failed", error=type(exc).__name__)
        return 0
    return math.ceil(int(ttl) / 1000) if ttl and int(ttl) > 0 else 0


async def set_lock(registration_id: uuid.UUID, seconds: int) -> None:
    try:
        await get_redis().set(lock_key(registration_id), "1", ex=max(1, seconds))
    except (RedisError, OSError) as exc:
        log.warning("open_link.personal_lock_set_failed", error=type(exc).__name__)


async def clear_lock(registration_id: uuid.UUID) -> None:
    try:
        await get_redis().delete(lock_key(registration_id))
    except (RedisError, OSError) as exc:
        log.warning("open_link.personal_lock_clear_failed", error=type(exc).__name__)
