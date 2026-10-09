"""Intake activity transcript job (INTAKE_INTERVIEW.md 7.7, 8.1).

* ``intake.activity_transcribe`` {session_id, activity_id, attempt?} - the live windowed final pass of an
  activity capture could not run (no spare Whisper replica within the wait, or an STT error): repeat it from the
  stored answer WAV and store ``transcript_final`` (``final_pass_worker``). No stored WAV (audio recording not
  consented) or still failing on the job's last attempt -> ``live_only`` (the live segment transcript is used,
  labelled ``live_segments``). Idempotent: a settled transcript or a newer attempt is never overwritten.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.ai.base import CallContext
from app.ai.registry import get_ai
from app.core.config import get_settings
from app.core.logging import bind_context, get_logger
from app.core.queue import JobContext, PermanentJobError, job_handler
from app.db.models import IntakeActivityResult, MediaAsset
from app.db.session import session_scope
from app.domain.enums import ModelTask
from app.realtime.audio.pcm import wav_to_pcm16
from app.realtime.glossary import Glossary
from app.services.intake import capture_store
from app.services.intake.transcribe import keeps_words, windowed_final_pass
from app.services.storage import get_storage

log = get_logger(__name__)

JOB_TYPE = "intake.activity_transcribe"


class FinalPassRetry(RuntimeError):
    """Retryable: the windowed pass did not complete on this attempt."""


def _uuid(value: Any, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError(f"payload.{name} must be a uuid") from exc


async def _glossary(session_id: str) -> Glossary:
    """The session glossary (kit passage terms + profile/job terms), as the live runtime uses it."""
    try:
        from app.realtime.context import load_session_context

        ctx = await load_session_context(uuid.UUID(session_id))
    except Exception as exc:  # noqa: BLE001 - correction is an optimisation
        log.info("intake.capture.glossary_unavailable", error=type(exc).__name__)
        ctx = None
    return Glossary((ctx.glossary_terms or ctx.vocabulary) if ctx is not None else [])


@job_handler(JOB_TYPE, concurrency=1, timeout_seconds=600)
async def activity_transcribe(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    session_id = str(_uuid(payload.get("session_id"), "session_id"))
    activity_id = _uuid(payload.get("activity_id"), "activity_id")
    bind_context(session_id=session_id, job_id=ctx.job_id)
    async with session_scope() as db:
        row = await db.get(IntakeActivityResult, activity_id)
        if row is None or str(row.session_id) != session_id:
            raise PermanentJobError("activity not found for this session")
        attempt = int(payload.get("attempt") or row.attempt)
        if attempt != row.attempt or row.transcript_status not in capture_store.PENDING_STATUSES:
            return {"skipped": row.transcript_status, "attempt": row.attempt}
        capture = dict(row.capture or {})
        live = row.transcript_live or ""
        answer_id = row.answer_id
        organization_id = str(row.organization_id)
        storage_key = None
        if row.audio_media_id is not None:
            media = await db.get(MediaAsset, row.audio_media_id)
            if media is not None and media.deleted_at is None:
                storage_key = media.storage_key
    if storage_key is None:
        await capture_store.mark_transcript_status(
            session_id, activity_id, attempt=attempt, status="live_only", source="live_segments"
        )
        return {"status": "live_only", "reason": "no_audio"}
    try:
        wav = await get_storage().read(storage_key)
        pcm, _rate = wav_to_pcm16(wav)
    except Exception as exc:  # noqa: BLE001 - unreadable audio: the live transcript stays
        log.warning("intake.capture.audio_unreadable", error=type(exc).__name__)
        await capture_store.mark_transcript_status(
            session_id, activity_id, attempt=attempt, status="live_only", source="live_segments"
        )
        return {"status": "live_only", "reason": "audio_unreadable"}
    glossary = await _glossary(session_id)
    settings = get_settings()
    result = await windowed_final_pass(
        pcm,
        list(capture.get("segments") or []),
        list(capture.get("pauses") or []),
        origin_ms=int(capture.get("started_ms") or 0),
        stt=get_ai().stt,
        context=CallContext(task=ModelTask.STT, session_id=session_id, organization_id=organization_id),
        language=settings.whisper_language or None,
        wait_s=float(settings.intake_activity_final_pass_wait_seconds),
        correct=lambda value: glossary.correct(value)[0],
    )
    if result.status == "final":
        stored = await capture_store.store_final_transcript(
            session_id,
            activity_id,
            attempt=attempt,
            text_value=result.text,
            windows=result.window_records(),
            source="final_pass_worker",
        )
        revised = False
        if stored and answer_id is not None and result.text != live and keeps_words(live, result.text):
            from app.realtime.revision import revise_answer_transcript

            revised = await revise_answer_transcript(
                session_id,
                started_ms=int(capture.get("started_ms") or 0),
                ended_ms=int(capture.get("ended_ms") or 0),
                live_text=live,
                revised_text=result.text,
            )
        log.info(
            "intake.capture.final_pass_worker",
            windows=len(result.windows),
            stored=stored,
            answer_revised=revised,
            latency_ms=round(result.latency_ms),
        )
        return {"status": "final" if stored else "superseded", "windows": len(result.windows)}
    if result.status == "empty":
        await capture_store.mark_transcript_status(
            session_id, activity_id, attempt=attempt, status="unavailable"
        )
        return {"status": "unavailable"}
    if ctx.is_last_attempt:
        await capture_store.mark_transcript_status(
            session_id, activity_id, attempt=attempt, status="live_only", source="live_segments"
        )
        return {"status": "live_only", "reason": result.error or result.status}
    raise FinalPassRetry(result.error or result.status)


__all__ = ["JOB_TYPE", "activity_transcribe"]
