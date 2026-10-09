"""Helpers that compute and store embeddings on ORM rows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIServices, CallContext
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.domain.enums import ModelTask
from app.services.retrieval.pipeline import embed_documents

log = get_logger(__name__)

TextSource = str | Callable[[Any], str]


def _text_of(row: Any, source: TextSource) -> str:
    if callable(source):
        return source(row) or ""
    return str(getattr(row, source, "") or "")


async def embed_and_store(
    db: AsyncSession,
    rows: Sequence[Any],
    *,
    text: TextSource = "text",
    column: str = "embedding",
    overwrite: bool = False,
    ai: AIServices | None = None,
    context: CallContext | None = None,
    flush: bool = True,
) -> int:
    """Embed ``text`` of each row lacking an embedding (all rows with
    ``overwrite``) and assign it to ``column``. Rows with empty text are
    skipped. Returns the number of rows updated; the caller commits."""
    targets = [row for row in rows if overwrite or getattr(row, column, None) is None]
    pairs = [(row, _text_of(row, text).strip()) for row in targets]
    pairs = [(row, body) for row, body in pairs if body]
    if not pairs:
        return 0
    services = ai or get_ai()
    vectors = await embed_documents(
        services.embeddings,
        [body for _, body in pairs],
        context=context or CallContext(task=ModelTask.EMBEDDING),
    )
    for (row, _), vector in zip(pairs, vectors, strict=True):
        setattr(row, column, vector)
    if flush:
        await db.flush()
    log.info("retrieval.embedded", rows=len(pairs), model=type(pairs[0][0]).__name__)
    return len(pairs)


async def embed_and_store_by_ids(
    db: AsyncSession,
    model: type[Any],
    ids: Sequence[Any],
    *,
    text: TextSource = "text",
    column: str = "embedding",
    overwrite: bool = False,
    ai: AIServices | None = None,
    context: CallContext | None = None,
) -> int:
    """Load rows of ``model`` by primary key and embed them (idempotent:
    rows that already have an embedding are left alone unless ``overwrite``)."""
    if not ids:
        return 0
    rows = (await db.execute(select(model).where(model.id.in_(list(ids))))).scalars().all()
    return await embed_and_store(
        db, rows, text=text, column=column, overwrite=overwrite, ai=ai, context=context
    )
