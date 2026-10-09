"""Builders for interview-engine unit tests (no I/O)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import ClaimStatus, Difficulty, RoundType, SegmentKind, TurnAction
from app.schemas.ai import ClaimToVerify, CompetencyTarget, InterviewPlan, PlanSegment, TurnDecision
from app.schemas.rounds import RoundConfig
from app.services.interview.state import ClaimState, CompetencyState, InterviewState, QuestionRecord

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def round_config(**overrides: Any) -> RoundConfig:
    base: dict[str, Any] = {
        "round_type": RoundType.TECHNICAL_SCREENING,
        "name": "Technical Screening",
        "duration_minutes": 30,
        "max_duration_minutes": 40,
        "expected_difficulty": "medium",
        "competencies": [
            {
                "key": "kubernetes",
                "name": "Kubernetes",
                "weight": 1.5,
                "focus_areas": ["autoscaling", "networking"],
            },
            {"key": "aws", "name": "AWS", "weight": 1.2, "focus_areas": ["iam", "vpc"]},
            {"key": "communication", "name": "Communication", "weight": 0.5, "required": False},
        ],
        "min_evidence_per_competency": 2,
        "confidence_threshold": 0.65,
        "stopping": {
            "stop_on_sufficient_evidence": True,
            "min_duration_minutes": 8,
            "min_questions": 4,
            "max_questions": 14,
        },
        "question_strategy": {"max_followup_depth": 3},
        "resume_validation": {"enabled": True, "max_claims": 2, "min_priority": 3},
    }
    base.update(overrides)
    return RoundConfig.model_validate(base)


def claim(cid: str, priority: int = 4, keys: list[str] | None = None, text: str | None = None) -> ClaimState:
    return ClaimState(
        claim=ClaimToVerify(
            id=cid,
            claim=text or f"Claim {cid}: ran 40-node EKS clusters",
            priority=priority,
            competency_keys=keys or ["kubernetes"],
            evidence_topics=["node count", "autoscaling"],
        )
    )


def state(
    *,
    rc: RoundConfig | None = None,
    elapsed_s: float = 120.0,
    claims: dict[str, ClaimState] | None = None,
    plan: InterviewPlan | None = None,
    now: datetime = T0,
    **fields: Any,
) -> InterviewState:
    rc = rc or round_config()
    comps = {
        c.key: CompetencyState(
            key=c.key,
            name=c.name,
            weight=c.weight,
            required=c.required,
            focus_areas=list(c.focus_areas),
            current_difficulty=c.target_difficulty or rc.expected_difficulty,
        )
        for c in rc.competencies
    }
    s = InterviewState(
        session_id=str(uuid.uuid4()),
        interview_id=str(uuid.uuid4()),
        organization_id=str(uuid.uuid4()),
        candidate_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        round_id=str(uuid.uuid4()),
        round_config=rc,
        competencies=comps,
        claims=claims if claims is not None else {},
        plan=plan,
        time_limit_s=rc.duration_minutes * 60.0,
        max_time_s=rc.max_duration_minutes * 60.0,
        candidate_name="Jane Doe",
        job_summary={"title": "Senior DevOps Engineer", "company": "Demo Corp"},
        **fields,
    )
    # Clock: running, ``elapsed_s`` seconds in at ``now``.
    s.last_resumed_at = now - timedelta(seconds=elapsed_s)
    s.started_at = s.last_resumed_at
    return s


def ask(
    s: InterviewState,
    key: str,
    *,
    action: TurnAction = TurnAction.ASK_QUESTION,
    difficulty: Difficulty = Difficulty.MEDIUM,
    followups: int = 0,
    topic: str | None = None,
) -> QuestionRecord:
    """Record that a question on ``key`` is current (as the engine would)."""
    q = QuestionRecord(
        id=str(uuid.uuid4()),
        sequence=len(s.questions) + 1,
        action=action,
        text=f"Question about {key}?",
        competency_key=key,
        difficulty=difficulty,
        topic=topic or key,
    )
    s.questions.append(q)
    s.current_question_id = q.id
    s.current_competency = key
    s.current_topic = topic or key
    comp = s.competencies[key]
    comp.questions_asked += 1
    comp.current_difficulty = difficulty
    comp.followups_on_current_topic = followups
    s.difficulty_counts[difficulty.value] = s.difficulty_counts.get(difficulty.value, 0) + 1
    return q


def sufficient(
    s: InterviewState, *keys: str, score: float = 7.5, confidence: float = 0.8, evidence: int = 3
) -> None:
    for key in keys:
        c = s.competencies[key]
        c.sufficient_evidence = True
        c.score_estimate = score
        c.confidence = confidence
        c.evidence_count = evidence
        c.positive_evidence = evidence


def plan(
    segments: list[tuple[SegmentKind, float, list[str]]], *, claims: list[str] | None = None
) -> InterviewPlan:
    return InterviewPlan(
        opening_focus="Recent Kubernetes work",
        segments=[
            PlanSegment(key=k.value, kind=k, time_budget_minutes=m, competency_keys=keys)
            for k, m, keys in segments
        ],
        competency_targets=[
            CompetencyTarget(key="kubernetes", priority=5),
            CompetencyTarget(key="aws", priority=4),
        ],
        claims_priority=claims or [],
    )


def decision(**overrides: Any) -> TurnDecision:
    base: dict[str, Any] = {
        "speech": "Thanks. How did you size the node groups for that cluster?",
        "action": "ask_followup",
        "answer_quality": None,
        "competency_key": "kubernetes",
        "topic": "autoscaling",
        "difficulty": "medium",
        "kind": "followup",
        "expression": "curious",
        "gesture": "none",
        "gesture_intensity": "low",
        "question_focus": "node group sizing",
        "expected_evidence": ["instance types", "autoscaling"],
        "claim_id": None,
        "bank_question_id": None,
        "missing_specifics": [],
        "new_claims": [],
        "possible_contradiction": None,
        "reason": "follow up on scale",
    }
    base.update(overrides)
    return TurnDecision.model_validate(base)


__all__ = ["T0", "ask", "claim", "decision", "plan", "round_config", "state", "sufficient", "ClaimStatus"]
