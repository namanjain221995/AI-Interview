"""Open-link test hooks (OPEN_LINKS.md section 9.6).

Only mounted when ``ENABLE_TEST_HOOKS=true`` and never in production; every
call needs ``X-Test-Hooks-Token`` (except under APP_ENV=test).

``POST /api/v1/test/interviews/{interview_id}/complete {end_reason?}``
    Ends the interview the way the engine's close path does - latest session
    (or a minimal new one) ``completed`` with ``ended_at``/``end_reason``,
    interview ``completed`` (``cancelled`` for ``end_reason="cancelled"``) -
    without any model call, then runs ``sync_registration``. Scoring and
    report jobs are NOT enqueued (no GPU work); the progression result is the
    same as after ``open_link.round_finished``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routers.test_hooks import require_test_hooks_token
from app.core.config import get_settings
from app.core.deps import DbSession
from app.core.errors import Conflict, NotFound
from app.db.models import Interview, InterviewSession, Round
from app.domain.enums import InterviewStatus, SessionEndReason, SessionStatus
from app.services.open_links.progression import sync_for_interview

TEST_HOOKS = True

router = APIRouter(
    prefix="/test/interviews", tags=["test-hooks"], dependencies=[Depends(require_test_hooks_token)]
)


class CompleteInterviewRequest(BaseModel):
    end_reason: SessionEndReason = SessionEndReason.SUFFICIENT_EVIDENCE


def _guard() -> None:
    settings = get_settings()
    if not settings.enable_test_hooks or settings.is_production:
        raise NotFound()


@router.post("/{interview_id}/complete", summary="Complete an interview without a live run (test only)")
async def complete_interview(
    interview_id: uuid.UUID, db: DbSession, body: CompleteInterviewRequest | None = None
) -> dict[str, Any]:
    _guard()
    reason = (body or CompleteInterviewRequest()).end_reason
    interview = await db.get(Interview, interview_id)
    if interview is None:
        raise NotFound("Interview not found.")
    if interview.status not in (InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value):
        raise Conflict(f"The interview is {interview.status}.", code="invalid_status")
    round_id = interview.round_id
    round_ = await db.get(Round, round_id) if round_id else None
    if round_ is None:
        raise Conflict("The interview has no round.", code="round_required")
    now = datetime.now(UTC)
    aborted = reason == SessionEndReason.CANCELLED
    session = await db.scalar(
        select(InterviewSession)
        .where(InterviewSession.interview_id == interview.id)
        .order_by(InterviewSession.created_at.desc())
        .limit(1)
    )
    if session is None:
        session = InterviewSession(
            interview_id=interview.id,
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=round_.id,
            status=SessionStatus.CREATED.value,
            round_config=dict(round_.config or {}),
            state={},
            started_at=now,
        )
        db.add(session)
    session.status = SessionStatus.ABORTED.value if aborted else SessionStatus.COMPLETED.value
    session.started_at = session.started_at or now
    session.ended_at = now
    session.end_reason = reason.value
    interview.status = InterviewStatus.CANCELLED.value if aborted else InterviewStatus.COMPLETED.value
    interview.completed_at = None if aborted else now
    await db.flush()
    result = await sync_for_interview(db, interview.id)
    registration = result.registration if result else None
    return {
        "interview_id": str(interview.id),
        "session_id": str(session.id),
        "interview_status": interview.status,
        "end_reason": reason.value,
        "registration_id": str(registration.id) if registration else None,
        "registration_status": registration.status if registration else None,
        "unlocked_interview_id": str(result.unlocked_interview_id)
        if result and result.unlocked_interview_id
        else None,
    }
