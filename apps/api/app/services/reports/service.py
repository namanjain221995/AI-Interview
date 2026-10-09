"""Report generation pipeline (``report.generate``) and candidate release.

Phases (no database transaction is held while the model writes):

1. *reserve*  — serialised per session: final score recompute, then new
   ``reports`` rows (candidate + recruiter, next version, status
   ``generating``) tagged with the job id so a retry reuses them.
2. *assemble* — deterministic document (read-only) and the two narratives.
3. *finish*   — candidate/recruiter content, HTML artifacts stored through
   ``app.services.storage`` (``media_assets`` kind ``report_artifact``), rows
   marked ``ready``, candidate report released when the policy's
   ``candidate_report_mode`` is ``immediate``; then ``report.ready`` is
   published on ``session:{id}:bus``.

Regeneration always creates a new version; earlier versions stay readable.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound
from app.core.lifecycle import run_after_commit
from app.core.logging import get_logger
from app.core.queue import publish_session_event
from app.core.rbac import Principal
from app.db.models import MediaAsset, Report
from app.db.session import session_scope
from app.domain.enums import CandidateReportMode, MediaKind, ReportStatus, ReportType
from app.services.reports.assembly import ReportData, assemble_report_data, candidate_view
from app.services.reports.narratives import NarrativeOutcome, generate_narratives
from app.services.reports.queries import (
    as_uuid,
    latest_ready_report,
    report_retention_days,
    resolve_candidate_report_mode,
    review_count,
)
from app.services.reports.rendering import render_report_html
from app.services.scoring.persistence import (
    advisory_lock_key,
    compute_session_scores,
    load_session,
    recompute_session_scores,
)
from app.services.storage.media import delete_media, store_media

log = get_logger(__name__)

REPORT_TYPES = (ReportType.CANDIDATE, ReportType.RECRUITER)


@dataclass(slots=True)
class GeneratedReports:
    session_id: uuid.UUID
    reports: dict[str, uuid.UUID]
    versions: dict[str, int]
    candidate_released: bool
    narrative_source: dict[str, str]
    duration_ms: float
    artifacts: dict[str, uuid.UUID] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "reports": {k: str(v) for k, v in self.reports.items()},
            "versions": self.versions,
            "candidate_released": self.candidate_released,
            "narrative_source": self.narrative_source,
            "duration_ms": round(self.duration_ms, 1),
            "artifacts": {k: str(v) for k, v in self.artifacts.items()},
        }


def artifact_key(organization_id: uuid.UUID, session_id: uuid.UUID, report_type: str, version: int) -> str:
    return f"orgs/{organization_id}/sessions/{session_id}/reports/{report_type}-v{version}.html"


# --------------------------------------------------------------------------
# Phase 1: reserve
# --------------------------------------------------------------------------


async def reserve_report_rows(
    db: AsyncSession, session_id: uuid.UUID, *, job_id: str | None, reason: str
) -> dict[str, Report]:
    """Rows for this generation (reused when the same job retries)."""
    await db.execute(select(func.pg_advisory_xact_lock(advisory_lock_key("reports", session_id))))
    rows: dict[str, Report] = {}
    if job_id:
        existing = (
            await db.scalars(
                select(Report).where(
                    Report.session_id == session_id,
                    Report.content["job_id"].astext == job_id,
                    Report.status != ReportStatus.READY.value,
                )
            )
        ).all()
        rows = {r.type: r for r in existing}
    for report_type in REPORT_TYPES:
        if report_type.value in rows:
            row = rows[report_type.value]
            row.status = ReportStatus.GENERATING.value
            row.error = None
            continue
        version = int(
            await db.scalar(
                select(func.coalesce(func.max(Report.version), 0)).where(
                    Report.session_id == session_id, Report.type == report_type.value
                )
            )
            or 0
        )
        row = Report(
            id=uuid.uuid4(),
            session_id=session_id,
            type=report_type.value,
            status=ReportStatus.GENERATING.value,
            version=version + 1,
            content={"job_id": job_id, "reason": reason} if job_id else {"reason": reason},
        )
        db.add(row)
        rows[report_type.value] = row
    await db.flush()
    return rows


async def mark_reports_failed(report_ids: list[uuid.UUID], error: str) -> None:
    async with session_scope() as db:
        await db.execute(
            update(Report)
            .where(Report.id.in_(report_ids), Report.status != ReportStatus.READY.value)
            .values(status=ReportStatus.FAILED.value, error=error[:2000])
        )


# --------------------------------------------------------------------------
# Phase 3: finish
# --------------------------------------------------------------------------


async def _store_artifact(
    db: AsyncSession,
    *,
    row: Report,
    organization_id: uuid.UUID,
    candidate_id: uuid.UUID,
    html: str,
    retention_days: int,
) -> MediaAsset:
    key = artifact_key(organization_id, row.session_id, row.type, row.version)
    data = html.encode("utf-8")
    kwargs: dict[str, Any] = {
        "organization_id": organization_id,
        "kind": MediaKind.REPORT_ARTIFACT,
        "key": key,
        "data": data,
        "content_type": "text/html",
        "session_id": row.session_id,
        "candidate_id": candidate_id,
        "retention_days": retention_days,
        "details": {"report_id": str(row.id), "report_type": row.type, "version": row.version},
    }
    try:
        return await store_media(db, **kwargs)
    except Conflict:
        # A previous attempt of this exact version left an artifact: replace it.
        existing = await db.scalar(select(MediaAsset).where(MediaAsset.storage_key == key))
        if existing is not None:
            await delete_media(db, existing, reason="report_regenerated")
        return await store_media(db, **kwargs)


def build_contents(data: ReportData, narratives: NarrativeOutcome) -> tuple[dict[str, Any], dict[str, Any]]:
    recruiter = dict(data.document)
    recruiter["narrative"] = narratives.recruiter
    recruiter["narrative_source"] = narratives.source.get("recruiter", "model")
    recruiter["narrative_validation"] = narratives.stats.get("recruiter", {})
    recruiter["candidate_narrative_validation"] = narratives.stats.get("candidate", {})
    recruiter["candidate_narrative_source"] = narratives.source.get("candidate", "model")
    recruiter["narrative_errors"] = dict(narratives.errors)
    suggested = narratives.recruiter.get("suggested_recommendation")
    recruiter["narrative_suggested_recommendation"] = suggested
    recruiter["narrative_disagrees"] = bool(suggested and suggested != recruiter["recommendation"]["value"])
    candidate = candidate_view(data.document, narratives.candidate)
    candidate["narrative_source"] = narratives.source.get("candidate", "model")
    return candidate, recruiter


async def finish_reports(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    report_ids: dict[str, uuid.UUID],
    data: ReportData,
    narratives: NarrativeOutcome,
    job_id: str | None,
    reason: str,
    requested_by: uuid.UUID | None,
) -> tuple[dict[str, Report], dict[str, uuid.UUID], bool]:
    session = await load_session(db, session_id)
    rows = {
        r.type: r
        for r in (await db.scalars(select(Report).where(Report.id.in_(list(report_ids.values()))))).all()
    }
    if set(rows) != {t.value for t in REPORT_TYPES}:
        raise NotFound("Reserved report rows disappeared.")
    candidate_content, recruiter_content = build_contents(data, narratives)
    now = datetime.now(UTC)
    retention = await report_retention_days(db, session)
    mode = await resolve_candidate_report_mode(db, session)
    contents = {ReportType.CANDIDATE.value: candidate_content, ReportType.RECRUITER.value: recruiter_content}
    artifacts: dict[str, uuid.UUID] = {}
    overall = data.document["scores"]["overall"]
    for report_type, row in rows.items():
        content = contents[report_type]
        content["generated_at"] = now.isoformat()
        content["version"] = row.version
        if report_type == ReportType.RECRUITER.value:
            content["generation"] = {"job_id": job_id, "reason": reason}
        meta = {"version": row.version, "report_id": str(row.id), "generated_at": now.isoformat()}
        html = render_report_html(report_type, content, meta)
        asset = await _store_artifact(
            db,
            row=row,
            organization_id=session.organization_id,
            candidate_id=session.candidate_id,
            html=html,
            retention_days=retention,
        )
        artifacts[report_type] = asset.id
        row.content = content
        row.status = ReportStatus.READY.value
        row.overall_score = overall["score"]
        row.confidence = overall["confidence"]
        row.recommendation = (
            data.document["recommendation"]["value"] if report_type == ReportType.RECRUITER.value else None
        )
        row.artifact_storage_key = asset.storage_key
        row.model_call_ids = list(narratives.model_call_ids)
        row.generated_at = now
        row.error = None
    released = False
    if mode is CandidateReportMode.IMMEDIATE:
        candidate_row = rows[ReportType.CANDIDATE.value]
        candidate_row.released_to_candidate = True
        candidate_row.released_at = now
        candidate_row.released_by_id = None
        released = True
    await audit(
        db,
        None,
        "report.generate",
        resource_type="session",
        resource_id=str(session_id),
        organization_id=session.organization_id,
        details={
            "versions": {t: r.version for t, r in rows.items()},
            "reason": reason,
            "requested_by": str(requested_by) if requested_by else None,
            "candidate_released": released,
            "candidate_report_mode": mode.value,
            "narrative_source": narratives.source,
        },
    )
    await db.flush()
    return rows, artifacts, released


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


async def generate_reports(
    session_id: uuid.UUID | str,
    *,
    reason: str = "session_completed",
    job_id: str | None = None,
    requested_by: uuid.UUID | str | None = None,
    allow_fallback: bool = True,
    use_model: bool = True,
) -> GeneratedReports:
    """Generate (or regenerate) both reports of a session.

    ``allow_fallback=False`` lets retryable model outages propagate (the job
    retries); the reserved rows are then reused by the next attempt.
    """
    started = time.perf_counter()
    sid = as_uuid(session_id)
    requester = as_uuid(requested_by) if requested_by else None
    async with session_scope() as db:
        await load_session(db, sid)
        await recompute_session_scores(db, sid, final=True, trigger="report")
        reserved = await reserve_report_rows(db, sid, job_id=job_id, reason=reason)
        report_ids = {t: r.id for t, r in reserved.items()}
        versions = {t: r.version for t, r in reserved.items()}
    try:
        async with session_scope() as db:
            computation = await compute_session_scores(db, sid)
            data = await assemble_report_data(db, computation)
        narratives = await generate_narratives(data, allow_fallback=allow_fallback, use_model=use_model)
        async with session_scope() as db:
            rows, artifacts, released = await finish_reports(
                db,
                session_id=sid,
                report_ids=report_ids,
                data=data,
                narratives=narratives,
                job_id=job_id,
                reason=reason,
                requested_by=requester,
            )
    except Exception as exc:
        if allow_fallback:
            await mark_reports_failed(list(report_ids.values()), f"{type(exc).__name__}: {exc}")
        raise
    result = GeneratedReports(
        session_id=sid,
        reports=report_ids,
        versions=versions,
        candidate_released=released,
        narrative_source=narratives.source,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        artifacts=artifacts,
    )
    await publish_session_event(
        sid,
        {
            "type": "report.ready",
            "session_id": str(sid),
            "versions": versions,
            "candidate_released": released,
            "candidate_report_available": released,
            "narrative_source": narratives.source,
        },
    )
    log.info(
        "reports.generated",
        session_id=str(sid),
        versions=versions,
        candidate_released=released,
        narrative_source=narratives.source,
        duration_ms=round(result.duration_ms, 1),
        narrative_latency_ms=narratives.latency_ms,
    )
    return result


# --------------------------------------------------------------------------
# Candidate release (after_review / never modes)
# --------------------------------------------------------------------------


class ReleaseNotApplicable(Conflict):
    code = "release_not_applicable"


async def release_candidate_report(
    db: AsyncSession,
    principal: Principal,
    session_id: uuid.UUID,
    *,
    version: int | None = None,
    acknowledge_policy_override: bool = False,
    note: str | None = None,
    request: Any = None,
) -> Report:
    """Release the candidate report by an authorized reviewer.

    * ``immediate`` mode: reports are released automatically -> 409.
    * ``after_review`` mode: a human review decision must exist first.
    * ``never`` mode: an explicit ``acknowledge_policy_override`` is required.
    """
    session = await load_session(db, session_id)
    mode = await resolve_candidate_report_mode(db, session)
    if mode is CandidateReportMode.IMMEDIATE:
        raise ReleaseNotApplicable(
            "Candidate reports for this interview are released automatically when they are generated.",
            details={"candidate_report_mode": mode.value},
        )
    if mode is CandidateReportMode.AFTER_REVIEW and await review_count(db, session_id) == 0:
        raise Conflict(
            "Record a human review decision before releasing the candidate report.",
            code="review_required",
            details={"candidate_report_mode": mode.value},
        )
    if mode is CandidateReportMode.NEVER and not acknowledge_policy_override:
        raise Conflict(
            "This interview's policy does not share reports with candidates. Confirm the policy override to release it.",
            code="policy_override_required",
            details={"candidate_report_mode": mode.value},
        )
    if version is not None:
        report = await db.scalar(
            select(Report).where(
                Report.session_id == session_id,
                Report.type == ReportType.CANDIDATE.value,
                Report.version == version,
            )
        )
        if report is None:
            raise NotFound("Candidate report version not found.")
    else:
        report = await latest_ready_report(db, session_id, ReportType.CANDIDATE)
        if report is None:
            raise NotFound("No candidate report is ready yet.", code="report_not_ready")
    if report.status != ReportStatus.READY.value:
        raise Conflict("The candidate report is not ready yet.", code="report_not_ready")
    already = report.released_to_candidate
    if not already:
        report.released_to_candidate = True
        report.released_at = datetime.now(UTC)
        report.released_by_id = principal.user_id
    await audit(
        db,
        principal,
        "report.release",
        resource_type="report",
        resource_id=str(report.id),
        organization_id=session.organization_id,
        request=request,
        details={
            "session_id": str(session_id),
            "version": report.version,
            "candidate_report_mode": mode.value,
            "policy_override": mode is CandidateReportMode.NEVER,
            "already_released": already,
            "note": note,
        },
    )
    await db.flush()
    if not already:
        released_version = report.version

        async def _announce() -> None:
            await publish_session_event(
                session_id,
                {
                    "type": "report.released",
                    "session_id": str(session_id),
                    "version": released_version,
                    "candidate_report_available": True,
                },
            )

        run_after_commit(db, _announce)
    return report
