"""Access windows of an interview invitation (S7, audit B4).

The invite expiry is the deadline to *start* the interview, never a cut-off for one that is in progress: a
candidate whose invitation lapses while they are live or paused (a long interview, a reconnect after a drop)
keeps access until the session ends (the lifecycle sweep ends abandoned ones). F15 extends this module with
``scheduled_for`` ("not open yet").

Vocabulary: "started" means ``InterviewSession.started_at IS NOT NULL``; "in progress" means a started session
that is live or paused.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Interview, InterviewSession
from app.domain.enums import InterviewStatus, SessionStatus

IN_PROGRESS_STATUSES: frozenset[str] = frozenset({SessionStatus.LIVE.value, SessionStatus.PAUSED.value})

REFUSAL_CANCELLED = "invite_cancelled"
REFUSAL_EXPIRED = "invite_expired"


def invite_expired(interview: Interview, now: datetime | None = None) -> bool:
    """The invitation's start deadline has passed (completed interviews never count as expired)."""
    expires = interview.invite_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return interview.status == InterviewStatus.EXPIRED.value or (
        expires <= (now or datetime.now(UTC)) and interview.status not in (InterviewStatus.COMPLETED.value,)
    )


def in_progress(session: InterviewSession | None) -> bool:
    return session is not None and session.started_at is not None and session.status in IN_PROGRESS_STATUSES


def start_refusal(interview: Interview, *, now: datetime | None = None) -> str | None:
    """None when a new session may start, else ``invite_cancelled`` | ``invite_expired``."""
    if interview.status == InterviewStatus.CANCELLED.value:
        return REFUSAL_CANCELLED
    if invite_expired(interview, now):
        return REFUSAL_EXPIRED
    return None


def continue_refusal(
    interview: Interview, session: InterviewSession | None, *, now: datetime | None = None
) -> str | None:
    """None when the candidate may continue (resume, reconnect, fetch a ticket).

    A cancelled invitation never continues. An expired one continues only while a started session is open:
    expiry is the deadline to start, not a cut-off mid-interview.
    """
    refusal = start_refusal(interview, now=now)
    if refusal == REFUSAL_EXPIRED and in_progress(session):
        return None
    return refusal


async def latest_open_session(db: AsyncSession, interview_id: object) -> InterviewSession | None:
    """The most recent live or paused session of the interview, if any."""
    return await db.scalar(
        select(InterviewSession)
        .where(
            InterviewSession.interview_id == interview_id,
            InterviewSession.status.in_(tuple(IN_PROGRESS_STATUSES)),
        )
        .order_by(InterviewSession.created_at.desc())
        .limit(1)
    )


async def access_refusal(
    db: AsyncSession, interview: Interview, *, now: datetime | None = None
) -> str | None:
    """``continue_refusal`` with the interview's latest open session looked up (one query, only when expired)."""
    refusal = start_refusal(interview, now=now)
    if refusal != REFUSAL_EXPIRED:
        return refusal
    return continue_refusal(interview, await latest_open_session(db, interview.id), now=now)


__all__ = [
    "IN_PROGRESS_STATUSES",
    "REFUSAL_CANCELLED",
    "REFUSAL_EXPIRED",
    "access_refusal",
    "continue_refusal",
    "in_progress",
    "invite_expired",
    "latest_open_session",
    "start_refusal",
]
