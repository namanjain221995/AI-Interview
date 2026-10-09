"""``resume.process``: stored file -> text -> quick profile -> ``profile.generate``.

Stages (each persisted to ``candidate_resumes.status``/``progress`` in its own
short transaction so the UI sees live progress):

``extracting`` (text layers, DOCX, TXT) -> ``ocr`` (pages without a usable
text layer, images; page counter; page 1 first, and the readable prefix gets a
quick profile while later pages are scanned) -> normalisation, section
detection, the deterministic **quick profile** (``candidate_profiles`` row with
status ``quick``, visible to the candidate at once) -> ``analyzing`` and
``profile.generate`` enqueued in the same transaction. Chunks and embeddings
are built by ``profile.generate`` beside its model calls.

An identical file (same sha256) that the candidate uploaded before is not read
again: its text, chunks and cached model parts are copied, so the quick profile
appears immediately and ``profile.generate`` only runs what the cache lacks.

The job is idempotent. Failures on the last attempt (or permanent ones, such as
a file without readable text) set ``status=failed`` with a candidate-safe
message; transient failures keep the current stage and say that a retry is
coming.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIServices
from app.ai.registry import get_ai
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.queue import PermanentJobError, enqueue
from app.db.models import CandidateProfile, CandidateResume, Interview, ResumeChunk
from app.db.session import session_scope
from app.domain.enums import ExtractionMethod, ResumeStatus
from app.services.resume import progress as progress_mod
from app.services.resume.extraction import NoReadableText, ResumeExtractor, kind_from_content_type
from app.services.resume.normalize import detect_sections, meaningful_chars, normalize_text, section_stats
from app.services.resume.quick import QUICK_STATUS, QuickProfile, build_quick_profile, initial_sections
from app.services.storage import ObjectNotFound, get_storage

log = get_logger(__name__)

PROFILE_JOB = "profile.generate"
STAGE_BUDGET_SECONDS = 840.0  # under the worker timeout so failures are recorded by us

MSG_NO_TEXT = (
    "We could not find any readable text in this file. If it is a scan or a photo, please upload a clearer "
    "copy or a PDF with selectable text."
)
MSG_MISSING_FILE = "The uploaded file is no longer available. Please upload it again."
MSG_GENERIC = "We could not process this resume right now. Please try again later."
MSG_RETRYING = "Taking longer than usual - retrying shortly"


class ResumeFailure(PermanentJobError):
    """Permanent failure with a candidate-safe message."""

    retryable = False

    def __init__(self, safe_message: str, detail: str) -> None:
        super().__init__(detail)
        self.safe_message = safe_message


def profile_dedupe_key(resume_id: uuid.UUID | str) -> str:
    return f"{PROFILE_JOB}:{resume_id}"


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ResumeFailure | PermanentJobError):
        return False
    if isinstance(exc, AIError):
        return exc.retryable
    return bool(getattr(exc, "retryable", True))


async def mark_failed(resume_id: uuid.UUID, message: str, *, detail: str | None = None) -> None:
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        if resume is None:
            return
        resume.status = ResumeStatus.FAILED.value
        resume.error = message
        resume.status_detail = (detail or "")[:255] or None
        resume.processing_finished_at = datetime.now(UTC)
        resume.progress = progress_mod.fail(resume.progress, message)


async def mark_retrying(resume_id: uuid.UUID, attempt: int) -> None:
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        if resume is None or resume.status in (ResumeStatus.READY.value, ResumeStatus.FAILED.value):
            return
        resume.progress = {**progress_mod.set_percent(resume.progress, 0, MSG_RETRYING), "attempt": attempt}


async def run_with_failure_handling(
    resume_id: uuid.UUID,
    work: Callable[[], Awaitable[dict[str, Any]]],
    *,
    attempt: int,
    max_attempts: int,
    budget_seconds: float = STAGE_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Run ``work``; record failures on the resume and re-raise for the queue."""
    try:
        async with asyncio.timeout(budget_seconds):
            return await work()
    except ResumeFailure as exc:
        await mark_failed(resume_id, exc.safe_message, detail=str(exc))
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        final = attempt >= max_attempts or not is_retryable(exc)
        log.warning(
            "resume.stage_failed",
            resume_id=str(resume_id),
            attempt=attempt,
            final=final,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
        if final:
            await mark_failed(resume_id, MSG_GENERIC, detail=type(exc).__name__)
        else:
            await mark_retrying(resume_id, attempt)
        raise


async def _update(resume_id: uuid.UUID, mutate: Callable[[CandidateResume], None]) -> None:
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        if resume is not None:
            mutate(resume)


async def upsert_quick_profile(
    db: AsyncSession, resume: CandidateResume, quick: QuickProfile, *, job_id: uuid.UUID | None
) -> CandidateProfile:
    """Create or refresh the resume's quick profile row (status ``quick``)."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"candidate_profile:{resume.candidate_id}"},
    )
    row = await db.scalar(
        select(CandidateProfile)
        .where(CandidateProfile.resume_id == resume.id, CandidateProfile.status == QUICK_STATUS)
        .order_by(CandidateProfile.version.desc())
        .limit(1)
        .with_for_update()
    )
    document = quick.document
    if row is None:
        previous = await db.scalar(
            select(func.max(CandidateProfile.version)).where(
                CandidateProfile.candidate_id == resume.candidate_id
            )
        )
        row = CandidateProfile(
            candidate_id=resume.candidate_id,
            resume_id=resume.id,
            job_id=job_id,
            version=int(previous or 0) + 1,
            status=QUICK_STATUS,
            model_call_ids=[],
        )
        db.add(row)
    row.profile = document
    row.summary = document.get("summary") or document.get("headline")
    row.experience_years = document.get("experience_years")
    row.relevant_experience_years = None
    row.job_match_score = None
    row.claims_count = len(document.get("claims_to_verify") or [])
    resume.scan_result = {**(resume.scan_result or {}), "quick": quick.meta}
    await db.flush()
    return row


async def _find_reusable(db: AsyncSession, resume: CandidateResume) -> CandidateResume | None:
    """The candidate's latest earlier resume with the same bytes and extracted text."""
    if not resume.sha256:
        return None
    return await db.scalar(
        select(CandidateResume)
        .where(
            CandidateResume.candidate_id == resume.candidate_id,
            CandidateResume.sha256 == resume.sha256,
            CandidateResume.id != resume.id,
            CandidateResume.normalized_text.is_not(None),
            CandidateResume.status.in_((ResumeStatus.ANALYZING.value, ResumeStatus.READY.value)),
        )
        .order_by(CandidateResume.created_at.desc())
        .limit(1)
    )


_REUSED_SCAN_KEYS = ("extraction", "sections", "ai_parts", "chunks", "chunks_text_sha")


async def _finish_extraction(
    db: AsyncSession,
    resume: CandidateResume,
    *,
    extracted_text: str,
    normalized: str,
    page_count: int | None,
    method: str,
    scan_updates: dict[str, Any],
    job_id: uuid.UUID | None,
) -> QuickProfile:
    """Store the text, the quick profile and the ``analyzing`` stage, and enqueue
    ``profile.generate`` (dispatched when the transaction commits)."""
    sections = detect_sections(normalized)
    quick = build_quick_profile(
        normalized, candidate_id=resume.candidate_id, sections=sections, pages_total=page_count
    )
    resume.extracted_text = extracted_text
    resume.normalized_text = normalized
    resume.page_count = page_count
    resume.extraction_method = method
    resume.scan_result = {**(resume.scan_result or {}), **scan_updates, "sections": section_stats(sections)}
    await upsert_quick_profile(db, resume, quick, job_id=job_id)
    resume.status = ResumeStatus.ANALYZING.value
    progress = progress_mod.start_stage(
        resume.progress, "analyzing", message="Analyzing your experience and skills"
    )
    resume.progress = progress_mod.with_sections(progress, initial_sections())
    await enqueue(
        PROFILE_JOB,
        {
            "candidate_id": str(resume.candidate_id),
            "resume_id": str(resume.id),
            "job_id": str(job_id) if job_id else None,
        },
        dedupe_key=profile_dedupe_key(resume.id),
        db=db,
    )
    return quick


async def _reuse_previous(
    resume_id: uuid.UUID, previous_id: uuid.UUID, job_id: uuid.UUID | None
) -> dict[str, Any] | None:
    """Copy the text, chunks and cached model parts of an identical earlier upload."""
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        previous = await db.get(CandidateResume, previous_id)
        if resume is None or previous is None or not previous.normalized_text:
            return None
        scan = previous.scan_result or {}
        updates = {key: scan[key] for key in _REUSED_SCAN_KEYS if key in scan}
        updates["reused_from"] = str(previous.id)
        await db.execute(delete(ResumeChunk).where(ResumeChunk.resume_id == resume_id))
        await db.execute(
            text(
                "INSERT INTO resume_chunks (id, resume_id, candidate_id, chunk_index, section, text, embedding, created_at) "
                "SELECT gen_random_uuid(), :new_id, candidate_id, chunk_index, section, text, embedding, now() "
                "FROM resume_chunks WHERE resume_id = :old_id"
            ),
            {"new_id": resume_id, "old_id": previous.id},
        )
        await _finish_extraction(
            db,
            resume,
            extracted_text=previous.extracted_text or previous.normalized_text,
            normalized=previous.normalized_text,
            page_count=previous.page_count,
            method=previous.extraction_method or ExtractionMethod.TEXT.value,
            scan_updates=updates,
            job_id=job_id,
        )
    log.info("resume.reused", resume_id=str(resume_id), reused_from=str(previous_id))
    return {
        "resume_id": str(resume_id),
        "reused_from": str(previous_id),
        "job_id": str(job_id) if job_id else None,
    }


async def process_resume(
    resume_id: uuid.UUID,
    job_id: uuid.UUID | None = None,
    *,
    attempt: int = 1,
    ai: AIServices | None = None,
) -> dict[str, Any]:
    """Extraction (or reuse) -> quick profile -> enqueue profile.generate."""
    settings = get_settings()
    services = ai or get_ai()
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        if resume is None:
            raise PermanentJobError(f"resume {resume_id} not found")
        if resume.status == ResumeStatus.REJECTED.value:
            return {"skipped": "rejected"}
        resume.status = ResumeStatus.EXTRACTING.value
        resume.error = None
        resume.status_detail = None
        resume.processing_started_at = datetime.now(UTC)
        resume.processing_finished_at = None
        resume.progress = progress_mod.start_stage(
            resume.progress, "extracting", message="Reading your document", attempt=attempt
        )
        storage_key = resume.storage_key
        content_type = resume.content_type
        organization_id = resume.organization_id
        candidate_id = resume.candidate_id
        interview_id = resume.interview_id
        if job_id is None and interview_id is not None:
            interview = await db.get(Interview, interview_id)
            job_id = interview.job_id if interview is not None else None
        reusable = await _find_reusable(db, resume)
        reusable_id = reusable.id if reusable is not None else None

    if reusable_id is not None:
        reused = await _reuse_previous(resume_id, reusable_id, job_id)
        if reused is not None:
            return reused

    try:
        data = await get_storage().read(storage_key)
    except ObjectNotFound as exc:
        raise ResumeFailure(MSG_MISSING_FILE, "stored file missing") from exc
    try:
        kind = kind_from_content_type(content_type)
    except ValueError as exc:
        raise ResumeFailure(MSG_GENERIC, str(exc)) from exc

    async def on_ocr_start(_: int, total: int) -> None:
        def mutate(row: CandidateResume) -> None:
            row.status = ResumeStatus.OCR.value
            row.progress = progress_mod.update_pages(
                progress_mod.start_stage(row.progress, "ocr", message="Scanning pages"),
                done=0,
                total=total,
                message=f"Scanning page 1 of {total}" if total > 1 else "Scanning your document",
            )

        await _update(resume_id, mutate)

    async def on_ocr_page(done: int, total: int) -> None:
        message = f"Scanned {done} of {total} pages" if total > 1 else "Scan complete"
        await _update(
            resume_id,
            lambda row: setattr(
                row,
                "progress",
                progress_mod.update_pages(row.progress, done=done, total=total, message=message),
            ),
        )

    async def on_partial_text(prefix: str, pages_done: int, pages_total: int) -> None:
        """First pages read while the rest is scanned: show a quick read of them."""
        normalized_prefix = normalize_text(prefix)
        if meaningful_chars(normalized_prefix) < 40:
            return
        quick = build_quick_profile(
            normalized_prefix,
            candidate_id=candidate_id,
            partial=True,
            pages_done=pages_done,
            pages_total=pages_total,
        )
        async with session_scope() as db:
            row = await db.get(CandidateResume, resume_id, with_for_update=True)
            if row is None or row.status not in (ResumeStatus.OCR.value, ResumeStatus.EXTRACTING.value):
                return
            await upsert_quick_profile(db, row, quick, job_id=job_id)
            if not (row.progress or {}).get("sections"):
                row.progress = progress_mod.with_sections(row.progress, initial_sections(partial=True))
        log.info(
            "resume.partial_quick_profile", resume_id=str(resume_id), pages=pages_done, total=pages_total
        )

    extractor = ResumeExtractor(
        services, ocr_max_pages=settings.ocr_max_pages, organization_id=str(organization_id)
    )
    try:
        extraction = await extractor.extract(
            data, kind, on_ocr_start=on_ocr_start, on_ocr_page=on_ocr_page, on_partial_text=on_partial_text
        )
    except NoReadableText as exc:
        raise ResumeFailure(MSG_NO_TEXT, f"no readable text ({exc})") from exc

    normalized = normalize_text(extraction.text)
    if meaningful_chars(normalized) < 40:
        raise ResumeFailure(MSG_NO_TEXT, "no readable text after normalisation")

    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id, with_for_update=True)
        if resume is None:
            raise PermanentJobError(f"resume {resume_id} deleted during processing")
        quick = await _finish_extraction(
            db,
            resume,
            extracted_text=extraction.text,
            normalized=normalized,
            page_count=extraction.page_count,
            method=extraction.method.value,
            scan_updates={"extraction": extraction.summary()},
            job_id=job_id,
        )
    log.info(
        "resume.processed",
        resume_id=str(resume_id),
        method=extraction.method.value,
        pages=extraction.page_count,
        ocr_pages=extraction.ocr_pages,
        quick_ms=quick.meta.get("built_ms"),
        duration_ms=round(extraction.duration_ms),
    )
    return {
        "resume_id": str(resume_id),
        "method": extraction.method.value,
        "page_count": extraction.page_count,
        "ocr_pages": extraction.ocr_pages,
        "ocr_ms": round(extraction.ocr_ms, 1),
        "warnings": extraction.warnings,
        "job_id": str(job_id) if job_id else None,
    }
