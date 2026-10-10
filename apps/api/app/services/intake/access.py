"""Intake access layer: feature flag, org-scoped lookups, the intake API route class and the candidate-portal
state (onboarding / résumé-review progress, start guard, journey step keys, ``PortalInterview.intake``).

* ``INTAKE_ENABLED=false``: every intake route answers 404 ``intake_disabled``; the portal start guard answers
  409 ``intake_disabled`` for an intake round without an open session; sessions already created finish normally.
* Staff lookups are scoped to the caller's organisation: an id of another organisation is a 404
  (``intake_not_found``), never a 403.
* :class:`IntakeRoute` turns request-validation errors into the platform envelope with ``details.field`` and, for
  the intake-specific error types of :data:`app.schemas.intake.INTAKE_ERROR_CODES`, that code as ``error.code``.

Logging hygiene: ids and counts only (never onboarding values, eligibility, résumé text).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentPrincipal, DbSession
from app.core.errors import Conflict, NotFound, ValidationFailed, sanitize_validation_errors
from app.core.logging import get_logger
from app.db.models import (
    CandidateResume,
    Consent,
    IntakeInterview,
    Interview,
    InterviewSession,
    Organization,
    Round,
)
from app.domain.enums import ResumeStatus, RoundType, SessionStatus
from app.schemas.intake import (
    INTAKE_ERROR_CODES,
    IntakeInviteSettings,
    ResumeReviewRecord,
)

log = get_logger(__name__)

INTAKE_ROUND_TYPE = RoundType.INTAKE.value
OPEN_SESSION_STATUSES = frozenset(
    {
        SessionStatus.CREATED.value,
        SessionStatus.PREPARING.value,
        SessionStatus.READY.value,
        SessionStatus.LIVE.value,
        SessionStatus.PAUSED.value,
    }
)
#: Once a session reached one of these, the candidate's onboarding answers and résumé review are locked.
LOCKING_SESSION_STATUSES = frozenset(
    {
        SessionStatus.LIVE.value,
        SessionStatus.PAUSED.value,
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
        SessionStatus.ABORTED.value,
        SessionStatus.FAILED.value,
    }
)
UNUSABLE_RESUME_STATUSES = frozenset({ResumeStatus.FAILED.value, ResumeStatus.REJECTED.value})

# Field suffixes for model-level intake errors (pydantic reports them at the model's location).
_CODE_FIELDS: Mapping[str, str] = {
    "work_authorization_country_required": "country",
    "target_role_other_required": "target_role_other",
    "primary_language_other_required": "primary_language_other",
    "candidate_or_candidate_id_required": "candidate",
    "document_required": "document",
    "invalid_claim_limits": "min_claims",
}
_LOCATION_ROOTS = frozenset({"body", "query", "path", "header", "cookie"})


# ================================================================================== feature flag


def intake_enabled() -> bool:
    return bool(get_settings().intake_enabled)


def ensure_enabled() -> None:
    if not intake_enabled():
        raise NotFound("Intake interviews are turned off for this workspace.", code="intake_disabled")


INTAKE_FEATURE = "intake"


async def intake_entitled(db: AsyncSession, organization_id: uuid.UUID | None) -> bool:
    """The platform switch is on and the organisation's plan includes intake (G8)."""
    if not intake_enabled() or organization_id is None:
        return False
    features = await db.scalar(select(Organization.features).where(Organization.id == organization_id))
    return bool((features or {}).get(INTAKE_FEATURE))


async def require_intake_enabled() -> None:
    """Router-level FastAPI dependency (``dependencies=[Depends(require_intake_enabled)]``): the platform
    switch. Listed first, so every intake route is a 404 while it is off, whoever asks."""
    ensure_enabled()


async def require_intake_entitled(principal: CurrentPrincipal, db: DbSession) -> None:
    """Staff intake routes: 404 unless the caller's organisation's plan includes intake (G8); platform staff
    see every organisation's. Candidate routes only use ``require_intake_enabled``: removing the feature from a
    plan never locks out candidates already in an intake."""
    if principal.is_super_admin:
        return
    if not await intake_entitled(db, principal.organization_id):
        raise NotFound("Intake interviews are not part of this workspace.", code="intake_disabled")


# ============================================================================ validation errors


def _field_of(error: Mapping[str, Any]) -> str:
    parts = [str(p) for p in error.get("loc", ())]
    if parts and parts[0] in _LOCATION_ROOTS:
        parts = parts[1:]
    out = ""
    for part in parts:
        if part.isdigit():
            out += f"[{part}]"
        else:
            out = f"{out}.{part}" if out else part
    suffix = _CODE_FIELDS.get(str(error.get("type", "")))
    if suffix and not out.endswith(suffix):
        out = f"{out}.{suffix}" if out else suffix
    return out


def intake_validation_error(errors: Sequence[Mapping[str, Any]]) -> ValidationFailed:
    """Platform 422 for pydantic errors: ``details.field`` (dotted path of the first error), ``details.errors``
    (all, sanitised) and, for intake error types, that type as the error code."""
    cleaned = sanitize_validation_errors(errors)
    for error in cleaned:
        if error["type"] in INTAKE_ERROR_CODES:
            return ValidationFailed(
                error["msg"], code=error["type"], details={"field": _field_of(error), "errors": cleaned}
            )
    first = _field_of(cleaned[0]) if cleaned else None
    return ValidationFailed("The request is invalid.", details={"field": first, "errors": cleaned})


def raise_validation(exc: ValidationError) -> None:
    """Re-raise a pydantic error raised while validating a document inside a handler."""
    raise intake_validation_error(exc.errors()) from None


class IntakeRoute(APIRoute):
    """APIRoute whose request-validation errors carry ``details.field`` and intake error codes."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise intake_validation_error(exc.errors()) from None

        return route_handler


# ===================================================================================== lookups


async def get_intake(
    db: AsyncSession, intake_id: uuid.UUID, organization_id: uuid.UUID, *, lock: bool = False
) -> IntakeInterview:
    """The organisation's intake row (404 ``intake_not_found`` otherwise). ``lock`` = ``SELECT ... FOR UPDATE``
    (flush pending changes first: ``populate_existing`` discards unflushed attributes)."""
    stmt = select(IntakeInterview).where(IntakeInterview.id == intake_id)
    if lock:
        await db.flush()
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None or row.organization_id != organization_id:
        raise NotFound("Intake interview not found.", code="intake_not_found")
    return row


async def intake_for_interview(db: AsyncSession, interview_id: uuid.UUID) -> IntakeInterview | None:
    return await db.scalar(select(IntakeInterview).where(IntakeInterview.interview_id == interview_id))


def is_intake_round(round_: Round | None) -> bool:
    return round_ is not None and round_.round_type == INTAKE_ROUND_TYPE


async def intake_round_of_interview(db: AsyncSession, interview: Interview) -> Round | None:
    """The interview's intake round: its round when that is an intake round, or (round selection) the first
    active selectable intake round of the job. ``None`` for every other interview."""
    if interview.round_id is not None:
        round_ = await db.get(Round, interview.round_id)
        return round_ if is_intake_round(round_) else None
    if interview.allow_round_selection:
        return await db.scalar(
            select(Round)
            .where(
                Round.job_id == interview.job_id,
                Round.is_active.is_(True),
                Round.candidate_selectable.is_(True),
                Round.round_type == INTAKE_ROUND_TYPE,
            )
            .order_by(Round.sequence, Round.created_at)
            .limit(1)
        )
    return None


def parse_settings(intake: IntakeInterview) -> IntakeInviteSettings:
    """The row's frozen settings (tolerant: an unreadable document falls back to defaults, logged)."""
    raw = intake.settings if isinstance(intake.settings, dict) else {}
    try:
        return IntakeInviteSettings.model_validate(raw)
    except ValidationError:
        log.warning("intake.settings_unreadable", intake_id=str(intake.id))
        known = {k: v for k, v in raw.items() if k in IntakeInviteSettings.model_fields}
        for name in list(known):
            try:
                IntakeInviteSettings.model_validate({name: known[name]})
            except ValidationError:
                known.pop(name)
        return IntakeInviteSettings.model_validate(known)


def parse_review_record(intake: IntakeInterview) -> ResumeReviewRecord | None:
    raw = intake.resume_review
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return ResumeReviewRecord.model_validate(raw)
    except ValidationError:
        log.warning("intake.resume_review_unreadable", intake_id=str(intake.id))
        return None


async def latest_session(db: AsyncSession, interview_id: uuid.UUID) -> InterviewSession | None:
    return await db.scalar(
        select(InterviewSession)
        .where(InterviewSession.interview_id == interview_id)
        .order_by(InterviewSession.created_at.desc())
        .limit(1)
    )


async def latest_resume(db: AsyncSession, interview: Interview) -> CandidateResume | None:
    """The candidate's current résumé (one uploaded for this interview first), as the portal shows it."""
    return await db.scalar(
        select(CandidateResume)
        .where(CandidateResume.candidate_id == interview.candidate_id)
        .order_by(
            case((CandidateResume.interview_id == interview.id, 0), else_=1),
            CandidateResume.created_at.desc(),
        )
        .limit(1)
    )


async def has_consent(db: AsyncSession, interview_id: uuid.UUID) -> bool:
    row = await db.scalar(
        select(Consent.id)
        .where(Consent.interview_id == interview_id, Consent.withdrawn_at.is_(None))
        .order_by(Consent.accepted_at.desc())
        .limit(1)
    )
    return row is not None


def resume_usable(resume: CandidateResume | None) -> bool:
    return resume is not None and resume.status not in UNUSABLE_RESUME_STATUSES


def review_done(record: ResumeReviewRecord | None, resume: CandidateResume | None) -> tuple[bool, bool]:
    """``(confirmed, skipped)`` for the *current* résumé (a new upload needs a new review)."""
    if record is None or resume is None or record.source.resume_id != resume.id:
        return False, False
    return record.action == "confirmed", record.action == "skipped"


# ================================================================================ portal state


@dataclass(slots=True)
class PortalState:
    """Candidate-side intake progress for one interview."""

    intake: IntakeInterview
    interview: Interview
    settings: IntakeInviteSettings
    consent: bool
    resume: CandidateResume | None
    session: InterviewSession | None
    record: ResumeReviewRecord | None
    missing: list[str] = field(default_factory=list)

    @property
    def resume_usable(self) -> bool:
        return resume_usable(self.resume)

    @property
    def onboarding_submitted(self) -> bool:
        return self.intake.onboarding_submitted_at is not None and bool(self.intake.onboarding)

    @property
    def locked(self) -> bool:
        return self.session is not None and self.session.status in LOCKING_SESSION_STATUSES

    @property
    def review_status(self) -> tuple[bool, bool]:
        return review_done(self.record, self.resume)

    @property
    def review_required(self) -> bool:
        return self.resume_usable

    @property
    def review_complete(self) -> bool:
        confirmed, skipped = self.review_status
        return confirmed or skipped or not self.review_required

    @property
    def ready_to_start(self) -> bool:
        return not self.missing


def _missing(state: PortalState) -> list[str]:
    missing: list[str] = []
    if not state.consent:
        missing.append("consent")
    if state.settings.resume_required and not state.resume_usable:
        missing.append("resume")
    if not state.onboarding_submitted:
        missing.append("onboarding")
    if state.review_required and not state.review_complete:
        missing.append("resume_review")
    return missing


async def portal_state(db: AsyncSession, interview: Interview, *, create: bool = True) -> PortalState | None:
    """Progress of an intake interview (``None`` when the interview is not an intake interview, or when the row
    is missing and ``create`` is false). Creates the intake row lazily (``ensure_intake_for_interview``)."""
    intake = await intake_for_interview(db, interview.id)
    if intake is None:
        if not create:
            return None
        from app.services.intake.invitations import ensure_intake_for_interview  # noqa: PLC0415 - cycle

        intake = await ensure_intake_for_interview(db, interview)
        if intake is None:
            return None
    state = PortalState(
        intake=intake,
        interview=interview,
        settings=parse_settings(intake),
        consent=await has_consent(db, interview.id),
        resume=await latest_resume(db, interview),
        session=await latest_session(db, interview.id),
        record=parse_review_record(intake),
    )
    state.missing = _missing(state)
    return state


_GUARD_CODES: Mapping[str, tuple[str, str]] = {
    "resume": ("intake_resume_required", "Please upload your résumé before starting the interview."),
    "onboarding": ("intake_onboarding_required", "Please complete the details form before starting."),
    "resume_review": (
        "intake_resume_review_required",
        "Please check the details we read from your résumé first.",
    ),
}


async def enforce_start_guard(db: AsyncSession, interview: Interview) -> None:
    """409 before a new intake session is created when a required step is missing (journey order: résumé,
    details, résumé review) or intake is disabled. No-op for other interviews and when a session is already
    open (reconnects and live sessions are never blocked)."""
    round_ = await intake_round_of_interview(db, interview)
    if round_ is None:
        return
    session = await latest_session(db, interview.id)
    if session is not None and session.status in OPEN_SESSION_STATUSES:
        return
    if not intake_enabled():
        raise Conflict("Intake interviews are turned off for this workspace.", code="intake_disabled")
    state = await portal_state(db, interview)
    if state is None:
        return
    for step in ("resume", "onboarding", "resume_review"):
        if step in state.missing:
            code, message = _GUARD_CODES[step]
            raise Conflict(
                message, code=code, details={"missing": [m for m in state.missing if m != "consent"]}
            )


async def intake_steps(db: AsyncSession, interview: Interview) -> list[str]:
    """Journey step keys an intake interview adds to ``steps_completed`` (``details``, ``resume-review``)."""
    if await intake_round_of_interview(db, interview) is None:
        return []
    state = await portal_state(db, interview, create=intake_enabled())
    if state is None:
        return []
    steps: list[str] = []
    if state.onboarding_submitted:
        steps.append("details")
    if state.review_complete and (state.resume_usable or not state.settings.resume_required):
        steps.append("resume-review")
    return steps


@dataclass(slots=True, frozen=True)
class PortalBrief:
    intake_id: uuid.UUID
    title: str
    estimated_minutes: int
    presence: str
    resume_required: bool


async def portal_brief(db: AsyncSession, interview: Interview) -> PortalBrief | None:
    """``PortalInterview.intake`` data for intake interviews (creates the row lazily while intake is enabled)."""
    if await intake_round_of_interview(db, interview) is None:
        return None
    intake = await intake_for_interview(db, interview.id)
    if intake is None and intake_enabled():
        from app.services.intake.invitations import ensure_intake_for_interview  # noqa: PLC0415 - cycle

        intake = await ensure_intake_for_interview(db, interview)
    if intake is None:
        return None
    settings = parse_settings(intake)
    return PortalBrief(
        intake_id=intake.id,
        title=intake.title,
        estimated_minutes=settings.duration_minutes,
        presence=settings.presence,
        resume_required=settings.resume_required,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "INTAKE_ROUND_TYPE",
    "LOCKING_SESSION_STATUSES",
    "OPEN_SESSION_STATUSES",
    "IntakeRoute",
    "PortalBrief",
    "PortalState",
    "enforce_start_guard",
    "ensure_enabled",
    "get_intake",
    "has_consent",
    "INTAKE_FEATURE",
    "intake_enabled",
    "intake_entitled",
    "intake_for_interview",
    "intake_round_of_interview",
    "intake_steps",
    "intake_validation_error",
    "is_intake_round",
    "latest_resume",
    "latest_session",
    "parse_review_record",
    "parse_settings",
    "portal_brief",
    "portal_state",
    "raise_validation",
    "require_intake_enabled",
    "require_intake_entitled",
    "resume_usable",
    "review_done",
    "utcnow",
]
