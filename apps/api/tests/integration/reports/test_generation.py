"""report.generate pipeline against Postgres + local storage, with MockAIServices."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.ai.base import AIUnavailable
from app.core.queue import enqueue, subscribe_session_events
from app.db.models import (
    AuditLog,
    BackgroundJob,
    CodeRun,
    CompetencyEvidence,
    InterviewSession,
    MediaAsset,
    Report,
)
from app.db.session import session_scope
from app.domain.enums import ModelTask, Recommendation
from app.schemas.ai import (
    CandidateReportNarrative,
    CompetencyFinding,
    EvidenceBackedPoint,
    ImprovementArea,
    QuestionHighlight,
    RecruiterReportNarrative,
    StudyArea,
)
from app.services.reports import build_demo_report, generate_reports
from app.services.storage import get_storage

pytestmark = pytest.mark.integration

FORBIDDEN_CANDIDATE_KEYS = {
    "integrity",
    "recommendation",
    "claims",
    "claims_summary",
    "contradictions",
    "interviewer_notes",
    "areas_for_human_followup",
    "risks",
    "recommendation_rationale",
    "suggested_recommendation",
    "reason",
    "unsupported_claims",
    "paste_event_count",
    "final_code",
    "passing_guidance",
}


def _keys(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            out |= _keys(v)
    elif isinstance(value, list):
        for item in value:
            out |= _keys(item)
    return out


def candidate_narrative() -> CandidateReportNarrative:
    return CandidateReportNarrative(
        overall_summary=(
            "You gave specific, well-quantified answers about running Kubernetes at scale. "
            "Integrity monitoring flagged nothing worth mentioning. "
            "Terraform change safety is the main area to strengthen."
        ),
        technical_strengths=[
            EvidenceBackedPoint(
                title="Kubernetes at scale",
                detail="You described cluster size and autoscaling precisely.",
                evidence_ids=["E1", "E2", "E999", "not-an-id"],
                competency_key="kubernetes",
            )
        ],
        areas_to_improve=[
            ImprovementArea(
                title="Terraform change safety",
                detail="Plans ran in CI without policy checks.",
                learning_guidance="Study policy-as-code (OPA/Sentinel) and practise explaining guardrails.",
                evidence_ids=["E12"],
                competency_key="made_up_competency",
            )
        ],
        strong_answers=[
            QuestionHighlight(question_id="Q1", note="Clear scale numbers."),
            QuestionHighlight(question_id="Q99", note="x"),
        ],
        incomplete_answers=[QuestionHighlight(question_id="Q6", note="Missing guardrails.")],
        recommended_study_areas=[
            StudyArea(topic="Policy as code", why="Safer changes.", next_steps=["Write an OPA policy."])
        ],
        next_steps=["Practise quantifying impact.", "Our recommendation is to hire you."],
    )


def recruiter_narrative() -> RecruiterReportNarrative:
    return RecruiterReportNarrative(
        executive_summary="Strong Kubernetes and CI/CD evidence; Terraform guardrails are a gap.",
        competency_findings=[
            CompetencyFinding(
                competency_key="kubernetes", summary="Strong.", evidence_ids=["E1", "E3", "E404"]
            ),
            CompetencyFinding(
                competency_key="unknown_key", summary="Should be dropped.", evidence_ids=["E5"]
            ),
        ],
        claims_summary="Scale claim verified; cost claim not yet verified.",
        contradictions_summary="One open medium inconsistency about SCP ownership.",
        coding_summary="Solved the log-parsing exercise with one hint.",
        areas_for_human_followup=["Probe Terraform guardrails"],
        recommendation_rationale="Evidence is strong but an integrity signal awaits review.",
        suggested_recommendation=Recommendation.HUMAN_REVIEW,
        risks=["Terraform change safety"],
    )


def script_narratives(mock_ai: Any) -> None:
    def respond(call: Any) -> Any:
        return (
            candidate_narrative()
            if issubclass(call.schema, CandidateReportNarrative)
            else recruiter_narrative()
        )

    mock_ai.llm.set_default(ModelTask.REPORT_GENERATION, respond)


async def _reports(session_id: uuid.UUID) -> list[Report]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(
                    select(Report)
                    .where(Report.session_id == session_id)
                    .order_by(Report.type, Report.version)
                )
            ).all()
        )


async def test_generate_reports_end_to_end(session_factory, mock_ai, storage, redis) -> None:
    script_narratives(mock_ai)
    fx = await session_factory(candidate_report_mode="immediate")
    received: list[dict[str, Any]] = []

    async def listen() -> None:
        async with subscribe_session_events(fx.session_id) as sub:
            ready.set()
            while True:
                event = await sub.get(timeout=0.5)
                if event is not None:
                    received.append(event)
                    if event["type"] == "report.ready":
                        return

    ready = asyncio.Event()
    listener = asyncio.create_task(listen())
    await ready.wait()
    result = await generate_reports(fx.session_id, job_id="job-1")
    await asyncio.wait_for(listener, 5)

    assert result.versions == {"candidate": 1, "recruiter": 1}
    assert result.candidate_released is True
    assert result.narrative_source == {"candidate": "model", "recruiter": "model"}
    assert received[-1]["type"] == "report.ready" and received[-1]["candidate_released"] is True
    assert len(mock_ai.llm.calls_for(ModelTask.REPORT_GENERATION)) == 2
    for call in mock_ai.llm.calls_for(ModelTask.REPORT_GENERATION):
        system = call.messages[0]["content"]
        assert "Fairness and evidence rules" in system
        assert "<interview_record>" in call.messages[1]["content"]
    candidate_prompt = next(
        c
        for c in mock_ai.llm.calls_for(ModelTask.REPORT_GENERATION)
        if issubclass(c.schema, CandidateReportNarrative)
    )
    prompt_text = json.dumps(candidate_prompt.messages)
    assert "possible_phone_visible" not in prompt_text and "human_review" not in prompt_text

    rows = await _reports(fx.session_id)
    by_type = {r.type: r for r in rows}
    assert set(by_type) == {"candidate", "recruiter"}
    cand, rec = by_type["candidate"], by_type["recruiter"]
    assert cand.status == rec.status == "ready"
    assert cand.released_to_candidate and cand.released_at is not None and cand.released_by_id is None
    assert rec.recommendation == "human_review" and cand.recommendation is None
    assert cand.overall_score == rec.overall_score and rec.confidence is not None

    # Evidence ids: aliases resolved to real ids of this session; invented ones dropped.
    evidence_ids = {str(e) for e in fx.evidence_ids}
    strengths = cand.content["narrative"]["technical_strengths"][0]
    assert len(strengths["evidence_ids"]) == 2 and set(strengths["evidence_ids"]) <= evidence_ids
    area = cand.content["narrative"]["areas_to_improve"][0]
    assert area["competency_key"] is None  # unknown competency cleared
    highlights = cand.content["narrative"]["strong_answers"]
    assert [h["question_id"] for h in highlights] == [str(fx.question_ids[0])]
    # Scrubbed: integrity / hiring-outcome sentences never reach the candidate.
    assert "Integrity" not in cand.content["narrative"]["overall_summary"]
    assert all("recommendation" not in s.lower() for s in cand.content["narrative"]["next_steps"])
    findings = rec.content["narrative"]["competency_findings"]
    assert [f["competency_key"] for f in findings] == ["kubernetes"]
    assert len(findings[0]["evidence_ids"]) == 2
    assert rec.content["narrative_validation"]["dropped_findings"] == 1

    # Candidate separation: no integrity, recommendation, claims, recruiter notes.
    leaked = _keys(cand.content) & FORBIDDEN_CANDIDATE_KEYS
    assert not leaked, leaked
    raw = json.dumps(cand.content)
    for needle in (
        "possible_phone_visible",
        "human_review",
        "tab_hidden",
        "SCP ownership",
        "Cut cloud cost",
        "secret",
    ):
        assert needle not in raw, needle
    assert cand.content["overall_score"] == rec.content["scores"]["overall"]["score"]
    referenced = {e["id"] for e in cand.content["evidence"]}
    assert referenced == set(strengths["evidence_ids"]) | set(area["evidence_ids"])

    # Recruiter content is complete.
    assert (
        rec.content["integrity"]["unreviewed_flags"] == 1
        and rec.content["integrity"]["flagged_for_review"] == 1
    )
    assert rec.content["integrity"]["by_type"]["tab_hidden"]["occurrences"] == 2
    assert [e["flagged"] for e in rec.content["integrity"]["events"]] == [False, True]
    assert rec.content["integrity"]["note"].startswith("Integrity signals are automated observations")
    assert rec.content["recommendation"]["value"] == "human_review"
    assert rec.content["claims_summary"] == {"verified": 1, "unverified": 1}
    assert len(rec.content["questions"]) == 15
    assert rec.content["questions"][1]["parent_question_id"] == str(fx.question_ids[0])
    assert rec.content["questions"][1]["followup_depth"] == 1
    assert rec.content["coding"][0]["hints_used"] == 1
    assert all("args" not in run for run in rec.content["coding"][0]["runs"])  # no hidden test data
    assert rec.content["timeline"]["windows"][0]["label"] == "Minute 0-10"

    # HTML artifacts stored through storage + media_assets (report_artifact).
    async with session_scope() as db:
        assets = {
            a.id: a
            for a in (
                await db.scalars(
                    select(MediaAsset).where(
                        MediaAsset.session_id == fx.session_id, MediaAsset.kind == "report_artifact"
                    )
                )
            ).all()
        }
        audit_rows = (await db.scalars(select(AuditLog).where(AuditLog.action == "report.generate"))).all()
    assert len(assets) == 2 and audit_rows
    assert {a.storage_key for a in assets.values()} == {cand.artifact_storage_key, rec.artifact_storage_key}
    assert rec.artifact_storage_key.endswith("/reports/recruiter-v1.html")
    html = (await get_storage().read(cand.artifact_storage_key)).decode()
    assert "<!doctype html>" in html.lower() and "Content-Security-Policy" in html
    assert "@media print" in html and "http://" not in html and "https://" not in html
    assert "integrity" not in html.lower()
    rec_html = (await get_storage().read(rec.artifact_storage_key)).decode()
    assert "Jane Doe" in rec_html and "Assistive recommendation" in rec_html
    assert "not determinations" in rec_html and "Signals flagged for human review" in rec_html


async def test_regeneration_creates_new_versions(session_factory, mock_ai, storage, redis) -> None:
    script_narratives(mock_ai)
    fx = await session_factory(candidate_report_mode="after_review")
    first = await generate_reports(fx.session_id, job_id="a")
    second = await generate_reports(fx.session_id, job_id="b", reason="regenerate")
    assert first.versions == {"candidate": 1, "recruiter": 1}
    assert second.versions == {"candidate": 2, "recruiter": 2}
    assert first.candidate_released is False and second.candidate_released is False
    rows = await _reports(fx.session_id)
    assert [(r.type, r.version) for r in rows] == [
        ("candidate", 1),
        ("candidate", 2),
        ("recruiter", 1),
        ("recruiter", 2),
    ]
    assert len({r.artifact_storage_key for r in rows}) == 4
    assert all(not r.released_to_candidate for r in rows)


async def test_model_outage_retries_then_falls_back(session_factory, mock_ai, storage, redis) -> None:
    mock_ai.llm.set_default(ModelTask.REPORT_GENERATION, AIUnavailable("main down", endpoint="main"))
    fx = await session_factory()
    with pytest.raises(AIUnavailable):
        await generate_reports(fx.session_id, job_id="job-x", allow_fallback=False)
    rows = await _reports(fx.session_id)
    assert {r.status for r in rows} == {"generating"} and {r.version for r in rows} == {1}
    # The retry of the same job reuses the reserved rows; the last attempt falls back.
    result = await generate_reports(fx.session_id, job_id="job-x", allow_fallback=True)
    assert result.versions == {"candidate": 1, "recruiter": 1}
    assert result.narrative_source == {"candidate": "fallback", "recruiter": "fallback"}
    rows = await _reports(fx.session_id)
    assert len(rows) == 2 and {r.status for r in rows} == {"ready"}
    rec = next(r for r in rows if r.type == "recruiter")
    narrative = rec.content["narrative"]
    assert narrative["suggested_recommendation"] == rec.recommendation
    assert {f["competency_key"] for f in narrative["competency_findings"]} >= {"kubernetes", "terraform"}
    assert any("terraform" in a.lower() for a in narrative["areas_for_human_followup"])
    cand = next(r for r in rows if r.type == "candidate")
    assert cand.content["narrative"]["technical_strengths"]
    assert not (_keys(cand.content) & FORBIDDEN_CANDIDATE_KEYS)


async def test_invalid_model_output_falls_back_without_retry(
    session_factory, mock_ai, storage, redis
) -> None:
    mock_ai.llm.set_default(ModelTask.REPORT_GENERATION, "this is not json")
    fx = await session_factory()
    result = await generate_reports(fx.session_id, job_id="j", allow_fallback=False)
    assert result.narrative_source == {"candidate": "fallback", "recruiter": "fallback"}


async def test_report_job_defers_while_evaluations_pending(
    session_factory, mock_ai, storage, redis, worker_factory, monkeypatch
) -> None:
    import app.workers.reports as reports_worker

    monkeypatch.setattr(reports_worker, "DEFER_FIRST_SECONDS", 0.05)
    monkeypatch.setattr(reports_worker, "DEFER_MAX_SECONDS", 0.05)
    script_narratives(mock_ai)
    fx = await session_factory()
    async with session_scope() as db:
        pending = BackgroundJob(
            type="answer.evaluate", status="queued", payload={"session_id": str(fx.session_id)}
        )
        db.add(pending)
    worker = worker_factory(job_types=["report.generate", "scoring.finalize"])
    job_id = await enqueue(
        "report.generate", {"session_id": str(fx.session_id)}, dedupe_key=f"report.generate:{fx.session_id}"
    )
    await worker.drain(timeout=10)
    async with session_scope() as db:
        first = await db.get(BackgroundJob, uuid.UUID(job_id))
        assert first.status == "succeeded" and first.result["deferred"] is True
        pending_row = await db.get(BackgroundJob, pending.id)
        pending_row.status = "succeeded"
    await worker.drain(timeout=10)
    rows = await _reports(fx.session_id)
    assert {r.status for r in rows} == {"ready"} and len(rows) == 2
    finalize = await enqueue("scoring.finalize", {"session_id": str(fx.session_id)})
    await worker.drain(timeout=10)
    async with session_scope() as db:
        job = await db.get(BackgroundJob, uuid.UUID(finalize))
    assert job.status == "succeeded" and job.result["recommendation"] == "human_review"


async def test_report_waits_for_evaluations_up_to_the_time_cap(session_factory, redis) -> None:
    """S11: during an LLM outage the report waits (growing delays) for up to 40 min, then proceeds."""
    import time

    import app.workers.reports as reports_worker

    assert [round(reports_worker.defer_delay(n), 2) for n in range(4)] == [10.0, 15.0, 22.5, 33.75]
    assert reports_worker.defer_delay(50) == reports_worker.DEFER_MAX_SECONDS
    fx = await session_factory()
    async with session_scope() as db:
        db.add(
            BackgroundJob(type="answer.evaluate", status="queued", payload={"session_id": str(fx.session_id)})
        )
    deferred = await reports_worker._maybe_defer(
        "report.generate", fx.session_id, {"session_id": str(fx.session_id)}
    )
    assert deferred is not None and deferred["deferred"] is True and deferred["deferrals"] == 1
    async with session_scope() as db:
        follow_up = (
            await db.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.type == "report.generate", BackgroundJob.dedupe_key.like("%:defer:1")
                )
            )
        ).one()
    assert follow_up.payload["first_deferred_at"] <= time.time()
    waited_long = {
        "session_id": str(fx.session_id),
        "deferrals": 30,
        "first_deferred_at": time.time() - reports_worker.REPORT_MAX_WAIT_SECONDS - 1,
    }
    assert await reports_worker._maybe_defer("report.generate", fx.session_id, waited_long) is None


async def test_report_job_rejects_live_session(
    session_factory, mock_ai, storage, redis, worker_factory
) -> None:
    fx = await session_factory(status="live")
    worker = worker_factory(job_types=["report.generate"])
    job_id = await enqueue("report.generate", {"session_id": str(fx.session_id)})
    await worker.drain(timeout=10)
    async with session_scope() as db:
        job = await db.get(BackgroundJob, uuid.UUID(job_id))
    assert job.status in ("failed", "dead") and "reports are generated once it has ended" in (
        job.last_error or ""
    )


async def test_build_demo_report_without_writes(session_factory, mock_ai, storage, redis) -> None:
    fx = await session_factory()
    report = await build_demo_report(fx.session_id, use_llm=False, include_html=True)
    assert report["persisted"] is False
    assert report["candidate"]["narrative"]["overall_summary"]
    assert report["recruiter"]["recommendation"]["value"] == "human_review"
    assert "<html" in report["html"]["recruiter"]
    assert await _reports(fx.session_id) == []
    persisted = await build_demo_report(fx.session_id, use_llm=False, persist=True)
    assert persisted["persisted"] and persisted["generation"]["versions"] == {"candidate": 1, "recruiter": 1}


async def test_a_run_the_code_runner_failed_is_never_the_final_coding_result(
    session_factory, mock_ai, storage, redis
) -> None:
    # S20: an infrastructure failure after the candidate's real submission must not read as their crash.
    fx = await session_factory()
    async with session_scope() as db:
        db.add(
            CodeRun(
                coding_session_id=fx.coding_session_id,
                kind="submit",
                language="python",
                status="error",
                passed_count=0,
                total_count=8,
                elapsed_seconds=1200,
                sandbox_meta={"platform_fault": True, "reason": "docker_error"},
            )
        )
    report = await build_demo_report(fx.session_id, use_llm=False)
    coding = report["recruiter"]["coding"][0]
    assert coding["final_result"]["status"] == "passed" and coding["final_result"]["passed"] == 8
    assert [r["platform_fault"] for r in coding["runs"]] == [False, True]


async def test_legacy_judgemental_text_is_neutral_in_reports_but_never_rewritten_in_storage(
    session_factory, mock_ai, storage, redis
) -> None:
    """Evidence rows and session-state contradictions written before the neutral-wording rules read
    neutrally in regenerated reports (JSON and HTML, both audiences); the stored records stay as they were."""
    fx = await session_factory()
    legacy_evidence = "Candidate failed to describe the Terraform guardrails, failing to mention plan review."
    legacy_contradiction = (
        "Candidate previously stated: 'Karpenter handled node autoscaling.' / earlier: "
        "Current answer ignores these specific triggers entirely."
    )
    async with session_scope() as db:
        row = await db.get(CompetencyEvidence, fx.evidence_ids[0])
        row.evidence = legacy_evidence
        session = await db.get(InterviewSession, fx.session_id)
        state = dict(session.state)
        state["contradictions"] = [{**state["contradictions"][0], "description": legacy_contradiction}]
        session.state = state
    report = await build_demo_report(fx.session_id, use_llm=False, include_html=True)
    recruiter = report["recruiter"]
    item = next(e for e in recruiter["evidence"] if e["id"] == str(fx.evidence_ids[0]))
    assert (
        item["evidence"] == "Candidate did not describe the Terraform guardrails, not mentioning plan review."
    )
    assert recruiter["contradictions"][0]["description"].endswith(
        "Current answer does not address these specific triggers at all."
    )
    html = report["html"]["recruiter"]
    assert "does not address these specific triggers at all" in html and "ignores these specific" not in html
    assert "failed to describe" not in json.dumps(report) and "failing to mention" not in json.dumps(report)
    async with session_scope() as db:
        assert (await db.get(CompetencyEvidence, fx.evidence_ids[0])).evidence == legacy_evidence
        stored = (await db.get(InterviewSession, fx.session_id)).state["contradictions"][0]["description"]
        assert stored == legacy_contradiction
