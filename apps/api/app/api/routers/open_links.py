"""Staff API for open interview links (OPEN_LINKS.md section 9.2).

Static paths (``/form/...``, ``/registrations``) are declared before
``/{link_id}``. Every query is scoped to the caller's organization (SUPER_ADMIN
may pass ``?organization_id=``); registration routes also require the
registration to belong to the link (404 otherwise). Responses that carry an
access code or a personal link are ``Cache-Control: no-store``. Every state
change is audited without tokens, e-mails, phones or answers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission
from app.core.errors import Conflict, NotFound
from app.core.rbac import Permission, Principal
from app.db.models import Candidate, InterviewLink, LinkRegistration
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.open_links import (
    AccessCodeRequest,
    AdvancedItem,
    AdvanceRequest,
    AdvanceResponse,
    CancelRegistrationRequest,
    FlagReviewRequest,
    FormCatalog,
    FormField,
    FormIssue,
    FormLimits,
    FormSchemaOut,
    FormValidateRequest,
    FormValidateResponse,
    FormWarning,
    LinkStatusLiteral,
    LinkStatusRequest,
    OpenLink,
    OpenLinkCreate,
    OpenLinkSummary,
    OpenLinkUpdate,
    OpenLinkWithCode,
    PersonalLinkResponse,
    Registration,
    RegistrationDetail,
    RegistrationStatusLiteral,
    RegistrationWithLink,
    RetakeRequest,
    SkippedItem,
    UnblockRequest,
    UnblockResponse,
)
from app.services.open_links import export as csv_export
from app.services.open_links import links as svc
from app.services.open_links import progression
from app.services.open_links.constants import (
    BUILTIN_FIELDS,
    MAX_FIELDS,
    MAX_OPTIONS,
    FieldType,
    FlagReview,
    RegistrationStatus,
    UnlockedBy,
    public_builtin,
)
from app.services.open_links.fairness import check_form
from app.services.open_links.form_schema import default_schema, normalize_schema
from app.services.open_links.personal import clear_lock, issue_personal_token
from app.services.open_links.registration import unblock_registration
from app.services.open_links.registrations import (
    candidate_registrations_query,
    get_registration,
    registration_query,
    serialize_detail,
    serialize_registrations,
)

router = APIRouter(prefix="/open-links", tags=["open-links"], responses=ERROR_RESPONSES)

JobsReader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Reader = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_READ))]
Inviter = Annotated[Principal, Depends(require_permission(Permission.INTERVIEWS_INVITE))]
Writer = Annotated[Principal, Depends(require_permission(Permission.CANDIDATES_WRITE))]
RS = RegistrationStatus


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


# ================================================================== form


@router.get("/form/catalog", response_model=FormCatalog, summary="Registration form catalog")
async def form_catalog(principal: JobsReader) -> FormCatalog:
    return FormCatalog(
        builtins=[FormField.model_validate(public_builtin(f)) for f in BUILTIN_FIELDS],
        types=[t.value for t in FieldType],  # type: ignore[misc]
        limits=FormLimits(max_fields=MAX_FIELDS, max_options=MAX_OPTIONS),
        default_schema=FormSchemaOut.model_validate(default_schema()),
    )


@router.post("/form/validate", response_model=FormValidateResponse, summary="Check a registration form")
async def form_validate(body: FormValidateRequest, principal: Inviter) -> FormValidateResponse:
    normalized = normalize_schema(body.form_schema.model_dump(), version=body.form_schema.version or 1)
    report = check_form(normalized, body.acknowledged_warnings)
    return FormValidateResponse(
        ok=not report.errors and not report.unacknowledged,
        errors=[FormIssue.model_validate(e) for e in report.errors],
        warnings=[FormWarning.model_validate(w) for w in report.warnings],
        normalized=FormSchemaOut.model_validate(normalized),
    )


# =========================================================== registrations (static)


@router.get(
    "/registrations",
    response_model=Page[RegistrationWithLink],
    summary="Registrations of one candidate (all links)",
)
async def registrations_by_candidate(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    candidate_id: Annotated[uuid.UUID, Query()],
) -> Page[RegistrationWithLink]:
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None or candidate.organization_id != org_id:
        return Page[RegistrationWithLink](items=[], total=0, limit=page.limit, offset=page.offset)
    stmt = candidate_registrations_query(org_id, candidate_id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await db.scalars(page.apply(stmt.order_by(LinkRegistration.created_at.desc())))).all())
    if await progression.sync_stale(db, rows):
        rows = list((await db.scalars(page.apply(stmt.order_by(LinkRegistration.created_at.desc())))).all())
    items = await serialize_registrations(db, rows, with_link=True)
    return Page[RegistrationWithLink](
        items=[i for i in items if isinstance(i, RegistrationWithLink)],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


# ================================================================ links


@router.get("", response_model=Page[OpenLinkSummary], summary="List interview links")
async def list_links(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    job_id: uuid.UUID | None = None,
    status_filter: Annotated[LinkStatusLiteral | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> Page[OpenLinkSummary]:
    stmt = select(InterviewLink).where(InterviewLink.organization_id == org_id)
    if job_id is not None:
        stmt = stmt.where(InterviewLink.job_id == job_id)
    if status_filter is not None:
        stmt = stmt.where(InterviewLink.status == status_filter)
    if q and q.strip():
        term = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(InterviewLink.name.ilike(term, escape="\\"))
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        await db.scalars(page.apply(stmt.order_by(InterviewLink.created_at.desc(), InterviewLink.id)))
    ).all()
    items = await svc.serialize_links(db, rows)
    return Page[OpenLinkSummary](items=list(items), total=total, limit=page.limit, offset=page.offset)


@router.post(
    "", response_model=OpenLinkWithCode, status_code=status.HTTP_201_CREATED, summary="Create a link"
)
async def create_link(
    body: OpenLinkCreate,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> OpenLinkWithCode:
    link, code = await svc.create_link(db, organization_id=org_id, user_id=principal.user_id, body=body)
    await audit(
        db,
        principal,
        "link.create",
        resource_type="interview_link",
        resource_id=link.id,
        request=request,
        organization_id=org_id,
        details={
            "job_id": str(link.job_id),
            "rounds": len(link.round_ids),
            "progression": link.progression,
            "access_code": code is not None,
            "form_version": link.form_schema.get("version"),
        },
    )
    if link.acknowledged_warnings:
        await audit(
            db,
            principal,
            "link.form_warnings_acknowledged",
            resource_type="interview_link",
            resource_id=link.id,
            request=request,
            organization_id=org_id,
            details={"warnings": list(link.acknowledged_warnings)},
        )
    _no_store(response)
    return OpenLinkWithCode(link=await svc.serialize_link(db, link), access_code=code)


@router.get("/{link_id}", response_model=OpenLink, summary="Get a link")
async def get_link(link_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession) -> OpenLink:
    return await svc.serialize_link(db, await svc.get_link(db, link_id, org_id))


@router.patch("/{link_id}", response_model=OpenLink, summary="Update a link")
async def update_link(
    link_id: uuid.UUID,
    body: OpenLinkUpdate,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> OpenLink:
    link = await svc.get_link(db, link_id, org_id, for_update=True)
    result = await svc.update_link(db, link, body, user_id=principal.user_id)
    if result["changed"]:
        await audit(
            db,
            principal,
            "link.update",
            resource_type="interview_link",
            resource_id=link.id,
            request=request,
            organization_id=org_id,
            details={"changed": result["changed"], "form_version": result["form_version"]},
        )
    if result["new_acks"]:
        await audit(
            db,
            principal,
            "link.form_warnings_acknowledged",
            resource_type="interview_link",
            resource_id=link.id,
            request=request,
            organization_id=org_id,
            details={"warnings": result["new_acks"]},
        )
    return await svc.serialize_link(db, link)


@router.post("/{link_id}/status", response_model=OpenLink, summary="Pause, resume or close a link")
async def set_link_status(
    link_id: uuid.UUID,
    body: LinkStatusRequest,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> OpenLink:
    link = await svc.get_link(db, link_id, org_id, for_update=True)
    previous = await svc.set_status(db, link, body.status)
    if previous is not None:
        await audit(
            db,
            principal,
            "link.status",
            resource_type="interview_link",
            resource_id=link.id,
            request=request,
            organization_id=org_id,
            details={"from": previous, "to": link.status},
        )
    return await svc.serialize_link(db, link)


@router.post("/{link_id}/regenerate-token", response_model=OpenLink, summary="Replace the shareable link")
async def regenerate_token(
    link_id: uuid.UUID, request: Request, principal: Inviter, org_id: OrgScope, db: DbSession
) -> OpenLink:
    link = await svc.get_link(db, link_id, org_id, for_update=True)
    await svc.regenerate_token(db, link)
    await audit(
        db,
        principal,
        "link.regenerate_token",
        resource_type="interview_link",
        resource_id=link.id,
        request=request,
        organization_id=org_id,
    )
    return await svc.serialize_link(db, link)


@router.post(
    "/{link_id}/access-code", response_model=OpenLinkWithCode, summary="Set or remove the access code"
)
async def set_access_code(
    link_id: uuid.UUID,
    body: AccessCodeRequest,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> OpenLinkWithCode:
    link = await svc.get_link(db, link_id, org_id, for_update=True)
    code = await svc.set_access_code(db, link, body.enabled)
    await audit(
        db,
        principal,
        "link.access_code",
        resource_type="interview_link",
        resource_id=link.id,
        request=request,
        organization_id=org_id,
        details={"enabled": body.enabled},
    )
    _no_store(response)
    return OpenLinkWithCode(link=await svc.serialize_link(db, link), access_code=code)


# ======================================================= registrations


@router.get("/{link_id}/registrations", response_model=Page[Registration], summary="Registrations of a link")
async def list_registrations(
    link_id: uuid.UUID,
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    status_filter: Annotated[RegistrationStatusLiteral | None, Query(alias="status")] = None,
    round_index: Annotated[int | None, Query(ge=0, le=20)] = None,
    needs_review: bool | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> Page[Registration]:
    link = await svc.get_link(db, link_id, org_id)
    stmt = registration_query(
        link.id, status=status_filter, round_index=round_index, needs_review=needs_review, q=q
    )
    ordered = stmt.order_by(LinkRegistration.created_at.desc(), LinkRegistration.id)
    rows = list((await db.scalars(page.apply(ordered))).all())
    if await progression.sync_stale(db, rows):
        rows = list((await db.scalars(page.apply(ordered))).all())
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    items = await serialize_registrations(db, rows, links={link.id: link})
    return Page[Registration](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{link_id}/registrations/export", summary="Export registrations as CSV")
async def export_registrations(
    link_id: uuid.UUID,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    status_filter: Annotated[RegistrationStatusLiteral | None, Query(alias="status")] = None,
    needs_review: bool | None = None,
) -> Response:
    link = await svc.get_link(db, link_id, org_id)
    stmt = registration_query(link.id, status=status_filter, needs_review=needs_review)
    rows = list(
        (await db.scalars(stmt.order_by(LinkRegistration.created_at, LinkRegistration.id).limit(20000))).all()
    )
    views = await serialize_registrations(db, rows, links={link.id: link})
    body, count = csv_export.build_csv(link, rows, views)
    await audit(
        db,
        principal,
        "link.registrations_export",
        resource_type="interview_link",
        resource_id=link.id,
        request=request,
        organization_id=org_id,
        details={"filters": {"status": status_filter, "needs_review": needs_review}, "rows": count},
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{csv_export.filename(link)}"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{link_id}/registrations/advance", response_model=AdvanceResponse, summary="Advance to the next round"
)
async def advance_registrations(
    link_id: uuid.UUID,
    body: AdvanceRequest,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> AdvanceResponse:
    link = await svc.get_link(db, link_id, org_id)
    result = await progression.advance(db, link, body.registration_ids, principal=principal, note=body.note)
    if result.advanced:
        await audit(
            db,
            principal,
            "link.advance",
            resource_type="interview_link",
            resource_id=link.id,
            request=request,
            organization_id=org_id,
            details={
                "registration_ids": [str(a["registration_id"]) for a in result.advanced],
                "count": len(result.advanced),
                "skipped": len(result.skipped),
            },
        )
        for item in result.advanced:
            await audit(
                db,
                principal,
                "link.round_unlocked",
                resource_type="link_registration",
                resource_id=item["registration_id"],
                request=request,
                organization_id=org_id,
                details={
                    "interview_id": str(item["interview_id"]),
                    "unlocked_by": UnlockedBy.RECRUITER.value,
                },
            )
    return AdvanceResponse(
        advanced=[AdvancedItem.model_validate(a) for a in result.advanced],
        skipped=[SkippedItem.model_validate(s) for s in result.skipped],
    )


@router.get(
    "/{link_id}/registrations/{registration_id}",
    response_model=RegistrationDetail,
    summary="One registration",
)
async def get_registration_detail(
    link_id: uuid.UUID, registration_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> RegistrationDetail:
    link = await svc.get_link(db, link_id, org_id)
    registration = await get_registration(db, link, registration_id)
    await progression.sync_stale(db, [registration])
    return await serialize_detail(db, link, registration)


async def _locked_registration(
    db: DbSession, link: InterviewLink, registration_id: uuid.UUID
) -> LinkRegistration:
    await get_registration(db, link, registration_id)
    registration = await progression.lock_registration(db, registration_id)
    if registration is None:
        raise NotFound("Registration not found.")
    return registration


@router.post(
    "/{link_id}/registrations/{registration_id}/retake",
    response_model=RegistrationDetail,
    summary="Allow another attempt of the current round",
)
async def retake_round(
    link_id: uuid.UUID,
    registration_id: uuid.UUID,
    body: RetakeRequest,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> RegistrationDetail:
    link = await svc.get_link(db, link_id, org_id)
    registration = await _locked_registration(db, link, registration_id)
    await progression.sync_registration(db, registration.id)
    interview = await progression.retake(
        db, link, registration, round_index=body.round_index, reason=body.reason, principal=principal
    )
    await audit(
        db,
        principal,
        "link.retake",
        resource_type="link_registration",
        resource_id=registration.id,
        request=request,
        organization_id=org_id,
        details={"round_index": body.round_index, "interview_id": str(interview.id), "reason": body.reason},
    )
    return await serialize_detail(db, link, registration)


@router.post(
    "/{link_id}/registrations/{registration_id}/cancel",
    response_model=RegistrationDetail,
    summary="Cancel a registration",
)
async def cancel_registration(
    link_id: uuid.UUID,
    registration_id: uuid.UUID,
    request: Request,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
    body: CancelRegistrationRequest | None = None,
) -> RegistrationDetail:
    link = await svc.get_link(db, link_id, org_id)
    registration = await _locked_registration(db, link, registration_id)
    previous = registration.status
    cancelled = await progression.cancel_registration(db, registration, reason=body.reason if body else None)
    await audit(
        db,
        principal,
        "link.registration_cancel",
        resource_type="link_registration",
        resource_id=registration.id,
        request=request,
        organization_id=org_id,
        details={"previous_status": previous, "interviews_cancelled": cancelled},
    )
    return await serialize_detail(db, link, registration)


@router.post(
    "/{link_id}/registrations/{registration_id}/unblock",
    response_model=UnblockResponse,
    summary="Allow a blocked registration",
)
async def unblock_registration_endpoint(
    link_id: uuid.UUID,
    registration_id: uuid.UUID,
    body: UnblockRequest,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> UnblockResponse:
    link = await svc.get_link(db, link_id, org_id)
    registration = await _locked_registration(db, link, registration_id)
    personal = await unblock_registration(db, link, registration, principal=principal)
    await audit(
        db,
        principal,
        "link.registration_unblock",
        resource_type="link_registration",
        resource_id=registration.id,
        request=request,
        organization_id=org_id,
        details={"reason": body.reason},
    )
    _no_store(response)
    return UnblockResponse(registration=await serialize_detail(db, link, registration), personal_url=personal)


@router.post(
    "/{link_id}/registrations/{registration_id}/personal-link",
    response_model=PersonalLinkResponse,
    summary="Reissue the personal link",
)
async def reissue_personal_link(
    link_id: uuid.UUID,
    registration_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Inviter,
    org_id: OrgScope,
    db: DbSession,
) -> PersonalLinkResponse:
    link = await svc.get_link(db, link_id, org_id)
    registration = await _locked_registration(db, link, registration_id)
    if registration.status == RS.BLOCKED.value or registration.candidate_id is None:
        raise Conflict("A blocked registration has no personal link.", code="invalid_status")
    url = issue_personal_token(registration)
    await clear_lock(registration.id)
    await audit(
        db,
        principal,
        "link.personal_link_reissued",
        resource_type="link_registration",
        resource_id=registration.id,
        request=request,
        organization_id=org_id,
    )
    await db.flush()
    _no_store(response)
    issued = registration.personal_token_issued_at or datetime.now(UTC)
    return PersonalLinkResponse(personal_url=url, issued_at=issued)


@router.post(
    "/{link_id}/registrations/{registration_id}/flag-review",
    response_model=RegistrationDetail,
    summary="Clear or confirm duplicate flags",
)
async def review_flags(
    link_id: uuid.UUID,
    registration_id: uuid.UUID,
    body: FlagReviewRequest,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> RegistrationDetail:
    link = await svc.get_link(db, link_id, org_id)
    registration = await _locked_registration(db, link, registration_id)
    if not registration.flags:
        raise Conflict("This registration has no flags to review.", code="no_open_flags")
    registration.flag_review = FlagReview(body.decision).value
    registration.flag_reviewed_by_id = principal.user_id
    registration.flag_reviewed_at = datetime.now(UTC)
    registration.flag_review_note = body.note
    await audit(
        db,
        principal,
        "link.flag_review",
        resource_type="link_registration",
        resource_id=registration.id,
        request=request,
        organization_id=org_id,
        details={"decision": body.decision},
    )
    await db.flush()
    return await serialize_detail(db, link, registration)
