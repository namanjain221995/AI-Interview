"""Quick profile, progressive sections, part cache and instant re-upload
(mock AI; real Postgres/pgvector, Redis streams, local storage)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.ai.base import AIUnavailable, OcrResult
from app.db.models import CandidateProfile, CandidateResume, Consent, ResumeChunk
from app.db.session import session_scope
from app.domain.enums import ModelTask, Role
from app.services.interview.sessions import latest_profile as engine_latest_profile
from app.services.resume.service import get_resume_service, quick_profile_fallback
from tests.integration.resume.helpers import (
    API,
    create_candidate,
    create_job,
    jane_analysis,
    jane_extraction,
    part_responder,
    script_jane,
    upload,
)
from tests.unit.resume import corpus

pytestmark = pytest.mark.integration

JANE_TXT = corpus.sample("jane_doe_devops.txt")


async def _consent(ctx) -> None:
    async with session_scope() as db:
        db.add(
            Consent(
                interview_id=ctx.interview_id,
                candidate_id=ctx.candidate_id,
                policy_version="2026-10-01",
                policy_hash="0" * 64,
                items={"microphone": True},
            )
        )


async def test_quick_profile_is_shown_before_the_model_profile(
    candidate_auth_client, worker_factory, mock_ai, storage
) -> None:
    import app.workers.resume  # noqa: F401 - registers the handlers

    http = await candidate_auth_client()
    ctx = http.candidate
    await _consent(ctx)
    script_jane(mock_ai)
    url = f"{API}/portal/interviews/{ctx.interview_id}/resume"
    uploaded = await http.post(url, files={"file": ("Jane CV.txt", JANE_TXT, "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["profile_stage"] == "none" and uploaded.json()["profile_summary"] is None

    # Text extraction + the deterministic quick profile only (no model call).
    assert await worker_factory(job_types=["resume.process"]).drain(timeout=20) == 1
    assert mock_ai.llm.calls == []
    body = (await http.get(url)).json()
    assert body["resume"]["status"] == "analyzing" and body["resume"]["profile_ready"] is False
    assert body["profile_stage"] == "quick"
    summary = body["profile_summary"]
    assert summary["candidate_name"] == "Jane Doe" and summary["experience_years"] == 5.8
    assert [r["company"] for r in summary["roles"]] == [
        "Bluepeak Analytics",
        "Harborline Logistics",
        "Cobalt Retail Group",
    ]
    assert "Kubernetes" in summary["top_skills"]
    assert "claims_to_verify" not in repr(summary) and "jane.doe@example.com" not in repr(body)
    states = {s["key"]: s["state"] for s in body["sections"]}
    assert states == {
        "basics": "quick",
        "experience": "quick",
        "skills": "quick",
        "education": "quick",
        "interview": "pending",
    }  # projects are not shown to candidates

    # The interview engine never treats the quick profile as the full one ...
    async with session_scope() as db:
        assert await engine_latest_profile(db, ctx.candidate_id, ctx.job_id) is None
        assert await get_resume_service().latest_profile(db, ctx.candidate_id, ctx.job_id) is None
        quick = await get_resume_service().latest_profile(
            db, ctx.candidate_id, ctx.job_id, include_quick=True
        )
        assert quick is not None and quick.status == "quick" and quick.version == 1
    # ... but it can fall back to it when its wait budget runs out.
    fallback = await quick_profile_fallback(ctx.candidate_id, ctx.job_id)
    assert fallback is not None and fallback.status == "quick"

    # The model profile: every section is confirmed, the profile becomes the full one.
    assert await worker_factory(job_types=["profile.generate"]).drain(timeout=30) == 1
    body = (await http.get(url)).json()
    assert body["resume"]["status"] == "ready" and body["resume"]["profile_ready"] is True
    assert body["profile_stage"] == "full"
    assert {s["state"] for s in body["sections"]} == {"done"}
    async with session_scope() as db:
        full = await engine_latest_profile(db, ctx.candidate_id, ctx.job_id)
        assert full is not None and full.status == "ready" and full.version == 2
        refined = await db.get(CandidateProfile, quick.id)
        assert refined.status == "quick"  # stays quick, refined in place with the model's sections
        assert refined.profile["roles"][0]["technologies"]  # from the model's role call
    fallback = await quick_profile_fallback(ctx.candidate_id, ctx.job_id)
    assert fallback is not None and fallback.status == "ready"  # the full profile wins once it exists


async def test_staff_profile_endpoint_can_include_the_quick_profile(
    auth_client, mock_ai, worker_factory, storage
) -> None:
    import app.workers.resume  # noqa: F401

    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    response = await upload(client, candidate["id"], "cv.txt", JANE_TXT)
    assert response.status_code == 201
    await worker_factory(job_types=["resume.process"]).drain(timeout=20)
    url = f"{API}/candidates/{candidate['id']}/profile"
    missing = await client.get(url)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "profile_not_ready"
    quick = await client.get(url, params={"include_quick": "true"})
    assert quick.status_code == 200 and quick.json()["status"] == "quick"
    assert quick.json()["profile"]["candidate_name"] == "Jane Doe"
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    summary = detail["extraction_summary"]
    assert summary["profile_status"] == "quick" and summary["quick"]["contact"]["email"] is True
    assert summary["quick"]["partial"] is False


async def test_a_retry_only_runs_the_missing_parts(auth_client, mock_ai, pipeline_worker) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    candidate = await create_candidate(client)
    responder = part_responder(jane_extraction(), jane_analysis())
    failures = {"skills": 1}

    def extraction(call):
        if call.schema.__name__ == "ResumeSkills" and failures["skills"]:
            failures["skills"] -= 1
            raise AIUnavailable("blip", endpoint="main")
        return responder(call)

    mock_ai.llm.set_default(ModelTask.RESUME_EXTRACTION, extraction)
    mock_ai.llm.set_default(ModelTask.PROFILE_ANALYSIS, responder)
    response = await upload(client, candidate["id"], "cv.txt", JANE_TXT, job_id=job["id"])
    await pipeline_worker.drain(timeout=30)
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    assert detail["status"] == "ready" and detail["status_detail"] is None  # not degraded
    async with session_scope() as db:
        resume = await db.get(CandidateResume, uuid.UUID(response.json()["id"]))
        calls = resume.scan_result["profile"]["calls"]
    first_attempt = len(calls)  # every part ran once on the first attempt
    assert len(mock_ai.llm.calls) == first_attempt + 1  # the retry ran only the skills part
    assert calls["skills:0"] == {"ms": calls["skills:0"]["ms"], "cached": False, "ok": True}
    assert calls["identity"]["cached"] is True and calls["roles:0"]["cached"] is True
    profile = (await client.get(f"{API}/candidates/{candidate['id']}/profile")).json()
    assert profile["status"] == "ready"


async def test_identical_reupload_reuses_text_chunks_and_parts(
    auth_client, mock_ai, pipeline_worker, db_session
) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    other_job = await create_job(client, title="Platform Engineer")
    candidate = await create_candidate(client)
    script_jane(mock_ai)
    first = (await upload(client, candidate["id"], "cv.txt", JANE_TXT, job_id=job["id"])).json()
    await pipeline_worker.drain(timeout=30)
    calls_after_first = len(mock_ai.llm.calls)
    embed_calls = len(mock_ai.embeddings.calls)

    second = (await upload(client, candidate["id"], "same cv.txt", JANE_TXT, job_id=job["id"])).json()
    await pipeline_worker.drain(timeout=30)
    assert len(mock_ai.llm.calls) == calls_after_first  # no model call at all
    resume = await db_session.get(CandidateResume, uuid.UUID(second["id"]))
    await db_session.refresh(resume)
    assert resume.status == "ready" and resume.scan_result["reused_from"] == first["id"]
    assert resume.normalized_text and resume.scan_result["profile"]["calls"]["identity"]["cached"] is True
    chunk_count = await db_session.scalar(
        select(func.count()).select_from(ResumeChunk).where(ResumeChunk.resume_id == resume.id)
    )
    first_count = await db_session.scalar(
        select(func.count()).select_from(ResumeChunk).where(ResumeChunk.resume_id == uuid.UUID(first["id"]))
    )
    assert chunk_count == first_count > 0
    # chunk vectors were copied: nothing but the requirement queries was embedded again
    assert len(mock_ai.embeddings.calls) - embed_calls <= len(job["requirements"])
    profile = (await client.get(f"{API}/candidates/{candidate['id']}/profile")).json()
    assert profile["resume_id"] == second["id"] and profile["status"] == "ready"

    # Another job: the resume parts are reused, only the job-aware analysis runs again.
    extraction_calls = len(mock_ai.llm.calls_for(ModelTask.RESUME_EXTRACTION))
    analysis_calls = len(mock_ai.llm.calls_for(ModelTask.PROFILE_ANALYSIS))
    await upload(client, candidate["id"], "cv.txt", JANE_TXT, job_id=other_job["id"])
    await pipeline_worker.drain(timeout=30)
    assert len(mock_ai.llm.calls_for(ModelTask.RESUME_EXTRACTION)) == extraction_calls
    assert len(mock_ai.llm.calls_for(ModelTask.PROFILE_ANALYSIS)) > analysis_calls


async def test_scanned_resume_shows_page_one_while_page_two_is_scanned(
    auth_client, mock_ai, pipeline_worker
) -> None:
    """Page 2's OCR result is only returned once the quick profile of page 1 is
    visible - if the partial read did not happen, this test would time out."""
    text = JANE_TXT.decode()
    split = text.index("DevOps Engineer — Harborline")
    seen: list[dict] = []
    calls = {"n": 0}

    class PagedOcr:
        async def ocr_image(self, image, *, mime_type="image/png", prompt=None, context=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return OcrResult(text=text[:split], status="ok", raw=text[:split])
            for _ in range(200):
                async with session_scope() as db:
                    row = await db.scalar(select(CandidateProfile).where(CandidateProfile.status == "quick"))
                    resume = await db.scalar(select(CandidateResume))
                if row is not None:
                    seen.append(
                        {
                            "status": resume.status,
                            "roles": len(row.profile["roles"]),
                            "quick": dict(resume.scan_result.get("quick") or {}),
                        }
                    )
                    break
                await asyncio.sleep(0.05)
            return OcrResult(text=text[split:], status="ok", raw=text[split:])

    mock_ai.ocr = PagedOcr()
    script_jane(mock_ai)
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    response = await upload(client, candidate["id"], "scan.pdf", corpus.sample("jane_doe_devops_scanned.pdf"))
    await pipeline_worker.drain(timeout=40)
    assert seen, "page 1 quick profile was not published while page 2 was scanned"
    assert seen[0]["status"] == "ocr" and seen[0]["roles"] == 1  # only page 1's role so far
    assert seen[0]["quick"]["partial"] is True and seen[0]["quick"]["pages_done"] == 1
    assert seen[0]["quick"]["pages_total"] == 2
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    assert detail["status"] == "ready"
    async with session_scope() as db:
        quick = await db.scalar(select(CandidateProfile).where(CandidateProfile.status == "quick"))
    assert len(quick.profile["roles"]) == 3  # refreshed with the whole document
