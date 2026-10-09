"""Candidate portal: open-link progress and round switching (OPEN_LINKS.md 5.4, 9.5).

``GET  /portal/link/progress``   rounds + next action (runs ``sync_registration``)
``POST /portal/link/continue``   switch the interview-scoped candidate session
                                 to another round attempt of the same registration

The registration is resolved from ``principal.interview_id`` and must belong
to ``principal.candidate_id``; anything else is 404, so ordinary invites and
other candidates' registrations are invisible.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response

from app.core.audit import audit
from app.core.deps import CandidatePrincipal, DbSession
from app.core.errors import Conflict, NotFound
from app.db.models import Candidate, Interview, LinkRegistration
from app.domain.enums import InterviewStatus
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.open_links import ContinueRequest, ContinueResponse, LinkProgress
from app.services.identity.cookies import set_auth_cookies
from app.services.identity.sessions import revoke_session_family_by_id
from app.services.open_links.hooks import registration_for_interview
from app.services.open_links.progression import (
    build_progress,
    load_attempts,
    next_step_for,
    round_state,
    sync_registration,
)
from app.services.open_links.registration import start_link_session

router = APIRouter(prefix="/portal/link", tags=["portal"], responses=ERROR_RESPONSES)


async def _registration(db: DbSession, principal: CandidatePrincipal) -> LinkRegistration:
    assert principal.interview_id is not None
    registration = await registration_for_interview(db, principal.interview_id)
    if registration is None or registration.candidate_id != principal.candidate_id:
        raise NotFound("This interview is not part of an interview link.", code="not_link_registration")
    return registration


@router.get("/progress", response_model=LinkProgress, summary="Round progress of my registration")
async def link_progress(principal: CandidatePrincipal, db: DbSession) -> LinkProgress:
    registration = await _registration(db, principal)
    await sync_registration(db, registration.id)
    return await build_progress(db, registration, bound_interview_id=principal.interview_id)


@router.post("/continue", response_model=ContinueResponse, summary="Continue with another round")
async def link_continue(
    body: ContinueRequest, request: Request, response: Response, principal: CandidatePrincipal, db: DbSession
) -> ContinueResponse:
    registration = await _registration(db, principal)
    await sync_registration(db, registration.id)
    attempts = (await load_attempts(db, [registration.id]))[registration.id]
    target = next((a for a in attempts if a.interview.id == body.interview_id), None)
    if target is None or target.interview.candidate_id != principal.candidate_id:
        raise NotFound("Interview not found.", code="interview_not_found")
    interview: Interview = target.interview
    state = round_state(interview, target.session)
    if state not in ("invited", "in_progress"):
        raise Conflict(
            "This round is not available.", code="interview_not_available", details={"status": state}
        )
    candidate = await db.get(Candidate, principal.candidate_id)
    if candidate is None:
        raise NotFound("Interview not found.", code="interview_not_found")
    assert principal.auth_session_id is not None
    await revoke_session_family_by_id(db, principal.auth_session_id, reason="link_round_switch")
    tokens, new_principal = await start_link_session(db, candidate, interview, request)
    interview.verified_at = interview.verified_at or datetime.now(UTC)
    await audit(
        db,
        new_principal,
        "link.round_continue",
        resource_type="interview",
        resource_id=interview.id,
        details={"registration_id": str(registration.id), "from_interview_id": str(principal.interview_id)},
        request=request,
    )
    set_auth_cookies(response, tokens)
    response.headers["Cache-Control"] = "no-store"
    next_step = await next_step_for(db, interview)
    if interview.status == InterviewStatus.COMPLETED.value:
        next_step = "complete"
    return ContinueResponse(interview_id=interview.id, next_step=next_step, csrf_token=tokens.csrf_token)  # type: ignore[arg-type]
