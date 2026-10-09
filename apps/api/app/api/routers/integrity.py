"""Integrity review API.

Integrity events are **automated observations for human review, not
determinations**. They never decide an interview on their own; reviewers
record a status (dismissed / noted / confirmed concern) and a note on each
observation, and every review is audited.

* ``GET /integrity/events`` - organization review queue (``integrity:review``).
* ``GET /integrity/events/{id}`` - one observation with its evidence frame URL.
* ``PATCH /integrity/events/{id}`` - review status + note (audited).
* ``POST /integrity/events/review`` - the same for up to 200 observations.
* ``GET /sessions/{id}/integrity`` - a session's observation timeline, counts
  and evidence frame signed URLs (``sessions:read``; audited as a media view).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Select, func, select

from app.core.audit import audit
from app.core.deps import DbSession, PaginationParams, require_permission, resolve_org_id
from app.core.errors import NotFound
from app.core.rbac import Permission, Principal
from app.db.models import Candidate, IntegrityEvent, Interview, InterviewSession, Job, MediaAsset
from app.domain.enums import (
    IntegrityAction,
    IntegrityEventType,
    IntegrityReviewStatus,
    IntegritySeverity,
    IntegritySource,
)
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.integrity import (
    OBSERVATION_NOTICE,
    IntegrityBulkReview,
    IntegrityBulkReviewResult,
    IntegrityEventOut,
    IntegrityReviewUpdate,
    JobRef,
    PersonRef,
    SessionIntegrityOut,
)
from app.services.integrity.policy import resolve_policy
from app.services.integrity.rules import ACTIONABLE_ACTIONS, REVIEWABLE_ACTIONS, label_for
from app.services.storage.signing import signed_url_with_expiry

router = APIRouter(tags=["integrity"], responses=ERROR_RESPONSES)

IntegrityReviewer = Annotated[Principal, Depends(require_permission(Permission.INTEGRITY_REVIEW))]
SessionReader = Annotated[Principal, Depends(require_permission(Permission.SESSIONS_READ))]

SESSION_EVENT_LIMIT = 2000
_DOC_SUFFIX = f"\n\n{OBSERVATION_NOTICE} A reviewer's status is a note on the observation, never a decision about the candidate."


def _org_filter(principal: Principal, organization_id: uuid.UUID | None) -> uuid.UUID | None:
    """Organization to filter on (``None`` = all, SUPER_ADMIN without a filter)."""
    if principal.is_super_admin:
        return organization_id
    return resolve_org_id(principal, organization_id)


def _visible(principal: Principal, organization_id: uuid.UUID) -> bool:
    return principal.is_super_admin or organization_id == principal.organization_id


def _event_out(
    event: IntegrityEvent,
    *,
    candidate: tuple[uuid.UUID | None, str | None] | None = None,
    job: tuple[uuid.UUID | None, str | None] | None = None,
    evidence: dict[str, Any] | None = None,
) -> IntegrityEventOut:
    out = IntegrityEventOut.model_validate(event)
    out.label = label_for(event.type)
    if candidate and candidate[0] is not None:
        out.candidate = PersonRef(id=candidate[0], full_name=candidate[1] or "")
    if job and job[0] is not None:
        out.job = JobRef(id=job[0], title=job[1] or "")
    if evidence is not None and event.evidence_media_id is not None:
        info = evidence.get(str(event.evidence_media_id))
        if info is None:
            out.evidence_available = False
        else:
            out.evidence_available = True
            out.evidence_url, out.evidence_url_expires_at = info["url"], info["expires_at"]
    return out


async def _evidence_urls(
    db: DbSession, media_ids: Iterable[uuid.UUID], organization_id: uuid.UUID
) -> dict[str, Any]:
    ids = {mid for mid in media_ids if mid is not None}
    if not ids:
        return {}
    now = datetime.now(UTC)
    assets = (
        await db.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_(ids),
                MediaAsset.organization_id == organization_id,
                MediaAsset.deleted_at.is_(None),
            )
        )
    ).all()
    urls: dict[str, Any] = {}
    for asset in assets:
        if asset.retention_until is not None and asset.retention_until <= now:
            continue
        url, expires_at = signed_url_with_expiry(asset.id)
        urls[str(asset.id)] = {"url": url, "expires_at": expires_at}
    return urls


def _with_refs[S: Select](stmt: S) -> S:  # type: ignore[type-arg]
    return (
        stmt.join(InterviewSession, InterviewSession.id == IntegrityEvent.session_id)
        .outerjoin(Candidate, Candidate.id == InterviewSession.candidate_id)
        .outerjoin(Job, Job.id == InterviewSession.job_id)
    )


async def _load_with_refs(db: DbSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, IntegrityEventOut]:
    if not ids:
        return {}
    rows = (
        await db.execute(
            _with_refs(select(IntegrityEvent, Candidate.id, Candidate.full_name, Job.id, Job.title)).where(
                IntegrityEvent.id.in_(list(ids))
            )
        )
    ).all()
    return {
        event.id: _event_out(event, candidate=(cand_id, cand_name), job=(job_id, job_title))
        for event, cand_id, cand_name, job_id, job_title in rows
    }


@router.get(
    "/integrity/events",
    response_model=Page[IntegrityEventOut],
    summary="Integrity review queue (observations, not determinations)",
    description="Organization-wide integrity observations, newest first." + _DOC_SUFFIX,
)
async def list_integrity_events(
    principal: IntegrityReviewer,
    db: DbSession,
    page: PaginationParams,
    status: Annotated[
        list[IntegrityReviewStatus] | None, Query(description="Review status (repeatable)")
    ] = None,
    type: Annotated[list[IntegrityEventType] | None, Query(description="Event type (repeatable)")] = None,  # noqa: A002
    severity: Annotated[list[IntegritySeverity] | None, Query(description="Severity (repeatable)")] = None,
    source: Annotated[list[IntegritySource] | None, Query(description="Source (repeatable)")] = None,
    action: Annotated[list[IntegrityAction] | None, Query(description="Action (repeatable)")] = None,
    actionable: Annotated[
        bool | None,
        Query(
            description="true: only observations a reviewer should look at (warned, flagged, paused, terminated)"
        ),
    ] = None,
    session_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    since: Annotated[datetime | None, Query(description="occurred_at >= since")] = None,
    until: Annotated[datetime | None, Query(description="occurred_at < until")] = None,
    organization_id: Annotated[
        uuid.UUID | None, Query(description="SUPER_ADMIN: filter by organization")
    ] = None,
) -> Page[IntegrityEventOut]:
    conditions: list[Any] = []
    org = _org_filter(principal, organization_id)
    if org is not None:
        conditions.append(IntegrityEvent.organization_id == org)
    if status:
        conditions.append(IntegrityEvent.review_status.in_([s.value for s in status]))
    if type:
        conditions.append(IntegrityEvent.type.in_([t.value for t in type]))
    if severity:
        conditions.append(IntegrityEvent.severity.in_([s.value for s in severity]))
    if source:
        conditions.append(IntegrityEvent.source.in_([s.value for s in source]))
    if action:
        conditions.append(IntegrityEvent.action.in_([a.value for a in action]))
    if actionable is True:
        conditions.append(IntegrityEvent.action.in_([a.value for a in ACTIONABLE_ACTIONS]))
    elif actionable is False:
        conditions.append(IntegrityEvent.action.not_in([a.value for a in ACTIONABLE_ACTIONS]))
    if session_id is not None:
        conditions.append(IntegrityEvent.session_id == session_id)
    if candidate_id is not None:
        conditions.append(InterviewSession.candidate_id == candidate_id)
    if since is not None:
        conditions.append(IntegrityEvent.occurred_at >= since)
    if until is not None:
        conditions.append(IntegrityEvent.occurred_at < until)
    total = int(
        await db.scalar(
            _with_refs(select(func.count(IntegrityEvent.id)).select_from(IntegrityEvent)).where(*conditions)
        )
        or 0
    )
    stmt = (
        _with_refs(select(IntegrityEvent, Candidate.id, Candidate.full_name, Job.id, Job.title))
        .where(*conditions)
        .order_by(IntegrityEvent.occurred_at.desc(), IntegrityEvent.id.desc())
    )
    rows = (await db.execute(stmt.limit(page.limit).offset(page.offset))).all()
    items = [
        _event_out(event, candidate=(cand_id, cand_name), job=(job_id, job_title))
        for event, cand_id, cand_name, job_id, job_title in rows
    ]
    return Page[IntegrityEventOut](items=items, total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/integrity/events/review",
    response_model=IntegrityBulkReviewResult,
    summary="Review several observations at once",
    description="Sets the same review status and note on up to 200 observations (each one audited)."
    + _DOC_SUFFIX,
)
async def bulk_review_integrity_events(
    body: IntegrityBulkReview, principal: IntegrityReviewer, db: DbSession, request: Request
) -> IntegrityBulkReviewResult:
    requested = list(dict.fromkeys(body.event_ids))
    events = (await db.scalars(select(IntegrityEvent).where(IntegrityEvent.id.in_(requested)))).all()
    visible = {event.id: event for event in events if _visible(principal, event.organization_id)}
    for event in visible.values():
        await _apply_review(db, principal, request, event, body)
    await db.flush()
    refreshed = await _load_with_refs(db, list(visible))
    return IntegrityBulkReviewResult(
        updated=len(visible),
        items=[refreshed[eid] for eid in requested if eid in refreshed],
        not_found=[eid for eid in requested if eid not in visible],
    )


@router.get(
    "/integrity/events/{event_id}",
    response_model=IntegrityEventOut,
    summary="One observation with its evidence frame",
    description="Includes a short-lived signed URL of the evidence frame when one is kept." + _DOC_SUFFIX,
)
async def get_integrity_event(
    event_id: uuid.UUID, principal: IntegrityReviewer, db: DbSession, request: Request
) -> IntegrityEventOut:
    event = await db.get(IntegrityEvent, event_id)
    if event is None or not _visible(principal, event.organization_id):
        raise NotFound("Integrity event not found.")
    out = (await _load_with_refs(db, [event.id]))[event.id]
    if event.evidence_media_id is not None:
        evidence = await _evidence_urls(db, [event.evidence_media_id], event.organization_id)
        out = _event_out(
            event,
            candidate=(out.candidate.id, out.candidate.full_name) if out.candidate else None,
            job=(out.job.id, out.job.title) if out.job else None,
            evidence=evidence,
        )
        if evidence:
            await audit(
                db,
                principal,
                "media.view",
                resource_type="media",
                resource_id=str(event.evidence_media_id),
                request=request,
                organization_id=event.organization_id,
                details={
                    "kind": "integrity_evidence",
                    "integrity_event_id": str(event.id),
                    "session_id": str(event.session_id),
                },
            )
    return out


async def _apply_review(
    db: DbSession, principal: Principal, request: Request, event: IntegrityEvent, body: IntegrityReviewUpdate
) -> None:
    previous = event.review_status
    event.review_status = body.review_status.value
    event.review_note = body.review_note
    if body.review_status is IntegrityReviewStatus.UNREVIEWED:
        event.reviewed_by_id = None
        event.reviewed_at = None
    else:
        event.reviewed_by_id = principal.user_id
        event.reviewed_at = datetime.now(UTC)
    await audit(
        db,
        principal,
        "integrity.review",
        resource_type="integrity_event",
        resource_id=str(event.id),
        request=request,
        organization_id=event.organization_id,
        details={
            "session_id": str(event.session_id),
            "type": event.type,
            "from": previous,
            "to": body.review_status.value,
            "note_added": body.review_note is not None,
        },
    )


@router.patch(
    "/integrity/events/{event_id}",
    response_model=IntegrityEventOut,
    summary="Review one observation",
    description="Records the reviewer's status and note on an observation (audited)." + _DOC_SUFFIX,
)
async def review_integrity_event(
    event_id: uuid.UUID,
    body: IntegrityReviewUpdate,
    principal: IntegrityReviewer,
    db: DbSession,
    request: Request,
) -> IntegrityEventOut:
    event = await db.get(IntegrityEvent, event_id)
    if event is None or not _visible(principal, event.organization_id):
        raise NotFound("Integrity event not found.")
    await _apply_review(db, principal, request, event, body)
    await db.flush()
    return (await _load_with_refs(db, [event.id]))[event.id]


@router.get(
    "/sessions/{session_id}/integrity",
    response_model=SessionIntegrityOut,
    summary="Session integrity timeline (observations, not determinations)",
    description="All observations of one session in time order, objective counts, the frozen integrity policy and "
    "short-lived signed URLs of evidence frames." + _DOC_SUFFIX,
)
async def session_integrity(
    session_id: uuid.UUID, principal: SessionReader, db: DbSession, request: Request
) -> SessionIntegrityOut:
    row = (
        await db.execute(
            select(
                InterviewSession,
                Interview.policy_snapshot,
                Candidate.id,
                Candidate.full_name,
                Job.id,
                Job.title,
            )
            .join(Interview, Interview.id == InterviewSession.interview_id)
            .outerjoin(Candidate, Candidate.id == InterviewSession.candidate_id)
            .outerjoin(Job, Job.id == InterviewSession.job_id)
            .where(InterviewSession.id == session_id)
        )
    ).first()
    if row is None or not _visible(principal, row[0].organization_id):
        raise NotFound("Session not found.")
    session, snapshot, cand_id, cand_name, job_id, job_title = row
    totals = (
        await db.execute(
            select(
                IntegrityEvent.type,
                IntegrityEvent.severity,
                IntegrityEvent.action,
                IntegrityEvent.review_status,
                func.count(),
                func.coalesce(func.sum(IntegrityEvent.count), 0),
            )
            .where(IntegrityEvent.session_id == session.id)
            .group_by(
                IntegrityEvent.type,
                IntegrityEvent.severity,
                IntegrityEvent.action,
                IntegrityEvent.review_status,
            )
        )
    ).all()
    counts: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_review: dict[str, int] = {}
    total = unreviewed = 0
    reviewable = {a.value for a in REVIEWABLE_ACTIONS}
    for event_type, severity, action, review_status, n, occ in totals:
        n, occ = int(n), int(occ)
        counts[event_type] = counts.get(event_type, 0) + n
        occurrences[event_type] = occurrences.get(event_type, 0) + occ
        by_severity[severity] = by_severity.get(severity, 0) + n
        by_action[action] = by_action.get(action, 0) + n
        by_review[review_status] = by_review.get(review_status, 0) + n
        total += n
        if action in reviewable and review_status == IntegrityReviewStatus.UNREVIEWED.value:
            unreviewed += n
    events = (
        await db.scalars(
            select(IntegrityEvent)
            .where(IntegrityEvent.session_id == session.id)
            .order_by(IntegrityEvent.occurred_at.asc(), IntegrityEvent.id.asc())
            .limit(SESSION_EVENT_LIMIT)
        )
    ).all()
    evidence = await _evidence_urls(
        db, [e.evidence_media_id for e in events if e.evidence_media_id], session.organization_id
    )
    await audit(
        db,
        principal,
        "session.integrity.view",
        resource_type="session",
        resource_id=str(session.id),
        request=request,
        organization_id=session.organization_id,
        details={"events": len(events), "evidence_urls": len(evidence)},
    )
    resolved = resolve_policy(snapshot)
    return SessionIntegrityOut(
        session_id=session.id,
        events=[
            _event_out(e, candidate=(cand_id, cand_name), job=(job_id, job_title), evidence=evidence)
            for e in events
        ],
        counts=dict(sorted(counts.items())),
        occurrences=dict(sorted(occurrences.items())),
        counts_by_severity=by_severity,
        counts_by_action=by_action,
        counts_by_review_status=by_review,
        total_events=total,
        unreviewed_flags=unreviewed,
        truncated=total > len(events),
        policy=resolved.integrity.model_dump(mode="json"),
    )
