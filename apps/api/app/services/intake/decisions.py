"""Human intake decisions (design contract section 8.9).

The AI band is advisory. A reviewer records one of five outcomes with a rationale; the decision is stored in
``intake_decisions`` (append-only, the newest is current), **mirrored** into a generic ``review_decisions`` row so
the review queue (``needs_review``), the sessions list and the generic analytics keep working, and audited
(``review.create`` by the mirror, then ``intake.decision.create``).

Outcome -> mirror: accept / accept_with_notes -> advance, second_interview -> hold, reject -> reject,
request_evidence -> needs_more_information.

``overrides_ai`` = the outcome is not in the set aligned with the band: strong_accept / accept_with_notes ->
{accept, accept_with_notes}; borderline -> {second_interview, request_evidence, accept_with_notes}; reject ->
{reject}; insufficient_evidence -> {request_evidence, second_interview}; no evaluation -> false. Overrides are
counted, never blocked.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.audit import audit
from app.core.errors import Conflict, ValidationFailed
from app.core.rbac import Principal
from app.db.models import InterviewSession
from app.db.models.intake import IntakeDecision, IntakeInterview
from app.domain.enums import ReviewDecisionValue
from app.services.intake.pipeline import latest_ready_evaluation, running_evaluation
from app.services.intake.scoring import INTAKE_COMPETENCY_KEYS
from app.services.reports.queries import REVIEWABLE_STATUSES
from app.services.reports.reviews import create_review

RATIONALE_MIN = 10
RATIONALE_MAX = 5000
NOTE_MAX = 1000
MAX_REQUESTED_EVIDENCE = 10

MIRROR: dict[str, ReviewDecisionValue] = {
    "accept": ReviewDecisionValue.ADVANCE,
    "accept_with_notes": ReviewDecisionValue.ADVANCE,
    "second_interview": ReviewDecisionValue.HOLD,
    "reject": ReviewDecisionValue.REJECT,
    "request_evidence": ReviewDecisionValue.NEEDS_MORE_INFORMATION,
}
ALIGNED: dict[str, frozenset[str]] = {
    "strong_accept": frozenset({"accept", "accept_with_notes"}),
    "accept_with_notes": frozenset({"accept", "accept_with_notes"}),
    "borderline": frozenset({"second_interview", "request_evidence", "accept_with_notes"}),
    "reject": frozenset({"reject"}),
    "insufficient_evidence": frozenset({"request_evidence", "second_interview"}),
}
RESOLUTIONS = frozenset({"confirmed", "dismissed", "follow_up"})


def overrides_ai(outcome: str, band: str | None) -> bool:
    """Whether a decision differs from the AI band (False when there is no evaluation)."""
    if not band:
        return False
    aligned = ALIGNED.get(band)
    return aligned is not None and outcome not in aligned


def _clean(text: str | None) -> str:
    return (text or "").strip()


def validate_decision_input(
    *,
    outcome: str,
    rationale: str | None,
    review_items: Sequence[Mapping[str, Any]],
    competency_notes: Mapping[str, Any],
    requested_evidence: Sequence[str],
    known_item_ids: set[str],
) -> tuple[str, list[dict[str, Any]], dict[str, str], list[str]]:
    """Normalised ``(rationale, resolutions, competency_notes, requested_evidence)`` or a 422 with the contract code."""
    if outcome not in MIRROR:
        raise ValidationFailed("Unknown decision outcome.", details={"field": "outcome"})
    text = _clean(rationale)
    if not RATIONALE_MIN <= len(text) <= RATIONALE_MAX:
        raise ValidationFailed(
            f"A rationale of {RATIONALE_MIN}-{RATIONALE_MAX} characters is required.",
            code="rationale_required",
            details={"field": "rationale"},
        )
    evidence = [_clean(e)[:300] for e in requested_evidence if _clean(e)]
    if outcome == "request_evidence" and not evidence:
        raise ValidationFailed(
            "List the evidence you are requesting.",
            code="requested_evidence_required",
            details={"field": "requested_evidence"},
        )
    if len(evidence) > MAX_REQUESTED_EVIDENCE:
        raise ValidationFailed(
            "At most 10 requested evidence items.", details={"field": "requested_evidence"}
        )
    resolutions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in review_items:
        item_id = str(entry.get("item_id") or "")
        if item_id not in known_item_ids:
            raise ValidationFailed(
                "A review item does not belong to the evaluation shown.",
                code="unknown_review_item",
                details={"field": "review_items", "item_id": item_id},
            )
        resolution = str(entry.get("resolution") or "")
        if resolution not in RESOLUTIONS:
            raise ValidationFailed("Unknown review item resolution.", details={"field": "review_items"})
        if item_id in seen:
            continue
        seen.add(item_id)
        note = _clean(entry.get("note"))[:NOTE_MAX] or None
        resolutions.append({"item_id": item_id, "resolution": resolution, "note": note})
    notes: dict[str, str] = {}
    for key, value in (competency_notes or {}).items():
        if key not in INTAKE_COMPETENCY_KEYS:
            raise ValidationFailed(
                f"Unknown competency {key!r}.", details={"field": "competency_notes", "competency": key}
            )
        cleaned = _clean(value)[:NOTE_MAX]
        if cleaned:
            notes[key] = cleaned
    return text, resolutions, notes, evidence


async def create_decision(
    db: AsyncSession,
    principal: Principal,
    *,
    intake: IntakeInterview,
    session: InterviewSession | None,
    outcome: str,
    rationale: str | None,
    review_items: Sequence[Mapping[str, Any]] = (),
    competency_notes: Mapping[str, Any] | None = None,
    requested_evidence: Sequence[str] = (),
    request: Request | None = None,
) -> IntakeDecision:
    """Record a human decision in one transaction (mirror ``review_decisions`` row, intake row, audit)."""
    if session is None or session.status not in REVIEWABLE_STATUSES:
        raise Conflict(
            "A decision can be recorded once the interview has ended.",
            code="session_not_finished",
            details={"status": session.status if session is not None else None},
        )
    if await running_evaluation(db, session.id) is not None:
        raise Conflict(
            "The AI evaluation is still running. Record the decision when it has finished.",
            code="evaluation_running",
        )
    evaluation = await latest_ready_evaluation(db, session.id)
    known = {str(item.get("id")) for item in (evaluation.review_items if evaluation is not None else [])}
    text, resolutions, notes, evidence = validate_decision_input(
        outcome=outcome,
        rationale=rationale,
        review_items=review_items,
        competency_notes=competency_notes or {},
        requested_evidence=requested_evidence,
        known_item_ids=known,
    )
    band = evaluation.band if evaluation is not None else None
    override = overrides_ai(outcome, band)
    mirror = await create_review(
        db, principal, session, decision=MIRROR[outcome], notes=text, request=request
    )
    decision = IntakeDecision(
        id=uuid.uuid4(),
        organization_id=intake.organization_id,
        intake_id=intake.id,
        session_id=session.id,
        evaluation_id=evaluation.id if evaluation is not None else None,
        review_decision_id=mirror.id,
        reviewer_id=principal.user_id,
        outcome=outcome,
        rationale=text,
        ai_band_at_decision=band,
        ai_total_at_decision=evaluation.weighted_total if evaluation is not None else None,
        overrides_ai=override,
        review_items_resolution=resolutions,
        competency_notes=notes,
        requested_evidence=evidence,
    )
    db.add(decision)
    await db.flush()
    await audit(
        db,
        principal,
        "intake.decision.create",
        resource_type="intake_interview",
        resource_id=str(intake.id),
        organization_id=intake.organization_id,
        request=request,
        details={
            "decision_id": str(decision.id),
            "session_id": str(session.id),
            "outcome": outcome,
            "band": band,
            "overrides_ai": override,
            "resolved_items": len(resolutions),
            "mirror_id": str(mirror.id),
            "evaluation_id": str(evaluation.id) if evaluation is not None else None,
        },
    )
    metrics.INTAKE_DECISIONS.labels(outcome=outcome, overrides_ai=str(override).lower()).inc()
    return decision


__all__ = [
    "ALIGNED",
    "MIRROR",
    "RATIONALE_MAX",
    "RATIONALE_MIN",
    "create_decision",
    "overrides_ai",
    "validate_decision_input",
]
