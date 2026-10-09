"""Staff views of registrations: queries and serialisation (OPEN_LINKS.md 6.4, 9.2)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models import Candidate, CandidateResume, InterviewLink, Job, LinkRegistration, Round
from app.schemas.api.open_links import (
    AnswerItem,
    CandidateBrief,
    Flag,
    LinkRef,
    Registration,
    RegistrationDetail,
    RegistrationRound,
    RegistrationWithLink,
)
from app.services.open_links.constants import FlagReview, RegistrationStatus, RoundState
from app.services.open_links.progression import Attempt, latest_by_sequence, load_attempts

RS = RegistrationStatus


def registration_query(
    link_id: uuid.UUID,
    *,
    status: str | None = None,
    round_index: int | None = None,
    needs_review: bool | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = select(LinkRegistration).where(LinkRegistration.link_id == link_id)
    if status:
        stmt = stmt.where(LinkRegistration.status == status)
    if round_index is not None:
        stmt = stmt.where(LinkRegistration.current_round_index == round_index)
    if needs_review:
        stmt = stmt.where(
            or_(
                LinkRegistration.flag_review == FlagReview.OPEN.value,
                LinkRegistration.status == RS.AWAITING_REVIEW.value,
            )
        )
    if q and q.strip():
        term = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:100] + "%"
        stmt = stmt.where(
            or_(
                LinkRegistration.full_name.ilike(term, escape="\\"),
                LinkRegistration.email.ilike(term, escape="\\"),
                LinkRegistration.phone.ilike(term, escape="\\"),
            )
        )
    return stmt


async def get_registration(
    db: AsyncSession, link: InterviewLink, registration_id: uuid.UUID
) -> LinkRegistration:
    registration = await db.get(LinkRegistration, registration_id, populate_existing=True)
    if (
        registration is None
        or registration.link_id != link.id
        or registration.organization_id != link.organization_id
    ):
        raise NotFound("Registration not found.", code="not_found")
    return registration


def _round_out(sequence: int, round_id: uuid.UUID, name: str, attempt: Attempt | None) -> RegistrationRound:
    if attempt is None:
        return RegistrationRound(
            sequence=sequence, round_id=round_id, name=name, state=RoundState.LOCKED.value, attempt=0
        )
    session = attempt.session
    return RegistrationRound(
        sequence=sequence,
        round_id=attempt.row.round_id,
        name=attempt.row.round_name,
        state=attempt.state,  # type: ignore[arg-type]
        attempt=attempt.row.attempt,
        interview_id=attempt.interview.id,
        session_id=session.id if session else None,
        session_status=session.status if session else None,
        end_reason=session.end_reason if session else None,
        started_at=session.started_at if session else None,
        completed_at=attempt.interview.completed_at,
        expires_at=attempt.interview.invite_expires_at,
    )


def _flags(registration: LinkRegistration) -> list[Flag]:
    out: list[Flag] = []
    for raw in registration.flags or []:
        try:
            out.append(Flag.model_validate(raw))
        except ValueError:
            continue
    return out


async def serialize_registrations(
    db: AsyncSession,
    registrations: Sequence[LinkRegistration],
    *,
    links: dict[uuid.UUID, InterviewLink] | None = None,
    with_link: bool = False,
) -> list[Registration]:
    if not registrations:
        return []
    link_ids = {r.link_id for r in registrations}
    if links is None or not link_ids <= set(links):
        links = {
            lnk.id: lnk
            for lnk in (await db.scalars(select(InterviewLink).where(InterviewLink.id.in_(link_ids)))).all()
        }
    round_ids = {rid for lnk in links.values() for rid in (lnk.round_ids or [])}
    round_names = (
        {
            row.id: row.name
            for row in (await db.execute(select(Round.id, Round.name).where(Round.id.in_(round_ids)))).all()
        }
        if round_ids
        else {}
    )
    candidate_ids = {r.candidate_id for r in registrations if r.candidate_id}
    candidates = (
        {
            row.id: row
            for row in (
                await db.execute(
                    select(Candidate.id, Candidate.full_name, Candidate.email).where(
                        Candidate.id.in_(candidate_ids)
                    )
                )
            ).all()
        }
        if candidate_ids
        else {}
    )
    job_titles: dict[uuid.UUID, str] = {}
    if with_link:
        job_ids = {lnk.job_id for lnk in links.values()}
        job_titles = {
            row.id: row.title
            for row in (await db.execute(select(Job.id, Job.title).where(Job.id.in_(job_ids)))).all()
        }
    attempts = await load_attempts(db, [r.id for r in registrations])
    out: list[Registration] = []
    for registration in registrations:
        link = links.get(registration.link_id)
        latest = latest_by_sequence(attempts.get(registration.id, []))
        rounds: list[RegistrationRound] = []
        for index, round_id in enumerate(link.round_ids if link else []):
            rounds.append(
                _round_out(index, round_id, round_names.get(round_id, "Unavailable round"), latest.get(index))
            )
        candidate = candidates.get(registration.candidate_id) if registration.candidate_id else None
        data: dict[str, Any] = {
            "id": registration.id,
            "link_id": registration.link_id,
            "candidate": CandidateBrief(id=candidate.id, full_name=candidate.full_name, email=candidate.email)
            if candidate
            else None,
            "full_name": registration.full_name,
            "email": registration.email,
            "phone": registration.phone,
            "status": registration.status,
            "status_reason": registration.status_reason,
            "current_round_index": registration.current_round_index,
            "rounds": rounds,
            "flags": _flags(registration),
            "flag_review": registration.flag_review,
            "created_at": registration.created_at,
            "updated_at": registration.updated_at,
        }
        if with_link and link is not None:
            out.append(
                RegistrationWithLink(
                    **data,
                    link=LinkRef(id=link.id, name=link.name, job_title=job_titles.get(link.job_id, "")),
                )
            )
        else:
            out.append(Registration(**data))
    return out


async def serialize_detail(
    db: AsyncSession, link: InterviewLink, registration: LinkRegistration
) -> RegistrationDetail:
    await db.refresh(registration)
    [base] = await serialize_registrations(db, [registration], links={link.id: link})
    attempts = (await load_attempts(db, [registration.id])).get(registration.id, [])
    ordered = sorted(attempts, key=lambda a: (a.row.sequence, a.row.attempt), reverse=True)
    resume_id = None
    if registration.candidate_id:
        resume_id = await db.scalar(
            select(CandidateResume.id)
            .where(CandidateResume.candidate_id == registration.candidate_id)
            .order_by(CandidateResume.created_at.desc())
            .limit(1)
        )
    answers = []
    for item in (registration.answers or {}).get("fields", []):
        try:
            answers.append(AnswerItem.model_validate(item))
        except ValueError:
            continue
    return RegistrationDetail(
        **base.model_dump(),
        answers=answers,
        attempts=[_round_out(a.row.sequence, a.row.round_id, a.row.round_name, a) for a in ordered],
        privacy_ack_at=registration.privacy_ack_at,
        personal_link_issued_at=registration.personal_token_issued_at,
        resume_id=resume_id,
        flag_reviewed_at=registration.flag_reviewed_at,
        flag_review_note=registration.flag_review_note,
    )


def candidate_registrations_query(organization_id: uuid.UUID, candidate_id: uuid.UUID) -> Select[Any]:
    return select(LinkRegistration).where(
        and_(
            LinkRegistration.organization_id == organization_id, LinkRegistration.candidate_id == candidate_id
        )
    )
