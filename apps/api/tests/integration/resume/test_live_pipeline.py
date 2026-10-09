"""Live end-to-end resume pipeline (opt-in: ``-m live``).

Real OCR on one scanned page, real main-model extraction + profile analysis,
real embeddings and reranker, against the seeded "Senior DevOps Engineer" demo
job. Run with ``-s`` to see the timings::

    uv run pytest tests/integration/resume/test_live_pipeline.py -m live -s
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.db.models import Candidate, CandidateProfile, CandidateResume, Job, ResumeChunk
from app.db.session import session_scope
from app.services.resume.pipeline import process_resume
from app.services.resume.profile import generate_profile
from app.services.resume.service import ResumeServiceImpl
from tests.unit.resume import corpus

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.timeout(900)]


@pytest_asyncio.fixture
async def live_ai() -> AsyncIterator[RealAIServices]:
    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    previous = set_ai(services)
    try:
        yield services
    finally:
        set_ai(previous)
        await services.aclose()


async def test_live_scanned_resume_pipeline(live_ai, org_factory, storage, settings) -> None:
    from app.seed.catalog import load_demo_jobs, seed_catalog

    settings.ocr_max_pages = 1  # one scanned page keeps the shared OCR endpoint load tiny
    timings: dict[str, float] = {}
    org_id = await org_factory()
    async with session_scope() as db:
        await seed_catalog(db)
    async with session_scope() as db:
        demo = await load_demo_jobs(db, org_id, None)
        job = await db.scalar(
            select(Job).where(Job.id.in_(list(demo.job_ids.values())), Job.title == "Senior DevOps Engineer")
        )
        assert job is not None
        job_id = job.id
        candidate = Candidate(
            organization_id=org_id, full_name="Jane Doe", email=f"jane-{uuid.uuid4().hex[:6]}@example.com"
        )
        db.add(candidate)
        await db.flush()
        candidate_id = candidate.id

    data = corpus.sample("jane_doe_devops_scanned.pdf")
    started = time.perf_counter()
    async with session_scope() as db:
        resume = await ResumeServiceImpl().accept_upload(
            db,
            organization_id=org_id,
            candidate_id=candidate_id,
            interview_id=None,
            job_id=job_id,
            filename="jane_doe_devops_scanned.pdf",
            declared_content_type="application/pdf",
            data=data,
            uploaded_by_candidate=True,
        )
        resume_id = resume.id
    timings["upload (validate + scan + store)"] = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    processed = await process_resume(resume_id, job_id)
    timings["resume.process (OCR page 1 + chunks + embeddings)"] = (time.perf_counter() - started) * 1000
    timings["  of which OCR"] = processed["ocr_ms"]

    started = time.perf_counter()
    outcome = await generate_profile(candidate_id, resume_id, job_id, last_attempt=False)
    timings["profile.generate (total)"] = (time.perf_counter() - started) * 1000
    for key, value in outcome.timings_ms.items():
        timings[f"  {key}"] = value

    async with session_scope() as db:
        row = await db.get(CandidateResume, resume_id)
        profile = await db.get(CandidateProfile, outcome.profile_id)
        chunks = (await db.scalars(select(ResumeChunk).where(ResumeChunk.resume_id == resume_id))).all()
        document = profile.profile
        breakdown = (row.scan_result or {}).get("profile", {}).get("match") or {}

    print("\n[live] resume pipeline timings (ms)")
    for key, value in timings.items():
        print(f"[live]   {key:<52} {value:>9.0f}")
    print(
        f"[live] extraction_method={row.extraction_method} pages={row.page_count} chunks={len(chunks)} "
        f"sections={sorted({c.section for c in chunks})}"
    )
    print(
        f"[live] profile v{profile.version} status={profile.status} degraded={outcome.degraded} "
        f"experience_years={profile.experience_years} job_match={profile.job_match_score} claims={profile.claims_count}"
    )
    print(
        f"[live] match weights={breakdown.get('weights')} coverage={breakdown.get('coverage')} "
        f"model={breakdown.get('model_fit')} experience={breakdown.get('experience_fit')}"
    )
    for role in document["roles"]:
        print(
            f"[live]   role {role['title'][:34]:<34} {role['company'][:24]:<24} {role['start']} -> {role['end']}"
        )
    for claim in document["claims_to_verify"][:5]:
        print(f"[live]   claim {claim['id']} p{claim['priority']}: {claim['claim'][:100]}")
    for item in document["inconsistencies"][:3]:
        print(f"[live]   inconsistency ({item['severity']}): {item['description'][:120]}")
    for requirement in (document.get("job_match") or {}).get("matched_requirements", [])[:8]:
        print(f"[live]   requirement {requirement['requirement'][:40]:<40} {requirement['strength']}")

    assert row.status == "ready" and row.extraction_method == "ocr"
    assert len(chunks) >= 2 and all(c.embedding is not None for c in chunks)
    assert profile.status == "ready" and outcome.degraded == []
    assert profile.claims_count >= 2
    assert profile.job_match_score is not None and 0 < profile.job_match_score <= 100
    assert document["candidate_name"] and "jane" in document["candidate_name"].lower()
    assert profile.experience_years is not None and 4.5 <= profile.experience_years <= 7.0
    assert all(role["start"] for role in document["roles"]), document[
        "roles"
    ]  # dates extracted (or backfilled)
    assert any("Overlapping employment dates" in item["description"] for item in document["inconsistencies"])
