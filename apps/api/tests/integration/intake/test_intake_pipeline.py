"""Intake evaluation + report pipeline against the test database (mock AI; INTAKE_INTERVIEW.md 8-9, test plan T2)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.ai.base import AIOutputInvalid, AIUnavailable
from app.core.queue import subscribe_session_events
from app.db.models import BackgroundJob, MediaAsset, Report, ReviewDecision, Score
from app.db.models.intake import (
    IntakeCompetencyScore,
    IntakeEvaluation,
    IntakeEvaluatorRun,
)
from app.db.session import session_scope
from app.services.intake.pipeline import RetryEvaluation, evaluate_session, generate_intake_report
from app.services.intake.scoring import INTAKE_COMPETENCY_KEYS

from .eval_helpers import (
    calls_for,
    demo_outputs,
    install_resolver,
    make_demo_rows,
    session_status,
    set_transcript_status,
)

pytestmark = pytest.mark.integration


async def _evaluation(session_id: uuid.UUID) -> IntakeEvaluation:
    async with session_scope() as db:
        row = await db.scalar(
            select(IntakeEvaluation)
            .where(IntakeEvaluation.session_id == session_id)
            .order_by(IntakeEvaluation.version.desc())
            .limit(1)
        )
        assert row is not None
        return row


async def test_evaluate_then_report_end_to_end(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    counts = install_resolver(mock_ai, await demo_outputs(demo.session_id))

    result = await evaluate_session(demo.session_id, enqueue_followups=False)

    assert result.status == "ready" and not result.fallbacks
    assert result.band == "accept_with_notes"
    assert result.weighted_total == Decimal("4.130")
    # seven evaluators, each called exactly once (technical after resume_evidence)
    assert counts == {
        k: 1
        for k in (
            "communication",
            "resume_evidence",
            "relay",
            "structured",
            "trainability",
            "composure",
            "technical",
        )
    }
    evaluation = await _evaluation(demo.session_id)
    assert evaluation.version == 1 and evaluation.status == "ready"
    assert evaluation.evidence_sufficiency == "sufficient"
    assert evaluation.normalized_100 == Decimal("82.600")
    assert evaluation.scoring_version == "intake-scoring-1"
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(IntakeCompetencyScore).where(IntakeCompetencyScore.evaluation_id == evaluation.id)
            )
        ).all()
        scores = {r.competency_key: r for r in rows}
        assert sorted(scores) == sorted(INTAKE_COMPETENCY_KEYS)
        assert scores["technical_reading"].score == Decimal("4.4")
        assert scores["technical_reading"].sub_scores["drill_a"]["score"] == 5
        assert scores["technical_reading"].sub_scores["drill_b"]["score"] == 4
        assert scores["technical_reading"].sub_scores["formula"] == "0.4 × Drill A + 0.6 × Drill B"
        assert all(r.evidence for r in rows)
        runs = {
            r.evaluator: r
            for r in (
                await db.scalars(
                    select(IntakeEvaluatorRun).where(IntakeEvaluatorRun.evaluation_id == evaluation.id)
                )
            ).all()
        }
        assert {"reading_metrics", "communication", "technical", "relay"} <= set(runs)
        assert all(r.status == "succeeded" for r in runs.values())
        generic = (await db.scalars(select(Score).where(Score.session_id == demo.session_id))).all()
        overall = [s for s in generic if s.category == "overall"]
        assert len(overall) == 1 and overall[0].details["recommendation"] == "proceed"
        assert overall[0].score == pytest.approx(82.6)
        comp = {s.key: s for s in generic if s.category == "competency"}
        assert comp["communication"].score == 8.0 and comp["communication"].details["source"] == "intake"
    # review items: MQ1 enabled (info) + the unreviewed integrity observation; never an outcome
    kinds = {i["kind"]: i for i in evaluation.review_items}
    assert kinds["work_authorization_review"]["severity"] == "info"
    assert "integrity_observation" in kinds
    assert all(i["status"] == "open" for i in evaluation.review_items)

    events: list[dict] = []
    async with subscribe_session_events(demo.session_id) as sub:
        report = await generate_intake_report(demo.session_id, evaluation.id, use_model=True)
        for _ in range(10):
            event = await sub.get(timeout=0.5)
            if event is not None:
                events.append(event)
                if event["type"] == "report.ready":
                    break
    ready = [e for e in events if e["type"] == "report.ready"]
    assert ready and ready[0]["candidate_report_available"] is False and ready[0]["kind"] == "intake"
    assert report.completed is True
    assert await session_status(demo.session_id) == "completed"
    async with session_scope() as db:
        rows = (await db.scalars(select(Report).where(Report.session_id == demo.session_id))).all()
        assert [r.type for r in rows] == ["recruiter"]  # never a candidate report for intake
        row = rows[0]
        assert row.status == "ready" and row.content["kind"] == "intake"
        assert row.recommendation == "proceed" and row.overall_score == pytest.approx(82.6)
        assert row.confidence == pytest.approx(1.0)
        for section in ("A_candidate_summary", "B_overall", "C_competencies", "D_resume_verification", "E_technical",
                        "F_reading", "G_structured", "H_trainability", "I_integrity", "J_training_plan",
                        "K_human_review"):  # fmt: skip
            assert section in row.content
        assert row.content["B_overall"]["weighted_total"] == "4.1300"
        assert row.content["J_training_plan"]["modules"][0]["key"]
        assert "eligibility" not in row.content and row.content["eligibility_note"]
        artifact = await db.scalar(
            select(MediaAsset).where(MediaAsset.storage_key == row.artifact_storage_key)
        )
        assert artifact is not None and artifact.kind == "report_artifact"
        refreshed = await db.get(IntakeEvaluation, evaluation.id)
        assert refreshed is not None and refreshed.training_plan is not None


async def test_duplicate_delivery_is_a_noop(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    counts = install_resolver(mock_ai, await demo_outputs(demo.session_id))
    first = await evaluate_session(demo.session_id, job_id="job-a", enqueue_followups=False)
    again = await evaluate_session(demo.session_id, job_id="job-b", enqueue_followups=False)
    retry_same_job = await evaluate_session(demo.session_id, job_id="job-a", enqueue_followups=False)
    assert again.duplicate and again.evaluation_id == first.evaluation_id
    assert retry_same_job.duplicate and retry_same_job.evaluation_id == first.evaluation_id
    assert max(counts.values()) == 1
    async with session_scope() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(IntakeEvaluation)
            .where(IntakeEvaluation.session_id == demo.session_id)
        )
    assert total == 1


async def test_reevaluation_creates_a_new_version(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    install_resolver(mock_ai, await demo_outputs(demo.session_id))
    first = await evaluate_session(demo.session_id, enqueue_followups=False)
    second = await evaluate_session(demo.session_id, reason="reevaluate", enqueue_followups=False)
    assert (first.version, second.version) == (1, 2)
    async with session_scope() as db:
        sets = await db.scalar(
            select(func.count(func.distinct(Score.created_at))).where(Score.session_id == demo.session_id)
        )
    assert sets == 1  # the generic score set is replaced, never duplicated


async def test_retry_reuses_checkpoints(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    outputs = await demo_outputs(demo.session_id)
    failing = {"relay": AIUnavailable("model down")}
    counts = install_resolver(mock_ai, outputs, overrides=failing)
    with pytest.raises(RetryEvaluation):
        await evaluate_session(
            demo.session_id, job_id="job-1", is_last_attempt=False, enqueue_followups=False
        )
    evaluation = await _evaluation(demo.session_id)
    assert evaluation.status == "running"
    async with session_scope() as db:
        runs = {
            r.evaluator: r.status
            for r in (
                await db.scalars(
                    select(IntakeEvaluatorRun).where(IntakeEvaluatorRun.evaluation_id == evaluation.id)
                )
            ).all()
        }
    assert runs["relay"] == "failed" and runs["communication"] == "succeeded"
    first_counts = dict(counts)
    install_resolver(mock_ai, outputs)  # the model is back
    mock_ai.llm.calls.clear()
    result = await evaluate_session(
        demo.session_id, job_id="job-1", is_last_attempt=True, enqueue_followups=False
    )
    assert result.status == "ready" and result.evaluation_id == evaluation.id
    assert {
        evaluator for evaluator in ("communication", "relay", "technical") if calls_for(mock_ai, evaluator)
    } == {"relay"}
    assert first_counts["relay"] >= 1


async def test_last_attempt_falls_back(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    install_resolver(
        mock_ai, await demo_outputs(demo.session_id), overrides={"composure": "this is not json at all"}
    )
    result = await evaluate_session(demo.session_id, is_last_attempt=True, enqueue_followups=False)
    assert result.status == "ready" and result.fallbacks == ["composure"]
    assert result.band == "insufficient_evidence"
    evaluation = await _evaluation(demo.session_id)
    assert evaluation.weighted_total is None and evaluation.partial_total is not None
    assert evaluation.evidence_sufficiency == "partial"
    assert any(i["kind"] == "evaluator_fallback" for i in evaluation.review_items)
    async with session_scope() as db:
        composure = await db.scalar(
            select(IntakeCompetencyScore).where(
                IntakeCompetencyScore.evaluation_id == evaluation.id,
                IntakeCompetencyScore.competency_key == "composure",
            )
        )
        generic = list((await db.scalars(select(Score).where(Score.session_id == demo.session_id))).all())
    assert composure is not None and composure.status == "insufficient" and composure.score is None
    # Without a band the overall row has no score (never a made-up number) and says why; the unscored
    # competency is stored as NULL, the scored ones keep their numbers.
    overall = [r for r in generic if r.category == "overall"]
    assert len(overall) == 1 and overall[0].score is None and overall[0].sufficient_evidence is False
    assert overall[0].details["recommendation"] == "insufficient_evidence"
    assert overall[0].details["insufficient_reason"] == "partial"
    comps = {r.key: r for r in generic if r.category == "competency"}
    assert len(comps) == 7
    assert comps["composure"].score is None and comps["composure"].sufficient_evidence is False
    assert all(r.score is not None for k, r in comps.items() if k != "composure")


async def test_invalid_output_before_last_attempt_retries(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    install_resolver(
        mock_ai, await demo_outputs(demo.session_id), overrides={"technical": AIOutputInvalid("bad")}
    )
    with pytest.raises(RetryEvaluation):
        await evaluate_session(
            demo.session_id, job_id="job-x", is_last_attempt=False, enqueue_followups=False
        )


async def test_transcript_wait_defers_then_proceeds(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    install_resolver(mock_ai, await demo_outputs(demo.session_id))
    await set_transcript_status(demo.session_id, "read_relay", "pending_worker")
    deferred = await evaluate_session(demo.session_id, deferrals=0)
    assert deferred.deferred and deferred.deferrals == 1
    async with session_scope() as db:
        job = await db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.dedupe_key == f"intake.evaluate:{demo.session_id}:defer:1"
            )
        )
        evaluations = await db.scalar(
            select(func.count())
            .select_from(IntakeEvaluation)
            .where(IntakeEvaluation.session_id == demo.session_id)
        )
    assert job is not None and job.payload["deferrals"] == 1
    assert evaluations == 0  # nothing reserved while transcripts settle
    exhausted = await evaluate_session(demo.session_id, deferrals=12, enqueue_followups=False)
    assert exhausted.status == "ready"
    evaluation = await _evaluation(demo.session_id)
    async with session_scope() as db:
        run = await db.scalar(
            select(IntakeEvaluatorRun).where(
                IntakeEvaluatorRun.evaluation_id == evaluation.id,
                IntakeEvaluatorRun.evaluator == "reading_metrics",
            )
        )
    assert run is not None and run.output["transcript_wait_exhausted"] is True


async def test_incomplete_interview_is_insufficient_evidence(org_factory, redis, storage, mock_ai) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org, variant="incomplete")
    install_resolver(mock_ai, await demo_outputs(demo.session_id))
    result = await evaluate_session(demo.session_id, enqueue_followups=False)
    assert result.band == "insufficient_evidence"
    evaluation = await _evaluation(demo.session_id)
    assert evaluation.partial_total is not None and evaluation.weighted_total is None
    kinds = {i["kind"] for i in evaluation.review_items}
    assert {"stage_incomplete", "mandatory_item_missing"} <= kinds
    # no model call for evaluators without any answers in scope (structured: the topic stage was not reached)
    assert not calls_for(mock_ai, "structured")


async def test_non_intake_or_live_session_is_rejected(org_factory, redis, storage, mock_ai) -> None:
    from app.core.queue import PermanentJobError
    from app.db.models import InterviewSession

    org = await org_factory()
    demo = await make_demo_rows(org)
    async with session_scope() as db:
        session = await db.get(InterviewSession, demo.session_id)
        assert session is not None
        session.status = "live"
    with pytest.raises(PermanentJobError):
        await evaluate_session(demo.session_id, enqueue_followups=False)
    async with session_scope() as db:
        session = await db.get(InterviewSession, demo.session_id)
        assert session is not None
        session.status = "completing"
        session.round_config = {**session.round_config, "round_type": "behavioral"}
    with pytest.raises(PermanentJobError):
        await evaluate_session(demo.session_id, enqueue_followups=False)
    async with session_scope() as db:
        assert await db.scalar(select(func.count()).select_from(ReviewDecision)) == 0


SCHEMA_EVALUATORS = {
    "CommunicationOutput": "communication",
    "ResumeEvidenceOutput": "resume_evidence",
    "TechnicalOutput": "technical",
    "RelayOutput": "relay",
    "StructuredOutput": "structured",
    "TrainabilityOutput": "trainability",
    "ComposureOutput": "composure",
}
TRAINING_DRAFT = {
    "summary": "Three weeks focused on modelling fundamentals and structured explanations.",
    "modules": [
        {
            "key": "ai_ml_fundamentals",
            "why": "Builds on the pipeline experience described in the interview.",
            "objectives": ["Train a baseline model", "Explain evaluation metrics"],
            "milestones": [
                {"week": 1, "milestone": "Core lessons done", "measure": "quiz"},
                {"week": 2, "milestone": "Baseline model trained", "measure": "notebook review"},
                {"week": 3, "milestone": "Improved model explained", "measure": "mentor walkthrough"},
            ],
        }
    ],
}


async def test_model_call_payloads_never_carry_eligibility_or_the_name(org_factory, redis, storage) -> None:
    """Real LLM client + ledger with MODEL_CALL_LOG_PAYLOADS semantics on the throwaway DB (HTTP mocked)."""
    import json

    import httpx

    from app.ai.clients.llm import OpenAICompatibleLLM
    from app.ai.http import EndpointClient
    from app.ai.ledger import ModelCallLedger
    from app.ai.mock import MockAIServices
    from app.ai.registry import set_ai
    from app.ai.router import ModelRouter
    from app.db.models import ModelCall

    org = await org_factory()
    demo = await make_demo_rows(org, with_eligibility=True)
    outputs = await demo_outputs(demo.session_id)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        name = body["response_format"]["json_schema"]["name"]
        content = TRAINING_DRAFT if name == "TrainingPlanDraft" else outputs[SCHEMA_EVALUATORS[name]]
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(content)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    endpoints = {
        name: EndpointClient(
            name, f"http://{name}.test/v1", timeout=10, max_concurrency=4, max_retries=0, transport=transport
        )
        for name in ("main", "vision")
    }
    ledger = ModelCallLedger(log_payloads=True)
    services = MockAIServices()
    services.llm = OpenAICompatibleLLM(
        endpoints=endpoints,
        models={"main": "test-model", "vision": "test-vision"},
        router=ModelRouter(),
        ledger=ledger,
    )
    previous = set_ai(services)
    try:
        result = await evaluate_session(demo.session_id, enqueue_followups=False)
        report = await generate_intake_report(demo.session_id, result.evaluation_id, use_model=True)
    finally:
        set_ai(previous)
        await ledger.aclose()
        for client in endpoints.values():
            await client.aclose()
    assert result.status == "ready" and not result.fallbacks and report.training_plan_source == "model"
    async with session_scope() as db:
        rows = (await db.scalars(select(ModelCall).where(ModelCall.session_id == demo.session_id))).all()
    assert len(rows) == 8 == len(seen)  # seven evaluators + the training plan
    assert {r.prompt_version.split("@")[0] for r in rows} == {
        *(f"intake-eval-{k}" for k in SCHEMA_EVALUATORS.values()),
        "intake-training",
    }
    forbidden = (
        "legally authorised",
        "legally authorized",
        "sponsorship",
        "United States",
        "needs_sponsorship",
        '"authorized"',
        "eligibility",
        "Alex",
        "Rivera",
    )
    for row in rows:
        assert row.request_payload is not None
        text = json.dumps(row.request_payload, ensure_ascii=False)
        for marker in forbidden:
            assert marker not in text, (row.prompt_version, marker)
    for body in seen:  # the exact bytes that left the process
        text = json.dumps(body, ensure_ascii=False)
        assert not any(marker in text for marker in forbidden)


async def test_jobs_run_through_the_queue(org_factory, redis, storage, mock_ai, worker_factory) -> None:
    import app.workers.intake  # noqa: F401 - registers the intake job handlers
    from app.core.queue import enqueue

    org = await org_factory()
    demo = await make_demo_rows(org)
    install_resolver(mock_ai, await demo_outputs(demo.session_id))
    await enqueue(
        "intake.evaluate",
        {"session_id": str(demo.session_id), "reason": "session_completed"},
        dedupe_key=f"intake.evaluate:{demo.session_id}:initial",
    )
    worker = worker_factory(job_types=["intake.evaluate", "intake.report"])
    await worker.drain(timeout=30.0)
    assert await session_status(demo.session_id) == "completed"
    async with session_scope() as db:
        jobs = {j.type: j for j in (await db.scalars(select(BackgroundJob))).all()}
        report = await db.scalar(select(Report).where(Report.session_id == demo.session_id))
    assert jobs["intake.evaluate"].status == "succeeded" and jobs["intake.report"].status == "succeeded"
    assert "open_link.round_finished" in jobs  # open-link progression is handed over on the first delivery
    assert report is not None and report.status == "ready" and report.content["kind"] == "intake"


async def test_superseded_evaluations_never_overwrite_newer_results(
    org_factory, redis, storage, mock_ai
) -> None:
    org = await org_factory()
    demo = await make_demo_rows(org)
    install_resolver(mock_ai, await demo_outputs(demo.session_id))
    first = await evaluate_session(demo.session_id, job_id="job-v1", enqueue_followups=False)
    second = await evaluate_session(
        demo.session_id, reason="reevaluate", job_id="job-v2", enqueue_followups=False
    )
    # v1 is redelivered late (e.g. a reclaimed job): it must not replace v2's generic score set
    async with session_scope() as db:
        row = await db.get(IntakeEvaluation, first.evaluation_id)
        assert row is not None
        row.status = "running"
    again = await evaluate_session(demo.session_id, job_id="job-v1", enqueue_followups=False)
    assert again.evaluation_id == first.evaluation_id and again.status == "ready"
    async with session_scope() as db:
        sources = {
            s.details["evaluation_id"]
            for s in (await db.scalars(select(Score).where(Score.session_id == demo.session_id))).all()
        }
    assert sources == {str(second.evaluation_id)}
    stale = await generate_intake_report(demo.session_id, first.evaluation_id, use_model=False)
    assert stale.superseded and stale.report_id is None
    current = await generate_intake_report(demo.session_id, second.evaluation_id, use_model=False)
    assert current.report_id is not None and not current.superseded
    async with session_scope() as db:
        reports = (await db.scalars(select(Report).where(Report.session_id == demo.session_id))).all()
    assert len(reports) == 1 and reports[0].content["evaluation_version"] == 2
