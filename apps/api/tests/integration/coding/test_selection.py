"""Problem selection: pinned > retrieved > generated, never repeating a problem."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, inspect, select, update

from app.ai.base import AIUnavailable
from app.db.models import CodingQuestion
from app.db.session import session_scope
from app.domain.enums import CodeLanguage, Difficulty, ModelTask
from app.services.coding.questions import embedding_text
from app.services.coding.schemas import GeneratedCodingProblem, GeneratedParam, GeneratedTest
from app.services.coding.selection import (
    MISSING_QUOTA,
    OVERLAP_QUOTA,
    PREFILTER_TOP_K,
    candidate_pool,
    competency_overlap,
    difficulty_tier,
)
from app.services.retrieval import cosine_similarity

from .conftest import create_session, round_config

pytestmark = pytest.mark.integration

LANGS = [CodeLanguage.PYTHON]


async def test_pinned_question_wins_and_is_never_repeated(service, coding_env) -> None:
    pinned = [
        str(coding_env.questions["most_frequent_error_service"]),
        str(coding_env.questions["merge_intervals"]),
    ]
    _, _, _, _, session_id = await create_session(
        coding_env.organization_id, round_config(question_ids=pinned, max_problems=2)
    )
    first = await service.select_question(
        str(session_id), competency_keys=["algorithms"], difficulty=Difficulty.EASY, languages=LANGS
    )
    assert first == pinned[0]
    state = await service.start(str(session_id), first, question_id=None, language=CodeLanguage.PYTHON)
    await service.finish(state.coding_session_id, reason="submitted")
    second = await service.select_question(
        str(session_id), competency_keys=["algorithms"], difficulty=Difficulty.EASY, languages=LANGS
    )
    assert second == pinned[1]


async def test_retrieval_prefers_competency_and_difficulty_and_excludes_used(service, coding_env) -> None:
    session_id = str(coding_env.session_id)
    chosen = await service.select_question(
        session_id,
        competency_keys=["sql", "databases"],
        difficulty=Difficulty.EASY,
        languages=[CodeLanguage.SQL],
    )
    async with session_scope() as db:
        question = await db.get(CodingQuestion, uuid.UUID(chosen))
    assert question.languages == ["sql"] and question.difficulty == "easy"
    seen = set()
    for _ in range(3):
        qid = await service.select_question(
            session_id,
            competency_keys=["algorithms", "data_structures"],
            difficulty=Difficulty.EASY,
            languages=LANGS,
        )
        assert qid is not None and qid not in seen
        async with session_scope() as db:
            q = await db.get(CodingQuestion, uuid.UUID(qid))
        assert "python" in q.languages and q.languages != ["sql"]
        seen.add(qid)
        state = await service.start(session_id, qid, question_id=None, language=CodeLanguage.PYTHON)
        await service.finish(state.coding_session_id, reason="submitted")


async def _medium_tier(env) -> list[CodingQuestion]:
    """The tier ``select_question(MEDIUM, LANGS)`` ranks in ``coding_env``."""
    async with session_scope() as db:
        pool = await candidate_pool(
            db,
            organization_id=env.organization_id,
            used=set(),
            languages=LANGS,
            section=round_config().coding,
        )
        return difficulty_tier(pool, Difficulty.MEDIUM)


async def _embed_all(mock_ai) -> dict[str, list[float]]:
    """Store mock vectors on every catalog row (as the seed embedding job does)."""
    async with session_scope() as db:
        rows = (await db.scalars(select(CodingQuestion))).all()
        vectors = await mock_ai.embeddings.embed_documents([embedding_text(q) for q in rows])
        for row, vector in zip(rows, vectors, strict=True):
            row.embedding = vector
        return {row.slug: vector for row, vector in zip(rows, vectors, strict=True)}


async def _texts(slugs) -> dict[str, str]:
    async with session_scope() as db:
        rows = (await db.scalars(select(CodingQuestion).where(CodingQuestion.slug.in_(list(slugs))))).all()
        return {q.slug: embedding_text(q) for q in rows}


def _fixed_query(mock_ai, vector: list[float]) -> None:
    async def embed_query(text, instruction=None, *, context=None):
        return vector

    mock_ai.embeddings.embed_query = embed_query  # type: ignore[method-assign]


async def test_retrieval_stores_missing_embeddings(service, coding_env, mock_ai) -> None:
    tier = await _medium_tier(coding_env)
    await service.select_question(
        str(coding_env.session_id),
        competency_keys=["algorithms"],
        difficulty=Difficulty.MEDIUM,
        languages=LANGS,
    )
    async with session_scope() as db:
        embedded = (
            await db.scalars(select(CodingQuestion).where(CodingQuestion.embedding.is_not(None)))
        ).all()
    assert embedded and all(q.difficulty == "medium" for q in embedded)
    # No vectors yet: the deterministic order picks the documents, only those are embedded.
    assert len(embedded) == min(PREFILTER_TOP_K, len(tier))
    assert len(mock_ai.reranker.calls[-1][1]) == len(embedded)


async def test_candidate_pool_loads_only_light_columns(coding_env) -> None:
    tier = await _medium_tier(coding_env)
    assert tier
    for question in tier[:5]:
        unloaded = inspect(question).unloaded
        assert {"prompt_markdown", "tests", "starter_code", "reference_solutions", "embedding"} <= unloaded
        assert question.title and question.languages and question.difficulty == "medium"


async def test_vector_prefilter_bounds_the_reranker(service, coding_env, mock_ai) -> None:
    vectors = await _embed_all(mock_ai)
    tier = await _medium_tier(coding_env)
    target = tier[len(tier) // 2]
    _fixed_query(mock_ai, vectors[target.slug])
    mock_ai.embeddings.calls.clear()
    qid = await service.select_question(
        str(coding_env.session_id),
        competency_keys=["algorithms"],
        difficulty=Difficulty.MEDIUM,
        languages=LANGS,
    )
    _, documents = mock_ai.reranker.calls[-1]
    assert len(documents) == min(PREFILTER_TOP_K, len(tier))
    # Nearest first; the reranker only sees the pre-filtered rows, nothing is re-embedded.
    assert documents[0] == (await _texts([target.slug]))[target.slug]
    assert mock_ai.embeddings.calls == []
    async with session_scope() as db:
        chosen = await db.get(CodingQuestion, uuid.UUID(qid))
    assert chosen.difficulty == "medium" and "python" in chosen.languages
    assert embedding_text(chosen) in documents


async def test_competency_matches_reach_the_reranker_despite_a_distant_query(
    service, coding_env, mock_ai
) -> None:
    vectors = await _embed_all(mock_ai)
    tier = await _medium_tier(coding_env)
    counts: dict[str, int] = {}
    for q in tier:
        for key in q.competency_keys:
            counts[key] = counts.get(key, 0) + 1
    rare = min(counts, key=lambda k: (counts[k], k))
    matching = [q for q in tier if competency_overlap(q, [rare]) == 1.0]
    # Query with the vector of the tier problem least similar to the matching ones.
    others = [q for q in tier if q not in matching]
    far = min(
        others, key=lambda q: max(cosine_similarity(vectors[q.slug], vectors[m.slug]) for m in matching)
    )
    _fixed_query(mock_ai, vectors[far.slug])
    await service.select_question(
        str(coding_env.session_id), competency_keys=[rare], difficulty=Difficulty.MEDIUM, languages=LANGS
    )
    _, documents = mock_ai.reranker.calls[-1]
    assert len(documents) == min(PREFILTER_TOP_K, len(tier))
    assert documents[0] == (await _texts([far.slug]))[far.slug]
    matching_texts = set((await _texts([q.slug for q in matching])).values())
    assert sum(doc in matching_texts for doc in documents) >= min(OVERLAP_QUOTA, len(matching))


async def test_rows_without_vectors_join_in_a_bounded_quota(service, coding_env, mock_ai) -> None:
    await _embed_all(mock_ai)
    tier = await _medium_tier(coding_env)
    stripped = [q.id for q in tier[: MISSING_QUOTA + 4]]
    async with session_scope() as db:
        await db.execute(update(CodingQuestion).where(CodingQuestion.id.in_(stripped)).values(embedding=None))
    mock_ai.embeddings.calls.clear()
    await service.select_question(
        str(coding_env.session_id),
        competency_keys=["algorithms"],
        difficulty=Difficulty.MEDIUM,
        languages=LANGS,
    )
    _, documents = mock_ai.reranker.calls[-1]
    assert len(documents) == min(PREFILTER_TOP_K, len(tier))
    document_batches = [
        batch for batch in mock_ai.embeddings.calls if len(batch) > 1 or batch[0] in documents
    ]
    assert sum(len(batch) for batch in document_batches) == MISSING_QUOTA
    async with session_scope() as db:
        still_missing = await db.scalar(
            select(func.count()).where(CodingQuestion.id.in_(stripped), CodingQuestion.embedding.is_(None))
        )
    assert still_missing == len(stripped) - MISSING_QUOTA


async def test_retrieval_survives_an_embedding_outage(service, coding_env, mock_ai) -> None:
    async def broken(*args, **kwargs):
        raise AIUnavailable("embeddings down")

    mock_ai.embeddings.embed_query = broken  # type: ignore[method-assign]
    qid = await service.select_question(
        str(coding_env.session_id),
        competency_keys=["data_structures"],
        difficulty=Difficulty.HARD,
        languages=LANGS,
    )
    async with session_scope() as db:
        q = await db.get(CodingQuestion, uuid.UUID(qid))
    assert q.difficulty == "hard"


async def test_language_restrictions_are_honoured(service, coding_env) -> None:
    # Only SQL problems support "sql"; no function problem may be returned.
    qid = await service.select_question(
        str(coding_env.session_id),
        competency_keys=["algorithms"],
        difficulty=Difficulty.MEDIUM,
        languages=[CodeLanguage.SQL],
    )
    async with session_scope() as db:
        q = await db.get(CodingQuestion, uuid.UUID(qid))
    assert q.languages == ["sql"]


def generated_problem() -> GeneratedCodingProblem:
    tests = [
        GeneratedTest(args_json=json.dumps([[i, i + 1, i + 2]]), expected_json=str(2 * i + 3), hidden=n >= 3)
        for n, i in enumerate(range(1, 9))
    ]
    return GeneratedCodingProblem(
        title="Largest Pair Sum",
        prompt_markdown="Return the largest sum of two distinct elements of nums. " * 3,
        function_name="largest_pair_sum",
        params=[GeneratedParam(name="nums", type="int[]")],
        return_type="int",
        tests=tests,
        reference_solution_python="def largest_pair_sum(nums):\n    a, b = sorted(nums)[-2:]\n    # PASS_ALL\n    return a + b\n",
        hints=[
            "Which two values matter?",
            "Only the two largest values matter.",
            "Track the two largest values in a single pass.",
        ],
        expected_concepts=["single pass", "two maxima"],
        time_complexity="O(n)",
        space_complexity="O(1)",
        rubric_excellent="One pass.",
        rubric_good="Sorting.",
        rubric_weak="Nested loops.",
    )


async def _empty_catalog_session(env, **coding):
    async with session_scope() as db:
        for q in (await db.scalars(select(CodingQuestion))).all():
            q.status = "retired"
    _, _, _, _, session_id = await create_session(env.organization_id, round_config(**coding))
    return str(session_id)


async def test_generated_problem_is_validated_in_the_sandbox_and_stored(
    service, coding_env, mock_ai, fake_sandbox
) -> None:
    session_id = await _empty_catalog_session(coding_env, allow_generated=True)
    mock_ai.llm.script(ModelTask.QUESTION_GENERATION, generated_problem())
    qid = await service.select_question(
        session_id, competency_keys=["algorithms"], difficulty=Difficulty.EASY, languages=LANGS
    )
    assert qid is not None
    async with session_scope() as db:
        q = await db.get(CodingQuestion, uuid.UUID(qid))
    assert q.source == "generated" and q.organization_id == coding_env.organization_id
    assert q.signature["function_name"] == "largest_pair_sum"
    assert set(q.starter_code) == {"python", "javascript", "typescript", "java", "go", "cpp"}
    assert len(q.hints) == 3 and len(q.tests) == 8 and sum(t["hidden"] for t in q.tests) == 5
    validation = fake_sandbox.requests[-1]
    assert validation.language == CodeLanguage.PYTHON and len(validation.tests) == 8
    state = await service.start(session_id, qid, question_id=None, language=CodeLanguage.PYTHON)
    assert "def largest_pair_sum" in state.code


async def test_generated_problem_failing_its_reference_is_retried_then_dropped(
    service, coding_env, mock_ai
) -> None:
    session_id = await _empty_catalog_session(coding_env, allow_generated=True)
    broken = generated_problem().model_copy(
        update={"reference_solution_python": "def largest_pair_sum(nums):\n    return 0\n"}
    )
    mock_ai.llm.script(ModelTask.QUESTION_GENERATION, broken, broken)
    assert (
        await service.select_question(
            session_id, competency_keys=["algorithms"], difficulty=Difficulty.EASY, languages=LANGS
        )
        is None
    )
    calls = mock_ai.llm.calls_for(ModelTask.QUESTION_GENERATION)
    assert len(calls) == 2 and "rejected" in calls[1].messages[1]["content"]


async def test_no_generation_unless_allowed(service, coding_env, mock_ai) -> None:
    session_id = await _empty_catalog_session(coding_env, allow_generated=False)
    assert (
        await service.select_question(
            session_id, competency_keys=["algorithms"], difficulty=Difficulty.EASY, languages=LANGS
        )
        is None
    )
    assert mock_ai.llm.calls_for(ModelTask.QUESTION_GENERATION) == []
