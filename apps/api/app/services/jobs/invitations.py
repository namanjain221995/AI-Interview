"""Interview invitations (``POST /interviews`` and friends).

* The effective policy is frozen into ``policy_snapshot`` at invite time
  (:func:`app.services.jobs.policy.build_policy_snapshot`).
* The invite token is 32 random bytes (URL-safe); only its SHA-256 is stored.
  The optional access code is stored as an argon2 hash. Both are returned to
  the recruiter exactly once (create / regenerate-link) and never logged.
* Cancelling or rotating the link revokes every candidate session bound to
  the interview, so a leaked link or cookie stops working immediately.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.core.queue import publish_session_event
from app.core.security import generate_access_code, generate_invite_token, hash_access_code_async
from app.db.models import AuthSession, Candidate, Interview, InterviewSession, Job, Round
from app.domain.enums import InterviewStatus, JobStatus, SessionStatus
from app.schemas.api.interviews import InterviewCreate, InterviewOut, JobRef, PersonRef, RoundRef, SessionRef
from app.services.candidates.service import ensure_active
from app.services.identity import get_organization_settings
from app.services.identity.auth_cache import invalidate_auth_cache
from app.services.jobs.policy import build_policy_snapshot

_OPEN_SESSION_STATUSES = (
    SessionStatus.CREATED.value,
    SessionStatus.PREPARING.value,
    SessionStatus.READY.value,
    SessionStatus.LIVE.value,
    SessionStatus.PAUSED.value,
)

log = get_logger(__name__)

CLOSED_JOB_STATUSES = frozenset({JobStatus.CLOSED.value, JobStatus.ARCHIVED.value})
CANCELLABLE = frozenset(
    {InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value, InterviewStatus.EXPIRED.value}
)
REGENERATABLE = CANCELLABLE


@dataclass(slots=True)
class IssuedInvite:
    interview: Interview
    token: str
    access_code: str | None

    @property
    def invite_url(self) -> str:
        return invite_url(self.token)


def invite_url(token: str) -> str:
    return f"{get_settings().public_base_url}/i/{token}"


def _now() -> datetime:
    return datetime.now(UTC)


def is_expired(interview: Interview, now: datetime | None = None) -> bool:
    if interview.status == InterviewStatus.EXPIRED.value:
        return True
    return interview.status == InterviewStatus.INVITED.value and interview.invite_expires_at <= (
        now or _now()
    )


async def get_interview(db: AsyncSession, interview_id: uuid.UUID, organization_id: uuid.UUID) -> Interview:
    interview = await db.get(Interview, interview_id)
    if interview is None or interview.organization_id != organization_id:
        raise NotFound("Interview not found.")
    return interview


def _ttl_hours(requested: int | None) -> int:
    return int(requested or get_settings().invite_ttl_hours)


async def create_invitation(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    invited_by_id: uuid.UUID | None,
    body: InterviewCreate,
    skip_access_code: bool = False,
    intake_managed: bool = False,
) -> IssuedInvite:
    """``skip_access_code=True`` (open interview links): never issue an access
    code, even when the organization requires one - the candidate enters
    through cookies or a personal link, never through this invite token.

    ``intake_managed=True`` (intake create/send/batch flows, which write their
    own ``intake_interviews`` row): skip the intake hook. Otherwise an
    invitation for an intake round gets its intake row here (never breaks the
    invitation; see :func:`_ensure_intake_row`)."""
    candidate = await db.get(Candidate, body.candidate_id)
    if candidate is None or candidate.organization_id != organization_id:
        raise NotFound("Candidate not found.")
    ensure_active(candidate, action="invite them")
    job = await db.get(Job, body.job_id)
    if job is None or job.organization_id != organization_id:
        raise NotFound("Job not found.")
    if job.status in CLOSED_JOB_STATUSES:
        raise Conflict(
            "This job is not open for interviews.", code="job_not_open", details={"status": job.status}
        )
    rounds = list(
        (
            await db.scalars(
                select(Round)
                .where(Round.job_id == job.id, Round.is_active.is_(True))
                .order_by(Round.sequence)
            )
        ).all()
    )
    if not rounds:
        raise ValidationFailed("The job has no active interview rounds.", code="no_rounds")
    round_id = body.round_id
    if round_id is not None and round_id not in {r.id for r in rounds}:
        raise ValidationFailed(
            "The round does not belong to this job or is inactive.", details={"field": "round_id"}
        )
    if body.allow_round_selection:
        if not any(r.candidate_selectable for r in rounds):
            raise ValidationFailed(
                "Round selection needs at least one active round marked candidate_selectable.",
                code="no_selectable_rounds",
            )
    elif round_id is None:
        round_id = rounds[0].id

    now = _now()
    existing = await db.scalar(
        select(Interview.id).where(
            Interview.candidate_id == candidate.id,
            Interview.job_id == job.id,
            or_(
                Interview.status == InterviewStatus.IN_PROGRESS.value,
                and_(Interview.status == InterviewStatus.INVITED.value, Interview.invite_expires_at > now),
            ),
        )
    )
    if existing is not None:
        raise Conflict(
            "This candidate already has an open invitation for this job. Regenerate its link instead.",
            code="interview_exists",
            details={"interview_id": str(existing)},
        )

    org_settings = await get_organization_settings(db, organization_id)
    snapshot = build_policy_snapshot(org_settings, job)
    require_code = (body.require_access_code or org_settings.require_access_code) and not skip_access_code
    token, token_hash = generate_invite_token()
    access_code = generate_access_code() if require_code else None
    interview = Interview(
        organization_id=organization_id,
        candidate_id=candidate.id,
        job_id=job.id,
        round_id=round_id,
        allow_round_selection=body.allow_round_selection,
        status=InterviewStatus.INVITED.value,
        invite_token_hash=token_hash,
        invite_expires_at=now + timedelta(hours=_ttl_hours(body.expires_in_hours)),
        access_code_hash=await hash_access_code_async(access_code) if access_code else None,
        policy_snapshot=snapshot.model_dump(mode="json"),
        invited_by_id=invited_by_id,
        scheduled_for=body.scheduled_for,
        notes=body.notes,
    )
    db.add(interview)
    await db.flush()
    log.info(
        "interview.invited",
        interview_id=str(interview.id),
        job_id=str(job.id),
        access_code=bool(access_code),
        ttl_hours=_ttl_hours(body.expires_in_hours),
    )
    if not intake_managed:
        await _ensure_intake_row(db, interview)
    return IssuedInvite(interview, token, access_code)


async def _ensure_intake_row(db: AsyncSession, interview: Interview) -> None:
    """Intake rounds (generic job invite, open-link round): create the
    ``intake_interviews`` row in a savepoint. Any failure is only logged - the
    candidate portal creates the row lazily later."""
    try:
        from app.services.intake.access import intake_enabled, intake_round_of_interview

        if not intake_enabled():
            return
        round_ = await intake_round_of_interview(db, interview)
        if round_ is None:
            return
        from app.services.intake.invitations import ensure_intake_for_interview

        async with db.begin_nested():
            await ensure_intake_for_interview(db, interview, round_=round_)
    except Exception as exc:  # noqa: BLE001 - the intake layer must never break invitations
        log.warning("interview.intake_hook_failed", interview_id=str(interview.id), error=type(exc).__name__)


async def revoke_candidate_sessions(db: AsyncSession, interview_id: uuid.UUID, *, reason: str) -> int:
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.interview_id == interview_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason[:64])
        .returning(AuthSession.id)
        .execution_options(synchronize_session=False)
    )
    ids = list(result.scalars().all())
    if ids:
        await invalidate_auth_cache(ids)

        async def _invalidate() -> None:
            await invalidate_auth_cache(ids)

        run_after_commit(db, _invalidate)
    return len(ids)


async def cancel_invitation(db: AsyncSession, interview: Interview, *, reason: str | None = None) -> int:
    if interview.status == InterviewStatus.CANCELLED.value:
        return 0
    if interview.status not in CANCELLABLE:
        raise Conflict(f"A {interview.status} interview cannot be cancelled.", code="invalid_status")
    interview.status = InterviewStatus.CANCELLED.value
    if reason:
        interview.notes = (
            f"{interview.notes}\n\nCancelled: {reason}".strip() if interview.notes else f"Cancelled: {reason}"
        )
    revoked = await revoke_candidate_sessions(db, interview.id, reason="interview_cancelled")
    await db.flush()
    # Live sessions must end too: the realtime runtime listens for this on the session bus.
    open_sessions = (
        await db.scalars(
            select(InterviewSession.id).where(
                InterviewSession.interview_id == interview.id,
                InterviewSession.status.in_(_OPEN_SESSION_STATUSES),
            )
        )
    ).all()
    for session_id in open_sessions:
        run_after_commit(db, functools.partial(_announce_cancelled, session_id))
    return revoked


async def _announce_cancelled(session_id: uuid.UUID) -> None:
    await publish_session_event(session_id, {"type": "session.cancelled", "reason": "interview_cancelled"})


async def regenerate_link(
    db: AsyncSession, interview: Interview, *, expires_in_hours: int | None, rotate_access_code: bool | None
) -> IssuedInvite:
    if interview.status not in REGENERATABLE:
        raise Conflict(
            f"The link of a {interview.status} interview cannot be regenerated.", code="invalid_status"
        )
    candidate = await db.get(Candidate, interview.candidate_id)
    if candidate is not None:
        ensure_active(candidate, action="send them a new link")
    token, token_hash = generate_invite_token()
    interview.invite_token_hash = token_hash
    interview.invite_expires_at = _now() + timedelta(hours=_ttl_hours(expires_in_hours))
    interview.failed_verification_count = 0
    if interview.status == InterviewStatus.EXPIRED.value:
        interview.status = InterviewStatus.INVITED.value
    access_code: str | None = None
    rotate = rotate_access_code if rotate_access_code is not None else interview.access_code_hash is not None
    if rotate:
        access_code = generate_access_code()
        interview.access_code_hash = await hash_access_code_async(access_code)
    await revoke_candidate_sessions(db, interview.id, reason="invite_link_rotated")
    await db.flush()
    return IssuedInvite(interview, token, access_code)


def interview_list_query(
    organization_id: uuid.UUID,
    *,
    status: InterviewStatus | None = None,
    job_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
) -> Select[Any]:
    stmt = select(Interview).where(Interview.organization_id == organization_id)
    now = _now()
    if status is InterviewStatus.EXPIRED:
        stmt = stmt.where(
            or_(
                Interview.status == InterviewStatus.EXPIRED.value,
                and_(Interview.status == InterviewStatus.INVITED.value, Interview.invite_expires_at <= now),
            )
        )
    elif status is InterviewStatus.INVITED:
        stmt = stmt.where(
            Interview.status == InterviewStatus.INVITED.value, Interview.invite_expires_at > now
        )
    elif status is not None:
        stmt = stmt.where(Interview.status == status.value)
    if job_id is not None:
        stmt = stmt.where(Interview.job_id == job_id)
    if candidate_id is not None:
        stmt = stmt.where(Interview.candidate_id == candidate_id)
    return stmt


async def serialize_interviews(db: AsyncSession, interviews: Sequence[Interview]) -> list[InterviewOut]:
    """``InterviewOut`` with candidate/job/round refs and the latest session (no secrets).

    Batched column-only lookups: four queries per page, and no session state,
    job embedding or round configuration is loaded just to render a reference.
    """
    if not interviews:
        return []
    candidate_ids = {i.candidate_id for i in interviews}
    job_ids = {i.job_id for i in interviews}
    round_ids = {i.round_id for i in interviews if i.round_id}
    interview_ids = [i.id for i in interviews]
    candidates = {
        row.id: row
        for row in (
            await db.execute(
                select(Candidate.id, Candidate.full_name, Candidate.email).where(
                    Candidate.id.in_(candidate_ids)
                )
            )
        ).all()
    }
    jobs = {
        row.id: row for row in (await db.execute(select(Job.id, Job.title).where(Job.id.in_(job_ids)))).all()
    }
    rounds = (
        {
            row.id: row
            for row in (
                await db.execute(
                    select(Round.id, Round.name, Round.round_type).where(Round.id.in_(round_ids))
                )
            ).all()
        }
        if round_ids
        else {}
    )
    sessions = {
        row.interview_id: row
        for row in (
            await db.execute(
                select(InterviewSession.interview_id, InterviewSession.id, InterviewSession.status)
                .where(InterviewSession.interview_id.in_(interview_ids))
                .order_by(InterviewSession.interview_id, InterviewSession.created_at.desc())
                .distinct(InterviewSession.interview_id)
            )
        ).all()
    }
    now = _now()
    out: list[InterviewOut] = []
    for interview in interviews:
        item = InterviewOut.model_validate(interview)
        item.requires_access_code = interview.access_code_hash is not None
        item.is_expired = is_expired(interview, now)
        candidate = candidates.get(interview.candidate_id)
        if candidate is not None:
            item.candidate = PersonRef(id=candidate.id, full_name=candidate.full_name, email=candidate.email)
        job = jobs.get(interview.job_id)
        if job is not None:
            item.job = JobRef(id=job.id, title=job.title)
        round_row = rounds.get(interview.round_id) if interview.round_id else None
        if round_row is not None:
            item.round = RoundRef(id=round_row.id, name=round_row.name, round_type=round_row.round_type)
        session = sessions.get(interview.id)
        if session is not None:
            item.latest_session = SessionRef(id=session.id, status=session.status)
        out.append(item)
    return out
