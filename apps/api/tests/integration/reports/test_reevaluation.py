"""Re-evaluating fallback evaluations (S12, audit B3): replaced in place, never scored, reports refreshed."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, select, update

import app.workers.interview  # noqa: F401  (registers answer.reevaluate)
import app.workers.reports  # noqa: F401
from app.ai.base import AIUnavailable
from app.core.lifecycle import drain_after_commit
from app.db.models import AnswerEvaluation, BackgroundJob, CompetencyEvidence, InterviewSession
from app.db.session import session_scope
from app.domain.enums import ModelTask, Role
from app.services.interview import reevaluation
from app.services.interview.adapters import get_scoring
from app.services.interview.evaluation import build_observations

pytestmark = pytest.mark.integration


async def _make_fallback(session_id: uuid.UUID) -> uuid.UUID:
    """What an LLM outage through every attempt leaves behind: a fallback row without evidence."""
    async with session_scope() as db:
        row = (
            await db.scalars(
                select(AnswerEvaluation).where(AnswerEvaluation.session_id == session_id).limit(1)
            )
        ).one()
        await db.execute(delete(CompetencyEvidence).where(CompetencyEvidence.evaluation_id == row.id))
        row.is_fallback = True
        return row.id


async def _evaluation(evaluation_id: uuid.UUID) -> AnswerEvaluation:
    async with session_scope() as db:
        row = await db.get(AnswerEvaluation, evaluation_id)
        assert row is not None
        return row


async def _observations(session_id: uuid.UUID) -> int:
    from app.schemas.rounds import load_round_config

    async with session_scope() as db:
        session = await db.get(InterviewSession, session_id)
        assert session is not None
        observations, _expected = await build_observations(
            db, session_id, load_round_config(session.round_config), get_scoring()
        )
        return len(observations)


async def test_fallback_answers_are_reevaluated_in_place_and_reports_refresh(
    auth_client, session_factory, mock_ai, worker_factory
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    before = await _observations(fx.session_id)
    fallback_id = await _make_fallback(fx.session_id)
    assert await _observations(fx.session_id) < before  # a fallback is unassessed, never evidence

    detail = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}")).json()
    assert detail["fallback_evaluations"] == 1
    response = await recruiter.post(f"/api/v1/sessions/{fx.session_id}/reevaluate")
    assert response.status_code == 202, response.text
    assert response.json() == {"queued": 1, "fallback_evaluations": 1}
    again = await recruiter.post(f"/api/v1/sessions/{fx.session_id}/reevaluate")
    assert again.json()["queued"] == 1  # the same job (deduplicated while queued)

    await worker_factory(job_types=["answer.reevaluate"]).drain(timeout=30)
    replaced = await _evaluation(fallback_id)
    assert replaced.is_fallback is False  # the same row, now a real evaluation
    async with session_scope() as db:
        evidence = int(
            await db.scalar(
                select(func.count())
                .select_from(CompetencyEvidence)
                .where(CompetencyEvidence.evaluation_id == fallback_id)
            )
            or 0
        )
        refresh = (
            await db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.type == "report.generate",
                    BackgroundJob.payload["reason"].astext == "reevaluation",
                )
            )
        ).all()
    assert evidence > 0 and len(refresh) == 1
    assert (await recruiter.get(f"/api/v1/sessions/{fx.session_id}")).json()["fallback_evaluations"] == 0


async def test_a_model_that_is_still_down_keeps_the_fallback(
    session_factory, mock_ai, worker_factory
) -> None:
    fx = await session_factory()
    fallback_id = await _make_fallback(fx.session_id)
    mock_ai.llm.set_default(ModelTask.ANSWER_EVALUATION, AIUnavailable("main LLM down", endpoint="main"))
    async with session_scope() as db:
        session = await db.get(InterviewSession, fx.session_id)
        assert await reevaluation.queue_reevaluation(db, session, actor="test") == 1
    await drain_after_commit()  # the job is dispatched once the transaction commits
    await worker_factory(job_types=["answer.reevaluate"]).drain(timeout=30)
    async with session_scope() as db:
        job = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "answer.reevaluate"))).one()
    assert job.status == "succeeded", (job.status, job.attempts, job.last_error)
    assert job.result["still_fallback"] is True and job.attempts == 6
    assert (await _evaluation(fallback_id)).is_fallback is True


async def test_reevaluation_limits_live_sessions_retry_cap_and_sweep(
    auth_client, session_factory, monkeypatch
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    live = await session_factory(recruiter.user.organization_id, status="live")
    refused = await recruiter.post(f"/api/v1/sessions/{live.session_id}/reevaluate")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "session_not_finished"

    fx = await session_factory(recruiter.user.organization_id)
    fallback_id = await _make_fallback(fx.session_id)
    answer_id = (await _evaluation(fallback_id)).answer_id

    from app.core import ai_health

    async def down(**_kw):
        return {"main": {"status": "error"}}

    async def healthy(**_kw):
        return {"main": {"status": "ok"}}

    monkeypatch.setattr(ai_health, "probe", down)
    assert (await reevaluation.sweep_fallbacks())["skipped"] == "main_unhealthy"
    monkeypatch.setattr(ai_health, "probe", healthy)
    assert (await reevaluation.sweep_fallbacks())["queued"] >= 1

    async with session_scope() as db:
        await db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.type == "answer.reevaluate")
            .values(status="succeeded", dedupe_key=None)
        )
        for _ in range(reevaluation.MAX_REEVALUATIONS_PER_ANSWER):
            db.add(
                BackgroundJob(
                    type="answer.reevaluate",
                    status="succeeded",
                    payload={"session_id": str(fx.session_id), "answer_id": str(answer_id)},
                )
            )
    async with session_scope() as db:
        session = await db.get(InterviewSession, fx.session_id)
        assert await reevaluation.queue_reevaluation(db, session, actor="test") == 0  # retry cap reached
