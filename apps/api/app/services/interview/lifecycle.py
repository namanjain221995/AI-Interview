"""Offline session lifecycle (plan package S1).

A started session whose candidate never comes back used to stay ``paused`` forever: no scoring,
no report, "in progress" on every recruiter screen, and a reconnect weeks later with the clock
stopped. :func:`end_session_offline` ends such a session from a worker, through the engine's
own ``end_session`` (partial-coverage scoring and report, intake evaluation), after proving that
nobody is driving it:

* the realtime ownership lock must be free - taken here *fail-closed* with a distinct instance
  id so a live runtime (which refreshes its lock every few seconds) is never taken over;
* optionally the session must have been idle (no state write, no heartbeat) for ``require_idle_s``.

:func:`sweep_abandoned_sessions` applies it to every ``live``/``paused`` session idle past
``SESSION_ABANDON_AFTER_SECONDS`` (batch-limited, dry-run capable). Staff cancellation and the
candidate's REST "end" use the same path through the ``session.end`` job.
"""

from __future__ import annotations

import uuid
from contextlib import aclosing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import SESSIONS_ABANDONED_PENDING, SESSIONS_COMPLETING_STALE, SESSIONS_ENDED_OFFLINE
from app.core.queue import enqueue
from app.core.redis import get_redis
from app.db.models import BackgroundJob, Interview, InterviewSession, Report
from app.db.session import session_scope
from app.domain.enums import InterviewStatus, JobState, ReportStatus, SessionEndReason, SessionStatus
from app.realtime.lock import SessionLock
from app.services.interview.engine import SessionNotFound, get_engine
from app.services.interview.records import log_event
from app.services.interview.sessions import mark_session_completed
from app.services.interview.state_store import ENDED_STATUSES

log = get_logger(__name__)

SWEEPABLE_STATUSES: frozenset[str] = frozenset({SessionStatus.LIVE.value, SessionStatus.PAUSED.value})
#: A session the sweep could not end (error, state mismatch) is left alone this long so it cannot hold the
#: oldest-first batch on every run.
SWEEP_COOLDOWN_S = 3600
_ENDED_VALUES: frozenset[str] = frozenset(status.value for status in ENDED_STATUSES)


@dataclass(frozen=True)
class OfflineEndOutcome:
    session_id: str
    action: Literal["ended", "skipped", "dry_run"]
    # ended/dry_run: the end reason; skipped: not_found | already_ended | active | locked | state_mismatch
    reason: str
    status_before: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SweepSummary:
    candidates: int = 0
    ended: int = 0
    dry_run: int = 0
    skipped: dict[str, int] | None = None
    pending: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "ended": self.ended,
            "dry_run": self.dry_run,
            "skipped": dict(self.skipped or {}),
            "pending": self.pending,
            "errors": self.errors,
        }


def _last_activity(row: InterviewSession) -> datetime | None:
    stamps = [t for t in (row.updated_at, row.last_heartbeat_at, row.started_at) if t is not None]
    if not stamps:
        return None
    latest = max(stamps)
    return latest if latest.tzinfo is not None else latest.replace(tzinfo=UTC)


async def end_session_offline(
    session_id: str,
    reason: SessionEndReason,
    *,
    actor: str,
    require_idle_s: float | None = None,
    dry_run: bool = False,
    engine: Any | None = None,
    redis: Any | None = None,
    now: datetime | None = None,
) -> OfflineEndOutcome:
    """End ``session_id`` from outside the realtime runtime; see the module docstring."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(session_id))
        if row is None:
            return OfflineEndOutcome(session_id, "skipped", "not_found")
        status_before = str(row.status)
        if status_before in _ENDED_VALUES:
            return OfflineEndOutcome(session_id, "skipped", "already_ended", status_before)
        interview_status = await db.scalar(select(Interview.status).where(Interview.id == row.interview_id))
        if require_idle_s is not None:
            last = _last_activity(row)
            idle = (now - last).total_seconds() if last is not None else float("inf")
            if idle < require_idle_s:
                return OfflineEndOutcome(session_id, "skipped", "active", status_before, f"idle {idle:.0f}s")

    # A live runtime refreshes its lock every few seconds: if we cannot take it, somebody is driving
    # the session (or Redis is unreachable, in which case we must not act either).
    lock = SessionLock(
        session_id,
        redis if redis is not None else get_redis(),
        instance_id=f"offline-{settings.instance_id}",
        ttl_seconds=settings.session_lock_ttl_seconds,
        fail_open=False,
    )
    if not await lock.acquire():
        return OfflineEndOutcome(session_id, "skipped", "locked", status_before, lock.holder)
    if interview_status == InterviewStatus.CANCELLED.value and reason is not SessionEndReason.CANCELLED:
        # A cancelled invitation is never scored or reported, whoever ends its session (e.g. the sweep finding
        # a session that was still paused when staff cancelled before S1).
        reason = SessionEndReason.CANCELLED

    async def _lock_lost() -> None:
        log.warning("session.offline_end_lock_lost", session_id=session_id)

    # Held for the whole end (a coding finish alone can take ~20 s): a reconnecting runtime must not resume the
    # session underneath us once the TTL would have run out.
    lock.start_refresh(_lock_lost)
    try:
        if dry_run:
            log.info(
                "session.offline_end_dry_run", session_id=session_id, reason=reason.value, actor=actor,
                status_before=status_before,
            )  # fmt: skip
            return OfflineEndOutcome(session_id, "dry_run", reason.value, status_before)
        active_engine = engine or get_engine()
        try:
            async with aclosing(active_engine.end_session(session_id, reason)) as stream:
                async for _event in stream:
                    pass
            status_after = await _row_status(session_id)
            store = getattr(active_engine, "store", None)
            if status_after not in _ENDED_VALUES and store is not None:
                # The Redis hot copy already said "ended" (a closing whose database write failed), so the engine
                # did nothing: end once more from the durable snapshot.
                log.warning("session.offline_end_stale_cache", session_id=session_id, status=status_after)
                await store.delete_cached(session_id)
                async with aclosing(active_engine.end_session(session_id, reason)) as stream:
                    async for _event in stream:
                        pass
                status_after = await _row_status(session_id)
        except SessionNotFound:
            return OfflineEndOutcome(session_id, "skipped", "not_found", status_before)
        if status_after not in _ENDED_VALUES:
            log.warning("session.offline_end_no_effect", session_id=session_id, status=status_after)
            return OfflineEndOutcome(session_id, "skipped", "state_mismatch", status_before, status_after)
        async with session_scope() as db:
            await log_event(
                db,
                session_id,
                "session.ended_offline",
                {"reason": reason.value, "actor": actor, "status_before": status_before},
            )
        SESSIONS_ENDED_OFFLINE.labels(reason=reason.value, actor=actor).inc()
        log.info(
            "session.ended_offline", session_id=session_id, reason=reason.value, actor=actor,
            status_before=status_before,
        )  # fmt: skip
        return OfflineEndOutcome(session_id, "ended", reason.value, status_before)
    finally:
        await lock.release()


_SWEEP_COOLDOWN = "lifecycle:sweep_cooldown:"
_RECOVERY_COOLDOWN = "lifecycle:recovery_cooldown:"


async def _without_cooldown(
    redis: Any, ids: list[uuid.UUID], *, prefix: str = _SWEEP_COOLDOWN
) -> list[uuid.UUID]:
    if not ids:
        return ids
    try:
        flags = await redis.mget([f"{prefix}{sid}" for sid in ids])
    except Exception:  # noqa: BLE001 - best effort: without Redis every candidate is tried
        return ids
    return [sid for sid, flag in zip(ids, flags, strict=True) if flag is None]


async def _cool_down(redis: Any, session_id: uuid.UUID, *, prefix: str = _SWEEP_COOLDOWN) -> None:
    try:
        await redis.set(f"{prefix}{session_id}", "1", ex=SWEEP_COOLDOWN_S)
    except Exception:  # noqa: BLE001 - best effort
        log.warning("session.sweep_cooldown_failed", session_id=str(session_id))


async def _enqueue_intake_recovery(db: Any, session_id: uuid.UUID, now: datetime) -> str:
    """The intake report when a ready evaluation exists, else the evaluation itself; both complete the session
    when they finish or give up (their on_exhausted hook)."""
    from app.services.intake.pipeline import EVALUATE_JOB, REPORT_JOB, latest_ready_evaluation

    evaluation = await latest_ready_evaluation(db, session_id)
    if evaluation is not None:
        await enqueue(
            REPORT_JOB,
            {"session_id": str(session_id), "evaluation_id": str(evaluation.id), "reason": "recovery"},
            dedupe_key=f"{REPORT_JOB}:{evaluation.id}:recovery:{now:%Y%m%d%H}",
            db=db,
        )
        return REPORT_JOB
    await enqueue(
        EVALUATE_JOB,
        {"session_id": str(session_id), "reason": "session_completed"},
        dedupe_key=f"{EVALUATE_JOB}:{session_id}:recovery:{now:%Y%m%d%H}",
        db=db,
    )
    return EVALUATE_JOB


async def _row_status(session_id: str) -> str | None:
    async with session_scope() as db:
        status = await db.scalar(
            select(InterviewSession.status).where(InterviewSession.id == uuid.UUID(session_id))
        )
    return str(status) if status is not None else None


async def sweep_abandoned_sessions(
    *,
    now: datetime | None = None,
    abandon_after_s: float | None = None,
    limit: int | None = None,
    dry_run: bool | None = None,
    engine: Any | None = None,
    redis: Any | None = None,
) -> SweepSummary:
    """End every live/paused session idle past the abandonment window (oldest first, batch-limited)."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    window = float(abandon_after_s if abandon_after_s is not None else settings.session_abandon_after_seconds)
    batch = int(limit if limit is not None else settings.session_sweep_batch)
    dry = settings.session_sweep_dry_run if dry_run is None else dry_run
    cutoff = now - timedelta(seconds=window)
    last_activity = func.greatest(
        func.coalesce(InterviewSession.last_heartbeat_at, InterviewSession.updated_at),
        InterviewSession.updated_at,
    )
    condition = (InterviewSession.status.in_(SWEEPABLE_STATUSES), last_activity < cutoff)
    summary = SweepSummary(skipped={})
    async with session_scope() as db:
        summary.candidates = int(
            await db.scalar(select(func.count()).select_from(InterviewSession).where(*condition)) or 0
        )
        ids = list(
            (
                await db.scalars(
                    select(InterviewSession.id).where(*condition).order_by(last_activity).limit(batch * 4)
                )
            ).all()
        )
    cooling = redis if redis is not None else get_redis()
    ids = (await _without_cooldown(cooling, ids))[:batch]
    for sid in ids:
        try:
            outcome = await end_session_offline(
                str(sid),
                SessionEndReason.DISCONNECT_TIMEOUT,
                actor="sweeper",
                require_idle_s=window,
                dry_run=dry,
                engine=engine,
                redis=redis,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - one bad session must not stop the sweep
            summary.errors += 1
            log.error("session.sweep_failed", session_id=str(sid), error=f"{type(exc).__name__}: {exc}"[:200])
            await _cool_down(cooling, sid)
            continue
        if outcome.action == "skipped" and outcome.reason == "state_mismatch":
            await _cool_down(cooling, sid)
        if outcome.action == "ended":
            summary.ended += 1
        elif outcome.action == "dry_run":
            summary.dry_run += 1
        else:
            assert summary.skipped is not None
            summary.skipped[outcome.reason] = summary.skipped.get(outcome.reason, 0) + 1
    summary.pending = max(0, summary.candidates - summary.ended)
    SESSIONS_ABANDONED_PENDING.set(summary.pending)
    log.info("session.sweep", **summary.as_dict(), dry_run_mode=dry, window_s=window)
    return summary


# ------------------------------------------------------------- stuck in COMPLETING (S2)

REPORT_JOB_TYPES: tuple[str, ...] = (
    "report.generate",
    "intake.report",
    "intake.evaluate",
    "scoring.finalize",
)
_LIVE_JOB_STATES: frozenset[str] = frozenset({JobState.QUEUED.value, JobState.RUNNING.value})


@dataclass
class RecoverySummary:
    stale: int = 0
    completed: int = 0
    re_enqueued: int = 0
    skipped_busy: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _live_report_jobs(db: Any, session_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(
                BackgroundJob.type.in_(REPORT_JOB_TYPES),
                BackgroundJob.status.in_(_LIVE_JOB_STATES),
                BackgroundJob.payload["session_id"].astext == str(session_id),
            )
        )
        or 0
    )


async def recover_stuck_completing(
    *, now: datetime | None = None, stale_after_s: float | None = None, limit: int | None = None
) -> RecoverySummary:
    """Sessions COMPLETING past the stale window with no report job alive.

    A dead ``report.generate`` (non-model error on the last attempt, or a worker killed through
    every attempt) used to leave the session in COMPLETING forever, outside the review queue.
    When a READY report already exists the session is simply completed; otherwise one more
    ``report.generate`` is enqueued (single attempt, so the deterministic fallback narrative is
    used) and its ``on_exhausted`` hook completes the session if even that fails."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    window = float(stale_after_s if stale_after_s is not None else settings.session_completing_stale_seconds)
    batch = int(limit if limit is not None else settings.session_sweep_batch)
    cutoff = now - timedelta(seconds=window)
    summary = RecoverySummary()
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(InterviewSession.id, InterviewSession.round_config["round_type"].astext)
                .where(
                    InterviewSession.status == SessionStatus.COMPLETING.value,
                    func.coalesce(InterviewSession.ended_at, InterviewSession.updated_at) < cutoff,
                )
                .order_by(func.coalesce(InterviewSession.ended_at, InterviewSession.updated_at))
                .limit(batch * 4)
            )
        ).all()
    round_types = {row[0]: row[1] for row in rows}
    cooling = get_redis()
    # A session this sweep already acted on waits an hour for that job before being looked at again, so it is
    # never re-queued every run and never holds the oldest-first batch.
    candidates = (await _without_cooldown(cooling, list(round_types), prefix=_RECOVERY_COOLDOWN))[:batch]
    summary.stale = len(rows)
    for sid in candidates:
        try:
            async with session_scope() as db:
                if await _live_report_jobs(db, sid):
                    summary.skipped_busy += 1
                    continue
                ready = await db.scalar(
                    select(func.count())
                    .select_from(Report)
                    .where(Report.session_id == sid, Report.status == ReportStatus.READY.value)
                )
                if ready:
                    if await mark_session_completed(db, sid):
                        summary.completed += 1
                        log.info("session.recovery_completed", session_id=str(sid), reason="report_ready")
                    continue
                if round_types.get(sid) == "intake":
                    # Intake sessions are reported by the intake pipeline: the generic report job only hands over
                    # when a ready evaluation exists, and otherwise "succeeded" without completing anything.
                    job = await _enqueue_intake_recovery(db, sid, now)
                else:
                    job = "report.generate"
                    await enqueue(
                        job,
                        {"session_id": str(sid), "reason": "recovery"},
                        dedupe_key=f"report.generate:{sid}:recovery:{now:%Y%m%d%H}",
                        max_attempts=1,
                        db=db,
                    )
            await _cool_down(cooling, sid, prefix=_RECOVERY_COOLDOWN)
            summary.re_enqueued += 1
            log.warning("session.recovery_report_enqueued", session_id=str(sid), job_type=job)
        except Exception as exc:  # noqa: BLE001 - one bad session must not stop the sweep
            summary.errors += 1
            log.error(
                "session.recovery_failed", session_id=str(sid), error=f"{type(exc).__name__}: {exc}"[:200]
            )
    SESSIONS_COMPLETING_STALE.set(max(0, summary.stale - summary.completed))
    log.info("session.recovery_sweep", **summary.as_dict(), window_s=window)
    return summary
