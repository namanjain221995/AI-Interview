"""Catalog embedding jobs: seed step -> question_bank.embed, periodic backfill."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from app.db.models import BackgroundJob, BankQuestion, CodingQuestion, Competency, Job, JobRequirement
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.question_bank.embeddings import BACKFILL_JOB, EMBED_JOB, backfill_missing, enqueue_embedding
from tests.integration.resume.helpers import create_job

pytestmark = pytest.mark.integration


async def _null_count(db, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model).where(model.embedding.is_(None))) or 0)


async def _row_count(db, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_seed_step_enqueues_and_worker_embeds(mock_ai, catalog_worker, db_session) -> None:
    from app.seed.catalog import catalog_seed_step
    from app.seed.embeddings import BATCH_SIZE, catalog_embeddings_seed_step

    async with session_scope() as db:
        context = await catalog_seed_step(db, {})
    # Counts come from the seeded rows: the catalog keeps growing.
    counts = {
        model: await _row_count(db_session, model) for model in (BankQuestion, CodingQuestion, Competency)
    }
    assert all(counts.values())
    assert len(context["question_ids_needing_embedding"]) == counts[BankQuestion]
    async with session_scope() as db:
        result = await catalog_embeddings_seed_step(db, context)
    batches = sum(-(-n // BATCH_SIZE) for n in counts.values())  # one job per 64 ids of each kind
    assert result["embedding_jobs_enqueued"] == batches
    jobs = (await db_session.scalars(select(BackgroundJob).where(BackgroundJob.type == EMBED_JOB))).all()
    assert len(jobs) == batches

    processed = await catalog_worker.drain(timeout=300)
    assert processed == batches
    for model in (BankQuestion, CodingQuestion, Competency):
        assert await _null_count(db_session, model) == 0
    succeeded = await db_session.scalar(
        select(func.count())
        .select_from(BackgroundJob)
        .where(BackgroundJob.type == EMBED_JOB, BackgroundJob.status == "succeeded")
    )
    assert succeeded == batches

    # Re-running the seed is idempotent: nothing new to embed.
    async with session_scope() as db:
        again = await catalog_seed_step(db, {})
    assert again["question_ids_needing_embedding"] == []


async def test_backfill_embeds_everything_missing(auth_client, mock_ai, catalog_worker, db_session) -> None:
    from app.seed.catalog import seed_catalog

    async with session_scope() as db:
        await seed_catalog(db)
    client = await auth_client(Role.RECRUITER)
    await create_job(client)
    await db_session.execute(update(Job).values(embedding=None))
    await db_session.execute(update(JobRequirement).values(embedding=None))
    retired = await db_session.scalar(select(BankQuestion).limit(1))
    retired.status = "retired"
    await db_session.commit()
    seeded_questions = await _row_count(db_session, BankQuestion)

    from app.core.queue import enqueue

    await enqueue(BACKFILL_JOB, {}, dedupe_key=f"{BACKFILL_JOB}:test")
    assert await catalog_worker.drain(timeout=300) == 1
    db_session.expire_all()
    assert await _null_count(db_session, Competency) == 0
    assert await _null_count(db_session, CodingQuestion) == 0
    assert await _null_count(db_session, JobRequirement) == 0
    assert await _null_count(db_session, Job) == 0
    assert await _null_count(db_session, BankQuestion) == 1  # the retired one is skipped
    job = await db_session.scalar(select(BackgroundJob).where(BackgroundJob.type == BACKFILL_JOB))
    assert job.status == "succeeded" and job.result["embedded"]["question_ids"] == seeded_questions - 1

    counts = await backfill_missing()
    assert sum(counts.values()) == 0  # idempotent


async def test_embed_job_for_explicit_ids(auth_client, mock_ai, catalog_worker, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    await db_session.execute(update(JobRequirement).values(embedding=None))
    await db_session.execute(update(Job).values(embedding=None))
    await db_session.commit()
    requirement_ids = [r["id"] for r in job["requirements"]]
    async with session_scope() as db:
        await enqueue_embedding(db, requirement_ids=requirement_ids, job_ids=[job["id"]])
    assert await catalog_worker.drain(timeout=30) == 1
    db_session.expire_all()
    assert await _null_count(db_session, JobRequirement) == 0 and await _null_count(db_session, Job) == 0


async def test_inline_embedding_failure_enqueues_job(auth_client, mock_ai, db_session) -> None:
    from app.ai.base import AIUnavailable

    class Down:
        dimensions = 1024

        async def embed(self, texts, *, context=None):
            raise AIUnavailable("embedding endpoint down", endpoint="embedding")

        embed_documents = embed

    mock_ai.embeddings = Down()
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    assert job["has_embedding"] is False and not any(r["has_embedding"] for r in job["requirements"])
    queued = (await db_session.scalars(select(BackgroundJob).where(BackgroundJob.type == EMBED_JOB))).all()
    assert len(queued) == 1
    assert set(queued[0].payload) == {"requirement_ids", "job_ids"}


async def test_embed_job_never_stores_the_vector_of_a_stale_text(mock_ai, db_session) -> None:
    """An edit committed while the job computes the vector must win: the job
    re-reads the row and embeds the new text instead of writing the old one."""
    import numpy as np

    from app.services.question_bank.embeddings import coding_question_text, embed_payload

    question = CodingQuestion(
        slug="stale_text_probe",
        title="Original title",
        difficulty="easy",
        prompt_markdown="Return the sum of a list.",
        languages=["python"],
        expected_concepts=["iteration"],
    )
    db_session.add(question)
    await db_session.commit()
    qid = question.id
    real_embed = mock_ai.embeddings.embed
    calls: list[list[str]] = []

    async def embed_with_concurrent_edit(texts, *, context=None):  # noqa: ANN001, ANN202
        calls.append(list(texts))
        if len(calls) == 1:  # a recruiter saves a new title meanwhile
            async with session_scope() as db:
                await db.execute(
                    update(CodingQuestion).where(CodingQuestion.id == qid).values(title="Edited title")
                )
        return await real_embed(texts, context=context)

    mock_ai.embeddings.embed = embed_with_concurrent_edit
    counts = await embed_payload({"coding_question_ids": [str(qid)]}, ai=mock_ai)
    assert counts == {"coding_question_ids": 1}
    assert len(calls) == 2 and "Edited title" in calls[1][0] and "Original title" in calls[0][0]
    db_session.expire_all()
    row = await db_session.get(CodingQuestion, qid)
    assert row.title == "Edited title"
    expected = np.asarray(mock_ai.embeddings.vector(coding_question_text(row)))
    assert np.allclose(np.asarray(row.embedding), expected, atol=1e-6)

    # Embedded by someone else in the meantime: the job leaves that vector alone.
    row.embedding = None
    await db_session.commit()
    calls.clear()

    async def embed_raced_by_other_writer(texts, *, context=None):  # noqa: ANN001, ANN202
        calls.append(list(texts))
        async with session_scope() as db:
            await db.execute(
                update(CodingQuestion).where(CodingQuestion.id == qid).values(embedding=[0.5] * 1024)
            )
        return await real_embed(texts, context=context)

    mock_ai.embeddings.embed = embed_raced_by_other_writer
    assert await embed_payload({"coding_question_ids": [str(qid)]}, ai=mock_ai) == {"coding_question_ids": 0}
    db_session.expire_all()
    assert np.allclose(np.asarray((await db_session.get(CodingQuestion, qid)).embedding), 0.5)
