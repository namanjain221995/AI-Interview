"""S2: a dead report job never strands a session in COMPLETING (on_exhausted + recovery sweep)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.queue import JobContext
from app.db.models import BackgroundJob, InterviewEvent, InterviewSession, Report
from app.db.session import session_scope
from app.services.interview.lifecycle import recover_stuck_completing
from app.workers.reports import on_report_exhausted

pytestmark = pytest.mark.integration


async def _set_session(sid: uuid.UUID, **values) -> None:
    async with session_scope() as db:
        await db.execute(update(InterviewSession).where(InterviewSession.id == sid).values(**values))


async def _status(sid: uuid.UUID) -> str:
    async with session_scope() as db:
        return (await db.get(InterviewSession, sid)).status


async def _report_jobs(sid: uuid.UUID) -> list[BackgroundJob]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.type == "report.generate", BackgroundJob.payload["session_id"].astext == str(sid)
                    )
                )
            ).all()
        )


async def test_report_hook_fails_pending_reports_and_completes_the_session(session_factory, mock_ai, storage, redis) -> None:
    fx = await session_factory()
    sid = fx.session_id
    await _set_session(sid, status="completing")
    async with session_scope() as db:
        db.add(Report(session_id=sid, type="recruiter", status="generating", version=1, content={}))
        db.add(Report(session_id=sid, type="candidate", status="pending", version=1, content={}))
    ctx = JobContext(job_id="job-dead", job_type="report.generate", attempt=3, max_attempts=3, payload={"session_id": str(sid)})
    await on_report_exhausted(ctx, OSError("disk full"))
    assert await _status(sid) == "completed"
    async with session_scope() as db:
        statuses = {r.status for r in (await db.scalars(select(Report).where(Report.session_id == sid))).all()}
        events = [e.type for e in (await db.scalars(select(InterviewEvent).where(InterviewEvent.session_id == sid))).all()]
    assert statuses == {"failed"} and "report.failed" in events


async def test_recovery_sweep_completes_or_reenqueues_stale_completing_sessions(session_factory, mock_ai, storage, redis) -> None:
    stale_at = datetime.now(UTC) - timedelta(minutes=30)
    with_report = await session_factory()
    await _set_session(with_report.session_id, status="completing", ended_at=stale_at)
    async with session_scope() as db:
        db.add(Report(session_id=with_report.session_id, type="recruiter", status="ready", version=1, content={}))
    without_report = await session_factory()
    await _set_session(without_report.session_id, status="completing", ended_at=stale_at)
    busy = await session_factory()
    await _set_session(busy.session_id, status="completing", ended_at=stale_at)
    async with session_scope() as db:
        db.add(BackgroundJob(type="report.generate", payload={"session_id": str(busy.session_id)}, status="running", attempts=1, max_attempts=3))
    fresh = await session_factory()
    await _set_session(fresh.session_id, status="completing", ended_at=datetime.now(UTC))

    summary = await recover_stuck_completing(stale_after_s=900, limit=10)
    assert summary.stale == 3 and summary.completed == 1 and summary.re_enqueued == 1 and summary.skipped_busy == 1
    assert await _status(with_report.session_id) == "completed"
    assert await _status(without_report.session_id) == "completing"  # the report job completes it
    recovery_jobs = [j for j in await _report_jobs(without_report.session_id) if j.payload.get("reason") == "recovery"]
    assert len(recovery_jobs) == 1 and recovery_jobs[0].max_attempts == 1
    assert await _status(fresh.session_id) == "completing" and not await _report_jobs(fresh.session_id)
