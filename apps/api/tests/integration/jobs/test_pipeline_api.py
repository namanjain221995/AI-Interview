"""Server-paged job pipeline with exact stage counts (S14, audit F3: the page stopped at 200 / 100 rows)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Candidate, Interview, InterviewSession, Job, Round
from app.db.session import session_scope
from app.domain.enums import Role, RoundType
from app.schemas.rounds import RoundConfig

pytestmark = pytest.mark.integration

API = "/api/v1"


async def _campus_job(organization_id: uuid.UUID) -> uuid.UUID:
    """250 invitations: 100 open, 30 past their deadline (still 'invited'), 60 in progress, 40 completed,
    20 cancelled; 120 sessions (20 completed interviews also have an older aborted session)."""
    now = datetime.now(UTC)
    config = RoundConfig(round_type=RoundType.TECHNICAL_SCREENING, name="Screen").model_dump(mode="json")
    async with session_scope() as db:
        job = Job(organization_id=organization_id, title="Campus drive", description="", status="open")
        db.add(job)
        await db.flush()
        round_ = Round(
            job_id=job.id,
            name="Screen",
            round_type="technical_screening",
            sequence=1,
            duration_minutes=20,
            config={},
        )
        db.add(round_)
        await db.flush()
        for i in range(250):
            candidate = Candidate(
                organization_id=organization_id, full_name=f"Person {i:03d}", email=f"p{i:03d}@example.com"
            )
            db.add(candidate)
            await db.flush()
            if i < 100:
                status, expires = "invited", now + timedelta(days=3)
            elif i < 130:
                status, expires = "invited", now - timedelta(hours=1)
            elif i < 190:
                status, expires = "in_progress", now + timedelta(days=3)
            elif i < 230:
                status, expires = "completed", now - timedelta(days=1)
            else:
                status, expires = "cancelled", now + timedelta(days=3)
            interview = Interview(
                organization_id=organization_id,
                candidate_id=candidate.id,
                job_id=job.id,
                round_id=round_.id,
                status=status,
                invite_token_hash=secrets.token_hex(32),
                invite_expires_at=expires,
                created_at=now - timedelta(minutes=300 - i),
            )
            db.add(interview)
            await db.flush()
            sessions: list[tuple[str, int]] = []
            if status == "in_progress":
                sessions = [("live", 10)]
            elif status == "completed":
                sessions = [("completed", 10)] + ([("aborted", 60)] if i < 210 else [])
            for session_status, minutes_ago in sessions:
                db.add(
                    InterviewSession(
                        interview_id=interview.id,
                        organization_id=organization_id,
                        candidate_id=candidate.id,
                        job_id=job.id,
                        round_id=round_.id,
                        status=session_status,
                        round_config=config,
                        created_at=now - timedelta(minutes=minutes_ago),
                    )
                )
        return job.id


async def test_pipeline_pages_every_row_with_exact_counts(auth_client, org_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    job_id = await _campus_job(recruiter.user.organization_id)

    first = await recruiter.get(f"{API}/jobs/{job_id}/pipeline", params={"limit": 50})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["counts"] == {"invited": 100, "in_progress": 60, "completed": 40, "closed": 50, "all": 250}
    assert body["total"] == 250 and len(body["items"]) == 50
    assert body["items"][0]["interview"]["candidate"]["full_name"] == "Person 249"  # newest first

    oldest = (
        await recruiter.get(f"{API}/jobs/{job_id}/pipeline", params={"limit": 100, "offset": 200})
    ).json()
    assert (
        len(oldest["items"]) == 50
        and oldest["items"][-1]["interview"]["candidate"]["full_name"] == "Person 000"
    )

    closed = (
        await recruiter.get(f"{API}/jobs/{job_id}/pipeline", params={"stage": "closed", "limit": 100})
    ).json()
    assert closed["total"] == 50
    assert all(
        r["interview"]["status"] == "cancelled" or r["interview"]["is_expired"] for r in closed["items"]
    )

    completed = (
        await recruiter.get(f"{API}/jobs/{job_id}/pipeline", params={"stage": "completed", "limit": 100})
    ).json()
    assert completed["total"] == 40
    assert {r["session"]["status"] for r in completed["items"]} == {
        "completed"
    }  # the latest session, not the aborted one

    invited = (
        await recruiter.get(f"{API}/jobs/{job_id}/pipeline", params={"stage": "invited", "limit": 100})
    ).json()
    assert invited["total"] == 100 and all(r["session"] is None for r in invited["items"])

    search = (
        await recruiter.get(f"{API}/jobs/{job_id}/pipeline", params={"q": "Person 01", "limit": 50})
    ).json()
    assert search["total"] == 10 and search["counts"]["all"] == 250  # counts describe the whole job

    stranger = await auth_client(Role.RECRUITER, organization_id=await org_factory())  # another organisation
    other_org = await stranger.get(f"{API}/jobs/{job_id}/pipeline")
    assert other_org.status_code == 404
