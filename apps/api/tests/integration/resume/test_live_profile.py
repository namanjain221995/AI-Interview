"""Live profile generation on the sample resumes (opt-in: ``-m live``; shared
main model, a few long generations per run).

Uploads text-layer samples (no OCR), runs ``resume.process`` and
``profile.generate`` against the matching seeded demo job, and prints the
step timings, model tokens and a compact view of the profile so runs before
and after a pipeline change can be compared::

    uv run pytest tests/integration/resume/test_live_profile.py -m live -s

``LIVE_PROFILE_SAMPLES`` (comma-separated ``file:Job title`` pairs) selects
the resumes; ``LIVE_PROFILE_OUT`` (a directory) also writes every profile
document as JSON for diffing.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.db.models import Candidate, CandidateProfile, Job
from app.db.session import session_scope
from app.services.resume.pipeline import process_resume
from app.services.resume.profile import generate_profile
from app.services.resume.service import ResumeServiceImpl
from tests.unit.resume import corpus

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.timeout(1500)]

DEFAULT_SAMPLES = (
    "jane_doe_devops.txt:Senior DevOps Engineer,marcus_okafor_backend.pdf:Backend Engineer (Python)"
)
CONTENT_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@pytest_asyncio.fixture
async def live_ai() -> AsyncIterator[tuple[RealAIServices, MemoryLedger]]:
    ledger = MemoryLedger()
    services = RealAIServices(get_settings(), redis_client=None, ledger=ledger)
    previous = set_ai(services)
    try:
        yield services, ledger
    finally:
        set_ai(previous)
        await services.aclose()


def summarize(document: dict[str, Any]) -> list[str]:
    lines = [
        f"name={document.get('candidate_name')!r} headline={document.get('headline')!r} "
        f"years={document.get('experience_years')} relevant={document.get('relevant_experience_years')}",
    ]
    for role in document.get("roles") or []:
        lines.append(
            f"  role {str(role.get('title'))[:40]:<40} {str(role.get('company'))[:26]:<26} "
            f"{role.get('start')} -> {role.get('end')} ach={len(role.get('achievements') or [])} "
            f"tech={len(role.get('technologies') or [])}"
        )
    skills = document.get("skills") or {}
    lines.append(
        f"  skills={sum(len(v) for v in skills.values())} groups={sorted(skills)} "
        f"projects={len(document.get('projects') or [])} education={len(document.get('education') or [])} "
        f"certs={len(document.get('certifications') or [])} inconsistencies={len(document.get('inconsistencies') or [])}"
    )
    for claim in (document.get("claims_to_verify") or [])[:6]:
        lines.append(
            f"  claim {claim.get('id')} p{claim.get('priority')} [{claim.get('category')}] {str(claim.get('claim'))[:90]}"
        )
    match = document.get("job_match") or {}
    lines.append(
        f"  job_match fit={match.get('overall_fit')} matched={len(match.get('matched_requirements') or [])} "
        f"missing={match.get('missing_requirements')} topics={[t.get('topic') for t in document.get('recommended_topics') or []][:6]}"
    )
    return lines


async def test_live_profile_generation(live_ai, org_factory, storage) -> None:
    from app.seed.catalog import load_demo_jobs, seed_catalog

    services, ledger = live_ai
    label = os.environ.get("LIVE_PROFILE_LABEL", "profile")
    out_dir = os.environ.get("LIVE_PROFILE_OUT")
    samples = [
        tuple(item.split(":", 1))
        for item in os.environ.get("LIVE_PROFILE_SAMPLES", DEFAULT_SAMPLES).split(",")
    ]
    org_id = await org_factory()
    async with session_scope() as db:
        await seed_catalog(db)
    async with session_scope() as db:
        demo = await load_demo_jobs(db, org_id, None)
        jobs = {
            job.title: job.id
            for job in (await db.scalars(select(Job).where(Job.id.in_(list(demo.job_ids.values()))))).all()
        }
    totals: list[float] = []
    for filename, job_title in samples:
        job_id = jobs[job_title]
        async with session_scope() as db:
            candidate = Candidate(
                organization_id=org_id,
                full_name="Sample Candidate",
                email=f"c-{uuid.uuid4().hex[:8]}@example.com",
            )
            db.add(candidate)
            await db.flush()
            candidate_id = candidate.id
            resume = await ResumeServiceImpl().accept_upload(
                db,
                organization_id=org_id,
                candidate_id=candidate_id,
                interview_id=None,
                job_id=job_id,
                filename=filename,
                declared_content_type=CONTENT_TYPES[Path(filename).suffix],
                data=corpus.sample(filename),
                uploaded_by_candidate=False,
            )
            resume_id = resume.id
        await process_resume(resume_id, job_id)
        mark = len(ledger.records)
        started = time.perf_counter()
        outcome = await generate_profile(candidate_id, resume_id, job_id, last_attempt=True)
        total = (time.perf_counter() - started) * 1000
        totals.append(total)
        async with session_scope() as db:
            document = (await db.get(CandidateProfile, outcome.profile_id)).profile
        calls = [r for r in ledger.records[mark:] if r.endpoint == "main"]
        print(
            f"\n[{label}] {filename} -> {job_title}: total {total / 1000:.1f} s status={outcome.status} "
            f"degraded={outcome.degraded} timings={ {k: round(v / 1000, 1) for k, v in outcome.timings_ms.items()} }"
        )
        for record in calls:
            print(
                f"[{label}]   {record.task:<20} {record.status:<9} {round((record.latency_ms or 0) / 1000, 1):>6} s "
                f"prompt={record.prompt_tokens} completion={record.completion_tokens}"
            )
        for line in summarize(document):
            print(f"[{label}] {line}")
        if out_dir:
            path = Path(out_dir) / f"{label}-{Path(filename).stem}.json"
            await asyncio.to_thread(path.write_text, json.dumps(document, indent=1, ensure_ascii=False))
        assert outcome.status in ("ready", "partial")
    print(f"\n[{label}] profile.generate totals (s): {[round(t / 1000, 1) for t in totals]}")
