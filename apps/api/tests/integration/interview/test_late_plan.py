"""Tier 1: a resume-aware plan that finishes after the candidate began is used for the rest of the interview
(it takes 20-45 s; most sessions used to start on the deterministic plan and discard the model plan)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import InterviewEvent
from app.db.session import session_scope
from app.domain.enums import SessionStatus
from app.services.interview import engine as engine_module
from app.services.interview.planner import fallback_plan

from .test_engine_interviews import Driver, _setup

pytestmark = pytest.mark.integration


async def test_a_plan_that_finishes_after_the_start_is_adopted(interview_env, monkeypatch) -> None:
    release = asyncio.Event()

    async def slow_model_plan(state, *, ai, timeout_s):  # noqa: ANN001, ANN202 - the planner's signature
        await release.wait()
        return fallback_plan(state).model_copy(
            update={"opening_focus": "Model plan: the EKS migration"}
        ), "model"

    monkeypatch.setattr(engine_module, "generate_plan", slow_model_plan)
    _, sid, engine = await _setup(interview_env, prepare=False)
    preparing = asyncio.create_task(engine.prepare_session(sid))
    await asyncio.sleep(0.05)  # session.prepare is under way
    driver = Driver(interview_env, engine, sid)
    await driver.start()  # the candidate begins before the plan is ready: deterministic plan
    before = await driver.state()
    assert before.status is SessionStatus.LIVE and before.plan_source == "fallback"
    release.set()
    await preparing  # the model plan arrives late
    after = await driver.state()
    assert after.status is SessionStatus.LIVE  # the interview goes on
    assert after.plan_source == "model_late" and after.plan is not None
    assert after.plan.opening_focus.startswith("Model plan")
    async with session_scope() as db:
        events = (await db.scalars(select(InterviewEvent).where(InterviewEvent.session_id == sid))).all()
    assert "session.plan_adopted" in [e.type for e in events]
    await driver.answer(
        "We moved forty services to EKS and I owned the rollout plan and the canary analysis."
    )
    assert (await driver.state()).plan_source == "model_late"  # turns keep running on it
