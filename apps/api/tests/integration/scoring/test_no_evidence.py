"""No evidence = no score: silent, one-answer and normal sessions (owner report 2026-10-09).

A session where the candidate said nothing must never show midpoint (50/100, 5/10) scores: unassessed
competencies, empty rubric categories and the overall score are stored as NULL and the recommendation is
``insufficient_evidence``. Weak or empty answers score low (the neutral prior never lifts them). The review
summary states the facts and withholds AI text when the evidence is too thin.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models import Report, Score
from app.db.session import session_scope
from app.domain.enums import Recommendation, Role, ScoreCategory
from app.services.scoring import recompute_session_scores
from app.services.scoring.compute import NO_EVIDENCE_REASON

pytestmark = pytest.mark.integration

WEAK_SPEC = {
    "competency": "kubernetes",
    "difficulty": "medium",
    "quality": "no_answer",
    "level": 1.0,
    "question": "Walk me through the EKS platform you ran: how many nodes, and how did autoscaling work?",
    "answer": "Sorry, I don't really know that one at all.",
    "expected": ["node count", "autoscaling"],
    "evidence": [
        (
            "autoscaling",
            "negative",
            0.6,
            0.8,
            "Could not describe any autoscaling setup.",
            "I don't really know that one",
        ),
    ],
}


async def _rows(session_id: uuid.UUID) -> list[Score]:
    async with session_scope() as db:
        return list((await db.scalars(select(Score).where(Score.session_id == session_id))).all())


async def test_silent_session_stores_null_scores_and_insufficient(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(
        recruiter.user.organization_id,
        question_count=0,
        with_coding=False,
        flagged_integrity=False,
        end_reason="candidate_ended",
        elapsed_seconds=20.0,
    )
    # Live view before anything was persisted: computed on the fly, still no made-up numbers.
    live = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/scores")).json()
    assert live["source"] == "live"
    assert live["overall"]["score"] is None
    assert live["rubric"] and all(r["score"] is None for r in live["rubric"])
    assert live["competencies"] and all(c["score"] is None for c in live["competencies"])
    assert live["recommendation"] == "insufficient_evidence"

    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
    assert result.rubric.overall_score is None
    assert result.recommendation.recommendation is Recommendation.INSUFFICIENT_EVIDENCE
    assert NO_EVIDENCE_REASON in result.recommendation.rationale
    assert "Round-scope calibration" not in result.recommendation.rationale
    rows = await _rows(fx.session_id)
    assert rows and all(r.score is None for r in rows), [(r.category, r.key, r.score) for r in rows]
    assert all(not r.sufficient_evidence for r in rows)
    overall = next(r for r in rows if r.category == ScoreCategory.OVERALL.value)
    assert overall.details["insufficient_reason"] == "no_evidence"

    detail = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}")).json()
    assert detail["overall_score"] is None and detail["confidence"] is None
    assert detail["rubric"] and all(r["score"] is None for r in detail["rubric"])
    assert detail["recommendation"] == "insufficient_evidence"
    listing = (await recruiter.get("/api/v1/sessions")).json()
    item = next(i for i in listing["items"] if i["id"] == str(fx.session_id))
    assert item["overall_score"] is None and item["recommendation"] == "insufficient_evidence"
    analytics = await recruiter.get("/api/v1/analytics/scores")
    assert analytics.status_code in (200, 404), analytics.text  # never a 500 on NULL scores

    summary = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/review-summary")).json()
    assert summary["evidence_level"] == "none" and summary["insufficient"] is True
    assert summary["headline"].startswith("Insufficient evidence")
    assert summary["facts"]["questions_answered"] == 0
    assert summary["facts"]["duration_seconds"] == 20.0
    assert summary["ai_assessment"] is None and summary["ai_withheld_reason"]
    assert "No score is given" in summary["evidence_statement"]
    assert summary["strengths"] == []


async def test_one_weak_answer_scores_low_not_midpoint(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(
        recruiter.user.organization_id,
        question_specs=[WEAK_SPEC],
        with_coding=False,
        flagged_integrity=False,
        end_reason="candidate_ended",
        elapsed_seconds=150.0,
    )
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
    k8s = result.competencies["kubernetes"]
    assert k8s.score_estimate < 2.0  # the prior never lifts an empty answer towards 5
    assert result.rubric.overall_score is not None and result.rubric.overall_score < 25
    assert result.rubric.assessed_weight_share < 1.0
    assert result.recommendation.recommendation is Recommendation.INSUFFICIENT_EVIDENCE
    rows = await _rows(fx.session_id)
    comps = {r.key: r for r in rows if r.category == ScoreCategory.COMPETENCY.value}
    assert comps["kubernetes"].score is not None and comps["kubernetes"].score < 2.0
    assert all(r.score is None for k, r in comps.items() if k != "kubernetes")  # never assessed

    summary = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/review-summary")).json()
    assert summary["evidence_level"] == "thin" and summary["insufficient"] is True
    assert summary["facts"]["questions_asked"] == 1
    assert summary["facts"]["questions_answered"] == 0  # "I don't know" is not an answer
    assert summary["facts"]["questions_skipped"] == 1
    assert summary["ai_assessment"] is None
    assert summary["weaknesses"] and summary["weaknesses"][0]["quote"] == "I don't really know that one"
    assert "too thin to judge" in summary["evidence_statement"]


async def test_normal_session_keeps_scores_and_shows_ai_assessment(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id, flagged_integrity=False)
    async with session_scope() as db:
        result = await recompute_session_scores(db, fx.session_id, final=True)
        db.add(
            Report(
                id=uuid.uuid4(),
                session_id=fx.session_id,
                type="recruiter",
                status="ready",
                version=1,
                content={
                    "narrative_source": "model",
                    "narrative": {
                        "executive_summary": "Strong Kubernetes depth with concrete scale figures."
                    },
                },
                recommendation=result.recommendation.recommendation.value,
                overall_score=result.rubric.overall_score,
                generated_at=datetime.now(UTC),
            )
        )
    assert result.rubric.overall_score is not None and 60 <= result.rubric.overall_score <= 90
    assert result.recommendation.recommendation is not Recommendation.INSUFFICIENT_EVIDENCE
    rows = await _rows(fx.session_id)
    scored = [r for r in rows if r.category == ScoreCategory.COMPETENCY.value and r.score is not None]
    assert len(scored) >= 7

    summary = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/review-summary")).json()
    assert summary["evidence_level"] in ("sufficient", "partial")
    assert summary["insufficient"] is False
    assert summary["facts"]["questions_answered"] == summary["facts"]["questions_asked"] > 5
    assert summary["facts"]["minutes_answered"] > 5
    assert 0.0 <= summary["facts"]["silence_share"] < 1.0
    assert summary["strengths"] and any(p["quote"] for p in summary["strengths"])
    assert summary["weaknesses"]
    assert summary["ai_assessment"]["text"].startswith("Strong Kubernetes depth")
    assert summary["ai_assessment"]["source"] == "recruiter_report"
