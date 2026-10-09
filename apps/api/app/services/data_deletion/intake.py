"""Permanent deletion of one intake interview (ADMIN).

Deletes the intake row (drafts too), its invitation (``interviews`` row: consents,
portal sign-ins, open-link round links cascade), every session of that
invitation with everything the sessions produced (see
:mod:`app.services.data_deletion.sessions`: stage/activity results, captures,
evaluations, evaluator runs, competency scores, decisions, reports + artifacts,
recordings, integrity events and frames) and the stored files.

The candidate, their résumés and profiles stay (they belong to the candidate,
not to one interview; delete the candidate to remove those).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.db.models import AuthSession, Interview, InterviewSession
from app.db.models.intake import IntakeDecision, IntakeEvaluation, IntakeInterview
from app.services.data_deletion.sessions import SessionDeletionSummary, delete_sessions
from app.services.identity.auth_cache import invalidate_auth_cache

log = get_logger(__name__)

INTAKE_DELETE_REASON = "intake_deleted"


@dataclass(slots=True)
class IntakeDeletionSummary:
    """Counts only (it is audited)."""

    intake_id: str
    was_draft: bool = False
    interviews: int = 0
    evaluations: int = 0
    decisions: int = 0
    auth_sessions_revoked: int = 0
    sessions: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def delete_intake(db: AsyncSession, intake: IntakeInterview) -> IntakeDeletionSummary:
    """Delete ``intake`` (row-locked: ``get_intake(..., lock=True)``) and everything tied to it."""
    summary = IntakeDeletionSummary(intake_id=str(intake.id), was_draft=intake.interview_id is None)
    interview_id = intake.interview_id
    session_ids: list[uuid.UUID] = []
    auth_ids: list[uuid.UUID] = []
    interview: Interview | None = None
    if interview_id is not None:
        interview = await db.scalar(select(Interview).where(Interview.id == interview_id).with_for_update())
        if interview is not None:
            summary.interviews = 1
        session_ids = list(
            (
                await db.scalars(
                    select(InterviewSession.id).where(InterviewSession.interview_id == interview_id)
                )
            ).all()
        )
        auth_ids = list(
            (await db.scalars(select(AuthSession.id).where(AuthSession.interview_id == interview_id))).all()
        )
    summary.evaluations = int(
        await db.scalar(
            select(func.count()).select_from(IntakeEvaluation).where(IntakeEvaluation.intake_id == intake.id)
        )
        or 0
    )
    summary.decisions = int(
        await db.scalar(
            select(func.count()).select_from(IntakeDecision).where(IntakeDecision.intake_id == intake.id)
        )
        or 0
    )
    summary.auth_sessions_revoked = len(auth_ids)
    sessions: SessionDeletionSummary = await delete_sessions(
        db, session_ids, reason=INTAKE_DELETE_REASON, busy_code="intake_busy"
    )
    summary.sessions = sessions.as_dict()

    intake_id = intake.id
    if interview_id is not None:
        await db.execute(
            delete(Interview).where(Interview.id == interview_id).execution_options(synchronize_session=False)
        )
    await db.execute(
        delete(IntakeInterview)
        .where(IntakeInterview.id == intake_id)
        .execution_options(synchronize_session=False)
    )
    db.expunge(intake)
    if interview is not None:
        db.expunge(interview)
    await db.flush()

    if auth_ids:

        async def _revoke() -> None:
            await invalidate_auth_cache(auth_ids)

        run_after_commit(db, _revoke)
    log.info(
        "intake.deleted",
        intake_id=str(intake_id),
        sessions=sessions.sessions,
        media_files=sessions.media_files_deleted,
    )
    return summary
