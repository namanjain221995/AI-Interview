"""Coding problem selection: pinned > retrieved > generated, never repeating a
problem inside one interview session.

Retrieval at catalog scale (~1k problems, a difficulty tier of ~300-500):

1. :func:`candidate_pool` loads only the light columns of the organization's
   and the global active problems (no prompt, tests, starters, solutions or
   vector; status, scope, used ids and languages are filtered in SQL, the exact
   language rule and the per-slug organization override in Python).
2. :func:`rank_questions` keeps the closest difficulty tier, embeds the query
   and asks pgvector for the nearest problems of that tier. A few problems
   with competency overlap and a few not-yet-embedded problems (deterministic
   order) join them, so the reranker sees at most ``PREFILTER_TOP_K``
   documents instead of the whole tier. Only those rows' prompt text is read;
   vectors computed on the fly are stored (same text as the catalog seeder).
   When no problem of the tier has a vector yet, the deterministic ranking
   picks the ``PREFILTER_TOP_K`` documents instead.
3. Reranker score + competency overlap + exact-difficulty bonus pick the
   problem. Its heavy columns load when the coding session starts.

If the embedding endpoint is down, a deterministic ranking (competency
overlap, difficulty distance, slug) over the whole tier keeps selection
working. Competencies are a ranking signal, not a filter (as before); rounds
carry no role tags for coding problems.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from pgvector.sqlalchemy import Vector
from sqlalchemy import Text, bindparam, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.core import metrics
from app.core.logging import get_logger
from app.db.base import EMBEDDING_DIM
from app.db.models import CodingQuestion, CodingSession, Question
from app.domain.enums import DIFFICULTY_ORDER, BankStatus, CodeLanguage, Difficulty, ModelTask
from app.schemas.rounds import CodingSection
from app.services.coding.questions import allowed_languages, supports_any
from app.services.retrieval import RetrievalCandidate, cosine_similarity, embed_documents, retrieve

log = get_logger(__name__)

RETRIEVAL_INSTRUCTION = "Given the competencies and difficulty an interviewer wants to assess, retrieve the most suitable coding problem"
OVERLAP_WEIGHT = 0.5
EXACT_DIFFICULTY_BONUS = 0.15
#: Documents the reranker sees at most (live: 30 docs ~0.9 s, 300 docs ~6 s).
PREFILTER_TOP_K = 30
#: Problems with competency overlap added beside the nearest vectors.
OVERLAP_QUOTA = 6
#: Not-yet-embedded problems considered (and embedded) per selection while
#: other problems of the tier already have vectors.
MISSING_QUOTA = 6
PROMPT_TEXT_CHARS = 1500  # = embedding_text() / CodingQuestionSeed.embedding_text()
#: Columns ``candidate_pool`` loads; everything else stays deferred.
LIGHT_COLUMNS = (
    CodingQuestion.id,
    CodingQuestion.organization_id,
    CodingQuestion.slug,
    CodingQuestion.title,
    CodingQuestion.difficulty,
    CodingQuestion.competency_keys,
    CodingQuestion.role_tags,
    CodingQuestion.languages,
    CodingQuestion.status,
    CodingQuestion.source,
    CodingQuestion.time_limit_minutes,
)


@dataclass(slots=True)
class RankedQuestion:
    question: CodingQuestion
    score: float
    overlap: float
    retrieval_score: float | None


def _uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


async def used_question_ids(db: AsyncSession, session_id: uuid.UUID) -> set[uuid.UUID]:
    """Coding problems already used (or asked) in this interview session."""
    used = set(
        (
            await db.scalars(
                select(CodingSession.coding_question_id).where(CodingSession.session_id == session_id)
            )
        ).all()
    )
    asked = (
        await db.scalars(
            select(Question.coding_question_id).where(
                Question.session_id == session_id, Question.coding_question_id.is_not(None)
            )
        )
    ).all()
    used.update(q for q in asked if q is not None)
    return used


def accessible(question: CodingQuestion, organization_id: uuid.UUID) -> bool:
    return question.organization_id is None or question.organization_id == organization_id


def difficulty_distance(a: str | Difficulty, b: str | Difficulty) -> int:
    try:
        return abs(DIFFICULTY_ORDER.index(Difficulty(a)) - DIFFICULTY_ORDER.index(Difficulty(b)))
    except ValueError:
        return 2


def competency_overlap(question: CodingQuestion, competency_keys: Sequence[str]) -> float:
    wanted = {k for k in competency_keys if k}
    if not wanted:
        return 0.0
    return len(wanted & set(question.competency_keys or [])) / len(wanted)


async def pinned_question(
    db: AsyncSession,
    section: CodingSection,
    *,
    organization_id: uuid.UUID,
    used: set[uuid.UUID],
    languages: Sequence[CodeLanguage],
) -> CodingQuestion | None:
    for raw in section.question_ids:
        qid = _uuid(raw)
        if qid is None or qid in used:
            continue
        question = await db.get(CodingQuestion, qid)
        if (
            question is not None
            and question.status != BankStatus.RETIRED.value
            and accessible(question, organization_id)
            and supports_any(question, languages)
        ):
            return question
    return None


async def candidate_pool(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    used: set[uuid.UUID],
    languages: Sequence[CodeLanguage],
    section: CodingSection,
) -> list[CodingQuestion]:
    """Usable problems with only :data:`LIGHT_COLUMNS` loaded (the prompt,
    tests, starters, solutions and vector are not read)."""
    stmt = (
        select(CodingQuestion)
        .options(load_only(*LIGHT_COLUMNS))
        .where(
            CodingQuestion.status == BankStatus.ACTIVE.value,
            or_(CodingQuestion.organization_id.is_(None), CodingQuestion.organization_id == organization_id),
        )
    )
    if used:
        stmt = stmt.where(CodingQuestion.id.not_in(list(used)))
    wanted = set(languages)
    if wanted:
        # Superset of ``usable`` below: a problem must list a wanted language
        # (SQL problems list only "sql"); the round restriction is applied in Python.
        values = sorted(lang.value for lang in wanted)
        stmt = stmt.where(CodingQuestion.languages.has_any(cast(values, ARRAY(Text))))
    rows = (await db.scalars(stmt.order_by(CodingQuestion.slug))).all()

    def usable(question: CodingQuestion) -> bool:
        allowed = set(allowed_languages(question, section, strict=True))
        return bool(allowed & wanted) if wanted else bool(allowed)

    pool = [q for q in rows if usable(q)]
    # Prefer an organization's own version of a slug over the global one.
    by_slug: dict[str, CodingQuestion] = {}
    for q in pool:
        current = by_slug.get(q.slug)
        if current is None or (current.organization_id is None and q.organization_id is not None):
            by_slug[q.slug] = q
    return list(by_slug.values())


def difficulty_tier(pool: Sequence[CodingQuestion], difficulty: Difficulty) -> list[CodingQuestion]:
    """Exact difficulty first; otherwise the closest available level."""
    for distance in (0, 1, 2):
        tier = [q for q in pool if difficulty_distance(q.difficulty, difficulty) == distance]
        if tier:
            return tier
    return list(pool)


def deterministic_rank(
    pool: Sequence[CodingQuestion], competency_keys: Sequence[str], difficulty: Difficulty
) -> list[RankedQuestion]:
    ranked = [
        RankedQuestion(
            q,
            competency_overlap(q, competency_keys) - 0.1 * difficulty_distance(q.difficulty, difficulty),
            competency_overlap(q, competency_keys),
            None,
        )
        for q in pool
    ]
    ranked.sort(key=lambda r: (-r.score, r.question.slug))
    return ranked


@dataclass(slots=True)
class _TierSearch:
    """Candidate source for :func:`retrieve`: pgvector top-k of the tier plus
    competency-overlap and not-yet-embedded quotas, at most ``limit`` rows."""

    db: AsyncSession
    tier: Sequence[CodingQuestion]
    keys: Sequence[str]
    difficulty: Difficulty
    ai: AIServices | None
    context: CallContext
    #: Vectors computed for rows that had none (stored after a successful retrieval).
    computed: dict[uuid.UUID, list[float]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    async def __call__(
        self, query_vector: list[float], limit: int
    ) -> list[RetrievalCandidate[CodingQuestion]]:
        by_id = {q.id: q for q in self.tier}
        missing_ids = set(
            (
                await self.db.scalars(
                    select(CodingQuestion.id).where(
                        CodingQuestion.id.in_(list(by_id)), CodingQuestion.embedding.is_(None)
                    )
                )
            ).all()
        )
        embedded = [q for q in self.tier if q.id not in missing_ids]
        missing = [q for q in self.tier if q.id in missing_ids]
        fresh_quota = min(len(missing), MISSING_QUOTA if embedded else limit)
        fresh = [r.question for r in deterministic_rank(missing, self.keys, self.difficulty)[:fresh_quota]]
        vector_limit = limit - len(fresh)
        nearest = await self._distances([q.id for q in embedded], query_vector, limit=vector_limit)
        chosen = {qid for qid, _ in nearest}
        extras = await self._overlap_extras(
            [q for q in embedded if q.id not in chosen], query_vector, min(OVERLAP_QUOTA, vector_limit)
        )
        if extras:
            nearest = nearest[: vector_limit - len(extras)] + extras
        similarity = {qid: 1.0 - distance for qid, distance in nearest}
        texts = await self._texts([*similarity, *(q.id for q in fresh)])
        if fresh:
            services = self.ai or get_ai()
            vectors = await embed_documents(
                services.embeddings, [texts[q.id] for q in fresh], context=self.context
            )
            for question, vector in zip(fresh, vectors, strict=True):
                self.computed[question.id] = vector
                similarity[question.id] = cosine_similarity(vector, query_vector)
        self.stats = {
            "tier": len(self.tier),
            "embedded": len(embedded),
            "vector_hits": len(nearest) - len(extras),
            "overlap_extras": len(extras),
            "embedded_on_the_fly": len(fresh),
        }
        order = sorted(similarity, key=lambda qid: (-similarity[qid], by_id[qid].slug))
        return [
            RetrievalCandidate(
                item=by_id[qid],
                text=texts[qid],
                embedding=self.computed.get(qid),
                vector_similarity=similarity[qid],
            )
            for qid in order
        ]

    async def _overlap_extras(
        self, rows: Sequence[CodingQuestion], query_vector: Sequence[float], quota: int
    ) -> list[tuple[uuid.UUID, float]]:
        """Up to ``quota`` problems with the best competency overlap (nearest
        first within an overlap level), so a strong competency match the vector
        ranking missed still reaches the reranker."""
        levels: dict[float, list[uuid.UUID]] = {}
        for question in rows:
            overlap = competency_overlap(question, self.keys)
            if overlap > 0:
                levels.setdefault(overlap, []).append(question.id)
        extras: list[tuple[uuid.UUID, float]] = []
        for overlap in sorted(levels, reverse=True):
            if len(extras) >= quota:
                break
            extras += await self._distances(levels[overlap], query_vector, limit=quota - len(extras))
        return extras

    async def _distances(
        self, ids: Sequence[uuid.UUID], query_vector: Sequence[float], *, limit: int | None = None
    ) -> list[tuple[uuid.UUID, float]]:
        """``(id, cosine distance)`` nearest first; only ids and floats cross the wire."""
        if not ids or (limit is not None and limit <= 0):
            return []
        distance = CodingQuestion.embedding.cosine_distance([float(x) for x in query_vector])
        stmt = (
            select(CodingQuestion.id, distance.label("distance"))
            .where(CodingQuestion.id.in_(list(ids)), CodingQuestion.embedding.is_not(None))
            .order_by(distance, CodingQuestion.slug)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        with metrics.DB_QUERY_SECONDS.labels(operation="vector_search").time():
            rows = (await self.db.execute(stmt)).all()
        return [(row[0], float(row[1])) for row in rows]

    async def _texts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Embedding/rerank text (= ``embedding_text()``) read for ``ids`` only."""
        if not ids:
            return {}
        rows = (
            await self.db.execute(
                select(
                    CodingQuestion.id,
                    CodingQuestion.title,
                    func.left(CodingQuestion.prompt_markdown, PROMPT_TEXT_CHARS),
                    CodingQuestion.expected_concepts,
                ).where(CodingQuestion.id.in_(list(ids)))
            )
        ).all()
        return {
            qid: f"{title}. {prompt or ''} Concepts: {', '.join(concepts or [])}"
            for qid, title, prompt, concepts in rows
        }

    async def store_computed(self) -> None:
        """Store vectors computed on the fly; a row embedded meanwhile keeps its own."""
        if not self.computed:
            return
        table = CodingQuestion.__table__
        stmt = (
            update(table)
            .where(table.c.id == bindparam("row_id"), table.c.embedding.is_(None))
            .values(embedding=bindparam("vector", type_=Vector(EMBEDDING_DIM)))
        )
        await self.db.execute(
            stmt, [{"row_id": qid, "vector": vector} for qid, vector in self.computed.items()]
        )


async def rank_questions(
    db: AsyncSession,
    pool: Sequence[CodingQuestion],
    *,
    competency_keys: Sequence[str],
    difficulty: Difficulty,
    ai: AIServices | None = None,
    session_id: str | None = None,
    organization_id: str | None = None,
) -> list[RankedQuestion]:
    """Best problems of the closest difficulty tier, best first (at most
    ``PREFILTER_TOP_K`` after retrieval; the whole tier on the deterministic
    fallback). ``pool`` rows only need :data:`LIGHT_COLUMNS`."""
    if not pool:
        return []
    tier = difficulty_tier(pool, difficulty)
    keys = [k for k in competency_keys if k]
    query = (
        f"Coding interview problem assessing {', '.join(k.replace('_', ' ') for k in keys) or 'general problem solving'} "
        f"at {difficulty.value} difficulty"
    )
    context = CallContext(task=ModelTask.EMBEDDING, session_id=session_id, organization_id=organization_id)
    search = _TierSearch(db, tier, keys, difficulty, ai, context)
    try:
        hits = await retrieve(
            query,
            candidates=search,
            top_k=PREFILTER_TOP_K,
            pool_size=PREFILTER_TOP_K,
            rerank=True,
            instruction=RETRIEVAL_INSTRUCTION,
            ai=ai,
            context=context,
        )
    except AIError as exc:
        log.warning("coding.selection.retrieval_failed", error=type(exc).__name__, candidates=len(tier))
        return deterministic_rank(tier, keys, difficulty)
    if not hits:
        return deterministic_rank(tier, keys, difficulty)
    await search.store_computed()
    log.info("coding.selection.prefilter", **search.stats)
    ranked: list[RankedQuestion] = []
    for hit in hits:
        overlap = competency_overlap(hit.item, keys)
        bonus = EXACT_DIFFICULTY_BONUS if difficulty_distance(hit.item.difficulty, difficulty) == 0 else 0.0
        ranked.append(
            RankedQuestion(hit.item, hit.score + OVERLAP_WEIGHT * overlap + bonus, overlap, hit.score)
        )
    ranked.sort(key=lambda r: (-r.score, r.question.slug))
    return ranked
