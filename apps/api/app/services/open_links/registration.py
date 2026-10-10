"""Public registration through an open link (OPEN_LINKS.md sections 3.3, 4).

One request = one transaction. Outcomes that must leave a trace although the
request fails (audit of duplicates/honeypot hits, ``blocked`` rows) commit
before raising, like the invite verification's failure counter.

Logs carry ids and outcomes only - never tokens, e-mails, phones or answers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from redis.exceptions import RedisError
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.core.metrics import OPEN_LINK_REGISTRATIONS
from app.core.ratelimit import enforce_rate_limit
from app.core.rbac import Principal
from app.core.redis import get_redis
from app.core.security import constant_time_equals, verify_access_code_async
from app.db.models import Candidate, CandidateResume, Interview, InterviewLink, Job, LinkRegistration
from app.domain.enums import InterviewStatus, Role, SubjectType
from app.realtime.origin import origin_allowed
from app.schemas.api.open_links import RegisterRequest
from app.services.identity.sessions import IssuedTokens, start_candidate_session
from app.services.jobs.invitations import open_invitation_id
from app.services.open_links.constants import (
    ACTIVE_STATUSES,
    DEFAULT_DUPLICATE_POLICY,
    NETWORK_WINDOW_SECONDS,
    FlagCode,
    FlagReview,
    LinkState,
    RegistrationStatus,
    UnlockedBy,
)
from app.services.open_links.form_schema import ValidatedAnswers, validate_answers
from app.services.open_links.links import (
    data_use,
    find_link_by_token,
    first_available,
    link_state,
    lock_link,
    pipeline,
)
from app.services.open_links.personal import issue_personal_token
from app.services.open_links.progression import (
    current_interview,
    next_step_for,
    sync_registration,
    unlock_round,
)
from app.services.open_links.signals import RequestSignals, has_high_flag, make_flag

log = get_logger(__name__)

RS = RegistrationStatus
LINK_LIMIT = 600  # registrations per link per 10 minutes
ACCESS_CODE_FAILURES = 10  # per (link, IP) in 10 minutes
ACCESS_CODE_WINDOW_S = 600
GENERIC_REFUSAL = "We couldn't register you. Please contact the hiring team."
_PRIORITY = {
    RS.COMPLETED.value: 0,
    RS.AWAITING_REVIEW.value: 1,
    RS.IN_PROGRESS.value: 2,
    RS.REGISTERED.value: 3,
    RS.EXPIRED.value: 4,
    RS.CANCELLED.value: 5,
}


class AccessCodeLocked(AppError):
    status_code = 423
    code = "access_code_locked"
    message = "Too many wrong access codes. Please wait before trying again."


@dataclass(slots=True)
class RegisterResult:
    outcome: str  # registered | resumed
    interview_id: uuid.UUID
    next_step: str
    tokens: IssuedTokens
    personal_url: str | None
    device_cookie: str | None  # set when the browser had none


# ================================================================ guards


def require_allowed_origin(request: Request) -> None:
    """403 unless Origin (or Referer) is this platform's origin (cross-site form posts)."""
    settings = get_settings()
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if referer:
            try:
                parts = urlsplit(referer)
                origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else "null"
            except ValueError:
                origin = "null"
    if not origin_allowed(
        origin,
        host=request.headers.get("host"),
        public_base_url=settings.public_base_url,
        cors_allowed_origins=settings.cors_allowed_origins,
        allow_missing=not settings.is_production,
    ):
        raise Forbidden("This request is not allowed from this page.", code="origin_not_allowed")


def _count(outcome: str) -> None:
    OPEN_LINK_REGISTRATIONS.labels(outcome=outcome).inc()


async def _fail(
    db: AsyncSession,
    exc: AppError,
    *,
    outcome: str,
    action: str | None = None,
    link: InterviewLink | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
    resource_id: uuid.UUID | None = None,
) -> AppError:
    """Count, audit (persisted although the request fails) and return ``exc`` to raise."""
    _count(outcome)
    if action and link is not None:
        await audit(
            db,
            None,
            action,
            resource_type="link_registration" if resource_id else "interview_link",
            resource_id=resource_id or link.id,
            organization_id=link.organization_id,
            details={"link_id": str(link.id), **(details or {})},
            request=request,
        )
        await db.commit()
    log.info(
        "open_link.register_refused", outcome=outcome, code=exc.code, link_id=str(link.id) if link else None
    )
    return exc


async def _check_access_code(link: InterviewLink, code: str | None, signals: RequestSignals) -> None:
    lock = f"openlink:lock:{link.id}:{signals.ip_hash[:32]}"
    failures = f"openlink:codefail:{link.id}:{signals.ip_hash[:32]}"
    lockout = get_settings().login_lockout_seconds
    redis = get_redis()
    try:
        ttl = await redis.pttl(lock)
    except (RedisError, OSError):
        ttl = 0
    if ttl and int(ttl) > 0:
        seconds = max(1, int(ttl) // 1000)
        raise AccessCodeLocked(
            details={"retry_after_seconds": seconds}, headers={"Retry-After": str(seconds)}
        )
    if not (code or "").strip():
        raise ValidationFailed("Please enter the access code you were given.", code="access_code_required")
    assert link.access_code_hash is not None
    if await verify_access_code_async(code or "", link.access_code_hash):
        return
    count = 0
    try:
        count = int(await redis.incr(failures))
        if count == 1:
            await redis.expire(failures, ACCESS_CODE_WINDOW_S)
        if count >= ACCESS_CODE_FAILURES:
            await redis.set(lock, "1", ex=lockout)
            await redis.delete(failures)
    except (RedisError, OSError) as exc:
        log.warning("open_link.code_counter_failed", error=type(exc).__name__)
    if count >= ACCESS_CODE_FAILURES:
        raise AccessCodeLocked(
            details={"retry_after_seconds": lockout}, headers={"Retry-After": str(lockout)}
        )
    raise Unauthorized("That access code is not correct.", code="invalid_access_code")


async def _advisory_lock(db: AsyncSession, key: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


# ======================================================= identity checks


def _scope_filter(link: InterviewLink) -> Any:
    scope = (link.duplicate_policy or DEFAULT_DUPLICATE_POLICY).get("scope", "job")
    if scope == "link":
        return LinkRegistration.link_id == link.id
    return and_(
        LinkRegistration.organization_id == link.organization_id, LinkRegistration.job_id == link.job_id
    )


async def _identity_matches(
    db: AsyncSession, link: InterviewLink, answers: ValidatedAnswers
) -> list[LinkRegistration]:
    conditions = [LinkRegistration.email_key == answers.email_key]
    if answers.phone_key:
        conditions.append(LinkRegistration.phone_key == answers.phone_key)
    return list(
        (
            await db.scalars(
                select(LinkRegistration).where(
                    _scope_filter(link), LinkRegistration.status != RS.BLOCKED.value, or_(*conditions)
                )
            )
        ).all()
    )


async def start_link_session(
    db: AsyncSession, candidate: Candidate, interview: Interview, request: Request
) -> tuple[IssuedTokens, Principal]:
    now = datetime.now(UTC)
    interview.invite_opened_at = interview.invite_opened_at or now
    interview.verified_at = now
    await db.flush()
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
    return tokens, principal


async def _handle_match(
    db: AsyncSession,
    link: InterviewLink,
    answers: ValidatedAnswers,
    matches: list[LinkRegistration],
    signals: RequestSignals,
    request: Request,
) -> RegisterResult:
    """Exact identity match: never a second attempt. Returns ``resumed`` or raises."""
    for match in matches:
        await sync_registration(db, match.id)
    matches.sort(key=lambda r: (_PRIORITY.get(r.status, 9), -r.created_at.timestamp()))
    match = matches[0]
    matched_on = "email" if match.email_key == answers.email_key else "phone"
    state = match.status
    details = {"match": matched_on, "state": state, "registration_id": str(match.id)}
    if (
        state in ACTIVE_STATUSES
        and match.device_hash is not None
        and not signals.device_is_new
        and constant_time_equals(match.device_hash, signals.device_hash)
        and constant_time_equals(match.email_key, answers.email_key)
        and match.candidate_id is not None
    ):
        interview = await current_interview(db, match)
        candidate = await db.get(Candidate, match.candidate_id)
        if interview is not None and candidate is not None:
            tokens, principal = await start_link_session(db, candidate, interview, request)
            await audit(
                db,
                principal,
                "link.registration_resumed",
                resource_type="link_registration",
                resource_id=match.id,
                details={"link_id": str(link.id)},
                request=request,
            )
            _count("resumed")
            return RegisterResult(
                outcome="resumed",
                interview_id=interview.id,
                next_step=await next_step_for(db, interview),
                tokens=tokens,
                personal_url=None,
                device_cookie=None,
            )
    if state == RS.COMPLETED.value:
        exc: AppError = Conflict("You have already completed this interview.", code="already_completed")
        outcome = "already_completed"
    elif state == RS.AWAITING_REVIEW.value:
        number = match.current_round_index + 1
        exc = Conflict(
            f"You've finished round {number}. The hiring team will review it; "
            "open your personal link to check for the next round.",
            code="already_registered",
            details={"state": state, "round": number},
        )
        outcome = "already_registered"
    elif state in ACTIVE_STATUSES:
        exc = Conflict(
            "You're already registered. Continue with the personal link you saved, "
            "or on the device you registered from.",
            code="already_registered",
            details={"state": state, "resume_with": "personal_link"},
        )
        outcome = "already_registered"
    else:
        exc = Conflict(
            "You're already registered for this interview. Please contact the hiring team.",
            code="already_registered",
            details={"state": state},
        )
        outcome = "already_registered"
    raise await _fail(
        db,
        exc,
        outcome=outcome,
        action="link.registration_duplicate",
        link=link,
        details=details,
        request=request,
    )


async def _may_reuse_candidate(
    db: AsyncSession, link: InterviewLink, candidate: Candidate, signals: RequestSignals
) -> bool:
    """Whether an existing candidate record (matched by email only) may be attached to this registration.

    A record without history (no interview, no résumé) holds nothing beyond what the registrant typed and is
    reused as before. A record with history is only reused from a browser that already registered as this
    person in this organisation (the device cookie of one of their registrations) - the returning applicant
    on their own laptop; anyone else could be using somebody else's email address.
    """
    has_history = (
        await db.scalar(select(Interview.id).where(Interview.candidate_id == candidate.id).limit(1))
        is not None
        or await db.scalar(
            select(CandidateResume.id).where(CandidateResume.candidate_id == candidate.id).limit(1)
        )
        is not None
    )
    if not has_history:
        return True
    if signals.device_is_new:
        return False
    known = (
        await db.scalars(
            select(LinkRegistration.device_hash).where(
                LinkRegistration.organization_id == link.organization_id,
                LinkRegistration.candidate_id == candidate.id,
                LinkRegistration.device_hash.is_not(None),
            )
        )
    ).all()
    return any(constant_time_equals(known_hash, signals.device_hash) for known_hash in known if known_hash)


async def _recruiter_invitation_conflict(
    db: AsyncSession, link: InterviewLink, answers: ValidatedAnswers
) -> AppError | None:
    """Job scope: the person already has a (non-link) interview for this job."""
    if (link.duplicate_policy or DEFAULT_DUPLICATE_POLICY).get("scope", "job") != "job":
        return None
    candidate_id = await db.scalar(
        select(Candidate.id).where(
            Candidate.organization_id == link.organization_id, Candidate.email == answers.email
        )
    )
    if candidate_id is None:
        return None
    statuses = (
        await db.scalars(
            select(Interview.status).where(
                Interview.candidate_id == candidate_id,
                Interview.job_id == link.job_id,
                Interview.status != InterviewStatus.CANCELLED.value,
            )
        )
    ).all()
    if not statuses:
        return None
    if InterviewStatus.COMPLETED.value in statuses:
        return Conflict("You have already completed this interview.", code="already_completed")
    return Conflict(
        "You already have an invitation for this role. Please use the link in your invitation.",
        code="already_invited",
    )


# ========================================================== soft signals


async def _soft_signals(
    db: AsyncSession, link: InterviewLink, signals: RequestSignals
) -> tuple[list[dict[str, Any]], bool]:
    """Flags for the new registration and whether the link's device policy blocks it."""
    policy = {**DEFAULT_DUPLICATE_POLICY, **(link.duplicate_policy or {})}
    flags: list[dict[str, Any]] = []
    blocked = False
    if not signals.device_is_new:
        same_device = await db.scalar(
            select(LinkRegistration.id)
            .where(
                LinkRegistration.link_id == link.id,
                LinkRegistration.device_hash == signals.device_hash,
                LinkRegistration.status != RS.BLOCKED.value,
            )
            .order_by(LinkRegistration.created_at.desc())
            .limit(1)
        )
        if same_device is not None:
            flags.append(make_flag(FlagCode.SAME_DEVICE.value, same_device))
            blocked = policy.get("device") == "block"
    if policy.get("network") == "flag":
        since = datetime.now(UTC) - timedelta(seconds=NETWORK_WINDOW_SECONDS)
        same_network = await db.scalar(
            select(LinkRegistration.id)
            .where(
                LinkRegistration.link_id == link.id,
                LinkRegistration.ip_ua_hash == signals.ip_ua_hash,
                LinkRegistration.created_at >= since,
                LinkRegistration.status != RS.BLOCKED.value,
            )
            .order_by(LinkRegistration.created_at.desc())
            .limit(1)
        )
        if same_network is not None:
            flags.append(make_flag(FlagCode.SAME_NETWORK.value, same_network))
    return flags, blocked


# ================================================================ flow


def _disclosure_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


async def register(db: AsyncSession, token: str, body: RegisterRequest, request: Request) -> RegisterResult:
    require_allowed_origin(request)
    link = await find_link_by_token(db, token)
    if link is None:
        raise NotFound("This link isn't valid. Check you copied the whole link.", code="link_not_found")
    signals = RequestSignals(request)
    await enforce_rate_limit(
        "link_register_link_ip",
        f"{link.id}:{signals.ip_hash[:32]}",
        limit=link.per_ip_hourly_limit,
        window_s=3600,
    )
    await enforce_rate_limit("link_register_link", str(link.id), limit=LINK_LIMIT, window_s=600)
    job = await db.get(Job, link.job_id)
    state = await link_state(db, link, job)
    if state != LinkState.OPEN.value:
        raise await _fail(
            db,
            Conflict("Registration is not open.", code="link_unavailable", details={"state": state}),
            outcome="unavailable",
        )
    if body.website and body.website.strip():
        raise await _fail(
            db,
            BadRequest("We couldn't process this registration.", code="registration_rejected"),
            outcome="rejected",
            action="link.registration_rejected",
            link=link,
            details={"reason": "honeypot"},
            request=request,
        )
    if not body.privacy_ack:
        raise ValidationFailed(
            "Please confirm you have read how your data will be used.", code="privacy_ack_required"
        )
    if link.access_code_hash is not None:
        await _check_access_code(link, body.access_code, signals)
    answers = validate_answers(link.form_schema, dict(body.answers))

    # Serialise registrations of this link (window / max checks are exact).
    link = await lock_link(db, link.id)
    state = await link_state(db, link, job)
    if state != LinkState.OPEN.value:
        raise await _fail(
            db,
            Conflict("Registration is not open.", code="link_unavailable", details={"state": state}),
            outcome="unavailable",
        )
    await _advisory_lock(db, f"openlink:{link.organization_id}:email:{answers.email_key}")
    if answers.phone_key:
        await _advisory_lock(db, f"openlink:{link.organization_id}:phone:{answers.phone_key}")

    matches = await _identity_matches(db, link, answers)
    if matches:
        return await _handle_match(db, link, answers, matches, signals, request)
    invited = await _recruiter_invitation_conflict(db, link, answers)
    if invited is not None:
        raise await _fail(
            db,
            invited,
            outcome=invited.code,
            action="link.registration_duplicate",
            link=link,
            details={"match": "email", "state": invited.code},
            request=request,
        )

    now = datetime.now(UTC)
    flags, blocked = await _soft_signals(db, link, signals)
    use = (await data_use(db, link, job)).model_dump(mode="json")
    registration = LinkRegistration(
        organization_id=link.organization_id,
        link_id=link.id,
        job_id=link.job_id,
        status=RS.REGISTERED.value,
        full_name=answers.full_name,
        email=answers.email,
        email_key=answers.email_key,
        phone=answers.phone,
        phone_key=answers.phone_key,
        answers=answers.snapshot,
        privacy_ack_at=now,
        disclosure_hash=_disclosure_hash(use),
        device_hash=signals.device_hash,
        ip_hash=signals.ip_hash,
        ip_ua_hash=signals.ip_ua_hash,
        flags=flags,
        flag_review=FlagReview.OPEN.value if has_high_flag(flags) else FlagReview.NONE.value,
    )
    if blocked:
        registration.status = RS.BLOCKED.value
        registration.status_reason = "device_policy"
        db.add(registration)
        await db.flush()
        raise await _fail(
            db,
            Forbidden(GENERIC_REFUSAL, code="registration_blocked"),
            outcome="blocked",
            action="link.registration_blocked",
            link=link,
            details={"reason": "device_policy", "flags": [f["code"] for f in flags]},
            request=request,
            resource_id=registration.id,
        )

    candidate = await db.scalar(
        select(Candidate).where(
            Candidate.organization_id == link.organization_id, Candidate.email == answers.email
        )
    )
    if candidate is not None and candidate.archived_at is not None:
        raise await _fail(
            db, Conflict(GENERIC_REFUSAL, code="registration_unavailable"), outcome="unavailable"
        )
    if candidate is not None and await open_invitation_id(db, candidate.id, link.job_id) is not None:
        # The accurate answer for the person who already has an open invitation for this job (and nothing to
        # gain for anyone else); checked before the identity rule so no dead-end blocked row is created.
        raise await _fail(
            db,
            Conflict(
                "You already have an invitation for this role. Please use the link in your invitation.",
                code="already_invited",
            ),
            outcome="already_invited",
        )
    if candidate is not None and not await _may_reuse_candidate(db, link, candidate, signals):
        # S4 stop-gap until the email is verified (G4): attaching a stranger to an existing person's record
        # would sign them in as that person (their résumé and history). The attempt is kept as a blocked
        # registration the hiring team can review and allow (unblock_registration reuses the record).
        registration.status = RS.BLOCKED.value
        registration.status_reason = "identity_unverified"
        db.add(registration)
        await db.flush()
        raise await _fail(
            db,
            Conflict(GENERIC_REFUSAL, code="registration_unavailable"),
            outcome="identity_unverified",
            action="link.registration_identity_refused",
            link=link,
            details={"match": "email", "reason": "existing_candidate_unverified_device"},
            request=request,
            resource_id=registration.id,
        )
    if candidate is None:
        candidate = Candidate(
            organization_id=link.organization_id,
            full_name=answers.full_name,
            email=answers.email,
            phone=answers.phone,
            location=answers.location,
            source="open_link",
            created_by_id=link.created_by_id,
        )
        db.add(candidate)
        await db.flush()
    else:
        if not candidate.phone and answers.phone:
            candidate.phone = answers.phone
        if not candidate.location and answers.location:
            candidate.location = answers.location
    registration.candidate_id = candidate.id
    personal = issue_personal_token(registration)
    db.add(registration)
    await db.flush()

    target = first_available(await pipeline(db, link))
    if target is None:
        raise await _fail(
            db,
            Conflict("Registration is not open.", code="link_unavailable", details={"state": "closed"}),
            outcome="unavailable",
        )
    try:
        interview = await unlock_round(
            db, link=link, registration=registration, target=target, unlocked_by=UnlockedBy.REGISTRATION
        )
    except Conflict as exc:
        if exc.code == "interview_exists":
            raise await _fail(
                db,
                Conflict(
                    "You already have an invitation for this role. Please use the link in your invitation.",
                    code="already_invited",
                ),
                outcome="already_invited",
            ) from exc
        if exc.code == "job_not_open":
            raise await _fail(
                db,
                Conflict("Registration is not open.", code="link_unavailable", details={"state": "closed"}),
                outcome="unavailable",
            ) from exc
        raise await _fail(
            db, Conflict(GENERIC_REFUSAL, code="registration_unavailable"), outcome="unavailable"
        ) from exc
    registration.current_round_index = target.sequence

    tokens, principal = await start_link_session(db, candidate, interview, request)
    await audit(
        db,
        principal,
        "link.registration_created",
        resource_type="link_registration",
        resource_id=registration.id,
        details={"link_id": str(link.id), "flags": [f["code"] for f in flags]},
        request=request,
    )
    if flags:
        await audit(
            db,
            None,
            "link.registration_flagged",
            resource_type="link_registration",
            resource_id=registration.id,
            organization_id=link.organization_id,
            details={"link_id": str(link.id), "flags": [f["code"] for f in flags]},
        )
    _count("registered")
    log.info(
        "open_link.registered", link_id=str(link.id), registration_id=str(registration.id), flags=len(flags)
    )
    return RegisterResult(
        outcome="registered",
        interview_id=interview.id,
        next_step="welcome",
        tokens=tokens,
        personal_url=personal,
        device_cookie=signals.device_value if signals.device_is_new else None,
    )


async def unblock_registration(
    db: AsyncSession, link: InterviewLink, registration: LinkRegistration, *, principal: Principal
) -> str:
    """Recruiter override of a ``blocked`` row: candidate, first round, personal
    link (returned once). The caller audits with the reason."""
    if registration.status != RS.BLOCKED.value:
        raise Conflict("This registration is not blocked.", code="not_blocked")
    answers = ValidatedAnswers(
        full_name=registration.full_name,
        email=registration.email,
        email_key=registration.email_key,
        phone=registration.phone,
        phone_key=registration.phone_key,
        location=None,
        snapshot=registration.answers,
    )
    await _advisory_lock(db, f"openlink:{link.organization_id}:email:{answers.email_key}")
    if answers.phone_key:
        await _advisory_lock(db, f"openlink:{link.organization_id}:phone:{answers.phone_key}")
    if await _identity_matches(db, link, answers) or await _recruiter_invitation_conflict(db, link, answers):
        raise Conflict("This person is already registered for this job.", code="already_registered")
    candidate = await db.scalar(
        select(Candidate).where(
            Candidate.organization_id == link.organization_id, Candidate.email == answers.email
        )
    )
    if candidate is not None and candidate.archived_at is not None:
        raise Conflict("This candidate is archived. Restore the candidate first.", code="candidate_archived")
    if candidate is None:
        location = next(
            (
                f.get("value")
                for f in (registration.answers or {}).get("fields", [])
                if f.get("key") == "location"
            ),
            None,
        )
        candidate = Candidate(
            organization_id=link.organization_id,
            full_name=registration.full_name,
            email=registration.email,
            phone=registration.phone,
            location=location if isinstance(location, str) else None,
            source="open_link",
            created_by_id=principal.user_id or link.created_by_id,
        )
        db.add(candidate)
        await db.flush()
    target = first_available(await pipeline(db, link))
    if target is None:
        raise Conflict("The link has no available round.", code="round_missing")
    registration.candidate_id = candidate.id
    registration.status = RS.REGISTERED.value
    registration.status_reason = "recruiter_allowed"
    registration.privacy_ack_at = registration.privacy_ack_at or datetime.now(UTC)
    personal = issue_personal_token(registration)
    await db.flush()
    try:
        async with db.begin_nested():
            await unlock_round(
                db,
                link=link,
                registration=registration,
                target=target,
                unlocked_by=UnlockedBy.RECRUITER,
                user_id=principal.user_id,
            )
    except Conflict as exc:
        code = "already_registered" if exc.code == "interview_exists" else exc.code
        raise Conflict("The registration could not be allowed.", code=code) from exc
    registration.current_round_index = target.sequence
    await db.flush()
    return personal
