"""Live end-to-end resume speed harness (opt-in: ``-m live``; shared models).

Runs every sample resume through the real pipeline - upload (validation +
storage) -> worker (``resume.process`` -> ``profile.generate``) with the real
OCR / embedding / reranker / main-model endpoints - one resume at a time, and
records when each stage became visible in the database:

* ``extracted`` - text available (status ``analyzing`` or later: the candidate
  may continue);
* ``quick`` - the deterministic quick profile row exists (status ``quick``);
* ``full`` - the model profile row exists (status ``ready``/``partial``);

plus OCR time per page and every model call (latency, prompt/completion
tokens, start offset). Prints a table and, with ``LIVE_SPEED_OUT`` (a
directory), writes one JSON per resume (timings + the stored profile
documents) for before/after diffs::

    LIVE_SPEED_LABEL=after LIVE_SPEED_OUT=/tmp/x \\
        uv run pytest tests/integration/resume/test_live_speed.py -m live -s

``LIVE_SPEED_SAMPLES`` (comma-separated file names, ``dense.txt`` for the
generated long resume) selects the resumes; ``LIVE_SPEED_REPEAT`` repeats the
text samples (p50/p95 over more runs).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
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
from app.core.queue import registered_job_types
from app.db.models import Candidate, CandidateProfile, CandidateResume, Job
from app.db.session import session_scope
from app.services.resume.service import ResumeServiceImpl
from tests.unit.resume import corpus

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.timeout(3600)]

SAMPLE_JOBS = {
    "jane_doe": "Senior DevOps Engineer",
    "marcus_okafor": "Backend Engineer (Python)",
    "sofia_martinez": "Frontend Engineer (React)",
    "priya_raman": "ML Engineer (LLM)",
    "dense": "Backend Engineer (Python)",
}
DEFAULT_SAMPLES = (
    "jane_doe_devops.pdf,jane_doe_devops.docx,jane_doe_devops.txt,marcus_okafor_backend.pdf,"
    "sofia_martinez_frontend.docx,priya_raman_ml.txt,jane_doe_devops.png,jane_doe_devops.jpg,"
    "jane_doe_devops_scanned.pdf,dense.txt"
)
CONTENT_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
}
POLL_SECONDS = 0.05
TIMEOUT_SECONDS = 420.0


def dense_resume() -> bytes:
    """A long, dense (fictional) resume: 6 roles, 10 projects, ~70 skills."""
    lines = [
        "MORGAN LEE",
        "Staff Backend Engineer",
        "Portland, OR · morgan.lee@example.com · +1 (503) 555-0110 · github.com/example-morgan",
        "",
        "SUMMARY",
        "Backend engineer with 11 years of experience designing distributed Python and Go services, data "
        "pipelines and developer platforms. Led teams of up to 8 engineers and owned systems serving 30 million "
        "requests per day.",
        "",
        "EXPERIENCE",
    ]
    roles = [
        ("Staff Backend Engineer", "Northwind Payments, Portland, OR", "Mar 2022 – Present"),
        ("Senior Backend Engineer", "Contoso Travel, Seattle, WA", "Jan 2019 – Feb 2022"),
        ("Backend Engineer", "Fabrikam Media, Remote", "Jun 2017 – Dec 2018"),
        ("Software Engineer", "Tailspin Logistics, Denver, CO", "Aug 2015 – May 2017"),
        ("Junior Developer", "Adventure Works, Boulder, CO", "Jul 2014 – Jul 2015"),
        ("Engineering Intern", "Litware Labs, Boulder, CO", "Jun 2013 – Aug 2013"),
    ]
    bullets = [
        "Designed an event-driven settlement service in Go handling {n} million events per day with exactly-once "
        "processing via Kafka transactions.",
        "Cut p99 API latency from {a} ms to {b} ms by introducing connection pooling, query batching and Redis caching.",
        "Led a team of {t} engineers through a migration from a monolith to 14 services on Kubernetes.",
        "Built a data pipeline on Airflow and Spark that processes {n} TB of clickstream data nightly.",
        "Introduced contract testing with Pact and raised integration coverage from {a}% to {b}%.",
        "Reduced infrastructure spend by {a}% through rightsizing, autoscaling and reserved capacity planning.",
    ]
    for index, (title, company, dates) in enumerate(roles):
        lines.append(f"{title} — {company}")
        lines.append(dates)
        count = 6 if index < 3 else 3 if index < 5 else 2
        for b in bullets[:count]:
            lines.append("- " + b.format(n=index + 2, a=40 + index * 3, b=80 + index, t=3 + index))
        lines.append("")
    lines.append("PROJECTS")
    for index in range(10):
        lines.append(
            f"- Project {chr(65 + index)}{index}: open-source {['rate limiter', 'feature flag service', 'schema registry', 'load tester', 'log shipper', 'job scheduler', 'CLI toolkit', 'tracing exporter', 'config linter', 'cache proxy'][index]} "
            f"in {'Go' if index % 2 else 'Python'}; {200 + index * 150} GitHub stars and used by {3 + index} companies."
        )
    lines += [
        "",
        "SKILLS",
        "Languages: Python, Go, Java, Kotlin, TypeScript, SQL, Bash, Rust (basic)",
        "Frameworks: FastAPI, Django, Flask, gRPC, Spring Boot, SQLAlchemy, Pydantic, Celery, React",
        "Data: PostgreSQL, MySQL, Redis, Kafka, Cassandra, Elasticsearch, ClickHouse, Snowflake, Spark, Airflow, dbt",
        "Cloud: AWS (EC2, ECS, EKS, Lambda, S3, RDS, DynamoDB, SQS, SNS, CloudFront, IAM), GCP (GKE, BigQuery, Pub/Sub)",
        "DevOps: Docker, Kubernetes, Helm, Terraform, Argo CD, GitHub Actions, Jenkins, Prometheus, Grafana, OpenTelemetry",
        "Practices: Distributed systems, Event-driven architecture, Domain-driven design, TDD, Code review, Mentoring",
        "",
        "CERTIFICATIONS",
        "- AWS Certified Solutions Architect – Professional (2021)",
        "- Certified Kubernetes Application Developer (2020)",
        "- Google Cloud Professional Data Engineer (2019)",
        "",
        "EDUCATION",
        "- M.S. Computer Science, Example Tech University",
        "- B.S. Mathematics, Example State College",
    ]
    return ("\n".join(lines) + "\n").encode()


def sample_bytes(name: str) -> bytes:
    return dense_resume() if name == "dense.txt" else corpus.sample(name)


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


async def _snapshot(resume_id: uuid.UUID) -> tuple[str, dict[str, Any], list[tuple[str, int]]]:
    async with session_scope() as db:
        resume = await db.get(CandidateResume, resume_id)
        rows = (
            await db.execute(
                select(CandidateProfile.status, CandidateProfile.version).where(
                    CandidateProfile.resume_id == resume_id
                )
            )
        ).all()
        assert resume is not None
        return resume.status, dict(resume.scan_result or {}), [(r[0], r[1]) for r in rows]


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


async def test_live_resume_speed(live_ai, org_factory, storage, worker_factory) -> None:
    import app.workers.resume  # noqa: F401 - registers the resume handlers
    from app.seed.catalog import load_demo_jobs, seed_catalog

    services, ledger = live_ai
    label = os.environ.get("LIVE_SPEED_LABEL", "run")
    out_dir = os.environ.get("LIVE_SPEED_OUT")
    samples = [
        s.strip() for s in os.environ.get("LIVE_SPEED_SAMPLES", DEFAULT_SAMPLES).split(",") if s.strip()
    ]
    repeat = max(1, int(os.environ.get("LIVE_SPEED_REPEAT", "1")))
    org_id = await org_factory()
    try:
        async with session_scope() as db:
            await seed_catalog(db)
    except Exception as exc:  # noqa: BLE001 - the shared samples/ catalog may be mid-edit; jobs still load
        print(f"[{label}] catalog seed skipped: {type(exc).__name__}: {str(exc)[:200]}")
    async with session_scope() as db:
        demo = await load_demo_jobs(db, org_id, None)
        jobs = {
            job.title: job.id
            for job in (await db.scalars(select(Job).where(Job.id.in_(list(demo.job_ids.values()))))).all()
        }
    types = [t for t in registered_job_types() if t.startswith(("resume.", "profile."))]
    worker = worker_factory(job_types=types, visibility_timeout=120.0, block_ms=1000)
    stop = asyncio.Event()
    runner = asyncio.create_task(worker.run(stop))
    results: list[dict[str, Any]] = []
    try:
        await asyncio.sleep(0.5)
        runs = [
            name
            for name in samples
            for _ in range(repeat if not name.endswith((".png", ".jpg")) and "scanned" not in name else 1)
        ]
        for name in runs:
            prefix = next(k for k in SAMPLE_JOBS if name.startswith(k))
            job_id = jobs[SAMPLE_JOBS[prefix]]
            async with session_scope() as db:
                candidate = Candidate(
                    organization_id=org_id,
                    full_name="Sample Candidate",
                    email=f"c-{uuid.uuid4().hex[:8]}@example.com",
                )
                db.add(candidate)
                await db.flush()
                candidate_id = candidate.id
            mark = len(ledger.records)
            started_wall = time.time()
            t0 = time.perf_counter()
            async with session_scope() as db:
                resume = await ResumeServiceImpl().accept_upload(
                    db,
                    organization_id=org_id,
                    candidate_id=candidate_id,
                    interview_id=None,
                    job_id=job_id,
                    filename=name,
                    declared_content_type=CONTENT_TYPES[Path(name).suffix],
                    data=sample_bytes(name),
                    uploaded_by_candidate=True,
                )
                resume_id = resume.id
            upload_ms = (time.perf_counter() - t0) * 1000
            marks: dict[str, float] = {}
            status = "uploaded"
            scan: dict[str, Any] = {}
            while time.perf_counter() - t0 < TIMEOUT_SECONDS:
                status, scan, rows = await _snapshot(resume_id)
                now_ms = (time.perf_counter() - t0) * 1000
                if status in ("analyzing", "ready") and "extracted" not in marks:
                    marks["extracted"] = now_ms
                if any(s == "quick" for s, _ in rows) and "quick" not in marks:
                    marks["quick"] = now_ms
                if any(s in ("ready", "partial") for s, _ in rows) and "full" not in marks:
                    marks["full"] = now_ms
                if status == "ready" and "full" in marks:
                    break
                if status in ("failed", "rejected"):
                    break
                await asyncio.sleep(POLL_SECONDS)
            calls = ledger.records[mark:]
            extraction = scan.get("extraction") or {}
            model_calls = [
                {
                    "task": r.task,
                    "endpoint": r.endpoint,
                    "status": r.status,
                    "start_s": round(r.created_at.timestamp() - started_wall - (r.latency_ms or 0) / 1000, 2),
                    "latency_s": round((r.latency_ms or 0) / 1000, 2),
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "version": r.prompt_version,
                }
                for r in calls
                if r.endpoint in ("main", "ocr", "vision")
            ]
            async with session_scope() as db:
                documents = {
                    row.status: row.profile
                    for row in (
                        await db.scalars(
                            select(CandidateProfile)
                            .where(CandidateProfile.resume_id == resume_id)
                            .order_by(CandidateProfile.version)
                        )
                    ).all()
                }
            result = {
                "label": label,
                "sample": name,
                "status": status,
                "upload_ms": round(upload_ms, 1),
                "marks_ms": {k: round(v) for k, v in marks.items()},
                "method": extraction.get("method"),
                "pages": extraction.get("page_count"),
                "ocr_pages": extraction.get("ocr_pages"),
                "ocr_ms": extraction.get("ocr_ms"),
                "profile_timings": (scan.get("profile") or {}).get("timings"),
                "model_calls": model_calls,
                "main_completion_tokens": sum(
                    c["completion_tokens"] or 0 for c in model_calls if c["endpoint"] == "main"
                ),
                "main_prompt_tokens": sum(
                    c["prompt_tokens"] or 0 for c in model_calls if c["endpoint"] == "main"
                ),
                "documents": documents,
            }
            results.append(result)
            print(
                f"\n[{label}] {name}: status={status} upload={upload_ms:.0f}ms marks={result['marks_ms']} "
                f"method={result['method']} ocr_pages={result['ocr_pages']} ocr_ms={result['ocr_ms']} "
                f"main tokens prompt={result['main_prompt_tokens']} completion={result['main_completion_tokens']}",
                flush=True,
            )
            for call in model_calls:
                print(
                    f"[{label}]   {call['endpoint']:<5} {call['task']:<20} {call['status']:<9} start {call['start_s']:>6.2f}s "
                    f"took {call['latency_s']:>6.2f}s prompt={call['prompt_tokens']} completion={call['completion_tokens']}",
                    flush=True,
                )
            if out_dir:
                path = Path(out_dir) / f"{label}-{Path(name).stem}-{len(results)}.json"
                await asyncio.to_thread(
                    path.write_text, json.dumps(result, indent=1, ensure_ascii=False, default=str)
                )
            await asyncio.sleep(1.0)
    finally:
        stop.set()
        worker.stop()
        await asyncio.wait_for(runner, timeout=60)

    text_runs = [r for r in results if r["method"] == "text"]
    print(f"\n[{label}] sample                      extracted   quick    full   ocr_ms  completion")
    for r in results:
        m = r["marks_ms"]
        print(
            f"[{label}] {r['sample']:<28} {m.get('extracted', '-'):>8} {m.get('quick', '-'):>7} {m.get('full', '-'):>7} "
            f"{r['ocr_ms'] or 0:>8.0f} {r['main_completion_tokens']:>10}"
        )
    for key in ("quick", "full"):
        values = [r["marks_ms"][key] / 1000 for r in text_runs if key in r["marks_ms"]]
        if values:
            print(
                f"[{label}] text documents upload->{key}: n={len(values)} p50={statistics.median(values):.1f}s "
                f"p95={_pct(values, 0.95):.1f}s max={max(values):.1f}s"
            )
    assert all(r["status"] == "ready" for r in results), [(r["sample"], r["status"]) for r in results]
