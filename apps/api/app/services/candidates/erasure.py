"""Permanent deletion of a candidate's personal data (data-subject erasure).

``erase_candidate`` runs inside the request transaction:

1. Preconditions (row lock held): the candidate is archived first (two-step
   safety for an irreversible action) and nothing is still working on their
   data - no running interview session, no resume being processed.
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
   profiles, interviews, consents, auth sessions, sessions and everything a
   session produced (questions, answers, transcripts, evaluations, evidence,
   scores, reports, reviews, coding data, integrity events).

After the commit, the candidate's cached auth state and the sessions' Redis
keys (state, outbox, preparation caches) are removed.

A failure in any step rolls the transaction back; file deletion is idempotent,
so the request can simply be retried.
"""

from __future__ import annotations

import functools
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ServiceUnavailable
from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.core.queue import publish_session_event
from app.db.models import (
    Answer,
    AnswerEvaluation,
    AuditLog,
    AuthSession,
    Candidate,
    CandidateProfile,
    CandidateResume,
    Interview,
    InterviewSession,
    LinkRegistration,
    MediaAsset,
    ModelCall,
    Report,
)
from app.domain.enums import ActorType, ResumeStatus, SessionStatus
from app.services.identity.auth_cache import invalidate_auth_cache
from app.services.storage import get_storage
from app.services.storage.media import delete_media

log = get_logger(__name__)

ERASURE_REASON = "data_subject_deletion"

# A session in one of these states is being worked on by the realtime runtime
# or a background job, unless nothing touched it for ACTIVE_WINDOW.
RUNNING_SESSION_STATUSES = frozenset(
    {
        SessionStatus.PREPARING.value,
        SessionStatus.LIVE.value,
        SessionStatus.PAUSED.value,
        SessionStatus.COMPLETING.value,
    }
)
# Waiting sessions only count as active while a client is connected
# (the runtime writes ``last_heartbeat_at`` every 30 s).
WAITING_SESSION_STATUSES = frozenset({SessionStatus.CREATED.value, SessionStatus.READY.value})
TERMINAL_SESSION_STATUSES = frozenset(
    {SessionStatus.COMPLETED.value, SessionStatus.ABORTED.value, SessionStatus.FAILED.value}
)
PROCESSING_RESUME_STATUSES = frozenset(
    {
        ResumeStatus.UPLOADED.value,
        ResumeStatus.VALIDATING.value,
        ResumeStatus.EXTRACTING.value,
        ResumeStatus.OCR.value,
        ResumeStatus.ANALYZING.value,
    }
)
ACTIVE_WINDOW = timedelta(minutes=15)
CONNECTED_WINDOW = timedelta(minutes=2)
_REDIS_SCAN_COUNT = 500
_ERASED_PREFIX = "erased/"


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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


async def find_blockers(
    db: AsyncSession, candidate_id: uuid.UUID, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Sessions or resumes of the candidate that something is still working on."""
    now = now or datetime.now(UTC)
    blockers: list[dict[str, Any]] = []
    sessions = (
        await db.execute(
            select(
                InterviewSession.id,
                InterviewSession.status,
                InterviewSession.updated_at,
                InterviewSession.last_heartbeat_at,
            ).where(
                InterviewSession.candidate_id == candidate_id,
                InterviewSession.status.not_in(TERMINAL_SESSION_STATUSES),
            )
        )
    ).all()
    for sid, status, updated_at, heartbeat_at in sessions:
        active = False
        if status in RUNNING_SESSION_STATUSES:
            last = _latest(updated_at, heartbeat_at)
            active = last is not None and now - last < ACTIVE_WINDOW
        elif status in WAITING_SESSION_STATUSES:
            active = heartbeat_at is not None and now - heartbeat_at < CONNECTED_WINDOW
        if active:
            blockers.append({"type": "session", "id": str(sid), "status": status})
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


def _ids(values: Iterable[Any]) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for value in values:
        if value is None:
            continue
        try:
            out.add(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


async def _model_call_ids(
    db: AsyncSession, candidate_id: uuid.UUID, session_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for (call_ids,) in (
        await db.execute(
            select(CandidateProfile.model_call_ids).where(CandidateProfile.candidate_id == candidate_id)
        )
    ).all():
        ids |= _ids(call_ids or [])
    if session_ids:
        for (call_ids,) in (
            await db.execute(select(Report.model_call_ids).where(Report.session_id.in_(session_ids)))
        ).all():
            ids |= _ids(call_ids or [])
        ids |= _ids(
            (
                await db.scalars(
                    select(AnswerEvaluation.model_call_id).where(
                        AnswerEvaluation.session_id.in_(session_ids),
                        AnswerEvaluation.model_call_id.is_not(None),
                    )
                )
            ).all()
        )
    return ids


async def _delete_files(
    db: AsyncSession, candidate_id: uuid.UUID, session_ids: list[uuid.UUID], summary: ErasureSummary
) -> None:
    conditions = [MediaAsset.candidate_id == candidate_id]
    if session_ids:
        conditions.append(MediaAsset.session_id.in_(session_ids))
    assets = list(
        (await db.scalars(select(MediaAsset).where(or_(*conditions)).order_by(MediaAsset.created_at))).all()
    )
    registered = {a.storage_key for a in assets}
    storage = get_storage()
    kinds: Counter[str] = Counter()
    failures = 0
    for asset in assets:
        if asset.storage_key.startswith(_ERASED_PREFIX):
            continue  # anonymized by an earlier erasure
        if asset.deleted_at is None:
            size = int(asset.size_bytes or 0)
            try:
                deleted = await delete_media(db, asset, reason=ERASURE_REASON)
            except OSError as exc:
                failures += 1
                log.error("candidate.erase_file_failed", media_id=str(asset.id), error=type(exc).__name__)
                continue
            except ValueError:  # InvalidStorageKey: no file can exist there
                asset.deleted_at = datetime.now(UTC)
                deleted = True
            if deleted:
                summary.media_files_deleted += 1
                summary.media_bytes_deleted += size
                kinds[asset.kind] += 1
        else:
            # Soft-deleted earlier (e.g. retention): make sure no bytes survived.
            try:
                await storage.delete(asset.storage_key)
            except ValueError:
                pass
            except OSError as exc:
                failures += 1
                log.error("candidate.erase_file_failed", media_id=str(asset.id), error=type(exc).__name__)
                continue
        # Keep the row as an anonymous deletion record (no file name, hash or details).
        asset.storage_key = f"{_ERASED_PREFIX}{asset.id}"
        asset.sha256 = None
        asset.details = {"deleted_reason": ERASURE_REASON, "erased": True}
        summary.media_records_anonymized += 1
    # Files referenced by a row but never registered as media (defensive).
    keys: set[str] = set(
        (
            await db.scalars(
                select(CandidateResume.storage_key).where(CandidateResume.candidate_id == candidate_id)
            )
        ).all()
    )
    if session_ids:
        keys |= {
            k
            for k in (
                await db.scalars(
                    select(Answer.audio_storage_key).where(
                        Answer.session_id.in_(session_ids), Answer.audio_storage_key.is_not(None)
                    )
                )
            ).all()
            if k
        }
        keys |= {
            k
            for k in (
                await db.scalars(
                    select(Report.artifact_storage_key).where(
                        Report.session_id.in_(session_ids), Report.artifact_storage_key.is_not(None)
                    )
                )
            ).all()
            if k
        }
    for key in sorted(keys - registered):
        try:
            if await storage.delete(key):
                summary.unregistered_files_deleted += 1
        except (OSError, ValueError) as exc:  # ValueError: InvalidStorageKey
            if isinstance(exc, OSError):
                failures += 1
            log.error("candidate.erase_unregistered_file_failed", error=type(exc).__name__)
    if failures:
        raise ServiceUnavailable(
            "Some stored files could not be deleted. Nothing was deleted from the database; please retry.",
            code="erasure_incomplete",
            details={"failed_files": failures},
        )
    await db.flush()
    summary.media_by_kind = dict(sorted(kinds.items()))


async def _clear_redis(session_ids: list[uuid.UUID]) -> None:
    from app.core.redis import get_redis

    redis = get_redis()
    removed = 0
    try:
        for sid in session_ids:
            keys = [key async for key in redis.scan_iter(match=f"session:{sid}:*", count=_REDIS_SCAN_COUNT)]
            for start in range(0, len(keys), 500):
                removed += int(await redis.delete(*keys[start : start + 500]))
    except (RedisError, OSError) as exc:  # keys expire on their own (<= 24 h)
        log.warning("candidate.erase_redis_failed", error=type(exc).__name__, sessions=len(session_ids))
        return
    if removed:
        log.info("candidate.erase_redis_cleared", keys=removed, sessions=len(session_ids))


async def erase_candidate(db: AsyncSession, candidate: Candidate) -> ErasureSummary:
    """Delete the candidate and all their data; ``candidate`` must be row-locked
    (``get_candidate(..., for_update=True)``). Returns counts for the audit trail."""
    if candidate.archived_at is None:
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
    summary.resumes = len(
        (await db.scalars(select(CandidateResume.id).where(CandidateResume.candidate_id == cid))).all()
    )
    summary.profiles = len(
        (await db.scalars(select(CandidateProfile.id).where(CandidateProfile.candidate_id == cid))).all()
    )
    auth_conditions = [AuthSession.candidate_id == cid]
    if interview_ids:
        auth_conditions.append(AuthSession.interview_id.in_(interview_ids))
    auth_ids = list((await db.scalars(select(AuthSession.id).where(or_(*auth_conditions)))).all())
    summary.auth_sessions_revoked = len(auth_ids)

    # Ledger: keep metrics, drop content.
    call_ids = await _model_call_ids(db, cid, session_ids)
    ledger_conditions = []
    if call_ids:
        ledger_conditions.append(ModelCall.id.in_(call_ids))
    if session_ids:
        ledger_conditions.append(ModelCall.session_id.in_(session_ids))
    if ledger_conditions:
        result = await db.execute(
            update(ModelCall)
            .where(or_(*ledger_conditions))
            .values(request_payload=None, response_payload=None, error=None)
            .execution_options(synchronize_session=False)
        )
        summary.model_calls_scrubbed = int(result.rowcount or 0)  # type: ignore[attr-defined]
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
    summary.link_registrations = len(
        (await db.scalars(select(LinkRegistration.id).where(LinkRegistration.candidate_id == cid))).all()
    )
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
    await _delete_files(db, cid, session_ids, summary)

    await db.execute(
        delete(Candidate).where(Candidate.id == cid).execution_options(synchronize_session=False)
    )
    db.expunge(candidate)
    await db.flush()

    async def _after_commit() -> None:
        await invalidate_auth_cache(auth_ids)
        await _clear_redis(session_ids)

    async def _notify(sid: uuid.UUID) -> None:  # a runtime still attached to a stale session ends it
        await publish_session_event(sid, {"type": "session.cancelled", "reason": "candidate_deleted"})

    run_after_commit(db, _after_commit)
    for sid in open_session_ids:
        run_after_commit(db, functools.partial(_notify, sid))
    log.info(
        "candidate.erased",
        candidate_id=str(cid),
        sessions=summary.sessions,
        media_files=summary.media_files_deleted,
        model_calls=summary.model_calls_scrubbed,
    )
    return summary
