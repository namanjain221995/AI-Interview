"""Human review decisions on a session (the decision of record)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select

from app.core.deps import DbSession, PaginationParams, require_permission
from app.core.errors import AppError
from app.core.rbac import Permission, Principal
from app.db.models import ReviewDecision, User
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.reviews import ReviewCreate, ReviewListOut, ReviewOut
from app.schemas.api.sessions import PersonRef
from app.services.reports.queries import current_recommendation, get_session_for_principal
from app.services.reports.reviews import create_review
from app.services.reports.service import release_candidate_report

router = APIRouter(tags=["reviews"], responses=ERROR_RESPONSES)

ReviewReader = Annotated[Principal, Depends(require_permission(Permission.SESSIONS_READ))]
ReviewWriter = Annotated[
    Principal, Depends(require_permission(Permission.SESSIONS_READ, Permission.REVIEWS_WRITE))
]


def _out(review: ReviewDecision, reviewer: User | None) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        session_id=review.session_id,
        reviewer_id=review.reviewer_id,
        reviewer=PersonRef(id=reviewer.id, full_name=reviewer.full_name, email=reviewer.email)
        if reviewer
        else None,
        decision=review.decision,
        notes=review.notes,
        ai_recommendation_at_review=review.ai_recommendation_at_review,
        overrides_ai=review.overrides_ai,
        created_at=review.created_at,
    )


@router.get("/sessions/{session_id}/reviews", response_model=ReviewListOut, summary="Review decision history")
async def list_reviews(
    session_id: uuid.UUID, principal: ReviewReader, db: DbSession, page: PaginationParams
) -> ReviewListOut:
    session = await get_session_for_principal(db, principal, session_id)
    stmt = select(ReviewDecision).where(ReviewDecision.session_id == session.id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(page.apply(stmt.order_by(ReviewDecision.created_at.desc(), ReviewDecision.id)))
    ).all()
    reviewers = (
        {
            u.id: u
            for u in (await db.scalars(select(User).where(User.id.in_({r.reviewer_id for r in rows})))).all()
        }
        if rows
        else {}
    )
    recommendation = await current_recommendation(db, session.id)
    return ReviewListOut(
        session_id=session.id,
        items=[_out(r, reviewers.get(r.reviewer_id)) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
        current_ai_recommendation=recommendation,
    )


@router.post(
    "/sessions/{session_id}/reviews",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record the human decision",
)
async def post_review(
    session_id: uuid.UUID, body: ReviewCreate, principal: ReviewWriter, db: DbSession, request: Request
) -> ReviewOut:
    session = await get_session_for_principal(db, principal, session_id)
    review = await create_review(
        db, principal, session, decision=body.decision, notes=body.notes, request=request
    )
    released: bool | None = None
    if body.release_candidate_report:
        if not principal.has(Permission.REPORTS_RELEASE):
            released = False
        else:
            try:
                await release_candidate_report(db, principal, session.id, request=request)
                released = True
            except AppError:
                # The decision is recorded either way; the release can be retried explicitly.
                released = False
    reviewer = await db.get(User, principal.user_id) if principal.user_id else None
    out = _out(review, reviewer)
    out.candidate_report_released = released
    return out
