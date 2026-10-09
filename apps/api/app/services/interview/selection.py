"""Question-bank retrieval and near-duplicate prevention.

``bank_candidates`` finds the best curated/generated bank questions for a
target competency and difficulty. Bank questions are inspiration only: the
model writes its own question from the candidate's resume and answers.

1. pool = active bank rows for the competency (this organization or global)
   at the target difficulty or an adjacent one, written for the session's
   round type (``round_types`` contains it, or is empty = any round; combined
   and custom rounds also accept technical, behavioural and problem-solving
   items, :func:`accepted_round_types`). Rows with an embedding come from
   pgvector search (iterative scan with the filters), rows still lacking one
   (curated seeds before the ``question_bank.embed`` job ran) are embedded in
   memory. When fewer than ``MIN_ROUND_MATCHES`` rows fit the round, rows of
   other round types join the pool as a fallback ("off-round");
2. ``app.services.retrieval.retrieve`` ranks the pool (query embedding ->
   cosine -> reranker; reranker outages keep the vector order);
3. questions already asked in the session (by bank id) and near-duplicates of
   asked questions (cosine >= 0.88 against the asked questions' embeddings)
   are removed. Order: on-round before off-round, then exact difficulty, then
   score (+ ``ROLE_BOOST`` when a ``role_tags`` entry matches the job's role,
   :func:`job_role_tags`; - ``OFF_ROUND_PENALTY`` for off-round rows).

Every asked question gets an embedding (``embed_question``) so later
selections and the post-hoc duplicate check can compare against it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIServices, CallContext
from app.ai.registry import get_ai
from app.core.logging import get_logger
from app.db.models import BankQuestion, Question
from app.domain.enums import BankStatus, Difficulty, ModelTask, RoundType
from app.services.interview import difficulty as diff
from app.services.interview.policy import BankCandidate
from app.services.retrieval import RetrievalCandidate, embed_documents, retrieve, vector_search

log = get_logger(__name__)

QUERY_INSTRUCTION = (
    "Given an interview topic and target skill, retrieve interview questions that probe it well"
)
RERANK_INSTRUCTION = (
    "Judge whether the interview question is a good next question for the described topic and level"
)
NEAR_DUPLICATE_THRESHOLD = 0.88
#: Rows fetched per pool query (vector search / rows without an embedding).
POOL_LIMIT = 40
#: Combined and custom rounds mix question styles: besides their own type they accept items written for these.
MIXED_ROUND_SOURCES: tuple[RoundType, ...] = (
    RoundType.TECHNICAL_SCREENING,
    RoundType.DEEP_TECHNICAL,
    RoundType.BEHAVIORAL,
    RoundType.PROBLEM_SOLVING,
)
_MIXED_ROUNDS = frozenset({RoundType.COMBINED.value, RoundType.CUSTOM.value})
#: Fewer on-round rows than this -> rows of other round types join the pool (off-round, penalised).
MIN_ROUND_MATCHES = 6
#: Off-round rows fetched when the round filter is relaxed.
RELAXED_POOL_LIMIT = 20
#: Score adjustments on the retrieval score (reranker probability or cosine, 0..1).
OFF_ROUND_PENALTY = 0.2
ROLE_BOOST = 0.08

#: Job title/department -> ``role_tags`` keys (catalog/roles.yaml vocabulary). Matched on the normalised text
#: (lower case, punctuation -> spaces); several roles can match ("Senior DevOps / SRE Engineer").
_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (key, re.compile(pattern))
    for key, pattern in (
        ("devops", r"\bdev ?ops\b|\bdevsecops\b|\brelease engineer"),
        ("sre", r"\bsre\b|\bsite reliability\b|\breliability engineer"),
        ("platform", r"\bplatform (?:engineer|engineering|team|architect)"),
        ("cloud", r"\bcloud\b"),
        ("security", r"\bsecurity\b|\bsecops\b|\bdevsecops\b|\bappsec\b|\bcyber"),
        ("network", r"\bnetwork(?:ing)? (?:engineer|administrator|architect)"),
        ("dba", r"\bdba\b|\bdatabase (?:engineer|administrator|admin|reliability)"),
        ("backend", r"\bback ?end\b|\bserver side\b|\bapi (?:engineer|developer)"),
        ("frontend", r"\bfront ?end\b|\bui (?:engineer|developer)\b|\breact (?:engineer|developer)"),
        ("fullstack", r"\bfull ?stack\b"),
        ("mobile", r"\bmobile\b|\bios\b|\bandroid\b|\breact native\b|\bflutter\b"),
        ("qa", r"\bqa\b|\bquality (?:assurance|engineer)|\btest (?:automation|engineer)|\bsdet\b"),
        ("data_scientist", r"\bdata scien"),
        (
            "data_analyst",
            r"\bdata analyst|\bbi (?:analyst|developer)|\banalytics engineer|\bbusiness intelligence",
        ),
        ("data", r"\bdata (?:engineer|platform|infrastructure)|\betl\b"),
        ("ml", r"\bmachine learning\b|\bml\b|\bmlops\b"),
        ("llm", r"\bllm\b|\bgenerative ai\b|\bgen ?ai\b|\bai engineer|\bprompt engineer"),
        (
            "tech_lead",
            r"\btech(?:nical)? lead\b|\blead (?:engineer|developer)\b|\bstaff engineer|\bprincipal engineer",
        ),
        (
            "engineering_manager",
            r"\bengineering manager|\bhead of engineering|\b(?:director|vp|vice president) of engineering",
        ),
        ("product_manager", r"\bproduct (?:manager|owner)\b"),
        ("project_manager", r"\bproject manager|\bscrum master|\bprogram manager|\bdelivery manager"),
        ("business_analyst", r"\bbusiness analyst"),
        ("ux_designer", r"\bux\b|\bproduct designer|\binteraction designer|\bui designer"),
        ("support_engineer", r"\bsupport engineer|\btechnical support|\bhelp ?desk"),
    )
)


@dataclass(slots=True)
class AskedVector:
    question_id: str
    vector: list[float]


def build_query(
    *,
    competency_name: str,
    topics: Sequence[str],
    job_title: str | None,
    round_type: str | None,
    difficulty: Difficulty,
) -> str:
    parts = [f"{competency_name} interview question ({difficulty.value})"]
    if topics:
        parts.append("covering " + ", ".join(topics[:4]))
    if job_title:
        parts.append(f"for a {job_title}")
    if round_type:
        parts.append(f"in a {round_type.replace('_', ' ')} round")
    return " ".join(parts)


def accepted_round_types(round_type: RoundType | str | None) -> list[str]:
    """Round types whose bank items suit a ``round_type`` round ([] = no round filter)."""
    if round_type is None or str(round_type) == "":
        return []
    value = round_type.value if isinstance(round_type, RoundType) else str(round_type)
    if value in _MIXED_ROUNDS:
        return [value, *(rt.value for rt in MIXED_ROUND_SOURCES)]
    return [value]


def round_type_clause(accepted: Sequence[str]) -> ColumnElement[bool]:
    """Rows written for one of ``accepted`` round types, or for any round (empty ``round_types``)."""
    return or_(
        *(BankQuestion.round_types.contains([rt]) for rt in accepted),
        func.jsonb_array_length(BankQuestion.round_types) == 0,
    )


def fits_round(row_round_types: Sequence[str] | None, accepted: Sequence[str]) -> bool:
    if not accepted or not row_round_types:
        return True
    return any(rt in accepted for rt in row_round_types)


def _role_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def job_role_tags(job: Mapping[str, Any] | None) -> list[str]:
    """Role keys (``role_tags`` vocabulary) the job's title or department names, in pattern order; [] when
    nothing matches ("Software Engineer") - then no role boost applies."""
    if not job:
        return []
    text = " ".join(_role_text(str(job.get(field) or "")) for field in ("title", "department")).strip()
    if not text:
        return []
    return [key for key, pattern in _ROLE_PATTERNS if pattern.search(text)]


def _difficulty_levels(target: Difficulty) -> list[str]:
    lv = diff.level(target)
    levels = [target.value]
    for delta in (-1, 1):
        neighbour = lv + delta
        if 0 <= neighbour <= 2:
            levels.append(diff.from_level(neighbour).value)
    return levels


def max_similarity(vector: Sequence[float], others: Sequence[Sequence[float]]) -> float:
    if not others:
        return 0.0
    v = np.asarray(vector, dtype=np.float64)
    m = np.asarray(others, dtype=np.float64)
    norms = np.linalg.norm(m, axis=1) * (np.linalg.norm(v) or 1.0)
    norms[norms == 0] = 1.0
    return float(np.max(m @ v / norms))


async def bank_candidates(
    db: AsyncSession,
    *,
    organization_id: str,
    competency_key: str,
    competency_name: str,
    difficulty: Difficulty,
    topics: Sequence[str] = (),
    job_title: str | None = None,
    round_type: RoundType | str | None = None,
    role_tags: Sequence[str] = (),
    exclude_ids: Sequence[str] = (),
    asked_vectors: Sequence[Sequence[float]] = (),
    limit: int = 3,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
    session_id: str | None = None,
    ai: AIServices | None = None,
    min_round_matches: int = MIN_ROUND_MATCHES,
) -> list[BankCandidate]:
    services = ai or get_ai()
    org = uuid.UUID(str(organization_id))
    base_filters = [
        BankQuestion.status == BankStatus.ACTIVE.value,
        BankQuestion.competency_key == competency_key,
        or_(BankQuestion.organization_id == org, BankQuestion.organization_id.is_(None)),
        BankQuestion.difficulty.in_(_difficulty_levels(difficulty)),
    ]
    accepted = accepted_round_types(round_type)
    filters = [*base_filters, round_type_clause(accepted)] if accepted else base_filters
    roles = {str(r) for r in role_tags if r}
    excluded = {str(x) for x in exclude_ids if x}
    query = build_query(
        competency_name=competency_name,
        topics=topics,
        job_title=job_title,
        round_type=round_type,
        difficulty=difficulty,
    )
    context = CallContext(
        task=ModelTask.EMBEDDING, session_id=session_id, organization_id=str(organization_id)
    )
    pool: list[RetrievalCandidate[BankQuestion]] = []
    seen: set[str] = set()
    query_vec: list[float] | None = None

    async def collect(clauses: Sequence[Any], limit_rows: int) -> int:
        """Add rows matching ``clauses`` to the pool: rows with embeddings by vector search (ranked by the
        query embedding inside retrieve()), rows not yet embedded by question_bank.embed (embedded in memory)."""
        nonlocal query_vec
        added = 0
        has_embedded = (
            await db.execute(
                select(BankQuestion.id).where(*clauses, BankQuestion.embedding.is_not(None)).limit(1)
            )
        ).first()
        if has_embedded is not None:
            if query_vec is None:
                from app.services.retrieval import embed_query

                query_vec = await embed_query(services.embeddings, query, QUERY_INSTRUCTION, context=context)
            hits = await vector_search(db, BankQuestion, query_vec, filters=list(clauses), limit=limit_rows)
            for hit in hits:
                sid = str(hit.item.id)
                if sid in excluded or sid in seen:
                    continue
                seen.add(sid)
                added += 1
                pool.append(
                    RetrievalCandidate(hit.item, hit.item.text, embedding=list(hit.item.embedding or []))
                )
        missing_rows = (
            (
                await db.execute(
                    select(BankQuestion)
                    .where(*clauses, BankQuestion.embedding.is_(None))
                    .order_by(BankQuestion.usage_count.asc(), BankQuestion.created_at.asc())
                    .limit(limit_rows)
                )
            )
            .scalars()
            .all()
        )
        for row in missing_rows:
            sid = str(row.id)
            if sid in excluded or sid in seen:
                continue
            seen.add(sid)
            added += 1
            pool.append(RetrievalCandidate(row, row.text, embedding=None))
        return added

    on_round = await collect(filters, POOL_LIMIT)
    if accepted and on_round < min_round_matches:
        # Too few items written for this round type: other round types' items join (penalised below).
        relaxed = await collect([*base_filters, ~round_type_clause(accepted)], RELAXED_POOL_LIMIT)
        log.info(
            "engine.bank_round_relaxed",
            session_id=session_id,
            competency=competency_key,
            round_type=accepted[0],
            on_round=on_round,
            off_round=relaxed,
        )
    if not pool:
        return []
    ranked = await retrieve(
        query,
        candidates=pool,
        top_k=min(len(pool), max(limit * 4, 8)),
        rerank=True,
        instruction=QUERY_INSTRUCTION,
        rerank_instruction=RERANK_INSTRUCTION,
        ai=services,
        context=context,
    )
    by_id = {str(c.item.id): c for c in pool}
    results: list[tuple[int, int, float, BankCandidate]] = []
    for item in ranked:
        bank_row: BankQuestion = item.item
        candidate = by_id.get(str(bank_row.id))
        vector = candidate.embedding if candidate is not None else None
        if vector is not None and asked_vectors:
            if max_similarity(vector, asked_vectors) >= threshold:
                continue
        try:
            row_difficulty = Difficulty(bank_row.difficulty)
        except ValueError:
            row_difficulty = difficulty
        exact = 0 if row_difficulty == difficulty else 1
        off_round = 0 if fits_round(bank_row.round_types, accepted) else 1
        score = float(item.score) - OFF_ROUND_PENALTY * off_round
        if roles and roles.intersection(bank_row.role_tags or ()):
            score += ROLE_BOOST
        results.append(
            (
                off_round,
                exact,
                -score,
                BankCandidate(
                    id=str(bank_row.id),
                    text=bank_row.text,
                    competency_key=bank_row.competency_key,
                    difficulty=row_difficulty,
                    topic=bank_row.topic,
                    expected_concepts=list(bank_row.expected_concepts or [])[:8],
                    followups=list(bank_row.followups or [])[:4],
                    score=round(score, 4),
                ),
            )
        )
    results.sort(key=lambda r: (r[0], r[1], r[2]))
    return [r[3] for r in results[:limit]]


async def load_asked_vectors(db: AsyncSession, session_id: str) -> list[AskedVector]:
    rows = (
        await db.execute(
            select(Question.id, Question.embedding).where(
                Question.session_id == uuid.UUID(str(session_id)), Question.embedding.is_not(None)
            )
        )
    ).all()
    return [AskedVector(str(r[0]), list(r[1])) for r in rows if r[1] is not None]


def question_embedding_text(text: str, focus: str | None) -> str:
    return (focus or "").strip() or text.strip()


async def embed_question(
    db: AsyncSession, question_id: str, text: str, *, focus: str | None = None, ai: AIServices | None = None
) -> list[float] | None:
    """Compute and store the embedding of an asked question."""
    services = ai or get_ai()
    body = question_embedding_text(text, focus)
    if not body:
        return None
    vectors = await embed_documents(
        services.embeddings, [body], context=CallContext(task=ModelTask.EMBEDDING)
    )
    vector = vectors[0]
    await db.execute(
        update(Question).where(Question.id == uuid.UUID(str(question_id))).values(embedding=vector)
    )
    return vector


async def duplicate_of_asked(
    text: str,
    asked: Sequence[AskedVector],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
    ai: AIServices | None = None,
) -> tuple[str, float] | None:
    """(question_id, similarity) of an asked question ``text`` nearly duplicates."""
    if not asked or not text.strip():
        return None
    services = ai or get_ai()
    vector = (
        await embed_documents(services.embeddings, [text], context=CallContext(task=ModelTask.EMBEDDING))
    )[0]
    best_id, best = None, 0.0
    v = np.asarray(vector, dtype=np.float64)
    for item in asked:
        w = np.asarray(item.vector, dtype=np.float64)
        denom = float(np.linalg.norm(v) * np.linalg.norm(w)) or 1.0
        sim = float(v @ w / denom)
        if sim > best:
            best_id, best = item.question_id, sim
    if best_id is not None and best >= threshold:
        return best_id, best
    return None


async def bank_ids_missing_embeddings(
    db: AsyncSession, organization_id: str, competency_keys: Sequence[str]
) -> list[str]:
    org = uuid.UUID(str(organization_id))
    rows = (
        await db.execute(
            select(BankQuestion.id).where(
                BankQuestion.status == BankStatus.ACTIVE.value,
                BankQuestion.competency_key.in_(list(competency_keys)),
                or_(BankQuestion.organization_id == org, BankQuestion.organization_id.is_(None)),
                BankQuestion.embedding.is_(None),
            )
        )
    ).all()
    return [str(r[0]) for r in rows]


__all__ = [
    "AskedVector",
    "MIN_ROUND_MATCHES",
    "MIXED_ROUND_SOURCES",
    "NEAR_DUPLICATE_THRESHOLD",
    "OFF_ROUND_PENALTY",
    "ROLE_BOOST",
    "accepted_round_types",
    "bank_candidates",
    "bank_ids_missing_embeddings",
    "build_query",
    "duplicate_of_asked",
    "embed_question",
    "fits_round",
    "job_role_tags",
    "load_asked_vectors",
    "max_similarity",
    "round_type_clause",
]
