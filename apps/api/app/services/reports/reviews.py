"""Human review decisions. The AI recommendation is advisory; the review is
the decision of record and stores the recommendation shown at review time."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict
from app.core.rbac import Principal
from app.db.models import InterviewSession, ReviewDecision
from app.domain.enums import Recommendation, ReviewDecisionValue
from app.services.reports.queries import REVIEWABLE_STATUSES, current_recommendation

POSITIVE = frozenset({Recommendation.STRONG_PROCEED, Recommendation.PROCEED})


def overrides_ai(decision: ReviewDecisionValue, recommendation: Recommendation | None) -> bool:
    """Whether a human decision goes against the assistive recommendation.

    * proceed / strong_proceed: anything but *advance* overrides it;
    * insufficient_evidence: a decisive *advance* or *reject* overrides it
      (holding or asking for more information follows it);
    * human_review / borderline_evidence: the AI deferred to a human, so no
      decision is an override;
    * no recommendation: nothing to override.
    """
    if recommendation is None:
        return False
    if recommendation in POSITIVE:
        return decision is not ReviewDecisionValue.ADVANCE
    if recommendation is Recommendation.INSUFFICIENT_EVIDENCE:
        return decision in (ReviewDecisionValue.ADVANCE, ReviewDecisionValue.REJECT)
    return False


async def create_review(
    db: AsyncSession,
    principal: Principal,
    session: InterviewSession,
    *,
    decision: ReviewDecisionValue,
    notes: str | None,
    request: Any = None,
) -> ReviewDecision:
    if session.status not in REVIEWABLE_STATUSES:
        raise Conflict(
            "A decision can be recorded once the interview has ended.",
            code="session_not_finished",
            details={"status": session.status},
        )
    if principal.user_id is None:
        raise Conflict("Only staff users can record review decisions.")
    recommendation = await current_recommendation(db, session.id)
    review = ReviewDecision(
        id=uuid.uuid4(),
        session_id=session.id,
        reviewer_id=principal.user_id,
        decision=decision.value,
        notes=(notes or "").strip() or None,
        ai_recommendation_at_review=recommendation.value if recommendation else None,
        overrides_ai=overrides_ai(decision, recommendation),
    )
    db.add(review)
    await db.flush()
    await audit(
        db,
        principal,
        "review.create",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={
            "review_id": str(review.id),
            "decision": decision.value,
            "ai_recommendation_at_review": review.ai_recommendation_at_review,
            "overrides_ai": review.overrides_ai,
        },
    )
    return review
