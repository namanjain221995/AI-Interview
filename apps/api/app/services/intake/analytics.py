"""Intake analytics (design contract section 10.2 #18, shape ``IntakeAnalyticsOut`` 15.13).

Population: the organisation's intake invitations created in the period (``?days`` back from now, or
``created_from``/``created_to``), narrowed by job, target role, reviewer (assigned or deciding) and bucket — selected
through T3's listing (:func:`app.services.intake.listing.list_intakes`), so bucket and filter semantics are exactly
the list page's. Each invitation contributes its newest session, newest ready evaluation and newest decision.

Work-authorisation (MQ1) answers are never read here: analytics never count, group or filter by eligibility.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InterviewSession, User
from app.db.models.intake import (
    IntakeCompetencyScore,
    IntakeDecision,
    IntakeEvaluation,
    IntakeInterview,
    IntakeStageResult,
)
from app.schemas.intake import TARGET_ROLE_LABELS
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.listing import IntakeFilters, list_intakes
from app.services.intake.report import OUTCOME_LABELS
from app.services.intake.scoring import BAND_LABELS, INTAKE_COMPETENCIES, DecisionBand
from app.services.intake.training import CATALOG

PAGE = 500
FOUR_PLACES = Decimal("0.0001")
INCOMPLETE_END_REASONS = frozenset({"candidate_ended", "disconnect_timeout", "technical_failure"})
ENDED = frozenset({"completing", "completed"})
WEAKNESS_LABELS = {
    "self_rating_not_supported": "Self-rating not supported by the language check",
    "claim_unsupported": "Résumé claim not supported",
    "claim_team_level": "Résumé claim only at team level",
    "drill_b_low": "Low relay (Drill B) score",
    "reading_not_completed": "Passage not read aloud",
}


@dataclass(slots=True)
class AnalyticsPeriod:
    organization_id: uuid.UUID
    since: datetime
    until: datetime
    days: int
    job_id: uuid.UUID | None = None
    target_role: str | None = None
    reviewer_id: uuid.UUID | None = None
    bucket: str = "all"

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "until": self.until,
            "days": self.days,
            "job_id": self.job_id,
            "target_role": self.target_role,
            "reviewer_id": self.reviewer_id,
        }


def make_period(
    organization_id: uuid.UUID,
    *,
    days: int = 30,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    job_id: uuid.UUID | None = None,
    target_role: str | None = None,
    reviewer_id: uuid.UUID | None = None,
    bucket: str = "all",
) -> AnalyticsPeriod:
    until = created_to or datetime.now(UTC)
    since = created_from or (until - timedelta(days=days))
    span = max(1, int(round((until - since).total_seconds() / 86400)))
    return AnalyticsPeriod(
        organization_id, since, until, span, job_id, target_role, reviewer_id, bucket or "all"
    )


def _avg(values: Iterable[Decimal]) -> str | None:
    items = list(values)
    if not items:
        return None
    return format((sum(items, Decimal(0)) / len(items)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP), "f")


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


async def _population(db: AsyncSession, period: AnalyticsPeriod) -> list[Any]:
    filters = IntakeFilters(
        job_id=period.job_id,
        reviewer_id=period.reviewer_id,
        created_from=period.since,
        created_to=period.until,
    )
    items: list[Any] = []
    offset = 0
    while True:
        page, total = await list_intakes(
            db,
            period.organization_id,
            filters,
            bucket=period.bucket,
            sort="created_at",
            limit=PAGE,
            offset=offset,
        )
        items.extend(page)
        offset += PAGE
        if offset >= total or not page:
            return items


async def intake_analytics(
    db: AsyncSession, period: AnalyticsPeriod, *, kit: IntakeKit | None = None
) -> dict[str, Any]:
    kit = kit or load_intake_kit()
    population = [item for item in await _population(db, period) if item.status != "draft"]
    roles = await _effective_roles(db, [item.id for item in population])
    if period.target_role:
        # Effective target role: the candidate's choice on the form, else the staff preset.
        population = [item for item in population if roles.get(item.id) == period.target_role]
    intake_ids = [item.id for item in population]
    session_ids = [item.session.id for item in population if item.session is not None]
    sessions = (
        {
            row.id: row
            for row in (
                await db.execute(
                    select(
                        InterviewSession.id,
                        InterviewSession.status,
                        InterviewSession.started_at,
                        InterviewSession.end_reason,
                        InterviewSession.elapsed_seconds,
                    ).where(InterviewSession.id.in_(session_ids))
                )
            ).all()
        }
        if session_ids
        else {}
    )
    stage_rows = (
        (
            await db.execute(
                select(
                    IntakeStageResult.session_id, IntakeStageResult.stage_key, IntakeStageResult.status
                ).where(IntakeStageResult.session_id.in_(session_ids))
            )
        ).all()
        if session_ids
        else []
    )
    evaluation_ids = [
        item.evaluation.id
        for item in population
        if item.evaluation is not None and item.evaluation.status == "ready"
    ]
    evaluations = (
        {
            row.id: row
            for row in (
                await db.execute(
                    select(
                        IntakeEvaluation.id,
                        IntakeEvaluation.intake_id,
                        IntakeEvaluation.band,
                        IntakeEvaluation.weighted_total,
                        IntakeEvaluation.summary,
                        IntakeEvaluation.review_items,
                        IntakeEvaluation.training_plan,
                    ).where(IntakeEvaluation.id.in_(evaluation_ids))
                )
            ).all()
        }
        if evaluation_ids
        else {}
    )
    competency_rows = (
        (
            await db.execute(
                select(
                    IntakeCompetencyScore.competency_key,
                    IntakeCompetencyScore.status,
                    IntakeCompetencyScore.score,
                ).where(IntakeCompetencyScore.evaluation_id.in_(evaluation_ids))
            )
        ).all()
        if evaluation_ids
        else []
    )
    decision_rows = (
        (
            await db.execute(
                select(
                    IntakeDecision.intake_id,
                    IntakeDecision.outcome,
                    IntakeDecision.overrides_ai,
                    IntakeDecision.reviewer_id,
                    IntakeDecision.created_at,
                    User.full_name,
                )
                .join(User, User.id == IntakeDecision.reviewer_id, isouter=True)
                .where(IntakeDecision.intake_id.in_(intake_ids))
                .order_by(IntakeDecision.created_at)
            )
        ).all()
        if intake_ids
        else []
    )
    # ---- funnel
    incomplete_sessions = {sid for sid, _key, status in stage_rows if status == "incomplete"}
    started = completed = incomplete = 0
    durations: list[float] = []
    for item in population:
        session = sessions.get(item.session.id) if item.session is not None else None
        if session is None:
            continue
        if session.started_at is not None:
            started += 1
        if session.status in ENDED:
            completed += 1
            durations.append(float(session.elapsed_seconds or 0.0))
        ended = session.status in ENDED or session.status in ("aborted", "failed")
        if ended and (session.end_reason in INCOMPLETE_END_REASONS or session.id in incomplete_sessions):
            incomplete += 1
    latest_decision: dict[uuid.UUID, Any] = {}
    reviewer_counts: Counter[tuple[uuid.UUID, str]] = Counter()
    for row in decision_rows:
        latest_decision[row.intake_id] = row
        reviewer_counts[(row.reviewer_id, row.full_name or "")] += 1
    funnel = {
        "invitations": sum(1 for item in population if item.interview is not None),
        "onboarded": sum(1 for item in population if item.onboarding_submitted),
        "started": started,
        "completed": completed,
        "incomplete": incomplete,
        "evaluated": len(evaluations),
        "decided": len(latest_decision),
    }

    # ---- stage completion
    stage_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for _sid, key, status in stage_rows:
        stage_counts[key][status] += 1
    stage_completion = []
    for stage in kit.stages:
        counts = stage_counts.get(stage.key, Counter())
        done, open_ = counts.get("completed", 0), counts.get("incomplete", 0)
        stage_completion.append(
            {
                "stage": stage.key,
                "title": stage.title,
                "completed": done,
                "incomplete": open_,
                "rate": _rate(done, done + open_),
            }
        )

    # ---- bands, decisions, overrides
    band_counts = Counter(row.band for row in evaluations.values() if row.band)
    bands = [
        {"band": band.value, "label": BAND_LABELS[band], "count": band_counts.get(band.value, 0)}
        for band in DecisionBand
    ]
    outcome_counts = Counter(row.outcome for row in latest_decision.values())
    decisions = [
        {"outcome": key, "label": label, "count": outcome_counts.get(key, 0)}
        for key, label in OUTCOME_LABELS.items()
    ]
    override_count = sum(1 for row in latest_decision.values() if row.overrides_ai)

    # ---- competencies
    by_key: dict[str, list[Decimal]] = defaultdict(list)
    insufficient: Counter[str] = Counter()
    for key, status, score in competency_rows:
        if status == "scored" and score is not None:
            by_key[key].append(Decimal(str(score)))
        else:
            insufficient[key] += 1
    competency_averages = [
        {
            "key": c.key,
            "label": c.label,
            "average": _avg(by_key.get(c.key, [])),
            "scored": len(by_key.get(c.key, [])),
            "insufficient": insufficient.get(c.key, 0),
        }
        for c in INTAKE_COMPETENCIES
    ]

    # ---- technical weaknesses, training needs, target roles
    weaknesses: Counter[str] = Counter()
    training: Counter[str] = Counter()
    for row in evaluations.values():
        summary = row.summary or {}
        if summary.get("rating_supported") == "not_supported":
            weaknesses["self_rating_not_supported"] += 1
        verification = summary.get("verification") or {}
        if verification.get("quality") in ("incorrect", "no_answer"):
            weaknesses[f"verification_incorrect:{summary.get('language') or 'unknown'}"] += 1
        verdicts = [c.get("verdict") for c in summary.get("claims") or []]
        if any(v in ("unsupported", "inconsistent") for v in verdicts):
            weaknesses["claim_unsupported"] += 1
        if "team_level" in verdicts:
            weaknesses["claim_team_level"] += 1
        drill_b = (summary.get("drill_b") or {}).get("score")
        if isinstance(drill_b, int) and drill_b <= 2:
            weaknesses["drill_b_low"] += 1
        if any(item.get("kind") == "reading_not_completed" for item in row.review_items or []):
            weaknesses["reading_not_completed"] += 1
        for module in (row.training_plan or {}).get("modules") or []:
            if module.get("key"):
                training[module["key"]] += 1
    technical_weaknesses = [
        {"key": key, "label": _weakness_label(key, kit), "count": count}
        for key, count in sorted(weaknesses.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    training_needs = [
        {"module": key, "label": CATALOG[key].title if key in CATALOG else key, "count": count}
        for key, count in sorted(training.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    per_role: dict[str, list[Any]] = defaultdict(list)
    for item in population:
        role = roles.get(item.id) or "unspecified"
        per_role[role].append(item)
    totals_by_intake = {
        row.intake_id: Decimal(str(row.weighted_total))
        for row in evaluations.values()
        if row.weighted_total is not None
    }
    by_target_role = [
        {
            "target_role": role,
            "label": TARGET_ROLE_LABELS.get(role, "Not stated" if role == "unspecified" else role),
            "count": len(items),
            "average_weighted_total": _avg(totals_by_intake[i.id] for i in items if i.id in totals_by_intake),
        }
        for role, items in sorted(per_role.items())
    ]
    return {
        "period": period.as_dict(),
        "funnel": funnel,
        "average_weighted_total": _avg(
            Decimal(str(row.weighted_total))
            for row in evaluations.values()
            if row.weighted_total is not None and row.band != DecisionBand.INSUFFICIENT_EVIDENCE.value
        ),
        "average_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
        "stage_completion": stage_completion,
        "bands": bands,
        "decisions": decisions,
        "overrides": {"count": override_count, "rate": _rate(override_count, len(latest_decision))},
        "competency_averages": competency_averages,
        "technical_weaknesses": technical_weaknesses,
        "training_needs": training_needs,
        "reviewers": [
            {"id": reviewer_id, "full_name": name or "Reviewer", "decisions": count}
            for (reviewer_id, name), count in sorted(
                reviewer_counts.items(), key=lambda kv: (-kv[1], kv[0][1])
            )
            if reviewer_id is not None
        ],
        "by_target_role": by_target_role,
    }


async def _effective_roles(db: AsyncSession, intake_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    if not intake_ids:
        return {}
    rows = (
        await db.execute(
            select(
                IntakeInterview.id,
                IntakeInterview.target_role.label("preset"),
                IntakeInterview.onboarding["target_role"].astext.label("role"),
            ).where(IntakeInterview.id.in_(intake_ids))
        )
    ).all()
    return {row.id: (row.role or row.preset) for row in rows}


def _weakness_label(key: str, kit: IntakeKit) -> str:
    if key.startswith("verification_incorrect:"):
        language = key.split(":", 1)[1]
        check = kit.language_check(language) if language and language != "unknown" else None
        return f"Language check not answered correctly ({check.label if check else language})"
    return WEAKNESS_LABELS.get(key, key.replace("_", " "))


__all__ = ["AnalyticsPeriod", "intake_analytics", "make_period"]
