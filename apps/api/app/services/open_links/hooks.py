"""Guarded hooks the existing portal calls (OPEN_LINKS.md section 3.4).

Callers wrap each call in ``try/except`` (a failure of the open-link layer
never breaks the portal) but re-raise :class:`~app.core.errors.AppError`,
which is how ``ensure_resume_for_session`` refuses with 409 ``resume_required``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict
from app.core.logging import get_logger
from app.db.models import (
    CandidateResume,
    Interview,
    InterviewLink,
    InterviewSession,
    LinkRegistration,
    LinkRegistrationRound,
)
from app.domain.enums import ResumeStatus
from app.services.open_links.constants import (
    DEFAULT_DUPLICATE_POLICY,
    FlagCode,
    FlagReview,
    RegistrationStatus,
)
from app.services.open_links.progression import SyncResult, sync_for_interview
from app.services.open_links.signals import make_flag

log = get_logger(__name__)

UNUSABLE_RESUME = (ResumeStatus.FAILED.value, ResumeStatus.REJECTED.value)


async def registration_for_interview(db: AsyncSession, interview_id: uuid.UUID) -> LinkRegistration | None:
    return await db.scalar(
        select(LinkRegistration)
        .join(LinkRegistrationRound, LinkRegistrationRound.registration_id == LinkRegistration.id)
        .where(LinkRegistrationRound.interview_id == interview_id)
    )


async def registration_interview_ids(db: AsyncSession, interview_id: uuid.UUID) -> list[uuid.UUID] | None:
    """The interviews (rounds) of the open-link registration that owns ``interview_id``; None when the
    interview did not come from an open link.

    Open-link registrations are not verified identities (S4): what the candidate sees and what counts as
    "their résumé" is limited to these interviews, never the person's other applications.
    """
    registration = await registration_for_interview(db, interview_id)
    if registration is None:
        return None
    rows = await db.scalars(
        select(LinkRegistrationRound.interview_id).where(
            LinkRegistrationRound.registration_id == registration.id
        )
    )
    return [row for row in rows.all() if row is not None]


async def ensure_resume_for_session(db: AsyncSession, interview: Interview) -> None:
    """409 ``resume_required`` when the interview's link requires a resume and
    the candidate has none in a usable state within this registration."""
    registration = await registration_for_interview(db, interview.id)
    if registration is None:
        return
    link = await db.get(InterviewLink, registration.link_id)
    if link is None or not link.require_resume:
        return
    scope = await registration_interview_ids(db, interview.id) or [interview.id]
    usable = await db.scalar(
        select(CandidateResume.id)
        .where(
            CandidateResume.candidate_id == interview.candidate_id,
            CandidateResume.interview_id.in_(scope),
            CandidateResume.status.not_in(UNUSABLE_RESUME),
        )
        .limit(1)
    )
    if usable is None:
        raise Conflict("Please upload your resume before the interview starts.", code="resume_required")


async def on_resume_uploaded(db: AsyncSession, interview: Interview, resume: Any) -> bool:
    """Record the resume hash on the registration and flag ``same_resume``; True when flagged."""
    sha = getattr(resume, "sha256", None)
    if not sha:
        return False
    registration = await registration_for_interview(db, interview.id)
    if registration is None:
        return False
    registration.resume_sha256 = sha
    link = await db.get(InterviewLink, registration.link_id)
    if link is None:
        return False
    scope = (link.duplicate_policy or DEFAULT_DUPLICATE_POLICY).get("scope", "job")
    stmt = select(LinkRegistration.id).where(
        LinkRegistration.resume_sha256 == sha,
        LinkRegistration.id != registration.id,
        LinkRegistration.status != RegistrationStatus.BLOCKED.value,
    )
    if scope == "link":
        stmt = stmt.where(LinkRegistration.link_id == link.id)
    else:
        stmt = stmt.where(
            LinkRegistration.organization_id == link.organization_id, LinkRegistration.job_id == link.job_id
        )
    related = await db.scalar(stmt.order_by(LinkRegistration.created_at).limit(1))
    if related is None:
        return False
    flags = list(registration.flags or [])
    if any(
        f.get("code") == FlagCode.SAME_RESUME.value and f.get("related_registration_id") == str(related)
        for f in flags
    ):
        return False
    flags.append(make_flag(FlagCode.SAME_RESUME.value, related))
    registration.flags = flags
    if registration.flag_review in (FlagReview.NONE.value, FlagReview.CLEARED.value):
        registration.flag_review = FlagReview.OPEN.value
    await audit(
        db,
        None,
        "link.registration_flagged",
        resource_type="link_registration",
        resource_id=registration.id,
        organization_id=registration.organization_id,
        details={"link_id": str(link.id), "flags": [FlagCode.SAME_RESUME.value]},
    )
    await db.flush()
    return True


async def on_session_finished(db: AsyncSession, session_id: uuid.UUID) -> SyncResult | None:
    """Session -> interview -> registration sync (None when not a link interview)."""
    interview_id = await db.scalar(
        select(InterviewSession.interview_id).where(InterviewSession.id == session_id)
    )
    if interview_id is None:
        return None
    return await sync_for_interview(db, interview_id)
