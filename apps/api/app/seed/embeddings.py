"""Seed step ``catalog_embeddings`` (order 25, after ``catalog``).

The catalog seeder reports the ids of rows that are new, changed or never
embedded (``question_ids_needing_embedding``, ``coding_question_ids_needing_embedding``,
``competency_ids_needing_embedding``). This step enqueues
``question_bank.embed`` jobs for them in batches with stable dedupe keys; the
worker computes the vectors. Any row that slips through (for example because
Redis was unreachable while seeding) is picked up by the
``catalog.backfill_embeddings`` job the worker runs on start and hourly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.queue import enqueue
from app.seed.core import seed_step
from app.services.question_bank.embeddings import EMBED_JOB

log = get_logger(__name__)

BATCH_SIZE = 64

CONTEXT_KEYS: tuple[tuple[str, str], ...] = (
    ("question_ids_needing_embedding", "question_ids"),
    ("coding_question_ids_needing_embedding", "coding_question_ids"),
    ("competency_ids_needing_embedding", "competency_ids"),
)


def embedding_batches(
    context: dict[str, Any], batch_size: int = BATCH_SIZE
) -> list[tuple[dict[str, list[str]], str]]:
    """``[(payload, dedupe_key)]`` - one payload per batch of each id list."""
    jobs: list[tuple[dict[str, list[str]], str]] = []
    for context_key, payload_key in CONTEXT_KEYS:
        ids: Sequence[Any] = context.get(context_key) or []
        ordered = sorted({str(i) for i in ids})
        for start in range(0, len(ordered), batch_size):
            batch = ordered[start : start + batch_size]
            digest = hashlib.sha256(",".join(batch).encode()).hexdigest()[:16]
            jobs.append(({payload_key: batch}, f"{EMBED_JOB}:seed:{payload_key}:{digest}"))
    return jobs


@seed_step("catalog_embeddings", order=25)
async def catalog_embeddings_seed_step(db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
    batches = embedding_batches(context)
    # Enqueued in their own transactions (not ``db=``): the seed runner exits
    # right after the step, so the stream message must be sent before returning.
    job_ids = [await enqueue(EMBED_JOB, payload, dedupe_key=key) for payload, key in batches]
    counts = {payload_key: len(context.get(context_key) or []) for context_key, payload_key in CONTEXT_KEYS}
    log.info("seed.catalog_embeddings.enqueued", jobs=len(job_ids), **counts)
    return {"embedding_jobs_enqueued": len(job_ids)}
