"""Question bank: listing, CRUD, semantic search and AI generation.

Search embeds the query (instruction-formatted), runs a filtered pgvector
search over the organization's and the global questions, and reranks the
pool with the cross-encoder (vector order is kept if the reranker is down).
When no question has an embedding yet (fresh install before the embedding
job ran) it falls back to a lexical match so the UI still works.

Generation asks the main model for a ``GeneratedQuestionSet``, embeds every
question and skips near-duplicates (cosine >= 0.92) of existing questions and
of each other; the rest are stored as ``source=generated``, ``status=draft``
for a human to review and activate.
"""

from __future__ import annotations

import functools
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIOutputInvalid, AIServices, CallContext
from app.ai.registry import get_ai
from app.ai.structured import describe_schema
from app.core.errors import NotFound, ServiceUnavailable, ValidationFailed
from app.core.logging import get_logger
from app.db.models import BankQuestion
from app.domain.enums import BankStatus, Difficulty, ModelTask, QuestionKind, QuestionSource, RoundType
from app.schemas.ai import GeneratedQuestion, GeneratedQuestionSet
from app.schemas.api.question_bank import BankQuestionCreate, BankQuestionUpdate, QuestionGenerateRequest
from app.services.jobs.catalog import competency_by_key, org_visible, unknown_competency_keys
from app.services.jobs.jobs import escape_like
from app.services.question_bank.embeddings import (
    bank_question_text,
    embed_rows_best_effort,
    enqueue_embedding,
    question_text,
)
from app.services.question_bank.prompts import GENERATION_SYSTEM, generation_messages
from app.services.resume.llm_schemas import required_variant, to_contract
from app.services.retrieval import (
    DEFAULT_THRESHOLD,
    DbCandidateSource,
    RetrievedItem,
    embed_documents,
    find_near_duplicate,
    is_near_duplicate,
    retrieve,
)

log = get_logger(__name__)

SEARCH_INSTRUCTION = (
    "Given an interview topic or question, retrieve interview questions that assess the same skill"
)
EMBED_FIELDS = frozenset({"text", "topic", "expected_concepts"})
GENERATION_TIMEOUT_SECONDS = 150.0
_SLUG = re.compile(r"[^a-z0-9]+")


@functools.lru_cache(maxsize=32)
def generation_wire_model(count: int) -> type[BaseModel]:
    """``GeneratedQuestionSet`` for the model with exactly ``count`` items and every
    question field required: guided decoding otherwise stops after one item
    (measured: 1 question returned for count=2) and skips defaulted fields."""
    question = required_variant(GeneratedQuestion)
    return create_model(  # type: ignore[call-overload,no-any-return]
        "GeneratedQuestionSet",
        __config__=ConfigDict(extra="ignore"),
        questions=(list[question], Field(..., min_length=count, max_length=count)),  # type: ignore[valid-type]
    )


def generation_schema_text(count: int) -> str:
    return (
        f"{describe_schema(generation_wire_model(count))}\n\nGeneratedQuestion:\n"
        f"{describe_schema(required_variant(GeneratedQuestion))}"
    )


def local_external_key(source: QuestionSource, question_id: uuid.UUID) -> str:
    """Unique ``external_key`` for questions created through the API.

    ``uq_question_bank_org_external_key`` is declared NULLS NOT DISTINCT, so a
    second NULL key in the same organization would collide; curated seeds keep
    their stable YAML keys, everything else gets ``<source>:<id>``."""
    return f"{source.value}:{question_id}"


def role_tag(value: str) -> str:
    return _SLUG.sub("_", value.strip().lower()).strip("_")[:60] or "general"


async def get_question(db: AsyncSession, question_id: uuid.UUID, organization_id: uuid.UUID) -> BankQuestion:
    row = await db.get(BankQuestion, question_id)
    if row is None or (row.organization_id is not None and row.organization_id != organization_id):
        raise NotFound("Question not found.")
    return row


def question_list_query(
    organization_id: uuid.UUID,
    *,
    competency_key: str | None = None,
    difficulty: Difficulty | None = None,
    kind: QuestionKind | None = None,
    status: BankStatus | None = None,
    source: QuestionSource | None = None,
    round_type: RoundType | None = None,
    role: str | None = None,
    scope: str | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = select(BankQuestion).where(org_visible(BankQuestion.organization_id, organization_id))
    if scope == "organization":
        stmt = stmt.where(BankQuestion.organization_id == organization_id)
    elif scope == "global":
        stmt = stmt.where(BankQuestion.organization_id.is_(None))
    if competency_key:
        stmt = stmt.where(BankQuestion.competency_key == competency_key)
    if difficulty is not None:
        stmt = stmt.where(BankQuestion.difficulty == difficulty.value)
    if kind is not None:
        stmt = stmt.where(BankQuestion.kind == kind.value)
    if status is not None:
        stmt = stmt.where(BankQuestion.status == status.value)
    if source is not None:
        stmt = stmt.where(BankQuestion.source == source.value)
    if round_type is not None:
        stmt = stmt.where(BankQuestion.round_types.contains([round_type.value]))
    if role:
        stmt = stmt.where(BankQuestion.role_tags.contains([role_tag(role)]))
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(
            or_(BankQuestion.text.ilike(pattern, escape="\\"), BankQuestion.topic.ilike(pattern, escape="\\"))
        )
    return stmt


async def _check_competency(db: AsyncSession, key: str, organization_id: uuid.UUID) -> None:
    if await unknown_competency_keys(db, [key], organization_id):
        raise ValidationFailed(
            "Unknown competency.", code="unknown_competency", details={"field": "competency_key"}
        )


def _rubric(body_rubric: Any) -> dict[str, Any]:
    data = body_rubric.model_dump(exclude_none=True) if body_rubric is not None else {}
    if not data.get("dimensions"):
        data.pop("dimensions", None)
    return data


async def _embed(db: AsyncSession, rows: list[BankQuestion]) -> None:
    if not await embed_rows_best_effort(db, rows, question_text):
        await enqueue_embedding(db, question_ids=[r.id for r in rows if r.embedding is None])


async def create_question(
    db: AsyncSession, *, organization_id: uuid.UUID, created_by_id: uuid.UUID | None, body: BankQuestionCreate
) -> BankQuestion:
    await _check_competency(db, body.competency_key, organization_id)
    question_id = uuid.uuid4()
    row = BankQuestion(
        id=question_id,
        external_key=local_external_key(QuestionSource.CURATED, question_id),
        organization_id=organization_id,
        role_tags=sorted({role_tag(t) for t in body.role_tags}),
        round_types=[rt.value for rt in dict.fromkeys(body.round_types)],
        competency_key=body.competency_key,
        topic=body.topic,
        difficulty=body.difficulty.value,
        kind=body.kind.value,
        text=body.text.strip(),
        expected_concepts=list(body.expected_concepts),
        rubric=_rubric(body.rubric),
        followups=list(body.followups),
        common_errors=list(body.common_errors),
        source=QuestionSource.CURATED.value,
        status=body.status.value,
        created_by_id=created_by_id,
    )
    db.add(row)
    await db.flush()
    await _embed(db, [row])
    return row


async def update_question(
    db: AsyncSession, row: BankQuestion, body: BankQuestionUpdate, organization_id: uuid.UUID
) -> list[str]:
    changed: list[str] = []
    for name in sorted(body.model_fields_set):
        value = getattr(body, name)
        if value is None and name not in ("topic",):
            continue
        if name == "competency_key":
            await _check_competency(db, value, organization_id)
        elif name in ("difficulty", "kind", "status"):
            value = value.value
        elif name == "role_tags":
            value = sorted({role_tag(t) for t in value})
        elif name == "round_types":
            value = [rt.value for rt in dict.fromkeys(value)]
        elif name == "rubric":
            value = _rubric(value)
        elif name == "text":
            value = value.strip()
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed.append(name)
    if EMBED_FIELDS & set(changed):
        row.embedding = None
    await db.flush()
    if row.embedding is None:
        await _embed(db, [row])
    return changed


@dataclass(slots=True)
class SearchHit:
    question: BankQuestion
    score: float | None
    vector_similarity: float | None = None
    rerank_score: float | None = None


@dataclass(slots=True)
class SearchOutcome:
    hits: list[SearchHit] = field(default_factory=list)
    mode: str = "semantic"
    reranked: bool = False


def _search_filters(
    organization_id: uuid.UUID,
    *,
    competency_key: str | None,
    difficulty: Difficulty | None,
    kind: QuestionKind | None,
    round_type: RoundType | None,
    status: BankStatus | None,
) -> list[Any]:
    filters: list[Any] = [org_visible(BankQuestion.organization_id, organization_id)]
    if status is not None:
        filters.append(BankQuestion.status == status.value)
    if competency_key:
        filters.append(BankQuestion.competency_key == competency_key)
    if difficulty is not None:
        filters.append(BankQuestion.difficulty == difficulty.value)
    if kind is not None:
        filters.append(BankQuestion.kind == kind.value)
    if round_type is not None:
        filters.append(BankQuestion.round_types.contains([round_type.value]))
    return filters


async def search_questions(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    query: str,
    competency_key: str | None = None,
    difficulty: Difficulty | None = None,
    kind: QuestionKind | None = None,
    round_type: RoundType | None = None,
    status: BankStatus | None = BankStatus.ACTIVE,
    limit: int = 10,
    ai: AIServices | None = None,
) -> SearchOutcome:
    filters = _search_filters(
        organization_id,
        competency_key=competency_key,
        difficulty=difficulty,
        kind=kind,
        round_type=round_type,
        status=status,
    )
    services = ai or get_ai()
    items: list[RetrievedItem[BankQuestion]]
    try:
        items = await retrieve(
            query,
            candidates=DbCandidateSource(db, BankQuestion, text=question_text, filters=filters),
            top_k=limit,
            instruction=SEARCH_INSTRUCTION,
            pool_size=max(limit * 3, 30),
            ai=services,
            context=CallContext(task=ModelTask.EMBEDDING, organization_id=str(organization_id)),
        )
    except AIError as exc:
        log.warning("question_bank.search_semantic_failed", error=type(exc).__name__)
        items = []
    if items:
        return SearchOutcome(
            hits=[SearchHit(i.item, i.score, i.vector_similarity, i.rerank_score) for i in items],
            mode="semantic",
            reranked=any(i.rerank_score is not None for i in items),
        )
    words = [w for w in re.findall(r"[A-Za-z0-9+#]{3,}", query)][:8]
    if not words:
        return SearchOutcome(mode="lexical")
    clauses = [BankQuestion.text.ilike(f"%{escape_like(w)}%", escape="\\") for w in words]
    clauses += [BankQuestion.topic.ilike(f"%{escape_like(w)}%", escape="\\") for w in words]
    rows = (await db.scalars(select(BankQuestion).where(*filters, or_(*clauses)).limit(limit * 5))).all()
    lowered = [w.lower() for w in words]
    scored = []
    for row in rows:
        haystack = f"{row.topic or ''} {row.text}".lower()
        score = sum(1 for w in lowered if w in haystack) / len(lowered)
        scored.append(SearchHit(row, round(score, 3)))
    scored.sort(key=lambda hit: -(hit.score or 0.0))
    return SearchOutcome(hits=scored[:limit], mode="lexical")


@dataclass(slots=True)
class GenerationOutcome:
    created: list[BankQuestion]
    skipped: list[dict[str, Any]]
    generated: int
    model_call_id: str | None


async def generate_questions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    created_by_id: uuid.UUID | None,
    body: QuestionGenerateRequest,
    ai: AIServices | None = None,
) -> GenerationOutcome:
    competency = await competency_by_key(db, body.competency_key, organization_id)
    if competency is None or not competency.is_active:
        raise ValidationFailed(
            "Unknown competency.", code="unknown_competency", details={"field": "competency_key"}
        )
    services = ai or get_ai()
    existing_rows = (
        await db.scalars(
            select(BankQuestion.text)
            .where(
                org_visible(BankQuestion.organization_id, organization_id),
                BankQuestion.competency_key == competency.key,
                BankQuestion.status != BankStatus.RETIRED.value,
            )
            .order_by(BankQuestion.created_at.desc())
            .limit(25)
        )
    ).all()
    levels = "; ".join(f"{k}: {v}" for k, v in sorted((competency.rubric or {}).get("levels", {}).items()))
    messages = generation_messages(
        schema=generation_schema_text(body.count),
        count=body.count,
        role=body.role,
        competency={
            "key": competency.key,
            "name": competency.name,
            "category": competency.category,
            "description": competency.description,
        },
        levels=levels,
        difficulty=body.difficulty.value,
        topic=body.topic,
        kind=body.kind.value if body.kind else None,
        existing=[text[:240] for text in existing_rows],
    )
    try:
        result = await services.llm.structured(
            generation_wire_model(body.count),
            messages,
            context=CallContext(
                task=ModelTask.QUESTION_GENERATION,
                organization_id=str(organization_id),
                prompt_version=GENERATION_SYSTEM.version,
            ),
            temperature=0.7,
            max_tokens=min(1200 + 450 * body.count, 9000),
            timeout=GENERATION_TIMEOUT_SECONDS,
            max_attempts=2,
        )
    except AIOutputInvalid as exc:
        raise ServiceUnavailable(
            "The model could not produce valid questions. Please try again.", code="generation_failed"
        ) from exc
    except AIError as exc:
        raise ServiceUnavailable(
            "Question generation is temporarily unavailable. Please try again shortly.",
            code="generation_unavailable",
            retry_after=30,
        ) from exc

    generated = to_contract(result.value, GeneratedQuestionSet).questions[: body.count]
    texts = [bank_question_text(q.topic, q.text.strip(), q.expected_concepts) for q in generated]
    try:
        vectors = await embed_documents(
            services.embeddings,
            texts,
            context=CallContext(task=ModelTask.EMBEDDING, organization_id=str(organization_id)),
        )
    except AIError as exc:
        raise ServiceUnavailable(
            "Question generation is temporarily unavailable (embeddings). Please try again shortly.",
            code="generation_unavailable",
            retry_after=30,
        ) from exc
    dedupe_filters = [
        org_visible(BankQuestion.organization_id, organization_id),
        BankQuestion.status != BankStatus.RETIRED.value,
    ]
    created: list[BankQuestion] = []
    accepted_vectors: list[list[float]] = []
    skipped: list[dict[str, Any]] = []
    for question, vector in zip(generated, vectors, strict=True):
        if any(is_near_duplicate(vector, other) for other in accepted_vectors):
            skipped.append({"text": question.text, "reason": "duplicate_in_batch"})
            continue
        duplicate = await find_near_duplicate(
            db, BankQuestion, vector, threshold=DEFAULT_THRESHOLD, filters=dedupe_filters
        )
        if duplicate is not None:
            skipped.append(
                {
                    "text": question.text,
                    "reason": "near_duplicate",
                    "duplicate_of": duplicate.item.id,
                    "similarity": round(duplicate.similarity, 4),
                }
            )
            continue
        rubric = {
            k: v
            for k, v in {"excellent": question.rubric_excellent, "weak": question.rubric_weak}.items()
            if v
        }
        question_id = uuid.uuid4()
        row = BankQuestion(
            id=question_id,
            external_key=local_external_key(QuestionSource.GENERATED, question_id),
            organization_id=organization_id,
            role_tags=[role_tag(body.role)],
            round_types=[body.round_type.value] if body.round_type else [],
            competency_key=competency.key,
            topic=question.topic,
            difficulty=body.difficulty.value,
            kind=(body.kind or question.kind).value,
            text=question.text.strip(),
            expected_concepts=list(question.expected_concepts),
            rubric=rubric,
            followups=list(question.followups),
            common_errors=list(question.common_errors),
            source=QuestionSource.GENERATED.value,
            status=BankStatus.DRAFT.value,
            created_by_id=created_by_id,
            embedding=vector,
        )
        db.add(row)
        await db.flush()  # visible to the next near-duplicate query
        created.append(row)
        accepted_vectors.append(vector)
    log.info(
        "question_bank.generated",
        competency=competency.key,
        requested=body.count,
        generated=len(generated),
        created=len(created),
        skipped=len(skipped),
    )
    return GenerationOutcome(
        created=created, skipped=skipped, generated=len(generated), model_call_id=result.model_call_id
    )
