"""Session reports: read (audited), regenerate, release to the candidate.

Report generation itself runs in the worker (``report.generate``); every
regeneration produces a new version and earlier versions stay readable.
Candidate release is only meaningful for the ``after_review`` (requires a
recorded human review) and ``never`` (requires an explicit override) policy
modes; ``immediate`` reports are released automatically.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.audit import audit
from app.core.deps import DbSession, require_permission
from app.core.errors import Conflict, NotFound
from app.core.queue import enqueue
from app.core.rbac import Permission, Principal
from app.domain.enums import ReportType, SessionStatus
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.reports import (
    RegenerateResponse,
    ReleaseRequest,
    ReportListOut,
    ReportOut,
    ReportVersionOut,
)
from app.services.reports.queries import (
    REPORTABLE_STATUSES,
    get_session_for_principal,
    latest_report,
    media_ids_for_keys,
    report_versions,
    resolve_candidate_report_mode,
)
from app.services.reports.service import release_candidate_report

router = APIRouter(tags=["reports"], responses=ERROR_RESPONSES)

ReportReader = Annotated[Principal, Depends(require_permission(Permission.REPORTS_READ))]
ReportRegenerator = Annotated[
    Principal, Depends(require_permission(Permission.REPORTS_READ, Permission.REVIEWS_WRITE))
]
ReportReleaser = Annotated[
    Principal, Depends(require_permission(Permission.REPORTS_READ, Permission.REPORTS_RELEASE))
]


async def _report_out(db: DbSession, report, *, versions, mode: str | None) -> ReportOut:  # noqa: ANN001
    media = await media_ids_for_keys(db, [report.artifact_storage_key])
    out = ReportOut.model_validate(report)
    out.artifact_media_id = media.get(report.artifact_storage_key or "")
    out.candidate_report_mode = mode
    out.versions = [ReportVersionOut.model_validate(v) for v in versions]
    return out


@router.get("/sessions/{session_id}/reports", response_model=ReportListOut, summary="List report versions")
async def list_reports(session_id: uuid.UUID, principal: ReportReader, db: DbSession) -> ReportListOut:
    session = await get_session_for_principal(db, principal, session_id)
    rows = await report_versions(db, session.id)
    return ReportListOut(
        session_id=session.id, items=[ReportVersionOut.model_validate(r) for r in rows], total=len(rows)
    )


@router.get(
    "/sessions/{session_id}/reports/{report_type}",
    response_model=ReportOut,
    summary="Get the newest (or a specific) report version (audited)",
)
async def get_report(
    session_id: uuid.UUID,
    report_type: ReportType,
    principal: ReportReader,
    db: DbSession,
    request: Request,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ReportOut:
    session = await get_session_for_principal(db, principal, session_id)
    report = await latest_report(db, session.id, report_type, version=version)
    if report is None:
        raise NotFound("No report has been generated for this session yet.", code="report_not_ready")
    versions = await report_versions(db, session.id, report_type)
    mode = (await resolve_candidate_report_mode(db, session)).value
    await audit(
        db,
        principal,
        "report.view",
        resource_type="report",
        resource_id=str(report.id),
        organization_id=session.organization_id,
        request=request,
        details={"session_id": str(session.id), "type": report_type.value, "version": report.version},
    )
    return await _report_out(db, report, versions=versions, mode=mode)


@router.post(
    "/sessions/{session_id}/reports/regenerate",
    response_model=RegenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a new version of both reports",
)
async def regenerate_reports(
    session_id: uuid.UUID, principal: ReportRegenerator, db: DbSession, request: Request
) -> RegenerateResponse:
    session = await get_session_for_principal(db, principal, session_id)
    if session.status not in REPORTABLE_STATUSES:
        raise Conflict(
            "Reports can be generated once the interview has ended.",
            code="session_not_finished",
            details={"status": session.status},
        )
    # A failed session (technical failure) gets a report of what was collected;
    # the reason is recorded on the report versions and in the audit trail.
    reason = "regenerate_after_failure" if session.status == SessionStatus.FAILED.value else "regenerate"
    job_id = await enqueue(
        "report.generate",
        {"session_id": str(session.id), "reason": reason, "requested_by": str(principal.user_id)},
        dedupe_key=f"report.generate:{session.id}:regenerate",
        db=db,
    )
    await audit(
        db,
        principal,
        "report.regenerate",
        resource_type="session",
        resource_id=str(session.id),
        organization_id=session.organization_id,
        request=request,
        details={"job_id": job_id, "reason": reason, "session_status": session.status},
    )
    return RegenerateResponse(session_id=session.id, job_id=uuid.UUID(job_id))


@router.post(
    "/sessions/{session_id}/reports/candidate/release",
    response_model=ReportOut,
    summary="Release the candidate report (after_review / never policy modes)",
)
async def release_report(
    session_id: uuid.UUID,
    principal: ReportReleaser,
    db: DbSession,
    request: Request,
    body: ReleaseRequest | None = None,
) -> ReportOut:
    body = body or ReleaseRequest()
    session = await get_session_for_principal(db, principal, session_id)
    report = await release_candidate_report(
        db,
        principal,
        session.id,
        version=body.version,
        acknowledge_policy_override=body.acknowledge_policy_override,
        note=body.note,
        request=request,
    )
    await db.refresh(report)  # server-side updated_at after the release flush
    versions = await report_versions(db, session.id, ReportType.CANDIDATE)
    mode = (await resolve_candidate_report_mode(db, session)).value
    return await _report_out(db, report, versions=versions, mode=mode)
