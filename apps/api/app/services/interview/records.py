"""Postgres writes for session artefacts: answers, questions, transcript
segments and the per-session event log.

Per-session sequence numbers (``questions.sequence``, ``answers.sequence``,
``transcripts.seq``, ``interview_events.seq``) are allocated under a
transaction-scoped advisory lock keyed by (table, session), so concurrent
writers (live engine, background jobs, other API processes) never collide on
the unique constraints.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Answer, InterviewEvent, Question, Transcript
from app.domain.enums import MediaKind

log = get_logger(__name__)

_SEQ_COLUMNS = {
    "questions": (Question, Question.sequence),
    "answers": (Answer, Answer.sequence),
    "transcripts": (Transcript, Transcript.seq),
    "interview_events": (InterviewEvent, InterviewEvent.seq),
}


def _lock_key(table: str, session_id: uuid.UUID) -> int:
    digest = hashlib.blake2b(f"{table}:{session_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def next_seq(db: AsyncSession, table: str, session_id: uuid.UUID) -> int:
    model, column = _SEQ_COLUMNS[table]
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(table, session_id)})
    current = (
        await db.execute(select(func.coalesce(func.max(column), 0)).where(model.session_id == session_id))
    ).scalar_one()
    return int(current) + 1


def as_uuid(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def log_event(
    db: AsyncSession,
    session_id: str | uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    elapsed_s: float | None = None,
) -> int:
    sid = as_uuid(session_id)
    assert sid is not None
    seq = await next_seq(db, "interview_events", sid)
    db.add(
        InterviewEvent(
            session_id=sid,
            seq=seq,
            type=event_type[:64],
            elapsed_seconds=round(elapsed_s, 3) if elapsed_s is not None else None,
            payload=payload or {},
        )
    )
    await db.flush()
    return seq


async def add_transcript(
    db: AsyncSession,
    session_id: str | uuid.UUID,
    *,
    speaker: str,
    text_value: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    question_id: str | uuid.UUID | None = None,
    answer_id: str | uuid.UUID | None = None,
    confidence: float | None = None,
    interrupted: bool = False,
) -> Transcript:
    sid = as_uuid(session_id)
    assert sid is not None
    seq = await next_seq(db, "transcripts", sid)
    row = Transcript(
        id=uuid.uuid4(),
        session_id=sid,
        seq=seq,
        speaker=speaker,
        kind="final",
        text=text_value,
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=confidence,
        question_id=as_uuid(question_id),
        answer_id=as_uuid(answer_id),
        interrupted=interrupted,
    )
    db.add(row)
    await db.flush()
    return row


async def add_answer(
    db: AsyncSession,
    session_id: str | uuid.UUID,
    *,
    answer_id: uuid.UUID,
    question_id: str | uuid.UUID | None,
    text_value: str,
    typed: bool,
    started_ms: int | None,
    ended_ms: int | None,
    stt_confidence: float | None,
    interrupted_ai: bool,
    quick_assessment: dict[str, Any] | None = None,
    status: str = "received",
) -> Answer:
    sid = as_uuid(session_id)
    assert sid is not None
    existing = await db.get(Answer, answer_id)
    if existing is not None:
        return existing
    seq = await next_seq(db, "answers", sid)
    duration = None
    if started_ms is not None and ended_ms is not None and ended_ms >= started_ms:
        duration = int(ended_ms - started_ms)
    row = Answer(
        id=answer_id,
        session_id=sid,
        question_id=as_uuid(question_id),
        sequence=seq,
        transcript_text=text_value,
        typed=typed,
        started_ms=started_ms,
        ended_ms=ended_ms,
        duration_ms=duration,
        word_count=len(text_value.split()),
        stt_confidence=stt_confidence,
        interrupted_ai=interrupted_ai,
        quick_assessment=quick_assessment or {},
        status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def add_question(
    db: AsyncSession,
    session_id: str | uuid.UUID,
    *,
    question_id: uuid.UUID,
    kind: str,
    action: str,
    source: str,
    text_value: str,
    segment: str | None,
    topic: str | None,
    competency_keys: list[str],
    difficulty: str,
    question_focus: str | None,
    reason: str | None,
    expected_evidence: list[str],
    claim_id: str | None,
    decision: dict[str, Any],
    elapsed_s: float,
    parent_question_id: str | uuid.UUID | None = None,
    bank_question_id: str | uuid.UUID | None = None,
    coding_question_id: str | uuid.UUID | None = None,
    asked_at: datetime | None = None,
) -> tuple[Question, int]:
    sid = as_uuid(session_id)
    assert sid is not None
    existing = await db.get(Question, question_id)
    if existing is not None:
        return existing, existing.sequence
    seq = await next_seq(db, "questions", sid)
    row = Question(
        id=question_id,
        session_id=sid,
        sequence=seq,
        parent_question_id=as_uuid(parent_question_id),
        bank_question_id=as_uuid(bank_question_id),
        coding_question_id=as_uuid(coding_question_id),
        kind=kind,
        action=action,
        source=source,
        segment=(segment or None) and segment[:32],
        topic=(topic or None) and topic[:120],
        competency_keys=competency_keys,
        difficulty=difficulty,
        text=text_value,
        question_focus=(question_focus or None) and question_focus[:300],
        reason=reason,
        expected_evidence=expected_evidence,
        claim_id=claim_id,
        decision=decision,
        elapsed_seconds=round(elapsed_s, 3),
    )
    if asked_at is not None:
        row.asked_at = asked_at
    db.add(row)
    await db.flush()
    return row, seq


async def mark_question_interrupted(
    db: AsyncSession,
    question_id: str | uuid.UUID,
    *,
    spoken_text: str,
    transcript_id: str | uuid.UUID | None = None,
) -> None:
    qid = as_uuid(question_id)
    if qid is not None:
        await db.execute(
            update(Question).where(Question.id == qid).values(interrupted=True, spoken_text=spoken_text)
        )
    tid = as_uuid(transcript_id)
    if tid is not None:
        await db.execute(
            update(Transcript)
            .where(Transcript.id == tid)
            .values(interrupted=True, text=spoken_text or "(interrupted)")
        )


async def mark_interviewer_transcript_interrupted(
    db: AsyncSession, transcript_id: str | uuid.UUID | None, *, spoken_text: str
) -> None:
    tid = as_uuid(transcript_id)
    if tid is not None:
        await db.execute(
            update(Transcript)
            .where(Transcript.id == tid)
            .values(interrupted=True, text=spoken_text or "(interrupted)")
        )


async def store_answer_audio(
    db: AsyncSession,
    *,
    organization_id: str,
    session_id: str,
    candidate_id: str,
    answer_id: uuid.UUID,
    wav: bytes,
    retention_days: int | None,
) -> str | None:
    """Store the answer audio (``orgs/{org}/sessions/{sid}/audio/{answer_id}.wav``)."""
    from app.services.storage.media import store_media

    key = f"orgs/{organization_id}/sessions/{session_id}/audio/{answer_id}.wav"
    try:
        media = await store_media(
            db,
            organization_id=uuid.UUID(organization_id),
            kind=MediaKind.ANSWER_AUDIO.value,
            key=key,
            data=wav,
            content_type="audio/wav",
            session_id=uuid.UUID(session_id),
            candidate_id=uuid.UUID(candidate_id),
            retention_days=retention_days,
            details={"answer_id": str(answer_id)},
        )
    except Exception as exc:  # noqa: BLE001 - audio is optional; never lose the answer over it
        log.warning(
            "engine.answer_audio_store_failed",
            session_id=session_id,
            answer_id=str(answer_id),
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return None
    await db.execute(update(Answer).where(Answer.id == answer_id).values(audio_storage_key=key))
    return str(getattr(media, "id", "")) or key


__all__ = [
    "add_answer",
    "add_question",
    "add_transcript",
    "as_uuid",
    "log_event",
    "mark_interviewer_transcript_interrupted",
    "mark_question_interrupted",
    "next_seq",
    "store_answer_audio",
]
