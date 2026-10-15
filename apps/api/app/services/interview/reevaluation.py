"""Re-evaluating answers that ended with a fallback evaluation (S12, audit B3).

When the main LLM was down through every attempt of ``answer.evaluate`` (S11 waits ~34 min), the answer keeps
a neutral fallback evaluation (``answer_evaluations.is_fallback``), which scoring ignores (it is unassessed,
not evidence). ``answer.reevaluate`` runs the evaluation again and replaces the fallback in place; the session's
scores and reports are then refreshed (``report.generate`` reason ``reevaluation`` recomputes the scores).

Two entry points queue it: staff (``POST /sessions/{id}/reevaluate``) and a periodic sweep that runs only while
the main model is healthy. An answer is re-tried at most ``MAX_REEVALUATIONS_PER_ANSWER`` times (counted on the
evaluation row itself, ``evaluation.reevaluations_queued``), so one the model cannot evaluate never loops and the
sweep only ever selects sessions that still have a retryable fallback. Cancelled (aborted) sessions have no report
by design and are never re-evaluated.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.queue import enqueue
from app.db.models import AnswerEvaluation, BackgroundJob, InterviewSession
from app.db.session import session_scope
from app.domain.enums import SessionStatus

log = get_logger(__name__)

REEVALUATE_JOB = "answer.reevaluate"
SWEEP_JOB = "evaluation.fallback_sweep"
SWEEP_INTERVAL_SECONDS = 600
SWEEP_LOOKBACK_DAYS = 14
SWEEP_BATCH = 50
MAX_REEVALUATIONS_PER_ANSWER = 3
ATTEMPTS_FIELD = "reevaluations_queued"
# Statuses in which a session's evaluations are final (no live runtime writes them any more) and its reports are
# worth refreshing. ABORTED is a cancelled interview: deliberately no scoring and no report.
REEVALUABLE_STATUSES: frozenset[str] = frozenset({SessionStatus.COMPLETED.value, SessionStatus.FAILED.value})

_ATTEMPTS = func.coalesce(AnswerEvaluation.evaluation[ATTEMPTS_FIELD].astext.cast(Integer), 0)
_RETRYABLE = (AnswerEvaluation.is_fallback.is_(True), _ATTEMPTS < MAX_REEVALUATIONS_PER_ANSWER)


async def fallback_count(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Answers of the session whose evaluation is still a fallback."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AnswerEvaluation)
            .where(AnswerEvaluation.session_id == session_id, AnswerEvaluation.is_fallback.is_(True))
        )
        or 0
    )


async def queue_reevaluation(db: AsyncSession, session: InterviewSession, *, actor: str) -> int:
    """Queue ``answer.reevaluate`` for every retryable fallback answer of an ended session; returns how many are
    queued (including ones already waiting)."""
    if session.status not in REEVALUABLE_STATUSES:
        return 0
    rows = (
        await db.execute(
            select(AnswerEvaluation.id, AnswerEvaluation.answer_id).where(
                AnswerEvaluation.session_id == session.id, *_RETRYABLE
            )
        )
    ).all()
    queued = 0
    for evaluation_id, answer_id in rows:
        key = f"{REEVALUATE_JOB}:{answer_id}"
        # A finished job releases its dedupe key: one holding it is still waiting or running.
        if await db.scalar(select(BackgroundJob.id).where(BackgroundJob.dedupe_key == key)) is None:
            await enqueue(
                REEVALUATE_JOB,
                {"session_id": str(session.id), "answer_id": str(answer_id), "actor": actor[:40]},
                dedupe_key=key,
                db=db,
            )
            await db.execute(
                update(AnswerEvaluation)
                .where(AnswerEvaluation.id == evaluation_id)
                .values(
                    evaluation=AnswerEvaluation.evaluation.op("||")(
                        func.jsonb_build_object(ATTEMPTS_FIELD, _ATTEMPTS + 1)
                    )
                )
            )
        queued += 1
    if queued:
        log.info("evaluation.reevaluation_queued", session_id=str(session.id), answers=queued, actor=actor)
    return queued


async def sweep_fallbacks(*, limit: int = SWEEP_BATCH, now: datetime | None = None) -> dict[str, Any]:
    """Queue re-evaluations for recent ended sessions with fallbacks - only while the main model is healthy."""
    from app.core import ai_health

    report = await ai_health.probe()
    if (report.get("main") or {}).get("status") != "ok":
        return {"skipped": "main_unhealthy", "queued": 0}
    since = (now or datetime.now(UTC)) - timedelta(days=SWEEP_LOOKBACK_DAYS)
    queued = 0
    async with session_scope() as db:
        sessions = (
            await db.scalars(
                select(InterviewSession)
                .where(
                    InterviewSession.status.in_(tuple(REEVALUABLE_STATUSES)),
                    InterviewSession.ended_at.is_not(None),
                    InterviewSession.ended_at >= since,
                    # Only sessions that still have a retryable fallback: exhausted ones never hold the batch.
                    InterviewSession.id.in_(select(AnswerEvaluation.session_id).where(*_RETRYABLE)),
                )
                .order_by(InterviewSession.ended_at.desc())
                .limit(limit)
            )
        ).all()
        for session in sessions:
            queued += await queue_reevaluation(db, session, actor="fallback_sweep")
            if queued >= limit:
                break
    if queued:
        log.info("evaluation.fallback_sweep", sessions=len(sessions), queued=queued)
    return {"sessions": len(sessions), "queued": queued}


__all__ = [
    "ATTEMPTS_FIELD",
    "MAX_REEVALUATIONS_PER_ANSWER",
    "REEVALUABLE_STATUSES",
    "REEVALUATE_JOB",
    "SWEEP_INTERVAL_SECONDS",
    "SWEEP_JOB",
    "fallback_count",
    "queue_reevaluation",
    "sweep_fallbacks",
]
