"""recompute_session_scores against a realistic completed session (Postgres)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import BackgroundJob, InterviewSession, Score, ScoreSnapshot
from app.db.session import session_scope
from app.domain.enums import Recommendation, ScoreCategory
from app.services.scoring import compute_session_scores, latest_score_rows, recompute_session_scores
from app.services.scoring.persistence import pending_evaluation_jobs

pytestmark = pytest.mark.integration


async def _scores(session_id: uuid.UUID) -> list[Score]:
    async with session_scope() as db:
        return list((await db.scalars(select(Score).where(Score.session_id == session_id))).all())


async def _snapshots(session_id: uuid.UUID) -> list[ScoreSnapshot]:
    async with session_scope() as db:
        return list(
            (await db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.session_id == session_id))).all()
        )


async def test_final_recompute_writes_scores_rubric_overall_and_timeline(session_factory) -> None:
    fx = await session_factory(flagged_integrity=True)
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
    # Unreviewed integrity flag + sufficient evidence -> human review (never a verdict).
    assert result.recommendation.recommendation is Recommendation.HUMAN_REVIEW
    assert "not determinations" in result.recommendation.rationale
    assert result.rubric.required_coverage >= 0.7
    k8s = result.competencies["kubernetes"]
    assert k8s.sufficient_evidence and k8s.evidence_count == 5 and len(k8s.score_history) == 3
    # The seeded round packs 7 required + 4 optional competencies and a coding problem into 30 minutes
    # (about 1.7 scored answers per competency): two consistent Terraform answers (confidence 0.61, below
    # the configured 0.65, which assumes more answers than this round can give a competency) meet the
    # round-scope target.
    terraform = result.competencies["terraform"]
    assert terraform.sufficient_evidence and len(terraform.score_history) == 2
    assert 0.555 <= terraform.confidence < 0.65
    assert result.recommendation.factors["competency_confidence_target"] == pytest.approx(0.555, abs=1e-3)
    assert "Round-scope calibration: a 30-minute round" in result.recommendation.rationale
    rows = await _scores(fx.session_id)
    assert rows and all(r.is_final for r in rows)
    assert len({r.created_at for r in rows}) == 1
    by_cat = {c: [r for r in rows if r.category == c] for c in ScoreCategory}
    assert len(by_cat[ScoreCategory.OVERALL]) == 1
    overall = by_cat[ScoreCategory.OVERALL][0]
    assert overall.details["recommendation"] == "human_review"
    assert overall.details["human_decision_note"]
    scope = overall.details["round_scope"]
    assert scope["planned_minutes"] == 30 and scope["required_competencies"] == 7
    assert scope["confidence_target"] < scope["confidence_threshold"] == 0.65
    assert scope["min_confidence_target"] < scope["min_confidence"] == 0.6
    assert {r.key for r in by_cat[ScoreCategory.RUBRIC_CATEGORY]} >= {
        "technical_knowledge",
        "coding",
        "resume_consistency",
    }
    comp = next(r for r in by_cat[ScoreCategory.COMPETENCY] if r.key == "kubernetes")
    assert len(comp.positive_evidence_ids) == 5 and comp.supporting_question_ids
    assert comp.details["confidence_threshold"] == 0.65
    assert comp.details["confidence_target"] == pytest.approx(0.555, abs=1e-3)
    assert comp.details["extra_evidence"] == 1
    assert comp.transcript_refs and comp.transcript_refs[0]["quote"]
    assert comp.details["calculation"]["bayesian_mean"] > 5
    assert comp.details["observations"][0]["weight"] > 0
    assert "upgrades" in comp.unanswered_areas  # plan focus area never touched
    snaps = await _snapshots(fx.session_id)
    triggers = {s.trigger for s in snaps}
    assert triggers == {"backfill", "final"}
    finals = [s for s in snaps if s.trigger == "final"]
    assert {s.competency_key for s in finals} >= {"kubernetes", "aws", "data_structures"}


async def test_coding_assistance_and_hint_evidence_is_neutral(session_factory) -> None:
    fx = await session_factory()
    async with session_scope() as db:
        computation = await compute_session_scores(db, fx.session_id)
    coding_obs = [o for o in computation.inputs.observations if o.coding_session_id]
    assert len(coding_obs) == 1
    assert coding_obs[0].competency_key == "data_structures"  # only the round's competency
    assert coding_obs[0].assistance_factor == pytest.approx(0.93)
    assert coding_obs[0].score == pytest.approx(8.0)  # easy cap
    state = computation.states["data_structures"]
    assert state.evidence_count == 5  # hint usage (neutral) not counted
    assert computation.inputs.dimension_averages["code_quality"] == pytest.approx(8.0)
    assert "edge_case_handling" in computation.inputs.dimension_averages


async def test_reviewed_integrity_lets_score_decide(session_factory) -> None:
    fx = await session_factory(flagged_integrity=True, integrity_reviewed=True)
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
    assert result.recommendation.recommendation is Recommendation.PROCEED
    assert 65 <= result.rubric.overall_score < 80


async def test_little_evidence_is_insufficient(session_factory) -> None:
    fx = await session_factory(question_count=2, with_coding=False, flagged_integrity=False)
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
    assert result.recommendation.recommendation is Recommendation.INSUFFICIENT_EVIDENCE
    timed = await session_factory(
        question_count=6, with_coding=False, flagged_integrity=False, end_reason="time_limit"
    )
    async with session_scope() as db:
        result = await recompute_session_scores(db, timed.session_id, final=True)
    assert result.recommendation.recommendation is Recommendation.BORDERLINE_EVIDENCE
    # One Terraform answer stays below even the round-scope target.
    assert result.competencies["terraform"].sufficient_evidence is False


async def test_non_final_runs_replace_previous_and_dedupe_snapshots(session_factory) -> None:
    fx = await session_factory()
    async with session_scope() as db:
        first = await recompute_session_scores(db, fx.session_id, final=False)
    assert first.snapshot_written
    snaps_after_first = len(await _snapshots(fx.session_id))
    async with session_scope() as db:
        second = await recompute_session_scores(db, fx.session_id, final=False)
    assert not second.snapshot_written  # nothing changed -> no duplicate timeline points
    assert len(await _snapshots(fx.session_id)) == snaps_after_first
    rows = await _scores(fx.session_id)
    assert rows and not any(r.is_final for r in rows)
    assert len({r.created_at for r in rows}) == 1  # previous non-final set replaced
    # A final run replaces everything; repeated final runs are idempotent.
    async with session_scope() as db:
        await recompute_session_scores(db, fx.session_id, final=True)
    final_snaps = len(await _snapshots(fx.session_id))
    async with session_scope() as db:
        await recompute_session_scores(db, fx.session_id, final=True)
    assert len(await _snapshots(fx.session_id)) == final_snaps
    rows = await _scores(fx.session_id)
    assert all(r.is_final for r in rows)
    # Live snapshots exist, so the final run did not backfill.
    assert "backfill" not in {s.trigger for s in await _snapshots(fx.session_id)}
    # A later non-final run is newer and therefore what readers see.
    async with session_scope() as db:
        await recompute_session_scores(db, fx.session_id, final=False)
        latest = await latest_score_rows(db, fx.session_id)
    assert latest and not any(r.is_final for r in latest)


async def test_compute_only_writes_nothing(session_factory) -> None:
    fx = await session_factory()
    async with session_scope() as db:
        computation = await compute_session_scores(db, fx.session_id)
    assert computation.rubric.overall_score > 0
    assert await _scores(fx.session_id) == []
    assert await _snapshots(fx.session_id) == []


async def test_empty_session_scores_neutral(session_factory) -> None:
    fx = await session_factory(question_count=0, with_coding=False, flagged_integrity=False)
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
    assert result.recommendation.recommendation is Recommendation.INSUFFICIENT_EVIDENCE
    assert result.rubric.overall_confidence == 0.0
    assert all(s.score_estimate == 5.0 for s in result.competencies.values())


async def test_pending_evaluation_jobs(session_factory) -> None:
    fx = await session_factory(with_coding=True)
    async with session_scope() as db:
        assert await pending_evaluation_jobs(db, fx.session_id) == 0
        db.add(
            BackgroundJob(
                type="answer.evaluate",
                status="queued",
                payload={"session_id": str(fx.session_id), "answer_id": "x"},
            )
        )
        db.add(
            BackgroundJob(
                type="coding.evaluate",
                status="running",
                payload={"coding_session_id": str(fx.coding_session_id)},
            )
        )
        db.add(
            BackgroundJob(
                type="answer.evaluate", status="succeeded", payload={"session_id": str(fx.session_id)}
            )
        )
        db.add(
            BackgroundJob(type="answer.evaluate", status="queued", payload={"session_id": str(uuid.uuid4())})
        )
    async with session_scope() as db:
        assert await pending_evaluation_jobs(db, fx.session_id) == 2


async def test_engine_state_fields_preserved(session_factory) -> None:
    fx = await session_factory()
    async with session_scope() as db:
        session = await db.get(InterviewSession, fx.session_id)
        state = dict(session.state)
        state["competencies"] = {
            "kubernetes": {"key": "kubernetes", "questions_asked": 7, "current_difficulty": "hard"}
        }
        session.state = state
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=False)
        count = await db.scalar(
            select(func.count()).select_from(Score).where(Score.session_id == fx.session_id)
        )
    assert result.competencies["kubernetes"].questions_asked == 7
    assert result.competencies["kubernetes"].current_difficulty.value == "hard"
    assert count and count > 0


async def test_pending_coding_evaluation_is_ignored(session_factory) -> None:
    from app.db.models import CodingSession

    fx = await session_factory()
    async with session_scope() as db:
        cs = await db.get(CodingSession, fx.coding_session_id)
        cs.evaluation = {"status": "pending"}
    async with session_scope() as db:
        computation = await compute_session_scores(db, fx.session_id)
    assert not [o for o in computation.inputs.observations if o.coding_session_id]
    assert "code_quality" not in computation.inputs.dimension_averages
