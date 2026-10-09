"""Permanent deletion of a candidate's personal data (data-subject erasure).

``erase_candidate`` runs inside the request transaction:

1. Preconditions (row lock held): the candidate is archived first (two-step
   safety for an irreversible action; ``archive_first=True`` archives in the
   same request) and nothing is still working on their data - no running
   interview session, no resume being processed.
2. Every stored file of the candidate (resumes, answer audio, frames,
   recordings, report artifacts) is removed through
   :func:`app.services.storage.media.delete_media`; the ``media_assets`` rows
   stay as an anonymous deletion record (storage key, file name, hash and
   details are scrubbed). Files referenced by a row but never registered are
   removed too.
3. Model-call ledger rows produced for the candidate keep their metrics
   (latency, tokens, status) but lose any stored payload or error text; audit
   entries the candidate made keep the action but lose IP address and user
   agent.
4. The candidate row is deleted; the database cascades to resumes, chunks,
   profiles, interviews, consents, auth sessions, intake interviews (with their
   stage/activity results, evaluations, evaluator runs, competency scores and
   decisions), open-link registrations, sessions and everything a session
   produced (questions, answers, transcripts, evaluations, evidence, scores,
   reports, reviews, coding data, integrity events).

After the commit, the candidate's cached auth state and the sessions' Redis
keys (state, outbox, preparation caches) are removed.

A failure in any step rolls the transaction back; file deletion is idempotent,
so the request can simply be retried.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.db.models import (
    AuditLog,
    AuthSession,
    Candidate,
    CandidateProfile,
    CandidateResume,
    Interview,
    InterviewSession,
    LinkRegistration,
    MediaAsset,
    Report,
)
from app.db.models.intake import IntakeInterview
from app.domain.enums import ActorType, ResumeStatus
from app.services.data_deletion.common import (
    ACTIVE_WINDOW,
    CONNECTED_WINDOW,
    ERASED_PREFIX,
    RUNNING_SESSION_STATUSES,
    TERMINAL_SESSION_STATUSES,
    WAITING_SESSION_STATUSES,
    after_commit_session_cleanup,
    ids_of,
    purge_files,
    scrub_model_calls,
    session_blockers,
    session_file_keys,
    session_model_call_ids,
)
from app.services.identity.auth_cache import invalidate_auth_cache

log = get_logger(__name__)

ERASURE_REASON = "data_subject_deletion"

__all__ = [
    "ACTIVE_WINDOW",
    "CONNECTED_WINDOW",
    "ERASURE_REASON",
    "PROCESSING_RESUME_STATUSES",
    "RUNNING_SESSION_STATUSES",
    "TERMINAL_SESSION_STATUSES",
    "WAITING_SESSION_STATUSES",
    "ErasurePreview",
    "ErasureSummary",
    "erase_candidate",
    "erasure_preview",
    "find_blockers",
]

PROCESSING_RESUME_STATUSES = frozenset(
    {
        ResumeStatus.UPLOADED.value,
        ResumeStatus.VALIDATING.value,
        ResumeStatus.EXTRACTING.value,
        ResumeStatus.OCR.value,
        ResumeStatus.ANALYZING.value,
    }
)


@dataclass(slots=True)
class ErasureSummary:
    """Counts only - never names, e-mails or file names (it is audited)."""

    candidate_id: str
    interviews: int = 0
    sessions: int = 0
    resumes: int = 0
    profiles: int = 0
    media_files_deleted: int = 0
    media_bytes_deleted: int = 0
    media_by_kind: dict[str, int] = field(default_factory=dict)
    media_records_anonymized: int = 0
    unregistered_files_deleted: int = 0
    model_calls_scrubbed: int = 0
    audit_entries_scrubbed: int = 0
    auth_sessions_revoked: int = 0
    # Open interview links: registrations cascade with the candidate; blocked
    # attempts (no candidate row) with the same e-mail are deleted explicitly.
    link_registrations: int = 0
    link_blocked_registrations_deleted: int = 0
    intake_interviews: int = 0
    reports: int = 0
    archived_in_request: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ErasurePreview:
    """What a deletion would remove right now (counts only)."""

    candidate_id: str
    archived: bool
    interviews: int = 0
    sessions: int = 0
    resumes: int = 0
    reports: int = 0
    intake_interviews: int = 0
    link_registrations: int = 0
    stored_files: int = 0
    stored_bytes: int = 0
    blockers: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def find_blockers(
    db: AsyncSession, candidate_id: uuid.UUID, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Sessions or resumes of the candidate that something is still working on."""
    now = now or datetime.now(UTC)
    blockers = await session_blockers(db, InterviewSession.candidate_id == candidate_id, now=now)
    resumes = (
        await db.execute(
            select(CandidateResume.id, CandidateResume.status, CandidateResume.updated_at).where(
                CandidateResume.candidate_id == candidate_id,
                CandidateResume.status.in_(PROCESSING_RESUME_STATUSES),
            )
        )
    ).all()
    for rid, status, updated_at in resumes:
        if updated_at is not None and now - updated_at < ACTIVE_WINDOW:
            blockers.append({"type": "resume", "id": str(rid), "status": status})
    return blockers


async def _count(db: AsyncSession, model: Any, *where: ColumnElement[bool]) -> int:
    return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _media_conditions(candidate_id: uuid.UUID, session_ids: list[uuid.UUID]) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [MediaAsset.candidate_id == candidate_id]
    if session_ids:
        conditions.append(MediaAsset.session_id.in_(session_ids))
    return conditions


async def erasure_preview(db: AsyncSession, candidate: Candidate) -> ErasurePreview:
    """Counts of what :func:`erase_candidate` would remove (no side effects)."""
    cid = candidate.id
    session_ids = list(
        (await db.scalars(select(InterviewSession.id).where(InterviewSession.candidate_id == cid))).all()
    )
    preview = ErasurePreview(candidate_id=str(cid), archived=candidate.archived_at is not None)
    preview.interviews = await _count(db, Interview, Interview.candidate_id == cid)
    preview.sessions = len(session_ids)
    preview.resumes = await _count(db, CandidateResume, CandidateResume.candidate_id == cid)
    preview.intake_interviews = await _count(db, IntakeInterview, IntakeInterview.candidate_id == cid)
    preview.link_registrations = await _count(db, LinkRegistration, LinkRegistration.candidate_id == cid)
    if session_ids:
        preview.reports = await _count(db, Report, Report.session_id.in_(session_ids))
    files, size = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(
                or_(*_media_conditions(cid, session_ids)),
                MediaAsset.deleted_at.is_(None),
                ~MediaAsset.storage_key.startswith(ERASED_PREFIX),
            )
        )
    ).one()
    preview.stored_files, preview.stored_bytes = int(files or 0), int(size or 0)
    preview.blockers = await find_blockers(db, cid)
    return preview


async def erase_candidate(
    db: AsyncSession, candidate: Candidate, *, archive_first: bool = False
) -> ErasureSummary:
    """Delete the candidate and all their data; ``candidate`` must be row-locked
    (``get_candidate(..., for_update=True)``). Returns counts for the audit trail.

    ``archive_first``: archive a not-yet-archived candidate in the same request
    (one confirmed step in the UI); otherwise 409 ``candidate_not_archived``."""
    if candidate.archived_at is None and not archive_first:
        raise Conflict(
            "Archive the candidate before deleting their data permanently.",
            code="candidate_not_archived",
        )
    now = datetime.now(UTC)
    blockers = await find_blockers(db, candidate.id, now=now)
    if blockers:
        raise Conflict(
            "An interview session or resume of this candidate is still being processed. "
            "Try again in a few minutes.",
            code="candidate_busy",
            details={"blockers": blockers, "retry_after_seconds": int(ACTIVE_WINDOW.total_seconds())},
        )
    cid = candidate.id
    summary = ErasureSummary(candidate_id=str(cid))
    if candidate.archived_at is None:
        candidate.archived_at = now  # everything below goes anyway; recorded for the audit trail
        summary.archived_in_request = True
    interview_ids = list((await db.scalars(select(Interview.id).where(Interview.candidate_id == cid))).all())
    sessions = (
        await db.execute(
            select(InterviewSession.id, InterviewSession.status).where(InterviewSession.candidate_id == cid)
        )
    ).all()
    session_ids = [s.id for s in sessions]
    open_session_ids = [s.id for s in sessions if s.status not in TERMINAL_SESSION_STATUSES]
    summary.interviews = len(interview_ids)
    summary.sessions = len(session_ids)
    summary.resumes = await _count(db, CandidateResume, CandidateResume.candidate_id == cid)
    summary.profiles = await _count(db, CandidateProfile, CandidateProfile.candidate_id == cid)
    summary.intake_interviews = await _count(db, IntakeInterview, IntakeInterview.candidate_id == cid)
    if session_ids:
        summary.reports = await _count(db, Report, Report.session_id.in_(session_ids))
    auth_conditions: list[ColumnElement[bool]] = [AuthSession.candidate_id == cid]
    if interview_ids:
        auth_conditions.append(AuthSession.interview_id.in_(interview_ids))
    auth_ids = list((await db.scalars(select(AuthSession.id).where(or_(*auth_conditions)))).all())
    summary.auth_sessions_revoked = len(auth_ids)

    # Ledger: keep metrics, drop content.
    call_ids = await session_model_call_ids(db, session_ids)
    for (profile_calls,) in (
        await db.execute(select(CandidateProfile.model_call_ids).where(CandidateProfile.candidate_id == cid))
    ).all():
        call_ids |= ids_of(profile_calls or [])
    summary.model_calls_scrubbed = await scrub_model_calls(db, call_ids, session_ids)
    # Audit entries the candidate made: keep the action, drop network identifiers.
    result = await db.execute(
        update(AuditLog)
        .where(AuditLog.actor_type == ActorType.CANDIDATE.value, AuditLog.actor_id == cid)
        .values(ip_address=None, user_agent=None)
        .execution_options(synchronize_session=False)
    )
    summary.audit_entries_scrubbed = int(result.rowcount or 0)  # type: ignore[attr-defined]

    # Open-link registrations: the candidate's rows cascade with the candidate;
    # blocked attempts carry no candidate id, so they go by org + e-mail.
    summary.link_registrations = await _count(db, LinkRegistration, LinkRegistration.candidate_id == cid)
    result = await db.execute(
        delete(LinkRegistration)
        .where(
            LinkRegistration.organization_id == candidate.organization_id,
            LinkRegistration.candidate_id.is_(None),
            LinkRegistration.status == "blocked",
            LinkRegistration.email == candidate.email,
        )
        .execution_options(synchronize_session=False)
    )
    summary.link_blocked_registrations_deleted = int(result.rowcount or 0)  # type: ignore[attr-defined]

    # Files last among the reversible steps: a failure here rolls back the rest.
    extra_keys = set(
        (
            await db.scalars(select(CandidateResume.storage_key).where(CandidateResume.candidate_id == cid))
        ).all()
    ) | await session_file_keys(db, session_ids)
    files = await purge_files(
        db,
        media_conditions=_media_conditions(cid, session_ids),
        extra_keys=extra_keys,
        reason=ERASURE_REASON,
        error_code="erasure_incomplete",
    )
    summary.media_files_deleted = files.files_deleted
    summary.media_bytes_deleted = files.bytes_deleted
    summary.media_by_kind = files.by_kind
    summary.media_records_anonymized = files.records_anonymized
    summary.unregistered_files_deleted = files.unregistered_deleted

    await db.execute(
        delete(Candidate).where(Candidate.id == cid).execution_options(synchronize_session=False)
    )
    db.expunge(candidate)
    await db.flush()

    async def _revoke() -> None:
        await invalidate_auth_cache(auth_ids)

    run_after_commit(db, _revoke)
    after_commit_session_cleanup(db, session_ids, open_session_ids, reason="candidate_deleted")
    log.info(
        "candidate.erased",
        candidate_id=str(cid),
        sessions=summary.sessions,
        media_files=summary.media_files_deleted,
        model_calls=summary.model_calls_scrubbed,
    )
    return summary
