"""Catalog embedding jobs.

``question_bank.embed`` {question_ids, coding_question_ids?, competency_ids?, requirement_ids?, job_ids?}
    Embed the listed rows that have no embedding yet (batched, idempotent).
``catalog.backfill_embeddings`` {}
    Embed every active question / coding question / competency / job
    requirement / job whose embedding is NULL. Enqueued when the worker
    starts and hourly.
"""

from __future__ import annotations

from typing import Any

from app.core.lifecycle import on_startup
from app.core.logging import get_logger
from app.core.queue import enqueue, job_handler, register_periodic_job
from app.services.question_bank.embeddings import BACKFILL_JOB, EMBED_JOB, backfill_missing, embed_payload

log = get_logger(__name__)

BACKFILL_INTERVAL_SECONDS = 3600

register_periodic_job(BACKFILL_JOB, BACKFILL_INTERVAL_SECONDS)


@job_handler(EMBED_JOB, concurrency=2, timeout_seconds=600)
async def question_bank_embed(payload: dict[str, Any]) -> dict[str, Any]:
    counts = await embed_payload(payload)
    return {"embedded": counts}


@job_handler(BACKFILL_JOB, concurrency=1, timeout_seconds=1800)
async def catalog_backfill_embeddings(payload: dict[str, Any]) -> dict[str, Any]:
    counts = await backfill_missing()
    return {"embedded": counts}


@on_startup("catalog.backfill_embeddings.enqueue", processes=("worker",))
async def enqueue_backfill_on_start() -> None:
    try:
        await enqueue(BACKFILL_JOB, {}, dedupe_key=f"{BACKFILL_JOB}:startup")
    except Exception as exc:  # noqa: BLE001 - never block worker start
        log.warning("catalog.backfill_enqueue_failed", error=type(exc).__name__)
