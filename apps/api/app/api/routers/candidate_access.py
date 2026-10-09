"""Candidate invite access (CONTRACTS.md section 4; CSRF-exempt, unauthenticated).

``GET  /candidate-access/{token}``         public invite info
``POST /candidate-access/{token}/verify``  email (+ access code) -> candidate cookies

Only the SHA-256 of the invite token is stored. Verification is rate limited
per client address and per invite, and an invite locks for
``LOGIN_LOCKOUT_SECONDS`` after every ``LOCKOUT_AFTER`` consecutive failures
(permanently after ``HARD_LOCK_AFTER``: the recruiter must regenerate the
link). Wrong email and wrong access code produce the same response.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import DbSession
from app.core.errors import AppError, NotFound, Unauthorized, ValidationFailed
from app.core.logging import get_logger
from app.core.ratelimit import enforce_rate_limit, rate_limit
from app.core.rbac import Principal
from app.core.redis import get_redis
from app.core.security import constant_time_equals, hash_token, verify_access_code_async
from app.db.models import Candidate, Interview, InterviewSession, Job, Organization, Round
from app.domain.enums import InterviewStatus, Role, SessionStatus, SubjectType
from app.realtime.context import first_name
from app.realtime.policy import parse_policy_snapshot
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.portal import CandidateVerifyRequest, CandidateVerifyResponse, InviteInfo
from app.services.identity.cookies import set_auth_cookies
from app.services.identity.sessions import start_candidate_session

log = get_logger(__name__)

router = APIRouter(prefix="/candidate-access", tags=["candidate-access"], responses=ERROR_RESPONSES)

INFO_IP_LIMIT = 60  # per minute
VERIFY_IP_LIMIT = 20  # per minute
VERIFY_INVITE_LIMIT = 10  # per 5 minutes
LOCKOUT_AFTER = 5
HARD_LOCK_AFTER = 20
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class InviteLocked(AppError):
    status_code = 423
    code = "invite_locked"
    message = "Too many unsuccessful attempts. Please wait before trying again."


class InviteGone(AppError):
    status_code = 410
    code = "invite_unavailable"
    message = "This interview invitation is no longer available."


def lock_key(interview_id: uuid.UUID) -> str:
    return f"invite:lock:{interview_id}"


def invite_expired(interview: Interview, now: datetime | None = None) -> bool:
    expires = interview.invite_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return interview.status == InterviewStatus.EXPIRED.value or (
        expires <= (now or datetime.now(UTC)) and interview.status not in (InterviewStatus.COMPLETED.value,)
    )


def ensure_invite_usable(interview: Interview) -> None:
    """Raise 410 for cancelled / expired invitations (completed ones stay usable for the report)."""
    if interview.status == InterviewStatus.CANCELLED.value:
        raise InviteGone("This interview invitation was cancelled.", code="invite_cancelled")
    if invite_expired(interview):
        raise InviteGone("This interview invitation has expired.", code="invite_expired")


async def find_invite(db: AsyncSession, token: str) -> Interview | None:
    if not token or not _TOKEN.match(token):
        return None
    return await db.scalar(select(Interview).where(Interview.invite_token_hash == hash_token(token)))


@router.get(
    "/{token}",
    response_model=InviteInfo,
    dependencies=[Depends(rate_limit("candidate_access_info", INFO_IP_LIMIT, 60))],
)
async def invite_info(token: str, db: DbSession) -> InviteInfo:
    interview = await find_invite(db, token)
    if interview is None:
        raise NotFound("This invitation link is not valid.", code="invite_not_found")
    org = await db.get(Organization, interview.organization_id)
    job = await db.get(Job, interview.job_id)
    candidate = await db.get(Candidate, interview.candidate_id)
    round_ = await db.get(Round, interview.round_id) if interview.round_id else None
    policy = parse_policy_snapshot(interview.policy_snapshot)
    expired = invite_expired(interview)
    status = (
        InterviewStatus(interview.status)
        if interview.status in InterviewStatus._value2member_map_
        else InterviewStatus.INVITED
    )
    if expired and status is InterviewStatus.INVITED:
        status = InterviewStatus.EXPIRED
    return InviteInfo(
        organization_name=org.name if org else "",
        job_title=job.title if job else "",
        round_name=round_.name if round_ else None,
        candidate_first_name=first_name(candidate.full_name if candidate else None),
        requires_access_code=interview.access_code_hash is not None,
        expires_at=interview.invite_expires_at,
        expired=expired,
        cancelled=interview.status == InterviewStatus.CANCELLED.value,
        completed=interview.status == InterviewStatus.COMPLETED.value,
        status=status,
        interviewer_name=policy.avatar.interviewer_name,
        duration_minutes=await _planned_minutes(db, interview, round_),
    )


def round_minutes(round_: Round) -> int | None:
    """The round's planned duration (its config wins over the column)."""
    config = round_.config if isinstance(round_.config, dict) else {}
    value = config.get("duration_minutes") or round_.duration_minutes
    try:
        minutes = int(value) if value else None
    except (TypeError, ValueError):
        return None
    return minutes if minutes and minutes > 0 else None


async def _planned_minutes(db: AsyncSession, interview: Interview, round_: Round | None) -> int | None:
    if round_ is not None:
        return round_minutes(round_)
    # Round chosen by the candidate: only a duration every selectable round shares.
    rounds = (
        await db.scalars(
            select(Round).where(Round.job_id == interview.job_id, Round.candidate_selectable.is_(True))
        )
    ).all()
    durations = {round_minutes(r) for r in rounds}
    return durations.pop() if len(durations) == 1 else None


async def _lock_remaining(interview_id: uuid.UUID) -> int:
    try:
        ttl = await get_redis().pttl(lock_key(interview_id))
    except (RedisError, OSError) as exc:
        log.warning("candidate_access.lock_check_failed", error=type(exc).__name__)
        return 0
    return math.ceil(int(ttl) / 1000) if ttl and int(ttl) > 0 else 0


async def _set_lock(interview_id: uuid.UUID, seconds: int) -> None:
    try:
        await get_redis().set(lock_key(interview_id), "1", ex=max(1, seconds))
    except (RedisError, OSError) as exc:
        log.warning("candidate_access.lock_set_failed", error=type(exc).__name__)


async def _next_step(db: AsyncSession, interview: Interview) -> str:
    if interview.status == InterviewStatus.COMPLETED.value:
        return "complete"
    live = await db.scalar(
        select(InterviewSession.id).where(
            InterviewSession.interview_id == interview.id,
            InterviewSession.status.in_([SessionStatus.LIVE.value, SessionStatus.PAUSED.value]),
        )
    )
    return "interview" if live is not None else "welcome"


@router.post(
    "/{token}/verify",
    response_model=CandidateVerifyResponse,
    dependencies=[Depends(rate_limit("candidate_access_verify", VERIFY_IP_LIMIT, 60))],
)
async def verify_invite(
    token: str, body: CandidateVerifyRequest, request: Request, response: Response, db: DbSession
) -> CandidateVerifyResponse:
    settings = get_settings()
    interview = await find_invite(db, token)
    if interview is None:
        raise NotFound("This invitation link is not valid.", code="invite_not_found")
    ensure_invite_usable(interview)
    await enforce_rate_limit(
        "candidate_access_invite", str(interview.id), limit=VERIFY_INVITE_LIMIT, window_s=300
    )

    if interview.failed_verification_count >= HARD_LOCK_AFTER:
        raise InviteLocked(
            "This invitation is locked after too many unsuccessful attempts. Please contact the recruiter for a new link.",
            details={"permanent": True},
        )
    remaining = await _lock_remaining(interview.id)
    if remaining > 0:
        raise InviteLocked(
            details={"retry_after_seconds": remaining}, headers={"Retry-After": str(remaining)}
        )

    if interview.access_code_hash is not None and not (body.access_code or "").strip():
        raise ValidationFailed(
            "Please enter the access code from your invitation.", code="access_code_required"
        )

    candidate = await db.get(Candidate, interview.candidate_id)
    email_ok = candidate is not None and constant_time_equals(candidate.email.strip().lower(), body.email)
    code_ok = True
    if interview.access_code_hash is not None:
        code_ok = await verify_access_code_async(body.access_code or "", interview.access_code_hash)

    if not (email_ok and code_ok) or candidate is None:
        interview.failed_verification_count = (interview.failed_verification_count or 0) + 1
        failures = interview.failed_verification_count
        await audit(
            db,
            None,
            "candidate.verify_failed",
            resource_type="interview",
            resource_id=str(interview.id),
            organization_id=interview.organization_id,
            details={"failures": failures, "reason": "email" if not email_ok else "access_code"},
            request=request,
        )
        await db.commit()  # the failure count must persist although we answer with an error
        if failures % LOCKOUT_AFTER == 0:
            await _set_lock(interview.id, settings.login_lockout_seconds)
            log.warning("candidate_access.locked", interview_id=str(interview.id), failures=failures)
            raise InviteLocked(
                details={"retry_after_seconds": settings.login_lockout_seconds},
                headers={"Retry-After": str(settings.login_lockout_seconds)},
            )
        raise Unauthorized(
            "The email or access code does not match this invitation.", code="invalid_credentials"
        )

    now = datetime.now(UTC)
    interview.failed_verification_count = 0
    interview.verified_at = now
    if interview.invite_opened_at is None:
        interview.invite_opened_at = now
    tokens = await start_candidate_session(
        db,
        candidate_id=candidate.id,
        interview_id=interview.id,
        organization_id=interview.organization_id,
        request=request,
    )
    principal = Principal(
        subject_type=SubjectType.CANDIDATE,
        candidate_id=candidate.id,
        organization_id=interview.organization_id,
        role=Role.CANDIDATE,
        auth_session_id=tokens.auth_session_id,
        interview_id=interview.id,
        email=candidate.email,
        display_name=candidate.full_name,
    )
    await audit(
        db,
        principal,
        "candidate.verified",
        resource_type="interview",
        resource_id=str(interview.id),
        request=request,
    )
    set_auth_cookies(response, tokens)
    next_step = await _next_step(db, interview)
    log.info("candidate_access.verified", interview_id=str(interview.id))
    return CandidateVerifyResponse(
        interview_id=interview.id, next_step=next_step, csrf_token=tokens.csrf_token
    )  # type: ignore[arg-type]
