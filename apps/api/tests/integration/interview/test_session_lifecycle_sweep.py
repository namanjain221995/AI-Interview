"""S1: abandoned sessions are ended offline (scoring + partial report), never left paused forever."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import BackgroundJob, InterviewEvent, InterviewSession
from app.db.session import session_scope
from app.domain.enums import SessionEndReason, SessionStatus
from app.realtime.lock import SessionLock
from app.services.interview.lifecycle import end_session_offline, sweep_abandoned_sessions
from app.services.open_links.constants import REVIEW_END_REASONS

from .test_engine_interviews import Driver, _setup

pytestmark = pytest.mark.integration


async def _paused_session(env, *, idle_minutes: int = 0):
    _, sid, engine = await _setup(env)
    driver = Driver(env, engine, sid)
    await driver.start()
    await engine.pause(sid)
    assert (await engine.store.load(sid)).status is SessionStatus.PAUSED
    if idle_minutes:
        stamp = datetime.now(UTC) - timedelta(minutes=idle_minutes)
        async with session_scope() as db:
            await db.execute(
                update(InterviewSession)
                .where(InterviewSession.id == uuid.UUID(sid))
                .values(updated_at=stamp, last_heartbeat_at=stamp, started_at=stamp)
            )
    return sid, engine


async def _jobs(sid: str) -> set[str]:
    async with session_scope() as db:
        rows = (await db.scalars(select(BackgroundJob).where(BackgroundJob.payload["session_id"].astext == sid))).all()
    return {row.type for row in rows}


async def test_offline_end_of_an_abandoned_paused_session(interview_env, redis) -> None:
    sid, engine = await _paused_session(interview_env, idle_minutes=30)
    outcome = await end_session_offline(
        sid, SessionEndReason.DISCONNECT_TIMEOUT, actor="sweeper", require_idle_s=1200, engine=engine, redis=redis
    )
    assert outcome.action == "ended" and outcome.status_before == "paused"
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert row.status == "completing" and row.end_reason == "disconnect_timeout" and row.ended_at is not None
        events = (await db.scalars(select(InterviewEvent).where(InterviewEvent.session_id == uuid.UUID(sid)))).all()
        offline = [e for e in events if e.type == "session.ended_offline"]
        assert len(offline) == 1 and offline[0].payload["actor"] == "sweeper"
    assert {"scoring.finalize", "report.generate"} <= await _jobs(sid)  # the partial report is produced
    again = await end_session_offline(sid, SessionEndReason.DISCONNECT_TIMEOUT, actor="sweeper", engine=engine, redis=redis)
    assert again.action == "skipped" and again.reason == "already_ended"  # idempotent


async def test_offline_end_skips_active_locked_and_dry_run(interview_env, redis) -> None:
    sid, engine = await _paused_session(interview_env)
    active = await end_session_offline(
        sid, SessionEndReason.DISCONNECT_TIMEOUT, actor="sweeper", require_idle_s=600, engine=engine, redis=redis
    )
    assert active.action == "skipped" and active.reason == "active"

    runtime_lock = SessionLock(sid, redis, instance_id="api-elsewhere")
    assert await runtime_lock.acquire()
    try:
        locked = await end_session_offline(sid, SessionEndReason.DISCONNECT_TIMEOUT, actor="sweeper", engine=engine, redis=redis)
        assert locked.action == "skipped" and locked.reason == "locked" and locked.detail.startswith("api-elsewhere:")
    finally:
        await runtime_lock.release()

    dry = await end_session_offline(
        sid, SessionEndReason.DISCONNECT_TIMEOUT, actor="sweeper", dry_run=True, engine=engine, redis=redis
    )
    assert dry.action == "dry_run"
    assert (await engine.store.load(sid)).status is SessionStatus.PAUSED  # nothing changed
    assert "scoring.finalize" not in await _jobs(sid)


async def test_sweep_ends_only_idle_started_sessions_within_the_batch(interview_env, redis) -> None:
    old_a, engine = await _paused_session(interview_env, idle_minutes=40)
    old_b, _ = await _paused_session(interview_env, idle_minutes=35)
    fresh, _ = await _paused_session(interview_env)  # paused seconds ago: not abandoned
    _, never_started, _ = await _setup(interview_env, prepare=False)  # created, never started

    summary = await sweep_abandoned_sessions(abandon_after_s=1200, limit=1, dry_run=False, engine=engine, redis=redis)
    assert summary.candidates == 2 and summary.ended == 1 and summary.pending == 1
    statuses = {}
    async with session_scope() as db:
        for sid in (old_a, old_b, fresh, never_started):
            statuses[sid] = (await db.get(InterviewSession, uuid.UUID(sid))).status
    assert statuses[old_a] == "completing"  # oldest first
    assert statuses[old_b] == "paused" and statuses[fresh] == "paused" and statuses[never_started] == "created"

    second = await sweep_abandoned_sessions(abandon_after_s=1200, limit=5, dry_run=False, engine=engine, redis=redis)
    assert second.ended == 1 and second.pending == 0


async def test_cancel_reason_aborts_without_scoring(interview_env, redis) -> None:
    sid, engine = await _paused_session(interview_env)
    outcome = await end_session_offline(sid, SessionEndReason.CANCELLED, actor="staff_cancel", engine=engine, redis=redis)
    assert outcome.action == "ended"
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert row.status == "aborted" and row.end_reason == "cancelled"
    assert "scoring.finalize" not in await _jobs(sid)


def test_disconnect_timeout_holds_open_link_progression_for_review() -> None:
    assert "disconnect_timeout" in REVIEW_END_REASONS
