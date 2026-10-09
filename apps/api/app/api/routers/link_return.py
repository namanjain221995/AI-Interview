"""Personal "continue later" link (OPEN_LINKS.md sections 3.7, 9.4; unauthenticated).

``GET  /link-return/{token}``          organization, job and link names only
``POST /link-return/{token}/verify``   e-mail check -> candidate cookies + progress

Wrong e-mails are counted per registration; the link locks for
``LOGIN_LOCKOUT_SECONDS`` after every 5 consecutive failures and permanently
after 20 (a recruiter reissues the link).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.routers.candidate_access import InviteLocked
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import DbSession
from app.core.errors import AppError, NotFound, Unauthorized
from app.core.logging import get_logger
from app.core.ratelimit import enforce_rate_limit, rate_limit
from app.core.security import constant_time_equals
from app.db.models import Candidate, InterviewLink, Job, Organization
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.open_links import PersonalLinkInfo, PersonalVerifyRequest, PersonalVerifyResponse
from app.services.identity.cookies import set_auth_cookies
from app.services.open_links import personal
from app.services.open_links.constants import RegistrationStatus
from app.services.open_links.normalize import email_key
from app.services.open_links.progression import (
    build_progress,
    current_interview,
    next_step_for,
    sync_registration,
)
from app.services.open_links.registration import require_allowed_origin, start_link_session

log = get_logger(__name__)

router = APIRouter(prefix="/link-return", tags=["link-return"], responses=ERROR_RESPONSES)

INFO_IP_LIMIT = 60  # per minute
VERIFY_IP_LIMIT = 20  # per minute
VERIFY_REGISTRATION_LIMIT = 10  # per 5 minutes
RS = RegistrationStatus


class RegistrationGone(AppError):
    status_code = 410
    code = "registration_cancelled"
    message = "This registration was cancelled. Please contact the hiring team."


def _not_found() -> NotFound:
    return NotFound("This personal link isn't valid.", code="personal_link_not_found")


@router.get(
    "/{token}",
    response_model=PersonalLinkInfo,
    dependencies=[Depends(rate_limit("link_return_info", INFO_IP_LIMIT, 60))],
    summary="Personal link information",
)
async def personal_link_info(token: str, response: Response, db: DbSession) -> PersonalLinkInfo:
    registration = await personal.find_by_personal_token(db, token)
    if registration is None or registration.status == RS.BLOCKED.value:
        raise _not_found()
    link = await db.get(InterviewLink, registration.link_id)
    job = await db.get(Job, registration.job_id)
    org = await db.get(Organization, registration.organization_id)
    response.headers["Cache-Control"] = "no-store"
    return PersonalLinkInfo(
        organization_name=org.name if org else "",
        job_title=job.title if job else "",
        link_name=link.name if link else "",
        state="cancelled" if registration.status == RS.CANCELLED.value else "active",
    )


@router.post(
    "/{token}/verify",
    response_model=PersonalVerifyResponse,
    dependencies=[Depends(rate_limit("link_return_verify", VERIFY_IP_LIMIT, 60))],
    summary="Continue through the personal link",
)
async def verify_personal_link(
    token: str, body: PersonalVerifyRequest, request: Request, response: Response, db: DbSession
) -> PersonalVerifyResponse:
    require_allowed_origin(request)
    settings = get_settings()
    registration = await personal.find_by_personal_token(db, token)
    if registration is None or registration.status == RS.BLOCKED.value or registration.candidate_id is None:
        raise _not_found()
    await enforce_rate_limit(
        "link_return_registration", str(registration.id), limit=VERIFY_REGISTRATION_LIMIT, window_s=300
    )
    if registration.personal_failed_count >= personal.HARD_LOCK_AFTER:
        raise InviteLocked(
            "This personal link is locked after too many unsuccessful attempts. Please contact the hiring team.",
            details={"permanent": True},
        )
    remaining = await personal.lock_remaining(registration.id)
    if remaining > 0:
        raise InviteLocked(
            details={"retry_after_seconds": remaining}, headers={"Retry-After": str(remaining)}
        )

    matches = constant_time_equals(registration.email, body.email) | constant_time_equals(
        registration.email_key, email_key(body.email)
    )
    if not matches:
        registration.personal_failed_count = (registration.personal_failed_count or 0) + 1
        failures = registration.personal_failed_count
        await audit(
            db,
            None,
            "link.personal_link_failed",
            resource_type="link_registration",
            resource_id=registration.id,
            organization_id=registration.organization_id,
            details={"failures": failures},
            request=request,
        )
        await db.commit()  # the failure count must persist although we answer with an error
        if failures % personal.LOCKOUT_AFTER == 0:
            await personal.set_lock(registration.id, settings.login_lockout_seconds)
            raise InviteLocked(
                details={"retry_after_seconds": settings.login_lockout_seconds},
                headers={"Retry-After": str(settings.login_lockout_seconds)},
            )
        raise Unauthorized("That e-mail does not match this registration.", code="invalid_credentials")

    if registration.status == RS.CANCELLED.value:
        raise RegistrationGone()
    registration.personal_failed_count = 0
    await db.flush()  # sync re-reads the row (populate_existing); keep the reset
    await sync_registration(db, registration.id)
    interview = await current_interview(db, registration)
    candidate = await db.get(Candidate, registration.candidate_id)
    if interview is None or candidate is None:
        raise _not_found()
    tokens, principal = await start_link_session(db, candidate, interview, request)
    await audit(
        db,
        principal,
        "link.personal_link_verified",
        resource_type="link_registration",
        resource_id=registration.id,
        details={"link_id": str(registration.link_id)},
        request=request,
    )
    set_auth_cookies(response, tokens)
    response.headers["Cache-Control"] = "no-store"
    log.info("open_link.personal_verified", registration_id=str(registration.id))
    return PersonalVerifyResponse(
        interview_id=interview.id,
        next_step=await next_step_for(db, interview),  # type: ignore[arg-type]
        csrf_token=tokens.csrf_token,
        progress=await build_progress(db, registration, bound_interview_id=interview.id),
    )
