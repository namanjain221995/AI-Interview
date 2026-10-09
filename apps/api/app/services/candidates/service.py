"""Candidate CRUD, search (name / e-mail / skills), list aggregates and the
archive (soft delete) / restore lifecycle.

Archived candidates keep every record (interviews, sessions, reports) but are
hidden from the candidate list by default, cannot be invited or receive new
resumes, and can be restored. Permanent data-subject deletion lives in
:mod:`app.services.candidates.erasure`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.models import Candidate, CandidateProfile, CandidateResume, Interview
from app.domain.enums import InterviewStatus, ResumeStatus
from app.schemas.api.candidates import CandidateCreate, CandidateUpdate
from app.services.jobs.jobs import escape_like
from app.services.resume.service import get_resume_service

ArchivedFilter = Literal["exclude", "include", "only"]


async def get_candidate(
    db: AsyncSession, candidate_id: uuid.UUID, organization_id: uuid.UUID, *, for_update: bool = False
) -> Candidate:
    if for_update:
        candidate = await db.scalar(
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        candidate = await db.get(Candidate, candidate_id)
    if candidate is None or candidate.organization_id != organization_id:
        raise NotFound("Candidate not found.")
    return candidate


def ensure_active(candidate: Candidate, *, action: str = "change") -> None:
    """Reject new activity (invites, resumes) for an archived candidate."""
    if candidate.archived_at is not None:
        raise Conflict(
            f"This candidate is archived. Restore the candidate to {action}.",
            code="candidate_archived",
            details={"candidate_id": str(candidate.id), "archived_at": candidate.archived_at.isoformat()},
        )


async def _ensure_email_free(
    db: AsyncSession, organization_id: uuid.UUID, email: str, exclude: uuid.UUID | None = None
) -> None:
    stmt = select(Candidate.id, Candidate.archived_at).where(
        Candidate.organization_id == organization_id, Candidate.email == email
    )
    if exclude is not None:
        stmt = stmt.where(Candidate.id != exclude)
    existing = (await db.execute(stmt)).first()
    if existing is not None:
        archived = existing.archived_at is not None
        raise Conflict(
            "An archived candidate with this e-mail already exists. Restore that candidate instead."
            if archived
            else "A candidate with this e-mail already exists.",
            code="candidate_exists",
            details={"field": "email", "candidate_id": str(existing.id), "archived": archived},
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


async def create_candidate(
    db: AsyncSession, *, organization_id: uuid.UUID, created_by_id: uuid.UUID | None, body: CandidateCreate
) -> Candidate:
    await _ensure_email_free(db, organization_id, body.email)
    candidate = Candidate(
        organization_id=organization_id,
        full_name=body.full_name.strip(),
        email=body.email,
        phone=_clean(body.phone),
        location=_clean(body.location),
        source=_clean(body.source),
        external_ref=_clean(body.external_ref),
        notes=body.notes,
        created_by_id=created_by_id,
    )
    db.add(candidate)
    await db.flush()
    return candidate


async def update_candidate(db: AsyncSession, candidate: Candidate, body: CandidateUpdate) -> list[str]:
    changed: list[str] = []
    for name in sorted(body.model_fields_set):
        value = getattr(body, name)
        if name in ("full_name", "email") and value is None:
            continue
        if name == "email" and value != candidate.email:
            await _ensure_email_free(db, candidate.organization_id, value, exclude=candidate.id)
        if name in ("phone", "location", "source", "external_ref"):
            value = _clean(value)
        elif name == "full_name":
            value = value.strip()
        if getattr(candidate, name) != value:
            setattr(candidate, name, value)
            changed.append(name)
    await db.flush()
    return changed


# ------------------------------------------------------------- archive / restore


@dataclass(slots=True)
class ArchiveOutcome:
    changed: bool
    cancelled_interview_ids: list[uuid.UUID] = field(default_factory=list)


async def archive_candidate(db: AsyncSession, candidate: Candidate) -> ArchiveOutcome:
    """Soft-delete ``candidate`` (lock it first: ``get_candidate(..., for_update=True)``).

    Open invitations (invited, link still valid) are cancelled, which also
    revokes the candidate's portal sessions. An interview that is already in
    progress is never cut off: the request is refused instead.
    """
    from app.services.jobs.invitations import cancel_invitation

    if candidate.archived_at is not None:
        return ArchiveOutcome(changed=False)
    now = datetime.now(UTC)
    interviews = list(
        (
            await db.scalars(
                select(Interview)
                .where(
                    Interview.candidate_id == candidate.id,
                    Interview.status.in_([InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value]),
                )
                .order_by(Interview.created_at)
                .with_for_update()
            )
        ).all()
    )
    running = [i.id for i in interviews if i.status == InterviewStatus.IN_PROGRESS.value]
    if running:
        raise Conflict(
            "This candidate has an interview in progress. Cancel it or wait until it ends, then archive.",
            code="candidate_interview_in_progress",
            details={"interview_ids": [str(i) for i in running]},
        )
    cancelled: list[uuid.UUID] = []
    for interview in interviews:
        if interview.invite_expires_at <= now:
            continue  # already unusable; restore + regenerate-link can revive it later
        await cancel_invitation(db, interview, reason="Candidate archived")
        cancelled.append(interview.id)
    candidate.archived_at = now
    await db.flush()
    return ArchiveOutcome(changed=True, cancelled_interview_ids=cancelled)


async def restore_candidate(db: AsyncSession, candidate: Candidate) -> bool:
    """Undo :func:`archive_candidate`. Cancelled invitations stay cancelled."""
    if candidate.archived_at is None:
        return False
    candidate.archived_at = None
    await db.flush()
    return True


# ------------------------------------------------------------------ listing


def candidate_list_query(
    organization_id: uuid.UUID,
    *,
    q: str | None = None,
    job_id: uuid.UUID | None = None,
    status: InterviewStatus | None = None,
    resume_status: ResumeStatus | None = None,
    skill: str | None = None,
    archived: ArchivedFilter = "exclude",
) -> Select[Candidate]:
    stmt = select(Candidate).where(Candidate.organization_id == organization_id)
    if archived == "exclude":
        stmt = stmt.where(Candidate.archived_at.is_(None))
    elif archived == "only":
        stmt = stmt.where(Candidate.archived_at.is_not(None))
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        skill_match = exists().where(
            CandidateProfile.candidate_id == Candidate.id,
            CandidateProfile.profile["skills"].astext.ilike(pattern, escape="\\"),
        )
        stmt = stmt.where(
            or_(
                Candidate.full_name.ilike(pattern, escape="\\"),
                Candidate.email.ilike(pattern, escape="\\"),
                skill_match,
            )
        )
    if skill:
        pattern = f"%{escape_like(skill.strip())}%"
        stmt = stmt.where(
            exists().where(
                CandidateProfile.candidate_id == Candidate.id,
                CandidateProfile.profile["skills"].astext.ilike(pattern, escape="\\"),
            )
        )
    if job_id is not None or status is not None:
        clauses = [Interview.candidate_id == Candidate.id]
        if job_id is not None:
            clauses.append(Interview.job_id == job_id)
        if status is not None:
            clauses.append(Interview.status == status.value)
        stmt = stmt.where(exists().where(*clauses))
    if resume_status is not None:
        latest = (
            select(CandidateResume.status)
            .where(CandidateResume.candidate_id == Candidate.id)
            .order_by(CandidateResume.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = stmt.where(latest == resume_status.value)
    return stmt


async def candidate_extras(
    db: AsyncSession, candidate_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Latest resume status, interview counts and a headline from the latest
    profile for a page of candidates (four batched queries, no N+1)."""
    ids = list(candidate_ids)
    extras: dict[uuid.UUID, dict[str, Any]] = {i: {"interviews_count": 0} for i in ids}
    if not ids:
        return extras
    for resume in (
        await db.execute(
            select(CandidateResume.candidate_id, CandidateResume.id, CandidateResume.status)
            .where(CandidateResume.candidate_id.in_(ids))
            .order_by(CandidateResume.candidate_id, CandidateResume.created_at.desc())
            .distinct(CandidateResume.candidate_id)
        )
    ).all():
        extras[resume.candidate_id].update(latest_resume_id=resume.id, latest_resume_status=resume.status)
    for candidate_id, count in (
        await db.execute(
            select(Interview.candidate_id, func.count())
            .where(Interview.candidate_id.in_(ids))
            .group_by(Interview.candidate_id)
        )
    ).all():
        extras[candidate_id]["interviews_count"] = int(count)
    for interview in (
        await db.execute(
            select(Interview.candidate_id, Interview.status)
            .where(Interview.candidate_id.in_(ids))
            .order_by(Interview.candidate_id, Interview.created_at.desc())
            .distinct(Interview.candidate_id)
        )
    ).all():
        extras[interview.candidate_id]["latest_interview_status"] = interview.status
    service = get_resume_service()
    for profile in (
        await db.scalars(
            select(CandidateProfile)
            .where(CandidateProfile.candidate_id.in_(ids), CandidateProfile.status.in_(("ready", "partial")))
            .order_by(CandidateProfile.candidate_id, CandidateProfile.version.desc())
            .distinct(CandidateProfile.candidate_id)
        )
    ).all():
        summary = service.candidate_safe_summary(profile.profile or {})
        extras[profile.candidate_id].update(
            headline=summary.get("headline"),
            experience_years=profile.experience_years,
            top_skills=summary.get("top_skills", [])[:8],
        )
    return extras
