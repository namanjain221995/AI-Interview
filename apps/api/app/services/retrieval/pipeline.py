"""Retrieval pipeline: embed query -> vector top-N -> rerank -> top-k.

When the reranker fails (``AIUnavailable``) the vector order is kept, so a
reranker outage degrades ranking quality but never breaks retrieval.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIServices, CallContext, EmbeddingClient
from app.ai.clients.embeddings import format_query
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.services.retrieval.vector import Filters, vector_search

log = get_logger(__name__)


@dataclass(slots=True)
class RetrievalCandidate[M]:
    item: M
    text: str
    embedding: Sequence[float] | None = None
    vector_similarity: float | None = None


@dataclass(slots=True)
class RetrievedItem[M]:
    item: M
    text: str
    score: float  # rerank probability when reranked, otherwise cosine similarity
    vector_similarity: float | None
    rerank_score: float | None
    rank: int  # 1-based


CandidateSearch = Callable[[list[float], int], Awaitable[Sequence[RetrievalCandidate[Any]]]]


class DbCandidateSource[M]:
    """Candidates from a table with an ``embedding`` column."""

    def __init__(
        self,
        db: AsyncSession,
        model: type[M],
        *,
        text: str | Callable[[M], str] = "text",
        filters: Filters = None,
        column: str = "embedding",
    ) -> None:
        self._db = db
        self._model = model
        self._text = text
        self._filters = filters
        self._column = column

    def text_of(self, item: M) -> str:
        if callable(self._text):
            return self._text(item)
        return str(getattr(item, self._text) or "")

    async def __call__(self, query_vector: list[float], limit: int) -> list[RetrievalCandidate[M]]:
        hits = await vector_search(
            self._db, self._model, query_vector, filters=self._filters, limit=limit, column=self._column
        )
        return [
            RetrievalCandidate(hit.item, self.text_of(hit.item), vector_similarity=hit.similarity)
            for hit in hits
        ]


async def embed_query(
    embeddings: EmbeddingClient,
    text: str,
    instruction: str | None = None,
    *,
    context: CallContext | None = None,
) -> list[float]:
    method = getattr(embeddings, "embed_query", None)
    if method is not None:
        return list(await method(text, instruction, context=context))
    return list(await embeddings.embed_one(format_query(text, instruction), context=context))


async def embed_documents(
    embeddings: EmbeddingClient, texts: Sequence[str], *, context: CallContext | None = None
) -> list[list[float]]:
    method = getattr(embeddings, "embed_documents", None) or embeddings.embed
    return [list(v) for v in await method(list(texts), context=context)]


async def _rank_in_memory(
    candidates: Sequence[RetrievalCandidate[Any]],
    query_vector: list[float],
    pool: int,
    ai: AIServices,
    context: CallContext | None,
) -> list[RetrievalCandidate[Any]]:
    missing = [c for c in candidates if c.embedding is None]
    if missing:
        vectors = await embed_documents(ai.embeddings, [c.text for c in missing], context=context)
        for candidate, vector in zip(missing, vectors, strict=True):
            candidate.embedding = vector
    if not candidates:
        return []
    matrix = np.asarray([c.embedding for c in candidates], dtype=np.float64)
    query = np.asarray(query_vector, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
    norms[norms == 0] = 1.0
    sims = matrix @ query / norms
    for candidate, sim in zip(candidates, sims, strict=True):
        candidate.vector_similarity = float(sim)
    order = sorted(range(len(candidates)), key=lambda i: (-sims[i], i))[:pool]
    return [candidates[i] for i in order]


async def retrieve[M](
    query: str,
    *,
    candidates: CandidateSearch | Sequence[RetrievalCandidate[M]],
    top_k: int = 5,
    rerank: bool = True,
    instruction: str | None = None,
    rerank_instruction: str | None = None,
    pool_size: int | None = None,
    min_score: float | None = None,
    ai: AIServices | None = None,
    context: CallContext | None = None,
) -> list[RetrievedItem[M]]:
    """Return the ``top_k`` best candidates for ``query``.

    ``candidates`` is either a search callable ``(query_vector, limit)``
    (e.g. :class:`DbCandidateSource`) or an in-memory list (embeddings are
    computed for items that have none). ``instruction`` is the task
    description used for the query embedding and, unless
    ``rerank_instruction`` is given, for the reranker.
    """
    if top_k <= 0 or not query.strip():
        return []
    services = ai or get_ai()
    pool = pool_size or max(top_k * 4, 20)
    query_vector = await embed_query(services.embeddings, query, instruction, context=context)
    if callable(candidates):
        pool_items = list(await candidates(query_vector, pool))
    else:
        pool_items = await _rank_in_memory(list(candidates), query_vector, pool, services, context)
    if not pool_items:
        return []
    ordered: list[tuple[RetrievalCandidate[M], float | None]] = [(c, None) for c in pool_items]
    if rerank and len(pool_items) > 1:
        try:
            scores = await services.reranker.rerank(
                query,
                [c.text for c in pool_items],
                instruction=rerank_instruction or instruction,
                context=context,
            )
        except AIError as exc:
            log.warning(
                "retrieval.rerank_failed",
                error=f"{type(exc).__name__}: {exc}"[:200],
                candidates=len(pool_items),
            )
        else:
            ranked: list[tuple[RetrievalCandidate[M], float | None]] = [
                (pool_items[s.index], s.score) for s in scores if 0 <= s.index < len(pool_items)
            ]
            seen = {id(c) for c, _ in ranked}
            ranked.extend(
                (c, None) for c in pool_items if id(c) not in seen
            )  # keep anything the reranker dropped
            ordered = ranked
    results: list[RetrievedItem[M]] = []
    for candidate, rerank_score in ordered:
        score = rerank_score if rerank_score is not None else (candidate.vector_similarity or 0.0)
        if min_score is not None and score < min_score:
            continue
        results.append(
            RetrievedItem(
                item=candidate.item,
                text=candidate.text,
                score=float(score),
                vector_similarity=candidate.vector_similarity,
                rerank_score=rerank_score,
                rank=len(results) + 1,
            )
        )
        if len(results) >= top_k:
            break
    return results


def candidates_from(
    items: Sequence[Any], *, text: str | Callable[[Any], str] = "text", embedding: str | None = "embedding"
) -> list[RetrievalCandidate[Any]]:
    """Wrap ORM rows / dicts / objects as in-memory candidates."""

    def get(item: Any, name: str) -> Any:
        return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)

    result: list[RetrievalCandidate[Any]] = []
    for item in items:
        body = text(item) if callable(text) else str(get(item, text) or "")
        vector = get(item, embedding) if embedding else None
        result.append(
            RetrievalCandidate(item=item, text=body, embedding=list(vector) if vector is not None else None)
        )
    return result
