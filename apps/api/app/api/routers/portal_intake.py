"""Intake Interview candidate portal (``/api/v1/portal/intake``; INTAKE_INTERVIEW.md 10.4).

Candidate principal only, own interview only (anything else 404 ``interview_not_found``, as in ``portal.py``);
409 ``intake_not_applicable`` for interviews whose round is not an intake round. The details form
(onboarding, MQ1 only when the frozen policy enables it) and the résumé review are editable until a session goes
live (409 ``onboarding_locked``). Responses never contain scores, bands, claims-to-verify or reviewer data.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.candidate_access import ensure_invite_usable
from app.core.audit import audit
from app.core.deps import CandidatePrincipal, DbSession
from app.core.errors import Conflict, NotFound
from app.core.ratelimit import enforce_rate_limit
from app.core.rbac import Principal
from app.db.models import Interview, Organization
from app.domain.enums import InterviewStatus
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.intake_admin import PortalIntakeOut, PortalIntakePart, PortalIntakeResumeReview
from app.schemas.intake import (
    INTAKE_CANDIDATE_STAGE_TITLES,
    INTAKE_STAGE_KEYS,
    IntakeOnboardingIn,
    ResumeReviewIn,
    ResumeReviewOut,
)
from app.services.intake.access import (
    IntakeRoute,
    PortalState,
    intake_round_of_interview,
    portal_state,
    require_intake_enabled,
    utcnow,
)
from app.services.intake.onboarding import (
    apply_onboarding,
    build_onboarding,
    eligibility_view,
    stored_onboarding,
)
from app.services.intake.resume_review import confirm_review, review_view, skip_review

router = APIRouter(
    prefix="/portal/intake",
    tags=["portal"],
    responses=ERROR_RESPONSES,
    route_class=IntakeRoute,
    dependencies=[Depends(require_intake_enabled)],
)

ONBOARDING_RATE_LIMIT = 30  # per minute per interview
RESUME_REVIEW_RATE_LIMIT = 30  # per minute per interview
# Kit stage midpoints in minutes (INTAKE_INTERVIEW.md 6.3); scaled to the planned duration for the overview.
_STAGE_MIDPOINTS: dict[str, float] = {
    "greeting": 5,
    "resume": 9,
    "mandatory": 4,
    "reading": 7.5,
    "topic": 7.5,
    "learning": 5,
    "close": 4,
}
_CLOSING_RESERVE_S = 60


def stage_minutes(duration_minutes: int) -> list[tuple[str, int]]:
    """Approximate minutes per part for a planned duration (same scaling as the live stage budgets)."""
    total = sum(_STAGE_MIDPOINTS.values()) * 60
    factor = (duration_minutes * 60 - _CLOSING_RESERVE_S) / total
    return [(key, max(1, round(_STAGE_MIDPOINTS[key] * factor))) for key in INTAKE_STAGE_KEYS]


async def _interview(db: AsyncSession, principal: Principal, interview_id: uuid.UUID) -> Interview:
    if principal.interview_id != interview_id:
        raise NotFound(code="interview_not_found")
    interview = await db.get(Interview, interview_id)
    if interview is None or interview.candidate_id != principal.candidate_id:
        raise NotFound(code="interview_not_found")
    return interview


async def _context(db: AsyncSession, principal: Principal, interview_id: uuid.UUID) -> PortalState:
    interview = await _interview(db, principal, interview_id)
    state = None
    if await intake_round_of_interview(db, interview) is not None:
        state = await portal_state(db, interview)
    if state is None:
        raise Conflict("This interview is not an intake interview.", code="intake_not_applicable")
    return state


def _ensure_editable(state: PortalState) -> None:
    ensure_invite_usable(state.interview)
    if state.interview.status == InterviewStatus.COMPLETED.value:
        raise Conflict("This interview has already been completed.", code="interview_completed")
    if not state.consent:
        raise Conflict("Please review and accept the interview information first.", code="consent_required")
    if state.locked:
        raise Conflict(
            "Your details can no longer change because the interview has started.", code="onboarding_locked"
        )


async def _portal_out(db: AsyncSession, state: PortalState) -> PortalIntakeOut:
    intake, settings, interview = state.intake, state.settings, state.interview
    org = await db.get(Organization, interview.organization_id)
    confirmed, skipped = state.review_status
    closed = interview.status in (InterviewStatus.COMPLETED.value, InterviewStatus.CANCELLED.value)
    return PortalIntakeOut(
        intake_id=intake.id,
        interview_id=interview.id,
        title=intake.title,
        program_name=settings.program_name,
        organization_name=org.name if org else "",
        estimated_minutes=settings.duration_minutes,
        parts=[
            PortalIntakePart(number=index, key=key, title=INTAKE_CANDIDATE_STAGE_TITLES[key], minutes=minutes)
            for index, (key, minutes) in enumerate(stage_minutes(settings.duration_minutes), start=1)
        ],
        presence=settings.presence,
        target_role_preset=intake.target_role,  # type: ignore[arg-type]
        onboarding=stored_onboarding(intake),
        onboarding_submitted=state.onboarding_submitted,
        onboarding_locked=state.locked or closed,
        eligibility=eligibility_view(intake, settings),
        resume_required=settings.resume_required,
        resume_uploaded=state.resume_usable,
        resume_review=PortalIntakeResumeReview(
            required=state.review_required, confirmed=confirmed, skipped=skipped
        ),
        ready_to_start=state.ready_to_start,
        missing=state.missing,  # type: ignore[arg-type]
    )


@router.get("/{interview_id}", response_model=PortalIntakeOut, summary="Intake overview and progress")
async def portal_intake(
    interview_id: uuid.UUID, principal: CandidatePrincipal, db: DbSession
) -> PortalIntakeOut:
    return await _portal_out(db, await _context(db, principal, interview_id))


@router.put("/{interview_id}/onboarding", response_model=PortalIntakeOut, summary="Submit the details form")
async def portal_intake_onboarding(
    interview_id: uuid.UUID,
    body: IntakeOnboardingIn,
    request: Request,
    principal: CandidatePrincipal,
    db: DbSession,
) -> PortalIntakeOut:
    state = await _context(db, principal, interview_id)
    _ensure_editable(state)
    await enforce_rate_limit(
        "portal_intake_onboarding", str(state.interview.id), limit=ONBOARDING_RATE_LIMIT, window_s=60
    )
    result = build_onboarding(state.intake, state.settings, body, now=utcnow())
    apply_onboarding(state.intake, result)
    await db.flush()
    await audit(
        db,
        principal,
        "candidate.intake_onboarding",
        resource_type="intake",
        resource_id=state.intake.id,
        request=request,
        details={
            "resubmitted": result.resubmitted,
            "eligibility_enabled": state.settings.work_authorization.enabled,
            "eligibility_answered": result.eligibility is not None,
        },
    )
    refreshed = await portal_state(db, state.interview)
    assert refreshed is not None  # noqa: S101 - the row exists (just written)
    return await _portal_out(db, refreshed)


@router.get(
    "/{interview_id}/resume-review", response_model=ResumeReviewOut, summary="Résumé details to review"
)
async def portal_intake_resume_review(
    interview_id: uuid.UUID, principal: CandidatePrincipal, db: DbSession
) -> ResumeReviewOut:
    state = await _context(db, principal, interview_id)
    return await review_view(db, state, now=utcnow())


@router.put(
    "/{interview_id}/resume-review",
    response_model=ResumeReviewOut,
    summary="Confirm (or correct) the résumé details, or continue without reviewing",
)
async def portal_intake_resume_review_submit(
    interview_id: uuid.UUID,
    body: ResumeReviewIn,
    request: Request,
    principal: CandidatePrincipal,
    db: DbSession,
) -> ResumeReviewOut:
    state = await _context(db, principal, interview_id)
    _ensure_editable(state)
    await enforce_rate_limit(
        "portal_intake_resume_review", str(state.interview.id), limit=RESUME_REVIEW_RATE_LIMIT, window_s=60
    )
    now = utcnow()
    if body.action == "confirm":
        assert body.document is not None  # noqa: S101 - ResumeReviewIn guarantees it
        record = await confirm_review(db, state, body.document, now=now)
    else:
        record = await skip_review(db, state, now=now)
    await db.flush()
    await audit(
        db,
        principal,
        "candidate.intake_resume_review",
        resource_type="intake",
        resource_id=state.intake.id,
        request=request,
        details={
            "action": record.action,
            "corrections": len(record.corrections),
            "profile_status": record.source.profile_status,
            "resume_id": str(record.source.resume_id),
        },
    )
    refreshed = await portal_state(db, state.interview)
    assert refreshed is not None  # noqa: S101
    return await review_view(db, refreshed, now=now)
