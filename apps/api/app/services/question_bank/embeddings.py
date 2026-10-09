"""Embeddings for the recruiting catalog.

Text definitions (stable; the seed uses the same question text):

* bank question: ``"{topic}. {text} Concepts: {concepts}"`` (= ``BankQuestionSeed.embedding_text``)
* coding question: ``"{title}. {prompt (first 1500 chars)} Concepts: {concepts}"``
  (= ``CodingQuestionSeed.embedding_text``; the coding API clears the vector
  when those fields change and the backfill re-embeds it)
* competency: ``"{name}: {description} Dimensions: ... Levels: ..."``
* job requirement: ``"{name}. {description}"``
* job: ``"{title} ({seniority}, {department}). {description} Requirements: ..."``

Three ways rows get embedded:

1. inline, best effort, when the API creates or edits a row
   (:func:`embed_rows_best_effort`, bounded by a short timeout);
2. the ``question_bank.embed`` job (:func:`embed_payload`) for explicit id
   lists - enqueued by the seed step, and by the API when inline embedding
   failed;
3. the ``catalog.backfill_embeddings`` job (:func:`backfill_missing`), at
   worker start and hourly, for any active row still without an embedding.

Everything is idempotent: rows that already have an embedding are skipped
unless their text changed (callers clear the stale vector first).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.core.queue import enqueue
from app.db.models import BankQuestion, CodingQuestion, Competency, Job, JobRequirement
from app.db.session import session_scope
from app.domain.enums import BankStatus, JobStatus, ModelTask
from app.services.retrieval import embed_and_store, embed_documents

log = get_logger(__name__)

EMBED_JOB = "question_bank.embed"
BACKFILL_JOB = "catalog.backfill_embeddings"
BATCH_SIZE = 64
EMBED_CAS_PASSES = 3
INLINE_TIMEOUT_SECONDS = 8.0
# One run covers the whole curated library (~11k questions + 1k coding problems,
# about 3-5 minutes at 64 rows per embedding batch); the job timeout is 30 minutes.
BACKFILL_MAX_ROWS = 20_000


def bank_question_text(topic: str | None, text: str, concepts: Sequence[str] | None) -> str:
    """Identical to ``app.seed.loaders.BankQuestionSeed.embedding_text`` (topic optional)."""
    body = f"{topic}. {text}" if topic else text
    return f"{body} Concepts: {', '.join(concepts or [])}"


def question_text(row: Any) -> str:
    return bank_question_text(
        getattr(row, "topic", None), str(row.text), getattr(row, "expected_concepts", None)
    )


def coding_question_text(row: Any) -> str:
    """Identical to ``app.seed.loaders.CodingQuestionSeed.embedding_text``."""
    return f"{row.title}. {(row.prompt_markdown or '')[:1500]} Concepts: {', '.join(row.expected_concepts or [])}"


def competency_text(row: Any) -> str:
    parts = [f"{row.name}: {row.description or ''}".strip()]
    rubric = row.rubric or {}
    dimensions = rubric.get("dimensions") or []
    if dimensions:
        parts.append("Dimensions: " + ", ".join(str(d) for d in dimensions))
    levels = rubric.get("levels") or {}
    if levels:
        parts.append("Levels: " + " ".join(f"{k}: {v}" for k, v in sorted(levels.items())))
    return " ".join(parts)


def requirement_text(row: Any) -> str:
    return f"{row.name}. {row.description}" if row.description else str(row.name)


def job_text(row: Any, requirement_names: Sequence[str] = ()) -> str:
    qualifiers = ", ".join(x for x in (row.seniority, row.department) if x)
    head = f"{row.title} ({qualifiers})" if qualifiers else str(row.title)
    text = f"{head}. {(row.description or '')[:4000]}"
    if requirement_names:
        text += " Requirements: " + ", ".join(requirement_names[:40])
    return text


def with_embedding_flag[*Ts](stmt: Select[*Ts], model: type[Any]) -> Select[*Ts, bool]:
    """``stmt`` (a ``select(model)``) without the vector column plus a boolean
    ``has_embedding`` column.

    List pages only need to know *whether* a row is embedded; decoding the
    1024-float vector costs ~160 us per row (181 bank questions: 33 ms -> 4.5 ms).
    Rows come back as ``(entity, has_embedding)``.
    """
    return stmt.add_columns(model.embedding.is_not(None).label("has_embedding")).options(
        defer(model.embedding)
    )


@dataclass(frozen=True, slots=True)
class EmbedTarget:
    payload_key: str
    model: type[Any]
    text: Callable[[Any], str]
    active: Callable[[], Any] | None


TARGETS: tuple[EmbedTarget, ...] = (
    EmbedTarget(
        "question_ids", BankQuestion, question_text, lambda: BankQuestion.status != BankStatus.RETIRED.value
    ),
    EmbedTarget(
        "coding_question_ids",
        CodingQuestion,
        coding_question_text,
        lambda: CodingQuestion.status != BankStatus.RETIRED.value,
    ),
    EmbedTarget("competency_ids", Competency, competency_text, lambda: Competency.is_active.is_(True)),
    EmbedTarget("requirement_ids", JobRequirement, requirement_text, None),
)


async def job_texts(db: AsyncSession, jobs: Sequence[Job]) -> dict[uuid.UUID, str]:
    if not jobs:
        return {}
    rows = (
        await db.execute(
            select(JobRequirement.job_id, JobRequirement.name)
            .where(JobRequirement.job_id.in_([j.id for j in jobs]))
            .order_by(JobRequirement.sort_order, JobRequirement.created_at)
        )
    ).all()
    names: dict[uuid.UUID, list[str]] = {}
    for job_id, name in rows:
        names.setdefault(job_id, []).append(name)
    return {job.id: job_text(job, names.get(job.id, [])) for job in jobs}


async def embed_jobs(
    db: AsyncSession, jobs: Sequence[Job], *, ai: AIServices | None = None, overwrite: bool = False
) -> int:
    texts = await job_texts(db, jobs)
    return await embed_and_store(
        db,
        list(jobs),
        text=lambda job: texts.get(job.id, ""),
        overwrite=overwrite,
        ai=ai,
        context=CallContext(task=ModelTask.EMBEDDING),
    )


async def embed_rows_best_effort(
    db: AsyncSession,
    rows: Iterable[Any],
    text: Callable[[Any], str],
    *,
    ai: AIServices | None = None,
    timeout: float = INLINE_TIMEOUT_SECONDS,  # noqa: ASYNC109 - total budget
) -> bool:
    """Embed rows inside the request; ``False`` (rows left NULL) on any AI
    failure or timeout so the caller can enqueue the job instead."""
    targets = [row for row in rows if getattr(row, "embedding", None) is None]
    if not targets:
        return True
    try:
        async with asyncio.timeout(timeout):
            await embed_and_store(
                db, targets, text=text, ai=ai, context=CallContext(task=ModelTask.EMBEDDING)
            )
        return True
    except (AIError, TimeoutError) as exc:
        log.warning("catalog.inline_embedding_failed", rows=len(targets), error=type(exc).__name__)
        return False


async def enqueue_embedding(
    db: AsyncSession | None = None, *, revision: str | None = None, **ids: Sequence[Any]
) -> str | None:
    """Enqueue ``question_bank.embed`` for the given id lists (keys as in TARGETS + job_ids).

    An unfinished job for the same ids is reused (dedupe). Pass ``revision``
    (e.g. a hash of the new embedding text) after an edit: a job that is
    already *running* may have read the previous text, so a new revision gets
    its own job instead of being folded into that one.
    """
    payload = {key: sorted({str(v) for v in values}) for key, values in ids.items() if values}
    if not payload:
        return None
    material = repr(sorted(payload.items())) + (f"|{revision}" if revision else "")
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    return await enqueue(EMBED_JOB, payload, dedupe_key=f"{EMBED_JOB}:{digest}", db=db)


def text_revision(text: str) -> str:
    """Short content hash of an embedding text (``enqueue_embedding(revision=...)``)."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _ids(values: Any) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values or []:
        try:
            out.append(uuid.UUID(str(value)))
        except ValueError:
            log.warning("catalog.embed_bad_id", value=str(value)[:64])
    return out


async def _embed_batch(target: EmbedTarget, ids: Sequence[uuid.UUID], services: AIServices) -> int:
    """Embed the rows of ``ids`` that still lack a vector, compare-and-set.

    The texts are read, embedded outside any transaction, and a vector is only
    written (under a row lock) while the row still has no embedding *and* its
    text is unchanged: an edit committed meanwhile never receives the vector of
    the previous text. Rows edited mid-flight are re-read and embedded again
    (bounded passes).
    """
    written = 0
    pending = list(ids)
    for _ in range(EMBED_CAS_PASSES):
        async with session_scope() as db:
            rows = (
                await db.scalars(
                    select(target.model).where(target.model.id.in_(pending), target.model.embedding.is_(None))
                )
            ).all()
            texts = {row.id: target.text(row).strip() for row in rows}
        texts = {row_id: body for row_id, body in texts.items() if body}
        if not texts:
            break
        vectors = await embed_documents(
            services.embeddings, list(texts.values()), context=CallContext(task=ModelTask.EMBEDDING)
        )
        by_id = dict(zip(texts, vectors, strict=True))
        changed: list[uuid.UUID] = []
        async with session_scope() as db:
            locked = (
                await db.scalars(
                    select(target.model)
                    .where(target.model.id.in_(list(by_id)))
                    .order_by(target.model.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
            for row in locked:
                if row.embedding is not None:
                    continue  # embedded by someone else meanwhile
                if target.text(row).strip() != texts[row.id]:
                    changed.append(row.id)
                    continue
                row.embedding = by_id[row.id]
                written += 1
        if not changed:
            break
        log.info("catalog.embed_text_changed", rows=len(changed), model=target.model.__name__)
        pending = changed
    return written


async def embed_payload(payload: Mapping[str, Any], *, ai: AIServices | None = None) -> dict[str, int]:
    """Handler body of ``question_bank.embed``: embed every listed row that has
    no embedding yet, in batches, each batch committed on its own (see
    :func:`_embed_batch` for the stale-text guard)."""
    services = ai or get_ai()
    counts: dict[str, int] = {}
    for target in TARGETS:
        ids = _ids(payload.get(target.payload_key))
        done = 0
        for start in range(0, len(ids), BATCH_SIZE):
            done += await _embed_batch(target, ids[start : start + BATCH_SIZE], services)
        if ids:
            counts[target.payload_key] = done
    job_ids = _ids(payload.get("job_ids"))
    if job_ids:
        async with session_scope() as db:
            jobs = (await db.scalars(select(Job).where(Job.id.in_(job_ids)))).all()
            counts["job_ids"] = await embed_jobs(db, jobs, ai=services)
    return counts


async def backfill_missing(
    *, ai: AIServices | None = None, batch_size: int = BATCH_SIZE, max_rows: int = BACKFILL_MAX_ROWS
) -> dict[str, int]:
    """Embed active catalog rows whose embedding is NULL (keyset batches, so
    rows with empty text cannot loop forever)."""
    services = ai or get_ai()
    counts: dict[str, int] = {}
    budget = max_rows
    for target in TARGETS:
        last_id: uuid.UUID | None = None
        embedded = 0
        while budget > 0:
            async with session_scope() as db:
                stmt = select(target.model).where(target.model.embedding.is_(None))
                if target.active is not None:
                    stmt = stmt.where(target.active())
                if last_id is not None:
                    stmt = stmt.where(target.model.id > last_id)
                rows = (await db.scalars(stmt.order_by(target.model.id).limit(min(batch_size, budget)))).all()
                if not rows:
                    break
                last_id = rows[-1].id
                budget -= len(rows)
                embedded += await embed_and_store(
                    db, rows, text=target.text, ai=services, context=CallContext(task=ModelTask.EMBEDDING)
                )
        counts[target.payload_key] = embedded
    last_job: uuid.UUID | None = None
    embedded_jobs = 0
    while budget > 0:
        async with session_scope() as db:
            stmt = select(Job).where(Job.embedding.is_(None), Job.status != JobStatus.ARCHIVED.value)
            if last_job is not None:
                stmt = stmt.where(Job.id > last_job)
            jobs = (await db.scalars(stmt.order_by(Job.id).limit(min(batch_size, budget)))).all()
            if not jobs:
                break
            last_job = jobs[-1].id
            budget -= len(jobs)
            embedded_jobs += await embed_jobs(db, jobs, ai=services)
    counts["job_ids"] = embedded_jobs
    if any(counts.values()):
        log.info("catalog.backfill_embeddings", **counts)
    return counts
