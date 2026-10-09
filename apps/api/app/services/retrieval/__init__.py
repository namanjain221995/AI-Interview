"""Retrieval: embeddings + pgvector search + reranking.

Typical use (resume evidence for a topic)::

    from app.services.retrieval import DbCandidateSource, retrieve

    source = DbCandidateSource(db, ResumeChunk, text="text", filters={"candidate_id": candidate_id})
    hits = await retrieve(
        "Kubernetes autoscaling in production",
        candidates=source,
        top_k=5,
        instruction="Given an interview topic, retrieve resume passages that provide evidence about it",
    )
"""

from app.services.retrieval.dedupe import (
    DEFAULT_THRESHOLD,
    dedupe_indices,
    find_near_duplicate,
    find_near_duplicates,
    is_near_duplicate,
    most_similar_text,
    near_duplicate_pairs,
)
from app.services.retrieval.pipeline import (
    CandidateSearch,
    DbCandidateSource,
    RetrievalCandidate,
    RetrievedItem,
    candidates_from,
    embed_documents,
    embed_query,
    retrieve,
)
from app.services.retrieval.store import embed_and_store, embed_and_store_by_ids
from app.services.retrieval.vector import VectorHit, build_filters, cosine_similarity, vector_search

__all__ = [
    "DEFAULT_THRESHOLD",
    "CandidateSearch",
    "DbCandidateSource",
    "RetrievalCandidate",
    "RetrievedItem",
    "VectorHit",
    "build_filters",
    "candidates_from",
    "cosine_similarity",
    "dedupe_indices",
    "embed_and_store",
    "embed_and_store_by_ids",
    "embed_documents",
    "embed_query",
    "find_near_duplicate",
    "find_near_duplicates",
    "is_near_duplicate",
    "most_similar_text",
    "near_duplicate_pairs",
    "retrieve",
    "vector_search",
]
