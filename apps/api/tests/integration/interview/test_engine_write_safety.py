"""G1: engine write safety. Terminal and administrative transitions never run unlocked, and a
version conflict re-applies the transition to the other writer's state instead of overwriting it."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from app.db.models import InterviewSession
from app.db.session import session_scope
from app.domain.enums import SessionEndReason, SessionStatus
from app.services.interview import engine as engine_module
from app.services.interview.engine import EngineLockTimeout

from .harness import collect
from .test_engine_interviews import Driver, _setup

pytestmark = pytest.mark.integration


async def _concurrent_write(engine: Any, sid: str, marker: str) -> None:
    """What a turn handler in another process does between our load and our save."""
    fresh = await engine.store.load(sid)
    assert fresh is not None
    fresh.memory_summary = marker
    await engine.store.save(fresh)


async def test_strict_lock_timeout_raises_only_for_terminal_transitions(interview_env) -> None:
    _, sid, engine = await _setup(interview_env, prepare=False, lock_timeout_s=0.05)
    lock = engine._locks.setdefault(sid, asyncio.Lock())
    await lock.acquire()  # a wedged handler holds the session lock
    try:
        with pytest.raises(EngineLockTimeout) as excinfo:
            await engine.pause(sid)
        assert excinfo.value.session_id == sid
        with pytest.raises(EngineLockTimeout):
            await collect(engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED))
        with pytest.raises(EngineLockTimeout):
            await engine.advance_clock(sid, 10)
        # Turn-level handlers keep the historical behaviour: proceed (loudly) rather than block.
        await engine.mark_utterance_delivered(sid, "no-such-utterance", spoken_text="")
    finally:
        lock.release()
    # Nothing changed: the session is still where it was.
    assert (await engine.store.load(sid)).status is SessionStatus.CREATED


async def test_end_session_reapplies_on_a_concurrent_write(interview_env, monkeypatch) -> None:
    _, sid, engine = await _setup(interview_env)
    driver = Driver(interview_env, engine, sid)
    await driver.start()
    assert (await driver.state()).status is SessionStatus.LIVE

    async def closing_text_with_race(
        state: Any, reason: Any, last_answer: Any, generated: bool
    ) -> tuple[str, str]:
        await _concurrent_write(engine, sid, "saved by the other writer")
        return "Thank you for your time today.", "template"

    monkeypatch.setattr(engine, "_closing_text", closing_text_with_race)
    events = await collect(engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED))
    assert [type(e).__name__ for e in events][-1] == "SessionClosing"

    final = await engine.store.load(sid)
    assert final.status is SessionStatus.COMPLETING and final.end_reason is SessionEndReason.CANDIDATE_ENDED
    assert final.memory_summary == "saved by the other writer"  # the other writer's state survived
    assert final.closing_utterance_id and final.utterances[-1].kind == "closing"
    async with session_scope() as db:
        row = await db.get(InterviewSession, uuid.UUID(sid))
        assert row.status == "completing" and row.end_reason == "candidate_ended" and row.ended_at is not None


async def test_end_session_skips_when_the_other_writer_already_ended_it(interview_env, monkeypatch) -> None:
    _, sid, engine = await _setup(interview_env)
    driver = Driver(interview_env, engine, sid)
    await driver.start()

    async def closing_text_with_race(
        state: Any, reason: Any, last_answer: Any, generated: bool
    ) -> tuple[str, str]:
        fresh = await engine.store.load(sid)
        fresh.status = SessionStatus.ABORTED
        fresh.end_reason = SessionEndReason.CANCELLED
        await engine.store.save(fresh)
        return "Goodbye.", "template"

    monkeypatch.setattr(engine, "_closing_text", closing_text_with_race)
    await collect(engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED))
    final = await engine.store.load(sid)
    assert final.status is SessionStatus.ABORTED and final.end_reason is SessionEndReason.CANCELLED


async def test_pause_reapplies_on_a_concurrent_write(interview_env, monkeypatch) -> None:
    _, sid, engine = await _setup(interview_env)
    driver = Driver(interview_env, engine, sid)
    await driver.start()
    real_log_event = engine_module.log_event

    async def log_event_with_race(
        db: Any, session_id: Any, event_type: str, *args: Any, **kwargs: Any
    ) -> Any:
        if event_type == "session.paused":
            await _concurrent_write(engine, sid, "turn saved during the pause")
        return await real_log_event(db, session_id, event_type, *args, **kwargs)

    monkeypatch.setattr(engine_module, "log_event", log_event_with_race)
    await engine.pause(sid)
    final = await engine.store.load(sid)
    assert final.status is SessionStatus.PAUSED and final.memory_summary == "turn saved during the pause"
