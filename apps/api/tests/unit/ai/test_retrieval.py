from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.ai.base import AIUnavailable, CallContext
from app.ai.mock import MockAIServices
from app.domain.enums import ModelTask
from app.services.retrieval import (
    RetrievalCandidate,
    candidates_from,
    cosine_similarity,
    dedupe_indices,
    embed_and_store,
    is_near_duplicate,
    most_similar_text,
    near_duplicate_pairs,
    retrieve,
)

DOCS = [
    "Built a payments platform on Kubernetes handling 10,000 requests per second",
    "Led a team of six engineers across two time zones",
    "Reduced cloud cost by 40 percent with spot instances and autoscaling",
    "Kubernetes autoscaling with the horizontal pod autoscaler and cluster autoscaler",
    "Organised the office book club",
]


@dataclass
class Row:
    text: str
    embedding: list[float] | None = None


async def test_in_memory_retrieval_reranks(mock_ai: MockAIServices) -> None:
    hits = await retrieve(
        "kubernetes autoscaling", candidates=candidates_from([{"text": d} for d in DOCS]), top_k=2
    )
    assert hits[0].text == DOCS[3]
    assert hits[1].text in (DOCS[0], DOCS[2])  # both share one of the two query terms
    assert (hits[0].rank, hits[1].rank) == (1, 2)
    assert (hits[0].rerank_score, hits[1].rerank_score) == (1.0, 0.5)
    assert hits[0].vector_similarity is not None
    assert len(mock_ai.reranker.calls) == 1
    assert mock_ai.embeddings.calls  # query + documents embedded


async def test_reranker_failure_keeps_vector_order(mock_ai: MockAIServices) -> None:
    mock_ai.reranker.fail_with = AIUnavailable("reranker down")
    hits = await retrieve(
        "kubernetes autoscaling", candidates=candidates_from([{"text": d} for d in DOCS]), top_k=3
    )
    assert len(hits) == 3
    assert all(h.rerank_score is None for h in hits)
    sims = [h.vector_similarity for h in hits]
    assert sims == sorted(sims, reverse=True)
    assert hits[0].score == pytest.approx(sims[0])


async def test_rerank_disabled_min_score_and_empty(mock_ai: MockAIServices) -> None:
    hits = await retrieve(
        "kubernetes autoscaling",
        candidates=candidates_from([{"text": d} for d in DOCS]),
        rerank=False,
        top_k=5,
        min_score=0.2,
    )
    assert hits and all(h.score >= 0.2 for h in hits)
    assert mock_ai.reranker.calls == []
    assert await retrieve("", candidates=[]) == []
    assert await retrieve("x", candidates=[], top_k=3) == []


async def test_search_callable_source_and_precomputed_vectors(mock_ai: MockAIServices) -> None:
    vectors = await mock_ai.embeddings.embed(DOCS)
    seen: dict[str, object] = {}

    async def search(query_vector: list[float], limit: int) -> list[RetrievalCandidate[int]]:
        seen["limit"] = limit
        scored = sorted(range(len(DOCS)), key=lambda i: -cosine_similarity(query_vector, vectors[i]))
        return [
            RetrievalCandidate(i, DOCS[i], vector_similarity=cosine_similarity(query_vector, vectors[i]))
            for i in scored[:limit]
        ]

    ctx = CallContext(task=ModelTask.QUESTION_GENERATION, session_id="s1")
    hits = await retrieve(
        "team leadership",
        candidates=search,
        top_k=1,
        pool_size=4,
        instruction="Find leadership evidence",
        context=ctx,
    )
    assert seen["limit"] == 4
    assert hits[0].item == 1


async def test_dedupe_helpers(mock_ai: MockAIServices) -> None:
    questions = [
        "How would you scale a Kubernetes cluster for a traffic spike?",
        "How do you scale Kubernetes clusters during traffic spikes?",
        "Tell me about a conflict with a teammate.",
    ]
    vectors = await mock_ai.embeddings.embed(questions)
    pairs = near_duplicate_pairs(vectors, threshold=0.75)
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]
    assert dedupe_indices(vectors, threshold=0.75) == [0, 2]
    assert is_near_duplicate(vectors[0], vectors[1], 0.75)
    assert not is_near_duplicate(vectors[0], vectors[2], 0.75)
    match = await most_similar_text(
        "How would you scale Kubernetes for traffic spikes?", questions, threshold=0.7
    )
    assert match is not None and match[0] in (0, 1)
    assert await most_similar_text("What is your favourite pasta?", questions, threshold=0.7) is None
    assert near_duplicate_pairs([]) == [] and dedupe_indices([]) == []


async def test_embed_and_store_skips_existing_and_empty(mock_ai: MockAIServices) -> None:
    class FakeDb:
        flushed = 0

        async def flush(self) -> None:
            self.flushed += 1

    rows = [Row("first text"), Row("already", embedding=[0.0] * 1024), Row("   "), Row("second text")]
    db = FakeDb()
    updated = await embed_and_store(db, rows)  # type: ignore[arg-type]
    assert updated == 2
    assert rows[0].embedding is not None and len(rows[0].embedding) == 1024
    assert rows[1].embedding == [0.0] * 1024
    assert rows[2].embedding is None
    assert db.flushed == 1
    assert await embed_and_store(db, rows) == 0  # type: ignore[arg-type]
    assert await embed_and_store(db, rows, overwrite=True, text=lambda r: r.text.upper()) == 3  # type: ignore[arg-type]
