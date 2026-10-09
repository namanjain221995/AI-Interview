"""Round progression for open-link registrations (OPEN_LINKS.md section 5).

``sync_registration`` is the single, idempotent state function: it reads the
current round attempt (an ``interviews`` row + its latest session) and moves
the registration accordingly - next round unlocked (``immediate``),
``awaiting_review`` (``after_review`` or a ``policy_termination`` /
``technical_failure`` ending), ``completed``, ``expired``, ``cancelled`` or
``in_progress``. Progression never looks at scores and never rejects anyone.

Call sites: the ``open_link.round_finished`` job (enqueued from
``scoring.finalize``), lazy syncs on progress/list reads, the periodic
``open_link.sweep`` and the test hook. The row lock on the registration plus
``uq_link_registration_rounds_attempt`` make concurrent calls safe.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import AppError, Conflict
from app.core.logging import get_logger
from app.core.rbac import Principal
from app.db.models import (
    Candidate,
    Interview,
    InterviewLink,
    InterviewSession,
    Job,
    LinkRegistration,
    LinkRegistrationRound,
    Organization,
)
from app.domain.enums import InterviewStatus, SessionStatus
from app.schemas.api.interviews import InterviewCreate
from app.schemas.api.open_links import LinkProgress, LinkProgressRound
from app.services.jobs.invitations import CANCELLABLE, cancel_invitation, create_invitation
from app.services.open_links.constants import (
    REVIEW_END_REASONS,
    LinkStatus,
    Progression,
    RegistrationStatus,
    RoundState,
    UnlockedBy,
)
from app.services.open_links.links import PipelineRound, next_available, pipeline, round_minutes

log = get_logger(__name__)

RS = RegistrationStatus
SYNCABLE = frozenset({RS.REGISTERED.value, RS.IN_PROGRESS.value, RS.EXPIRED.value})
LIVE_SESSION = frozenset(
    {
        SessionStatus.LIVE.value,
        SessionStatus.PAUSED.value,
        SessionStatus.COMPLETING.value,
        SessionStatus.COMPLETED.value,
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


# ============================================================ attempts


@dataclass(slots=True)
class SessionInfo:
    id: uuid.UUID
    status: str
    end_reason: str | None
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(slots=True)
class Attempt:
    row: LinkRegistrationRound
    interview: Interview
    session: SessionInfo | None
    state: str

    @property
    def sequence(self) -> int:
        return self.row.sequence


def round_state(interview: Interview, session: SessionInfo | None, now: datetime | None = None) -> str:
    status = interview.status
    if status == InterviewStatus.COMPLETED.value:
        return RoundState.COMPLETED.value
    if status == InterviewStatus.CANCELLED.value:
        return RoundState.CANCELLED.value
    if status == InterviewStatus.IN_PROGRESS.value:
        return RoundState.IN_PROGRESS.value
    if session is not None and session.status in LIVE_SESSION:
        return RoundState.IN_PROGRESS.value
    expires = interview.invite_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if status == InterviewStatus.EXPIRED.value or (expires is not None and expires <= (now or _now())):
        return RoundState.EXPIRED.value
    return RoundState.INVITED.value


async def load_attempts(
    db: AsyncSession, registration_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[Attempt]]:
    """Every round attempt of the registrations, ordered by (sequence, attempt)."""
    ids = list(registration_ids)
    out: dict[uuid.UUID, list[Attempt]] = defaultdict(list)
    if not ids:
        return out
    rows = (
        await db.execute(
            select(LinkRegistrationRound, Interview)
            .join(Interview, Interview.id == LinkRegistrationRound.interview_id)
            .where(LinkRegistrationRound.registration_id.in_(ids))
            .order_by(LinkRegistrationRound.sequence, LinkRegistrationRound.attempt)
            .execution_options(populate_existing=True)
        )
    ).all()
    interview_ids = [interview.id for _, interview in rows]
    sessions: dict[uuid.UUID, SessionInfo] = {}
    if interview_ids:
        for s in (
            await db.execute(
                select(
                    InterviewSession.interview_id,
                    InterviewSession.id,
                    InterviewSession.status,
                    InterviewSession.end_reason,
                    InterviewSession.started_at,
                    InterviewSession.ended_at,
                )
                .where(InterviewSession.interview_id.in_(interview_ids))
                .order_by(InterviewSession.interview_id, InterviewSession.created_at.desc())
                .distinct(InterviewSession.interview_id)
            )
        ).all():
            sessions[s.interview_id] = SessionInfo(s.id, s.status, s.end_reason, s.started_at, s.ended_at)
    now = _now()
    for row, interview in rows:
        session = sessions.get(interview.id)
        out[row.registration_id].append(
            Attempt(row, interview, session, round_state(interview, session, now))
        )
    return out


def latest_by_sequence(attempts: Sequence[Attempt]) -> dict[int, Attempt]:
    latest: dict[int, Attempt] = {}
    for item in attempts:
        current = latest.get(item.sequence)
        if current is None or item.row.attempt > current.row.attempt:
            latest[item.sequence] = item
    return latest


def current_attempt(registration: LinkRegistration, attempts: Sequence[Attempt]) -> Attempt | None:
    return latest_by_sequence(attempts).get(registration.current_round_index)


def pick_current(attempts: Sequence[Attempt]) -> Attempt | None:
    """Newest attempt that is not cancelled (else the newest one)."""
    ordered = sorted(attempts, key=lambda a: (a.row.sequence, a.row.attempt), reverse=True)
    for item in ordered:
        if item.state != RoundState.CANCELLED.value:
            return item
    return ordered[0] if ordered else None


def looks_stale(registration: LinkRegistration, attempts: Sequence[Attempt]) -> bool:
    """True when ``sync_registration`` would change the registration."""
    if registration.status not in SYNCABLE:
        return False
    current = current_attempt(registration, attempts)
    if current is None:
        return False
    expected = {
        RoundState.INVITED.value: RS.REGISTERED.value,
        RoundState.IN_PROGRESS.value: RS.IN_PROGRESS.value,
        RoundState.EXPIRED.value: RS.EXPIRED.value,
    }.get(current.state)
    return expected != registration.status


# ============================================================== unlock


async def unlock_round(
    db: AsyncSession,
    *,
    link: InterviewLink,
    registration: LinkRegistration,
    target: PipelineRound,
    unlocked_by: UnlockedBy,
    user_id: uuid.UUID | None = None,
    note: str | None = None,
    attempt: int = 1,
) -> Interview:
    """Create the interview for one round attempt (+ its ``link_registration_rounds`` row).

    Raises the ``create_invitation`` conflicts (``interview_exists``,
    ``candidate_archived``, ``job_not_open``) unchanged."""
    assert registration.candidate_id is not None and target.round is not None
    issued = await create_invitation(
        db,
        organization_id=registration.organization_id,
        invited_by_id=user_id or link.created_by_id,
        body=InterviewCreate(
            candidate_id=registration.candidate_id,
            job_id=link.job_id,
            round_id=target.round_id,
            allow_round_selection=False,
            expires_in_hours=link.round_window_hours,
        ),
        skip_access_code=True,
    )
    db.add(
        LinkRegistrationRound(
            registration_id=registration.id,
            round_id=target.round_id,
            round_name=target.round.name[:200],
            sequence=target.sequence,
            attempt=attempt,
            interview_id=issued.interview.id,
            unlocked_by=unlocked_by.value,
            unlocked_by_user_id=user_id,
            note=note,
        )
    )
    await db.flush()
    return issued.interview


# ================================================================ sync


@dataclass(slots=True)
class SyncResult:
    registration: LinkRegistration | None
    changed: bool = False
    previous_status: str | None = None
    unlocked_interview_id: uuid.UUID | None = None
    notes: list[str] = field(default_factory=list)


async def lock_registration(db: AsyncSession, registration_id: uuid.UUID) -> LinkRegistration | None:
    return await db.scalar(
        select(LinkRegistration)
        .where(LinkRegistration.id == registration_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def sync_registration(db: AsyncSession, registration_id: uuid.UUID) -> SyncResult:
    registration = await lock_registration(db, registration_id)
    if registration is None:
        return SyncResult(None)
    result = SyncResult(registration, previous_status=registration.status)
    if registration.status not in SYNCABLE:
        return result
    attempts = (await load_attempts(db, [registration.id]))[registration.id]
    current = current_attempt(registration, attempts)
    if current is None:
        return result
    link = await db.get(InterviewLink, registration.link_id)
    if link is None:
        return result
    now = _now()
    status, reason = registration.status, registration.status_reason
    state = current.state
    if state == RoundState.COMPLETED.value:
        rounds = await pipeline(db, link)
        target = next_available(rounds, current.sequence)
        end_reason = current.session.end_reason if current.session else None
        if target is None:
            status, reason = RS.COMPLETED.value, None
            registration.completed_at = registration.completed_at or now
        elif end_reason in REVIEW_END_REASONS:
            status, reason = RS.AWAITING_REVIEW.value, end_reason
        elif link.progression == Progression.AFTER_REVIEW.value:
            status, reason = RS.AWAITING_REVIEW.value, "after_review"
        else:
            try:
                async with db.begin_nested():
                    interview = await unlock_round(
                        db, link=link, registration=registration, target=target, unlocked_by=UnlockedBy.AUTO
                    )
            except IntegrityError:
                # A concurrent sync unlocked it first: re-read and stop.
                log.info("open_link.sync_race", registration_id=str(registration.id))
                await db.refresh(registration)
                return SyncResult(registration, changed=True, previous_status=result.previous_status)
            except AppError as exc:
                # Job closed, candidate archived, open recruiter invite: a person decides.
                status, reason = RS.AWAITING_REVIEW.value, exc.code[:40]
                result.notes.append(exc.code)
            else:
                registration.current_round_index = target.sequence
                status, reason = RS.REGISTERED.value, None
                result.unlocked_interview_id = interview.id
                await audit(
                    db,
                    None,
                    "link.round_unlocked",
                    resource_type="link_registration",
                    resource_id=registration.id,
                    organization_id=registration.organization_id,
                    details={
                        "link_id": str(link.id),
                        "sequence": target.sequence,
                        "interview_id": str(interview.id),
                        "unlocked_by": UnlockedBy.AUTO.value,
                    },
                )
    elif state == RoundState.CANCELLED.value:
        status, reason = RS.CANCELLED.value, reason or "interview_cancelled"
        registration.cancelled_at = registration.cancelled_at or now
    elif state == RoundState.EXPIRED.value:
        status, reason = RS.EXPIRED.value, "round_window_passed"
    elif state == RoundState.IN_PROGRESS.value:
        status, reason = RS.IN_PROGRESS.value, None
    else:
        status, reason = RS.REGISTERED.value, None
    if (status, reason) != (registration.status, registration.status_reason) or result.unlocked_interview_id:
        registration.status, registration.status_reason = status, reason
        result.changed = True
        await db.flush()
        log.info(
            "open_link.synced",
            registration_id=str(registration.id),
            previous=result.previous_status,
            status=status,
            unlocked=bool(result.unlocked_interview_id),
        )
    return result


async def sync_for_interview(db: AsyncSession, interview_id: uuid.UUID) -> SyncResult | None:
    """Sync the registration owning ``interview_id`` (None when it is not a link interview)."""
    registration_id = await db.scalar(
        select(LinkRegistrationRound.registration_id).where(
            LinkRegistrationRound.interview_id == interview_id
        )
    )
    if registration_id is None:
        return None
    return await sync_registration(db, registration_id)


async def sync_stale(db: AsyncSession, registrations: Sequence[LinkRegistration]) -> bool:
    """Lazy sync for list/detail reads; True when anything changed."""
    candidates = [r for r in registrations if r.status in SYNCABLE]
    if not candidates:
        return False
    attempts = await load_attempts(db, [r.id for r in candidates])
    changed = False
    for registration in candidates:
        if looks_stale(registration, attempts.get(registration.id, [])):
            changed |= (await sync_registration(db, registration.id)).changed
    return changed


# ======================================================== staff actions


@dataclass(slots=True)
class AdvanceOutcome:
    advanced: list[dict[str, uuid.UUID]] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)


async def advance(
    db: AsyncSession,
    link: InterviewLink,
    registration_ids: Sequence[uuid.UUID],
    *,
    principal: Principal,
    note: str | None,
) -> AdvanceOutcome:
    outcome = AdvanceOutcome()
    rounds = await pipeline(db, link)
    for registration_id in dict.fromkeys(registration_ids):
        registration = await lock_registration(db, registration_id)
        if registration is None or registration.link_id != link.id:
            outcome.skipped.append({"registration_id": registration_id, "code": "not_found"})
            continue
        if registration.status != RS.AWAITING_REVIEW.value:
            outcome.skipped.append({"registration_id": registration_id, "code": "not_awaiting_review"})
            continue
        later = [r for r in rounds if r.sequence > registration.current_round_index]
        target = next_available(rounds, registration.current_round_index)
        if target is None:
            code = "round_missing" if later else "last_round"
            outcome.skipped.append({"registration_id": registration_id, "code": code})
            continue
        try:
            async with db.begin_nested():
                interview = await unlock_round(
                    db,
                    link=link,
                    registration=registration,
                    target=target,
                    unlocked_by=UnlockedBy.RECRUITER,
                    user_id=principal.user_id,
                    note=note,
                )
        except AppError as exc:
            code = {
                "candidate_archived": "candidate_archived",
                "interview_exists": "already_invited",
            }.get(exc.code, "round_missing")
            outcome.skipped.append({"registration_id": registration_id, "code": code})
            continue
        except IntegrityError:
            outcome.skipped.append({"registration_id": registration_id, "code": "already_invited"})
            continue
        registration.current_round_index = target.sequence
        registration.status, registration.status_reason = RS.REGISTERED.value, None
        outcome.advanced.append(
            {"registration_id": registration.id, "interview_id": interview.id, "round_id": target.round_id}
        )
    await db.flush()
    return outcome


async def retake(
    db: AsyncSession,
    link: InterviewLink,
    registration: LinkRegistration,
    *,
    round_index: int,
    reason: str,
    principal: Principal,
) -> Interview:
    """A new attempt of the current round (recruiter only; audited by the caller)."""
    if registration.status == RS.BLOCKED.value or registration.candidate_id is None:
        raise Conflict("Allow the registration first.", code="retake_not_allowed")
    attempts = (await load_attempts(db, [registration.id]))[registration.id]
    latest = latest_by_sequence(attempts)
    current = latest.get(round_index)
    if round_index != registration.current_round_index or current is None:
        raise Conflict("Only the candidate's current round can be taken again.", code="retake_not_allowed")
    if current.state not in (
        RoundState.COMPLETED.value,
        RoundState.EXPIRED.value,
        RoundState.CANCELLED.value,
    ):
        raise Conflict(
            "This round is still open; the candidate can take it now.",
            code="retake_not_allowed",
            details={"state": current.state},
        )
    candidate = await db.get(Candidate, registration.candidate_id)
    if candidate is None or candidate.archived_at is not None:
        raise Conflict("This candidate is archived. Restore the candidate first.", code="candidate_archived")
    rounds = await pipeline(db, link)
    target = next((r for r in rounds if r.sequence == round_index), None)
    if target is None or not target.available:
        raise Conflict("This round is no longer available in the job.", code="retake_not_allowed")
    try:
        async with db.begin_nested():
            interview = await unlock_round(
                db,
                link=link,
                registration=registration,
                target=target,
                unlocked_by=UnlockedBy.RETAKE,
                user_id=principal.user_id,
                note=reason,
                attempt=current.row.attempt + 1,
            )
    except AppError as exc:
        if exc.code == "candidate_archived":
            raise
        raise Conflict(
            "Another attempt cannot be created now.", code="retake_not_allowed", details={"reason": exc.code}
        ) from exc
    registration.status, registration.status_reason = RS.REGISTERED.value, None
    registration.completed_at = None
    registration.cancelled_at = None
    await db.flush()
    return interview


async def cancel_registration(db: AsyncSession, registration: LinkRegistration, *, reason: str | None) -> int:
    if registration.status in (RS.COMPLETED.value, RS.CANCELLED.value, RS.BLOCKED.value):
        raise Conflict(f"A {registration.status} registration cannot be cancelled.", code="invalid_status")
    attempts = (await load_attempts(db, [registration.id]))[registration.id]
    cancelled = 0
    for item in attempts:
        if item.interview.status in CANCELLABLE:
            await cancel_invitation(
                db, item.interview, reason=reason or "Registration cancelled by the hiring team"
            )
            cancelled += 1
    registration.status = RS.CANCELLED.value
    registration.status_reason = "recruiter_cancelled"
    registration.cancelled_at = _now()
    await db.flush()
    return cancelled


# ============================================================ candidate


async def next_step_for(db: AsyncSession, interview: Interview) -> str:
    if interview.status == InterviewStatus.COMPLETED.value:
        return "complete"
    live = await db.scalar(
        select(InterviewSession.id).where(
            InterviewSession.interview_id == interview.id,
            InterviewSession.status.in_([SessionStatus.LIVE.value, SessionStatus.PAUSED.value]),
        )
    )
    return "interview" if live is not None else "welcome"


async def build_progress(
    db: AsyncSession, registration: LinkRegistration, *, bound_interview_id: uuid.UUID | None
) -> LinkProgress:
    link = await db.get(InterviewLink, registration.link_id)
    assert link is not None
    job = await db.get(Job, link.job_id)
    org = await db.get(Organization, link.organization_id)
    attempts = (await load_attempts(db, [registration.id]))[registration.id]
    latest = latest_by_sequence(attempts)
    rounds_out: list[LinkProgressRound] = []
    for item in await pipeline(db, link):
        attempt = latest.get(item.sequence)
        if item.round is None and attempt is None:
            continue
        name = attempt.row.round_name if attempt is not None else item.name
        rounds_out.append(
            LinkProgressRound(
                sequence=item.sequence,
                name=name,
                round_type=item.round.round_type if item.round is not None else "unknown",
                duration_minutes=round_minutes(item.round) if item.round is not None else 0,
                state=attempt.state if attempt is not None else RoundState.LOCKED.value,  # type: ignore[arg-type]
                interview_id=attempt.interview.id if attempt is not None else None,
                is_current=item.sequence == registration.current_round_index,
            )
        )
    status = registration.status
    current = latest.get(registration.current_round_index)
    next_action = "closed"
    next_interview_id: uuid.UUID | None = None
    next_round_name: str | None = None
    deadline: datetime | None = None
    if status == RS.COMPLETED.value:
        next_action = "all_done"
    elif status == RS.AWAITING_REVIEW.value:
        next_action = "wait_for_review"
    elif status in (RS.REGISTERED.value, RS.IN_PROGRESS.value) and current is not None:
        next_action = "continue_current" if current.interview.id == bound_interview_id else "start_next"
        next_interview_id = current.interview.id
        next_round_name = current.row.round_name
        if current.state == RoundState.INVITED.value:
            deadline = current.interview.invite_expires_at
    return LinkProgress(
        organization_name=org.name if org else "",
        job_title=job.title if job else "",
        link_name=link.name,
        progression=link.progression,  # type: ignore[arg-type]
        rounds=rounds_out,
        next_action=next_action,  # type: ignore[arg-type]
        next_interview_id=next_interview_id,
        next_round_name=next_round_name,
        deadline=deadline,
    )


async def current_interview(db: AsyncSession, registration: LinkRegistration) -> Interview | None:
    attempts = (await load_attempts(db, [registration.id]))[registration.id]
    picked = pick_current(attempts)
    return picked.interview if picked is not None else None


def link_is_closed(link: InterviewLink) -> bool:
    return link.status == LinkStatus.CLOSED.value
