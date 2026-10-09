"""Coding questions keep their semantic-search embedding fresh: create and any
edit of the embedded text enqueue ``question_bank.embed`` (after commit)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select, update

from app.db.models import BackgroundJob, CodingQuestion
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.question_bank.embeddings import EMBED_JOB, coding_question_text
from tests.integration.coding.test_api import API, new_question

pytestmark = pytest.mark.integration


@pytest.fixture
def embed_worker(worker_factory: Callable[..., Any]) -> Any:
    import app.workers.catalog  # noqa: F401 - registers question_bank.embed

    return worker_factory(job_types=[EMBED_JOB])


async def _embed_jobs(question_id: str) -> list[BackgroundJob]:
    async with session_scope() as db:
        jobs = (
            await db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.type == EMBED_JOB)
                .order_by(BackgroundJob.created_at)
            )
        ).all()
    return [j for j in jobs if question_id in (j.payload.get("coding_question_ids") or [])]


async def _stored_vector(question_id: str) -> np.ndarray | None:
    async with session_scope() as db:
        row = await db.get(CodingQuestion, uuid.UUID(question_id))
        return None if row.embedding is None else np.asarray(row.embedding, dtype=np.float64)


async def _expected_vector(mock_ai: Any, question_id: str) -> np.ndarray:
    async with session_scope() as db:
        row = await db.get(CodingQuestion, uuid.UUID(question_id))
        return np.asarray(mock_ai.embeddings.vector(coding_question_text(row)), dtype=np.float64)


async def test_create_and_text_edits_enqueue_embedding(
    auth_client, coding_env, mock_ai, embed_worker
) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=coding_env.organization_id)
    created = await recruiter.post(API, json=new_question())
    assert created.status_code == 201, created.text
    qid = created.json()["id"]
    assert created.json()["has_embedding"] is False
    [first] = await _embed_jobs(qid)
    assert first.status == "queued" and first.payload == {"coding_question_ids": [qid]}

    assert await embed_worker.drain(timeout=30) == 1
    stored = await _stored_vector(qid)
    assert stored is not None and np.allclose(stored, await _expected_vector(mock_ai, qid), atol=1e-6)
    listed = (await recruiter.get(API, params={"scope": "organization"})).json()["items"]
    assert [i["has_embedding"] for i in listed if i["id"] == qid] == [True]

    # Editing fields outside the embedded text keeps the vector and queues nothing.
    hints = await recruiter.patch(f"{API}/{qid}", json={"hints": ["One", "Two", "Three"]})
    assert hints.status_code == 200 and hints.json()["has_embedding"] is True
    assert len(await _embed_jobs(qid)) == 1

    # A text edit clears the vector and queues a new job even while an older
    # job for the same question is still running (it read the previous text).
    async with session_scope() as db:
        await db.execute(update(BackgroundJob).where(BackgroundJob.id == first.id).values(status="running"))
    edited = await recruiter.patch(f"{API}/{qid}", json={"title": "Count Strictly Positive Numbers"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["has_embedding"] is False
    jobs = await _embed_jobs(qid)
    assert len(jobs) == 2 and jobs[-1].status == "queued" and jobs[-1].dedupe_key != first.dedupe_key
    async with session_scope() as db:
        await db.execute(update(BackgroundJob).where(BackgroundJob.id == first.id).values(status="succeeded"))

    assert await embed_worker.drain(timeout=30) == 1
    stored = await _stored_vector(qid)
    assert stored is not None and np.allclose(stored, await _expected_vector(mock_ai, qid), atol=1e-6)
