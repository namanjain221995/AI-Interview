"""Human review decision DTOs (``/api/v1/sessions/{id}/reviews``)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import Recommendation, ReviewDecisionValue
from app.schemas.api.sessions import PersonRef


class ReviewCreate(BaseModel):
    decision: ReviewDecisionValue
    notes: str | None = Field(None, max_length=5000)
    release_candidate_report: bool = Field(
        False, description="Also release the candidate report (after_review mode) in the same request"
    )


class ReviewOut(BaseModel):
    """The human decision. The AI recommendation is advisory only."""

    id: uuid.UUID
    session_id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewer: PersonRef | None = None
    decision: ReviewDecisionValue
    notes: str | None
    ai_recommendation_at_review: Recommendation | None
    overrides_ai: bool
    created_at: datetime
    candidate_report_released: bool | None = None


class ReviewListOut(BaseModel):
    session_id: uuid.UUID
    items: list[ReviewOut]
    total: int
    limit: int
    offset: int
    current_ai_recommendation: Recommendation | None = None
