"""Resume service: the implementation of :class:`app.services.resume.contracts.ResumeService`.

Used by the recruiter API (``POST /candidates/{id}/resumes``) and the
candidate portal (``POST /portal/interviews/{id}/resume``)::

    from app.services.resume.service import get_resume_service

    resume = await get_resume_service().accept_upload(db, organization_id=..., candidate_id=..., ...)

``accept_upload`` validates synchronously (type sniffing, structure, active
content, size, malware scan per ``MALWARE_SCAN_MODE``), stores the file
(``media_assets`` row, retention from the policy), creates the
``candidate_resumes`` row and enqueues ``resume.process`` in the caller's
transaction (dispatched after commit). It returns in tens of milliseconds;
everything slow runs in the background jobs.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.core.queue import enqueue
from app.db.models import Candidate, CandidateProfile, CandidateResume, Interview, Job, MediaAsset
from app.domain.enums import MediaKind, ResumeStatus
from app.schemas.policies import PolicySnapshot
from app.services.identity import get_organization_settings
from app.services.jobs.policy import build_policy_snapshot
from app.services.resume import progress as progress_mod
from app.services.resume.contracts import ResumeProgress, ResumeService
from app.services.resume.malware import scan_upload
from app.services.resume.quick import CANDIDATE_SECTIONS, QUICK_STATUS
from app.services.resume.skills import top_skills
from app.services.resume.validation import validate_resume_bytes
from app.services.storage import safe_filename
from app.services.storage.media import store_media

log = get_logger(__name__)

PROCESS_JOB = "resume.process"
ACTIVE_STATUSES = frozenset(
    {
        ResumeStatus.UPLOADED.value,
        ResumeStatus.EXTRACTING.value,
        ResumeStatus.OCR.value,
        ResumeStatus.ANALYZING.value,
    }
)
USABLE_PROFILE_STATUSES = ("ready", "partial")
# ``quick``: deterministic profile built right after text extraction. It is shown
# to the candidate and to staff (``include_quick``), but never returned by
# ``latest_profile()``, so the interview engine waits for the model profile.
QUICK_PROFILE_STATUS = QUICK_STATUS
STUCK_AFTER = timedelta(minutes=20)
_DISPLAY_NAME_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]+")


def process_dedupe_key(resume_id: uuid.UUID | str) -> str:
    return f"{PROCESS_JOB}:{resume_id}"


def display_filename(name: str | None) -> str:
    """Original file name for display only (never used as a path)."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _DISPLAY_NAME_UNSAFE.sub("_", base).strip() or "resume"
    return base[:255]


def stored_filename(name: str | None, extension: str) -> str:
    safe = safe_filename(name or "resume", default="resume")
    stem = (safe.rsplit(".", 1)[0] if "." in safe else safe)[:100].strip("._-")
    return f"{stem or 'resume'}.{extension}"


def media_id_of(resume: CandidateResume) -> str | None:
    value = (resume.scan_result or {}).get("media_id")
    return str(value) if value else None


class ResumeServiceImpl:
    """Default :class:`ResumeService`."""

    async def _retention_days(
        self, db: AsyncSession, organization_id: uuid.UUID, interview: Interview | None, job: Job | None
    ) -> int:
        if interview is not None and interview.policy_snapshot:
            try:
                return PolicySnapshot.model_validate(interview.policy_snapshot).retention.resume_days
            except ValueError:
                log.warning("resume.policy_snapshot_invalid", interview_id=str(interview.id))
        org_settings = await get_organization_settings(db, organization_id)
        return build_policy_snapshot(org_settings, job).retention.resume_days

    async def accept_upload(
        self,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        candidate_id: uuid.UUID,
        interview_id: uuid.UUID | None,
        job_id: uuid.UUID | None,
        filename: str,
        declared_content_type: str | None,
        data: bytes,
        uploaded_by_candidate: bool,
    ) -> CandidateResume:
        settings = get_settings()
        candidate = await db.get(Candidate, candidate_id)
        if candidate is None or candidate.organization_id != organization_id:
            raise NotFound("Candidate not found.")
        interview: Interview | None = None
        if interview_id is not None:
            interview = await db.get(Interview, interview_id)
            if (
                interview is None
                or interview.organization_id != organization_id
                or interview.candidate_id != candidate_id
            ):
                raise NotFound("Interview not found.")
            job_id = job_id or interview.job_id
        job: Job | None = None
        if job_id is not None:
            job = await db.get(Job, job_id)
            if job is None or job.organization_id != organization_id:
                raise NotFound("Job not found.")

        validated = await asyncio.to_thread(
            validate_resume_bytes,
            data,
            filename=filename,
            declared_content_type=declared_content_type,
            max_bytes=settings.max_resume_bytes,
        )
        scan = await scan_upload(data, settings)

        resume_id = uuid.uuid4()
        key = (
            f"orgs/{organization_id}/resumes/{candidate_id}/{resume_id}/"
            f"{stored_filename(filename, validated.extension)}"
        )
        retention_days = await self._retention_days(db, organization_id, interview, job)
        media = await store_media(
            db,
            organization_id=organization_id,
            kind=MediaKind.RESUME,
            key=key,
            data=data,
            content_type=validated.content_type,
            candidate_id=candidate_id,
            retention_days=retention_days,
            details={"resume_id": str(resume_id), "uploaded_by_candidate": uploaded_by_candidate},
        )
        resume = CandidateResume(
            id=resume_id,
            candidate_id=candidate_id,
            organization_id=organization_id,
            interview_id=interview_id,
            storage_key=key,
            original_filename=display_filename(filename),
            content_type=validated.content_type,
            size_bytes=len(data),
            sha256=media.sha256 or "",
            status=ResumeStatus.UPLOADED.value,
            progress=progress_mod.initial_progress(),
            page_count=validated.page_count,
            scan_result={
                "validation": validated.report(),
                "malware": scan.as_dict(),
                "warnings": list(validated.warnings),
                "media_id": str(media.id),
                "job_id": str(job_id) if job_id else None,
            },
            uploaded_by_candidate=uploaded_by_candidate,
        )
        db.add(resume)
        await db.flush()
        await enqueue(
            PROCESS_JOB,
            {"resume_id": str(resume_id), "job_id": str(job_id) if job_id else None},
            dedupe_key=process_dedupe_key(resume_id),
            db=db,
        )
        log.info(
            "resume.accepted",
            resume_id=str(resume_id),
            candidate_id=str(candidate_id),
            kind=validated.kind,
            size_bytes=len(data),
            scan=scan.status,
            warnings=len(validated.warnings),
            by_candidate=uploaded_by_candidate,
        )
        return resume

    async def reprocess(
        self,
        db: AsyncSession,
        resume: CandidateResume,
        *,
        job_id: uuid.UUID | None = None,
        force: bool = False,
    ) -> CandidateResume:
        """Reset the resume and run the pipeline again (optionally against another job)."""
        if resume.status in ACTIVE_STATUSES and not force:
            started = resume.processing_started_at or resume.updated_at or resume.created_at
            if started is not None and datetime.now(UTC) - started < STUCK_AFTER:
                raise Conflict("This resume is still being processed.", code="resume_processing")
        if job_id is not None:
            job = await db.get(Job, job_id)
            if job is None or job.organization_id != resume.organization_id:
                raise NotFound("Job not found.")
        effective_job = job_id or _uuid_or_none((resume.scan_result or {}).get("job_id"))
        resume.status = ResumeStatus.UPLOADED.value
        resume.error = None
        resume.status_detail = None
        resume.processing_started_at = None
        resume.processing_finished_at = None
        resume.progress = progress_mod.reset()
        resume.scan_result = {
            **(resume.scan_result or {}),
            "job_id": str(effective_job) if effective_job else None,
        }
        await db.flush()
        await enqueue(
            PROCESS_JOB,
            {"resume_id": str(resume.id), "job_id": str(effective_job) if effective_job else None},
            dedupe_key=process_dedupe_key(resume.id),
            db=db,
        )
        return resume

    async def progress(self, db: AsyncSession, resume_id: uuid.UUID) -> ResumeProgress:
        resume = await db.get(CandidateResume, resume_id)
        if resume is None:
            raise NotFound("Resume not found.")
        return progress_of(resume)

    async def latest_profile(
        self,
        db: AsyncSession,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
        *,
        include_quick: bool = False,
    ) -> CandidateProfile | None:
        """Latest usable (ready|partial) profile, preferring the job-specific one.
        With ``include_quick`` a newer quick profile (a resume whose model profile
        is still being generated) is returned when no usable profile is newer."""
        base = (
            select(CandidateProfile)
            .where(
                CandidateProfile.candidate_id == candidate_id,
                CandidateProfile.status.in_(USABLE_PROFILE_STATUSES),
            )
            .order_by(CandidateProfile.version.desc(), CandidateProfile.created_at.desc())
            .limit(1)
        )
        row: CandidateProfile | None = None
        if job_id is not None:
            row = await db.scalar(base.where(CandidateProfile.job_id == job_id))
        if row is None:
            row = await db.scalar(base)
        if include_quick:
            quick = await db.scalar(
                select(CandidateProfile)
                .where(CandidateProfile.candidate_id == candidate_id, CandidateProfile.status == QUICK_STATUS)
                .order_by(CandidateProfile.version.desc())
                .limit(1)
            )
            if quick is not None and (row is None or quick.version > row.version):
                full = await db.scalar(
                    select(CandidateProfile.id).where(
                        CandidateProfile.resume_id == quick.resume_id,
                        CandidateProfile.status.in_(USABLE_PROFILE_STATUSES),
                    )
                )
                if full is None:  # that resume's model profile is still coming
                    return quick
        return row

    async def profile_for_resume(
        self, db: AsyncSession, resume_id: uuid.UUID
    ) -> tuple[CandidateProfile | None, str]:
        """(profile row, stage) of one resume: its newest usable profile
        (``full``), else its quick profile (``quick``), else ``(None, "none")``."""
        rows = (
            await db.scalars(
                select(CandidateProfile)
                .where(
                    CandidateProfile.resume_id == resume_id,
                    CandidateProfile.status.in_((*USABLE_PROFILE_STATUSES, QUICK_STATUS)),
                )
                .order_by(CandidateProfile.version.desc())
            )
        ).all()
        full = next((r for r in rows if r.status in USABLE_PROFILE_STATUSES), None)
        if full is not None:
            return full, "full"
        quick = next((r for r in rows if r.status == QUICK_STATUS), None)
        return (quick, "quick") if quick is not None else (None, "none")

    def candidate_safe_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        """What a candidate may see: never claims, inconsistencies or match internals."""
        roles = []
        for role in (profile.get("roles") or [])[:15]:
            if not isinstance(role, dict):
                continue
            roles.append(
                {
                    "title": role.get("title"),
                    "company": role.get("company"),
                    "start": role.get("start"),
                    "end": role.get("end"),
                }
            )
        grouped: dict[str, Any] = profile.get("skills") or {}
        try:
            from app.schemas.ai import SkillItem

            parsed = {
                category: [SkillItem.model_validate(item) for item in items]
                for category, items in grouped.items()
                if isinstance(items, list)
            }
            skills = top_skills(parsed, limit=12, context=_profile_text(profile))
        except ValueError:
            skills = []
        return {
            "candidate_name": profile.get("candidate_name"),
            "headline": profile.get("headline"),
            "experience_years": profile.get("experience_years"),
            "roles": roles,
            "top_skills": skills,
            "education": [
                {"institution": e.get("institution"), "degree": e.get("degree"), "field": e.get("field")}
                for e in (profile.get("education") or [])[:5]
                if isinstance(e, dict)
            ],
            "certifications": [
                c.get("name") for c in (profile.get("certifications") or [])[:10] if isinstance(c, dict)
            ],
        }


def _profile_text(profile: dict[str, Any]) -> str:
    """Summary and role text of a profile document (where its key skills are named)."""
    parts: list[str] = [str(profile.get("summary") or ""), str(profile.get("headline") or "")]
    for role in profile.get("roles") or []:
        if not isinstance(role, dict):
            continue
        parts.extend(str(item) for item in role.get("responsibilities") or [])
        parts.extend(
            str(item.get("text", "")) for item in role.get("achievements") or [] if isinstance(item, dict)
        )
        parts.extend(str(item) for item in role.get("technologies") or [])
    return "\n".join(parts)


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def progress_of(resume: CandidateResume) -> ResumeProgress:
    data = resume.progress or {}
    return ResumeProgress(
        resume_id=str(resume.id),
        status=resume.status,
        steps=list(data.get("steps") or []),
        error=resume.error
        if resume.status in (ResumeStatus.FAILED.value, ResumeStatus.REJECTED.value)
        else None,
        profile_ready=resume.status == ResumeStatus.READY.value,
        sections=[dict(s) for s in data.get("sections") or [] if isinstance(s, dict)],
    )


def candidate_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The section states a candidate may see (never what the analysis found)."""
    return [
        {"key": s.get("key"), "label": s.get("label"), "state": s.get("state")}
        for s in sections
        if s.get("key") in CANDIDATE_SECTIONS
    ]


def extraction_summary(resume: CandidateResume, profile: CandidateProfile | None) -> dict[str, Any] | None:
    """Short, staff-facing summary of what was extracted (ResumeExtractionSummary)."""
    scan = resume.scan_result or {}
    extraction = scan.get("extraction") or {}
    warnings = list(dict.fromkeys([*(scan.get("warnings") or []), *(extraction.get("warnings") or [])]))
    if profile is None and not extraction:
        return {"warnings": warnings} if warnings else None
    summary: dict[str, Any] = {
        "warnings": warnings,
        "sections": scan.get("sections") or {},
        "chunks": scan.get("chunks"),
        "characters": extraction.get("characters"),
        "ocr_pages": extraction.get("ocr_pages"),
    }
    quick = scan.get("quick")
    if isinstance(quick, dict):
        summary["quick"] = {
            key: quick.get(key) for key in ("contact", "partial", "pages_done", "pages_total", "built_ms")
        }
    if scan.get("reused_from"):
        summary["reused_from"] = scan.get("reused_from")
    if profile is not None:
        document = profile.profile or {}
        grouped = document.get("skills") or {}
        skill_count = sum(len(items) for items in grouped.values() if isinstance(items, list))
        summary.update(
            {
                "candidate_name": document.get("candidate_name"),
                "headline": document.get("headline"),
                "total_experience_years": document.get("experience_years"),
                "roles_count": len(document.get("roles") or []),
                "skills_count": skill_count,
                "top_skills": ResumeServiceImpl().candidate_safe_summary(document)["top_skills"],
                "profile_id": str(profile.id),
                "profile_status": profile.status,
            }
        )
    return summary


async def resume_media(db: AsyncSession, resume: CandidateResume) -> MediaAsset | None:
    media_id = _uuid_or_none(media_id_of(resume))
    if media_id is not None:
        asset = await db.get(MediaAsset, media_id)
        if asset is not None:
            return asset
    return await db.scalar(select(MediaAsset).where(MediaAsset.storage_key == resume.storage_key))


_service: ResumeServiceImpl | None = None


def get_resume_service() -> ResumeServiceImpl:
    """Process-wide :class:`ResumeService` implementation."""
    global _service
    if _service is None:
        _service = ResumeServiceImpl()
    return _service


assert isinstance(ResumeServiceImpl(), ResumeService)  # noqa: S101 - protocol conformance at import time


async def quick_profile_fallback(
    candidate_id: uuid.UUID | str, job_id: uuid.UUID | str | None = None
) -> CandidateProfile | None:
    """For the interview engine when its profile wait budget runs out: the latest
    usable profile or, while the model profile is still being generated, the
    candidate's quick profile (status ``quick``). Opens its own session."""
    from app.db.session import session_scope

    async with session_scope() as db:
        return await get_resume_service().latest_profile(
            db, uuid.UUID(str(candidate_id)), _uuid_or_none(job_id), include_quick=True
        )
