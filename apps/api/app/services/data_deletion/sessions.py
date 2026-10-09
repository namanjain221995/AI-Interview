"""Permanent deletion of interview sessions (ADMIN), without the candidate.

Removes everything a session produced: questions, answers, transcripts,
evaluations, evidence, scores and snapshots, reports (and their HTML/PDF
artifacts), review decisions, coding sessions/snapshots/runs/hints, integrity
events, interview events, intake stage/activity results, evaluations, evaluator
runs, competency scores and decisions (all by database cascade), plus every
stored file of the session (answer audio, intake captures, camera/screen frames,
recordings, report artifacts) through :func:`common.purge_files`.

What stays: the candidate, their résumés and profiles, and the interview
invitation the session belonged to (so a completed invitation cannot be
re-taken by accident). Model-call ledger rows keep their metrics but lose any
stored payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.core.logging import get_logger
from app.db.models import (
    CodingSession,
    IntegrityEvent,
    InterviewSession,
    MediaAsset,
    Report,
    Transcript,
)
from app.services.data_deletion.common import (
    ACTIVE_WINDOW,
    TERMINAL_SESSION_STATUSES,
    after_commit_session_cleanup,
    purge_files,
    scrub_model_calls,
    session_blockers,
    session_file_keys,
    session_model_call_ids,
)

log = get_logger(__name__)

SESSION_DELETE_REASON = "session_deleted"


@dataclass(slots=True)
class SessionDeletionSummary:
    """Counts only (it is audited)."""

    sessions: int = 0
    transcripts: int = 0
    reports: int = 0
    integrity_events: int = 0
    coding_sessions: int = 0
    media_files_deleted: int = 0
    media_bytes_deleted: int = 0
    media_by_kind: dict[str, int] = field(default_factory=dict)
    media_records_anonymized: int = 0
    unregistered_files_deleted: int = 0
    model_calls_scrubbed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _count(db: AsyncSession, model: Any, column: Any, session_ids: Sequence[uuid.UUID]) -> int:
    if not session_ids:
        return 0
    return int(await db.scalar(select(func.count()).select_from(model).where(column.in_(session_ids))) or 0)


async def ensure_sessions_idle(
    db: AsyncSession, session_ids: Sequence[uuid.UUID], *, code: str, message: str
) -> None:
    """409 ``code`` while the runtime or a job is still working on one of the sessions."""
    if not session_ids:
        return
    blockers = await session_blockers(db, InterviewSession.id.in_(list(session_ids)))
    if blockers:
        raise Conflict(
            message,
            code=code,
            details={"blockers": blockers, "retry_after_seconds": int(ACTIVE_WINDOW.total_seconds())},
        )


async def delete_sessions(
    db: AsyncSession,
    session_ids: Sequence[uuid.UUID],
    *,
    reason: str = SESSION_DELETE_REASON,
    busy_code: str = "session_busy",
) -> SessionDeletionSummary:
    """Delete the sessions and all they produced. The caller checks access and locks the parent row."""
    ids = list(dict.fromkeys(session_ids))
    summary = SessionDeletionSummary()
    if not ids:
        return summary
    rows = (
        await db.execute(
            select(InterviewSession.id, InterviewSession.status)
            .where(InterviewSession.id.in_(ids))
            .with_for_update()
        )
    ).all()
    await ensure_sessions_idle(
        db,
        ids,
        code=busy_code,
        message="The interview session is still running or being scored. Try again in a few minutes.",
    )
    ids = [r.id for r in rows]
    open_ids = [r.id for r in rows if r.status not in TERMINAL_SESSION_STATUSES]
    summary.sessions = len(ids)
    summary.transcripts = await _count(db, Transcript, Transcript.session_id, ids)
    summary.reports = await _count(db, Report, Report.session_id, ids)
    summary.integrity_events = await _count(db, IntegrityEvent, IntegrityEvent.session_id, ids)
    summary.coding_sessions = await _count(db, CodingSession, CodingSession.session_id, ids)

    call_ids = await session_model_call_ids(db, ids)
    summary.model_calls_scrubbed = await scrub_model_calls(db, call_ids, ids)

    # Files last among the reversible steps: a failure here rolls back the rest.
    files = await purge_files(
        db,
        media_conditions=[MediaAsset.session_id.in_(ids)],
        extra_keys=await session_file_keys(db, ids),
        reason=reason,
    )
    summary.media_files_deleted = files.files_deleted
    summary.media_bytes_deleted = files.bytes_deleted
    summary.media_by_kind = files.by_kind
    summary.media_records_anonymized = files.records_anonymized
    summary.unregistered_files_deleted = files.unregistered_deleted

    await db.execute(
        delete(InterviewSession)
        .where(InterviewSession.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    for sid in ids:  # drop stale identity-map copies (the rows are gone)
        cached = db.sync_session.identity_map.get(db.sync_session.identity_key(InterviewSession, sid))
        if cached is not None:
            db.expunge(cached)
    await db.flush()
    after_commit_session_cleanup(db, ids, open_ids, reason=reason)
    log.info(
        "sessions.deleted",
        sessions=summary.sessions,
        media_files=summary.media_files_deleted,
        model_calls=summary.model_calls_scrubbed,
    )
    return summary
