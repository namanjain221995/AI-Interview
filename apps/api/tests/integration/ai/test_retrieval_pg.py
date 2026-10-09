"""pgvector retrieval against the running postgres (throwaway schema)."""

from __future__ import annotations

import uuid

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, Text, select, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.ai.mock import MockAIServices
from app.db.base import EMBEDDING_DIM
from app.services.retrieval import (
    DbCandidateSource,
    embed_and_store_by_ids,
    find_near_duplicate,
    find_near_duplicates,
    retrieve,
    vector_search,
)

pytestmark = pytest.mark.integration

ORG_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")
TOPICS = [
    "kubernetes",
    "postgres",
    "kafka",
    "react",
    "terraform",
    "python",
    "golang",
    "redis",
    "graphql",
    "spark",
]
VERBS = ["scaled", "debugged", "migrated", "designed", "monitored", "secured", "tuned", "automated"]


class RetrievalBase(DeclarativeBase):
    pass


class Doc(RetrievalBase):
    __tablename__ = "retrieval_docs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        Index(
            "ix_retrieval_docs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


@pytest.fixture(scope="module")
async def seeded(pg_schema: tuple[AsyncEngine, str]) -> dict[str, object]:
    engine, _ = pg_schema
    ai = MockAIServices()
    async with engine.begin() as conn:
        await conn.run_sync(RetrievalBase.metadata.create_all)
    texts_a = [f"I {verb} the {topic} platform in production" for topic in TOPICS for verb in VERBS] * 3
    texts_b = [
        "Organised the office book club",
        "Planned the team offsite",
        "Baked cakes for the charity sale",
    ]
    vectors_a = await ai.embeddings.embed(texts_a)
    vectors_b = await ai.embeddings.embed(texts_b)
    async with engine.begin() as conn:
        await conn.execute(
            Doc.__table__.insert(),
            [
                {"id": uuid.uuid4(), "organization_id": ORG_A, "kind": "chunk", "text": t, "embedding": v}
                for t, v in zip(texts_a, vectors_a, strict=True)
            ]
            + [
                {"id": uuid.uuid4(), "organization_id": ORG_B, "kind": None, "text": t, "embedding": v}
                for t, v in zip(texts_b, vectors_b, strict=True)
            ]
            + [
                {
                    "id": uuid.uuid4(),
                    "organization_id": ORG_A,
                    "kind": "pending",
                    "text": "Kafka consumer lag alerts",
                    "embedding": None,
                }
            ],
        )
        await conn.execute(text("ANALYZE retrieval_docs"))
    return {"ai": ai, "count_a": len(texts_a), "texts_b": texts_b}


async def test_vector_search_orders_by_cosine_distance(pg_session: AsyncSession, seeded: dict) -> None:
    ai: MockAIServices = seeded["ai"]  # type: ignore[assignment]
    query = await ai.embeddings.embed_query("scaled the kubernetes platform in production")
    hits = await vector_search(pg_session, Doc, query, limit=5)
    assert len(hits) == 5
    assert "kubernetes" in hits[0].item.text and "scaled" in hits[0].item.text
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)
    assert hits[0].similarity == pytest.approx(1 - hits[0].distance)
    assert hits[0].similarity > 0.95


async def test_filters_dict_in_none_and_expressions(pg_session: AsyncSession, seeded: dict) -> None:
    ai: MockAIServices = seeded["ai"]  # type: ignore[assignment]
    query = await ai.embeddings.embed_query("kafka platform")
    only_b = await vector_search(pg_session, Doc, query, filters={"organization_id": ORG_B}, limit=10)
    assert {h.item.organization_id for h in only_b} == {ORG_B} and len(only_b) == 3
    in_list = await vector_search(
        pg_session, Doc, query, filters={"organization_id": [ORG_A, ORG_B], "kind": None}, limit=10
    )
    assert {h.item.text for h in in_list} == set(seeded["texts_b"])
    expr = await vector_search(pg_session, Doc, query, filters=[Doc.text.ilike("%kafka%")], limit=4)
    assert all("kafka" in h.item.text for h in expr) and len(expr) == 4
    with pytest.raises(ValueError):
        await vector_search(pg_session, Doc, query, filters={"nope": 1})


async def test_filtered_hnsw_scan_returns_all_matches(pg_session: AsyncSession, seeded: dict) -> None:
    """Rows matching the filter are far from the query: a plain HNSW scan
    stops after ef_search candidates and would miss them."""
    ai: MockAIServices = seeded["ai"]  # type: ignore[assignment]
    await pg_session.execute(text("SET LOCAL enable_seqscan = off"))
    await pg_session.execute(text("SET LOCAL hnsw.ef_search = 10"))
    query = await ai.embeddings.embed_query("migrated the postgres platform")
    hits = await vector_search(pg_session, Doc, query, filters={"organization_id": ORG_B}, limit=3)
    assert len(hits) == 3


async def test_max_distance_and_near_duplicates(pg_session: AsyncSession, seeded: dict) -> None:
    ai: MockAIServices = seeded["ai"]  # type: ignore[assignment]
    vector = await ai.embeddings.embed_one("I tuned the redis platform in production")
    close = await vector_search(pg_session, Doc, vector, limit=50, max_distance=0.05)
    assert close and all(h.distance <= 0.05 for h in close)
    dup = await find_near_duplicate(pg_session, Doc, vector, threshold=0.97)
    assert dup is not None and dup.item.text == "I tuned the redis platform in production"
    dups = await find_near_duplicates(pg_session, Doc, vector, threshold=0.97, limit=10)
    assert len(dups) == 3  # the seed repeats every sentence three times
    unrelated = await ai.embeddings.embed_one("quantum chromodynamics lecture notes")
    assert await find_near_duplicate(pg_session, Doc, unrelated, threshold=0.9) is None


async def test_embed_and_store_by_ids(pg_session: AsyncSession, seeded: dict) -> None:
    ai: MockAIServices = seeded["ai"]  # type: ignore[assignment]
    pending = (await pg_session.execute(select(Doc).where(Doc.embedding.is_(None)))).scalars().all()
    assert len(pending) == 1
    updated = await embed_and_store_by_ids(pg_session, Doc, [pending[0].id], ai=ai)
    assert updated == 1
    again = await embed_and_store_by_ids(pg_session, Doc, [pending[0].id], ai=ai)
    assert again == 0  # idempotent
    query = await ai.embeddings.embed_query("Kafka consumer lag alerts")
    hits = await vector_search(pg_session, Doc, query, limit=1)
    assert hits[0].item.id == pending[0].id


async def test_retrieve_end_to_end_with_db_source(pg_session: AsyncSession, seeded: dict) -> None:
    ai: MockAIServices = seeded["ai"]  # type: ignore[assignment]
    source = DbCandidateSource(pg_session, Doc, text="text", filters={"organization_id": ORG_A})
    hits = await retrieve(
        "debugged golang platform",
        candidates=source,
        top_k=3,
        ai=ai,
        instruction="Find resume evidence for the topic",
    )
    assert len(hits) == 3
    assert hits[0].rerank_score == 1.0 and "golang" in hits[0].text and "debugged" in hits[0].text
    assert all(h.item.organization_id == ORG_A for h in hits)
