"""Near-duplicate detection by cosine similarity (questions, evidence)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIServices, CallContext
from app.ai.registry import get_ai
from app.services.retrieval.pipeline import embed_documents
from app.services.retrieval.vector import Filters, VectorHit, vector_search

DEFAULT_THRESHOLD = 0.92


async def find_near_duplicates[M](
    db: AsyncSession,
    model: type[M],
    vector: Sequence[float],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    filters: Filters = None,
    limit: int = 5,
    column: str = "embedding",
) -> list[VectorHit[M]]:
    """Stored rows whose cosine similarity to ``vector`` is >= ``threshold``."""
    hits = await vector_search(
        db, model, vector, filters=filters, limit=limit, max_distance=1.0 - threshold, column=column
    )
    return [hit for hit in hits if hit.similarity >= threshold]


async def find_near_duplicate[M](
    db: AsyncSession,
    model: type[M],
    vector: Sequence[float],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    filters: Filters = None,
    column: str = "embedding",
) -> VectorHit[M] | None:
    hits = await find_near_duplicates(
        db, model, vector, threshold=threshold, filters=filters, limit=1, column=column
    )
    return hits[0] if hits else None


def similarity_matrix(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.size == 0:
        return np.zeros((0, 0))
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    return unit @ unit.T


def near_duplicate_pairs(
    vectors: Sequence[Sequence[float]], threshold: float = DEFAULT_THRESHOLD
) -> list[tuple[int, int, float]]:
    """All index pairs (i < j) with cosine similarity >= ``threshold``."""
    sims = similarity_matrix(vectors)
    rows, cols = np.where(np.triu(sims, k=1) >= threshold)
    return [(int(i), int(j), float(sims[i, j])) for i, j in zip(rows, cols, strict=True)]


def dedupe_indices(vectors: Sequence[Sequence[float]], threshold: float = DEFAULT_THRESHOLD) -> list[int]:
    """Greedy keep-first deduplication: indices of the vectors to keep."""
    sims = similarity_matrix(vectors)
    kept: list[int] = []
    for i in range(len(sims)):
        if all(sims[i, j] < threshold for j in kept):
            kept.append(i)
    return kept


async def most_similar_text(
    text: str,
    existing: Sequence[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    ai: AIServices | None = None,
    context: CallContext | None = None,
) -> tuple[int, float] | None:
    """(index, similarity) of the most similar ``existing`` text when it
    reaches ``threshold``, else None. Embeds everything in one call."""
    if not existing:
        return None
    services = ai or get_ai()
    vectors = await embed_documents(services.embeddings, [text, *existing], context=context)
    sims = similarity_matrix(vectors)[0, 1:]
    best = int(np.argmax(sims))
    score = float(sims[best])
    return (best, score) if score >= threshold else None


def is_near_duplicate(a: Sequence[float], b: Sequence[float], threshold: float = DEFAULT_THRESHOLD) -> bool:
    sims = similarity_matrix([a, b])
    return bool(sims[0, 1] >= threshold)


__all__ = [
    "DEFAULT_THRESHOLD",
    "dedupe_indices",
    "find_near_duplicate",
    "find_near_duplicates",
    "is_near_duplicate",
    "most_similar_text",
    "near_duplicate_pairs",
    "similarity_matrix",
]
