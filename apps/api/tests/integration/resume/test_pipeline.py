"""Resume pipeline end to end: API upload -> resume.process -> profile.generate
(mock AI; real Postgres/pgvector, Redis streams, local storage)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.ai.base import AIUnavailable
from app.db.models import CandidateProfile, CandidateResume, MediaAsset, ResumeChunk
from app.domain.enums import ModelTask, Role
from app.workers.resume import PROFILE_RETRY_SCHEDULE
from tests.integration.resume.helpers import (
    API,
    create_candidate,
    create_job,
    jane_extraction,
    script_extraction,
    script_jane,
    upload,
)
from tests.unit.resume import corpus

pytestmark = pytest.mark.integration

JANE_TXT = corpus.sample("jane_doe_devops.txt").decode()
SPLIT = JANE_TXT.index("DevOps Engineer — Harborline")


def states(progress: dict) -> dict[str, str]:
    return {s["key"]: s["state"] for s in progress["steps"]}


async def test_txt_resume_end_to_end(auth_client, mock_ai, pipeline_worker, db_session, storage) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    candidate = await create_candidate(client)
    script_jane(mock_ai)

    response = await upload(
        client, candidate["id"], "Jane Doe CV.txt", corpus.sample("jane_doe_devops.txt"), job_id=job["id"]
    )
    assert response.status_code == 201, response.text
    resume = response.json()
    assert resume["status"] == "uploaded" and resume["content_type"] == "text/plain"
    assert states(resume["progress"])["validating"] == "done"
    assert resume["scan"]["status"] == "skipped"
    assert resume["original_filename"] == "Jane Doe CV.txt"

    assert await pipeline_worker.drain(timeout=30) == 2

    detail = (await client.get(f"{API}/resumes/{resume['id']}")).json()
    assert detail["status"] == "ready", detail
    assert detail["extraction_method"] == "text"
    assert set(states(detail["progress"]).values()) == {"done", "skipped"}
    assert states(detail["progress"])["ocr"] == "skipped"
    assert detail["progress"]["percent"] == 100
    summary = detail["extraction_summary"]
    assert summary["candidate_name"] == "Jane Doe" and summary["roles_count"] == 3
    assert summary["total_experience_years"] == 5.8
    assert {"experience", "skills", "summary"} <= set(summary["sections"])
    assert detail["media_url"].startswith("/api/v1/media/signed/")
    downloaded = await client.get(detail["media_url"])
    assert downloaded.status_code == 200 and downloaded.content == corpus.sample("jane_doe_devops.txt")

    profile = (
        await client.get(f"{API}/candidates/{candidate['id']}/profile", params={"job_id": job["id"]})
    ).json()
    # version 1 is the quick profile (shown while the model profile was generated)
    assert profile["version"] == 2 and profile["status"] == "ready"
    quick = (await client.get(f"{API}/candidates/{candidate['id']}/profile", params={"version": 1})).json()
    assert quick["status"] == "quick" and quick["profile"]["candidate_name"] == "Jane Doe"
    assert profile["job_id"] == job["id"]
    assert profile["experience_years"] == 5.8
    assert profile["relevant_experience_years"] == 5.8
    assert profile["claims_count"] == 2
    assert len(profile["model_call_ids"]) == len(mock_ai.llm.calls)  # one id per part call
    assert {c.task for c in mock_ai.llm.calls} == {ModelTask.RESUME_EXTRACTION, ModelTask.PROFILE_ANALYSIS}
    document = profile["profile"]
    assert [c["id"] for c in document["claims_to_verify"]] == ["c1", "c2"]
    assert document["companies"] == ["Bluepeak Analytics", "Harborline Logistics", "Cobalt Retail Group"]
    assert any(
        i["description"].startswith("Overlapping employment dates") for i in document["inconsistencies"]
    )
    assert "devops" in document["skills"]
    match = document["job_match"]
    strengths = {m["requirement"]: m["strength"] for m in match["matched_requirements"]}
    assert strengths["Kubernetes"] == "strong"
    assert strengths["Apache Kafka streaming"] in {"none", "weak"}
    assert "Apache Kafka streaming" in match["missing_requirements"]
    breakdown = profile["match_breakdown"]
    assert breakdown["weights"] == {"coverage": 0.5, "model": 0.35, "experience": 0.15}
    assert breakdown["experience_fit"] == 100.0 and breakdown["model_fit"] == 78.0
    assert profile["job_match_score"] == breakdown["score"]
    assert 50 <= profile["job_match_score"] <= 100
    assert len(breakdown["requirements"]) == 5

    chunks = (
        await db_session.scalars(select(ResumeChunk).where(ResumeChunk.resume_id == uuid.UUID(resume["id"])))
    ).all()
    assert len(chunks) >= 4 and all(c.embedding is not None for c in chunks)
    assert {c.section for c in chunks} >= {"experience", "skills"}

    extraction_call = mock_ai.llm.calls_for(ModelTask.RESUME_EXTRACTION)[0]
    assert "<resume>" in extraction_call.messages[1]["content"]
    analysis_call = mock_ai.llm.calls_for(ModelTask.PROFILE_ANALYSIS)[0]
    assert "Fairness and evidence rules" in analysis_call.messages[0]["content"]
    assert "[required_skill] Kubernetes (3+ years)" in analysis_call.messages[1]["content"]
    assert mock_ai.reranker.calls  # deterministic evidence was reranked


@pytest.mark.parametrize(
    ("name", "method", "ocr_calls"),
    [
        ("jane_doe_devops.pdf", "text", 0),
        ("jane_doe_devops.docx", "text", 0),
        ("jane_doe_devops.png", "ocr", 1),
        ("jane_doe_devops.jpg", "ocr", 1),
        ("jane_doe_devops_scanned.pdf", "ocr", 2),
    ],
)
async def test_every_sample_format(auth_client, mock_ai, pipeline_worker, name, method, ocr_calls) -> None:
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    mock_ai.ocr.script(*([JANE_TXT] if ocr_calls == 1 else [JANE_TXT[:SPLIT], JANE_TXT[SPLIT:]]))
    script_extraction(mock_ai, jane_extraction())
    response = await upload(client, candidate["id"], name, corpus.sample(name))
    assert response.status_code == 201, response.text
    await pipeline_worker.drain(timeout=30)
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    assert detail["status"] == "ready", detail
    assert detail["extraction_method"] == method
    assert len(mock_ai.ocr.calls) == ocr_calls
    if ocr_calls:
        assert states(detail["progress"])["ocr"] == "done"
        assert detail["progress"]["pages_total"] == ocr_calls
    profile = (await client.get(f"{API}/candidates/{candidate['id']}/profile")).json()
    assert profile["job_id"] is None and profile["job_match_score"] is None  # generic analysis
    assert profile["experience_years"] == 5.8


async def test_unreadable_image_fails_with_safe_message(
    auth_client, mock_ai, pipeline_worker, db_session
) -> None:
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    mock_ai.ocr.script("")
    response = await upload(client, candidate["id"], "photo.png", corpus.png_bytes())
    await pipeline_worker.drain(timeout=20)
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    assert detail["status"] == "failed"
    assert detail["error"].startswith("We could not find any readable text")
    assert "failed" in states(detail["progress"]).values()
    count = await db_session.scalar(select(func.count()).select_from(CandidateProfile))
    assert count == 0


async def test_model_outage_degrades_to_partial_profile(auth_client, mock_ai, pipeline_worker) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    candidate = await create_candidate(client)
    mock_ai.llm.set_default(ModelTask.RESUME_EXTRACTION, AIUnavailable("main down", endpoint="main"))
    mock_ai.llm.set_default(ModelTask.PROFILE_ANALYSIS, AIUnavailable("main down", endpoint="main"))
    response = await upload(
        client, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"), job_id=job["id"]
    )
    await pipeline_worker.drain(timeout=30)
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    assert detail["status"] == "ready" and detail["status_detail"] == "analysis degraded"
    # every extraction part per attempt (identity, 3 role groups + the rest, projects,
    # skills, background); retried on the outage schedule (S11), then the deterministic parse
    assert len(mock_ai.llm.calls_for(ModelTask.RESUME_EXTRACTION)) == (len(PROFILE_RETRY_SCHEDULE) + 1) * 8
    profile = (await client.get(f"{API}/candidates/{candidate['id']}/profile")).json()
    assert profile["status"] == "partial"
    assert profile["experience_years"] == 5.8  # heuristic parser + deterministic dates
    assert profile["profile"]["candidate_name"] == "Jane Doe"
    assert profile["job_match_score"] is not None  # deterministic evidence only
    weights = profile["match_breakdown"]["weights"]
    assert set(weights) == {"coverage", "experience"}  # model weight redistributed
    assert weights["coverage"] == pytest.approx(0.5 / 0.65, abs=0.001)


async def test_reprocess_creates_new_profile_version(auth_client, mock_ai, pipeline_worker) -> None:
    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    candidate = await create_candidate(client)
    response = await upload(client, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"))
    resume_id = response.json()["id"]
    conflict = await client.post(f"{API}/resumes/{resume_id}/reprocess", json={})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "resume_processing"
    await pipeline_worker.drain(timeout=30)
    first = (await client.get(f"{API}/candidates/{candidate['id']}/profile")).json()
    assert first["version"] == 2 and first["job_id"] is None  # v1 = quick profile

    again = await client.post(f"{API}/resumes/{resume_id}/reprocess", json={"job_id": job["id"]})
    assert again.status_code == 202 and again.json()["status"] == "uploaded"
    await pipeline_worker.drain(timeout=30)
    second = (
        await client.get(f"{API}/candidates/{candidate['id']}/profile", params={"job_id": job["id"]})
    ).json()
    assert second["version"] == 3 and second["job_id"] == job["id"]  # quick row refreshed in place
    assert second["job_match_score"] is not None
    old = (await client.get(f"{API}/candidates/{candidate['id']}/profile", params={"version": 2})).json()
    assert old["version"] == 2 and old["status"] in ("ready", "partial")  # unscripted mock answers


async def test_resume_rows_and_media_are_linked(
    auth_client, mock_ai, pipeline_worker, db_session, storage
) -> None:
    client = await auth_client(Role.RECRUITER)
    candidate = await create_candidate(client)
    response = await upload(
        client, candidate["id"], "../../etc/Jane CV (final).PDF", corpus.sample("jane_doe_devops.pdf")
    )
    body = response.json()
    resume = await db_session.get(CandidateResume, uuid.UUID(body["id"]))
    assert (
        resume.storage_key
        == f"orgs/{resume.organization_id}/resumes/{resume.candidate_id}/{resume.id}/Jane_CV_final.pdf"
    )
    assert resume.original_filename == "Jane CV (final).PDF"
    media = await db_session.get(MediaAsset, uuid.UUID(body["media_id"]))
    assert (
        media.kind == "resume" and media.candidate_id == resume.candidate_id and media.sha256 == resume.sha256
    )
    assert media.retention_until is not None
    assert await storage.exists(resume.storage_key)
    listing = (await client.get(f"{API}/candidates/{candidate['id']}/resumes")).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == body["id"]
    assert "media_url" not in listing["items"][0] or listing["items"][0]["media_url"] is None


async def test_one_failed_extraction_part_falls_back_for_its_fields_only(
    auth_client, mock_ai, pipeline_worker
) -> None:
    """The profile runs as small concurrent parts: when the skills part keeps
    failing, the profile keeps the model's roles and takes the skills from the
    deterministic parser (status partial), instead of losing everything."""
    from tests.integration.resume.helpers import jane_analysis, part_responder

    client = await auth_client(Role.RECRUITER)
    job = await create_job(client)
    candidate = await create_candidate(client)
    responder = part_responder(jane_extraction(), jane_analysis())

    def extraction(call):
        if call.schema.__name__ != "ResumeSkills":
            return responder(call)
        raise AIUnavailable("skills part down", endpoint="main")

    mock_ai.llm.set_default(ModelTask.RESUME_EXTRACTION, extraction)
    mock_ai.llm.set_default(ModelTask.PROFILE_ANALYSIS, responder)
    response = await upload(
        client, candidate["id"], "cv.txt", corpus.sample("jane_doe_devops.txt"), job_id=job["id"]
    )
    await pipeline_worker.drain(timeout=30)
    detail = (await client.get(f"{API}/resumes/{response.json()['id']}")).json()
    assert detail["status"] == "ready" and detail["status_detail"] == "analysis degraded"
    profile = (await client.get(f"{API}/candidates/{candidate['id']}/profile")).json()
    document = profile["profile"]
    assert profile["status"] == "partial"
    assert [r["company"] for r in document["roles"]] == [
        "Bluepeak Analytics",
        "Harborline Logistics",
        "Cobalt Retail Group",
    ]  # from the model
    assert document["skills"]  # from the deterministic parser
    assert [c["id"] for c in document["claims_to_verify"]] == ["c1", "c2"]  # analysis still from the model
