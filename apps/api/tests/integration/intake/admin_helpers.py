"""Helpers for the T3 intake integration tests (admin, listing, portal, invitation hook).

Jobs, rounds, résumés, profiles, sessions, evaluations and decisions are inserted directly (no catalog seeding,
no résumé pipeline, no model calls): T3 tests only need the rows those flows leave behind. Each track imports its
own helper module (there is no shared intake conftest).
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.db.models import (
    Candidate,
    CandidateProfile,
    CandidateResume,
    IntakeDecision,
    IntakeEvaluation,
    IntakeInterview,
    InterviewSession,
    Job,
    ReviewDecision,
    Round,
)
from app.db.session import session_scope
from app.domain.enums import RoundType
from app.realtime import deps as realtime_deps
from app.schemas.rounds import RoundConfig

API = "/api/v1"

PROFILE_DOC: dict[str, Any] = {
    "candidate_id": "x",
    "candidate_name": "Asha Rao",
    "headline": "Data analyst moving into ML",
    "experience_years": 3.0,
    "roles": [
        {
            "title": "Data Analyst",
            "company": "Acme Retail",
            "start": "2023-01",
            "end": "present",
            "responsibilities": ["Weekly sales dashboards"],
            "achievements": [{"text": "Cut report time by 40%", "measurable": True}],
            "technologies": ["SQL", "Python"],
        },
        {"title": "Junior Analyst", "company": "Beta Corp", "technologies": ["Excel"]},
    ],
    "skills": {
        "programming_language": [{"name": "Python", "category": "programming_language"}],
        "tool": [{"name": "Tableau", "category": "tool"}],
    },
    "projects": [{"name": "Churn model", "description": "Predicted churn", "technologies": ["scikit-learn"]}],
    "education": [
        {"institution": "Pune University", "degree": "BSc", "field": "Statistics", "start": "2017"}
    ],
    "certifications": [{"name": "Google Data Analytics"}],
    "claims_to_verify": [{"id": "c1", "claim": "Cut report time by 40% at Acme Retail", "priority": 5}],
    "job_match": {"overall_fit": 70.0},
}


def intake_round_config(**overrides: Any) -> dict[str, Any]:
    return RoundConfig(round_type=RoundType.INTAKE, name="Intake Interview", **overrides).model_dump(
        mode="json"
    )


async def make_intake_job(
    organization_id: uuid.UUID,
    *,
    status: str = "open",
    title: str = "AI/ML Training Program",
    with_intake_round: bool = True,
    other_round: bool = False,
    round_config: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Insert a job with an intake round (optionally also a technical round first). Returns (job, intake round)."""
    async with session_scope() as db:
        job = Job(
            organization_id=organization_id,
            title=title,
            description="Training program intake.",
            status=status,
        )
        db.add(job)
        await db.flush()
        sequence = 1
        if other_round:
            db.add(
                Round(
                    job_id=job.id,
                    name="Technical Screening",
                    round_type="technical_screening",
                    sequence=sequence,
                    config=RoundConfig(
                        round_type="technical_screening", name="Technical Screening"
                    ).model_dump(mode="json"),
                )
            )
            sequence += 1
        round_id = None
        if with_intake_round:
            config = round_config or intake_round_config()
            row = Round(
                job_id=job.id,
                name="Intake Interview",
                round_type="intake",
                sequence=sequence,
                duration_minutes=config.get("duration_minutes", 45),
                max_duration_minutes=config.get("max_duration_minutes", 50),
                config=config,
            )
            db.add(row)
            await db.flush()
            round_id = row.id
        return job.id, round_id


def create_body(job_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    suffix = secrets.token_hex(3)
    body: dict[str, Any] = {
        "candidate": {"full_name": f"Candidate {suffix}", "email": f"cand-{suffix}@example.test"},
        "job_id": str(job_id),
        "settings": {},
    }
    body.update(overrides)
    return body


async def create_intake(client: httpx.AsyncClient, job_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    response = await client.post(f"{API}/intake/interviews", json=create_body(job_id, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def token_of(invite_url: str) -> str:
    return invite_url.rsplit("/i/", 1)[1]


def portal_transport(app: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app, client=("10.9.8.7", 50000))


async def candidate_client(
    app: FastAPI, invite_url: str, email: str, access_code: str | None = None
) -> httpx.AsyncClient:
    """A browser verified through the invite link (candidate cookies + CSRF header). Close it after use."""
    http = httpx.AsyncClient(transport=portal_transport(app), base_url="http://testserver")
    body: dict[str, Any] = {"email": email}
    if access_code:
        body["access_code"] = access_code
    response = await http.post(f"{API}/candidate-access/{token_of(invite_url)}/verify", json=body)
    assert response.status_code == 200, response.text
    http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return http


async def give_consent(http: httpx.AsyncClient, interview_id: str) -> None:
    disclosure = (await http.get(f"{API}/portal/interviews/{interview_id}/disclosure")).json()
    items = {key: True for key in disclosure["required_items"]}
    response = await http.post(
        f"{API}/portal/interviews/{interview_id}/consent",
        json={"policy_hash": disclosure["policy_hash"], "items": items},
    )
    assert response.status_code == 201, response.text


async def add_resume(
    interview_id: uuid.UUID,
    *,
    status: str = "ready",
    profile_status: str | None = "ready",
    quick: bool = False,
    created_at: datetime | None = None,
    profile_doc: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Insert a résumé (and optionally a quick and/or full profile) for the interview's candidate."""
    async with session_scope() as db:
        intake = await db.scalar(select(IntakeInterview).where(IntakeInterview.interview_id == interview_id))
        from app.db.models import Interview

        interview = await db.get(Interview, interview_id)
        assert interview is not None
        resume = CandidateResume(
            candidate_id=interview.candidate_id,
            organization_id=interview.organization_id,
            interview_id=interview.id,
            storage_key=f"test/resumes/{uuid.uuid4()}.txt",
            original_filename="cv.txt",
            content_type="text/plain",
            size_bytes=100,
            sha256=secrets.token_hex(32),
            status=status,
        )
        if created_at is not None:
            resume.created_at = created_at
        db.add(resume)
        await db.flush()
        profile_id = None
        version = 1
        if quick:
            db.add(
                CandidateProfile(
                    candidate_id=interview.candidate_id,
                    resume_id=resume.id,
                    version=version,
                    status="quick",
                    profile=_quick_doc(profile_doc or PROFILE_DOC),
                )
            )
            version += 1
        if profile_status is not None:
            row = CandidateProfile(
                candidate_id=interview.candidate_id,
                resume_id=resume.id,
                job_id=interview.job_id,
                version=version,
                status=profile_status,
                profile=profile_doc or PROFILE_DOC,
            )
            db.add(row)
            await db.flush()
            profile_id = row.id
        _ = intake
        return resume.id, profile_id


def _quick_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """A coarser 'quick' read of the same résumé (one role fewer, no projects)."""
    quick = dict(doc)
    quick["roles"] = list(doc.get("roles") or [])[:1]
    quick["projects"] = []
    return quick


async def insert_session(
    interview_id: uuid.UUID,
    *,
    status: str = "completed",
    end_reason: str | None = None,
    elapsed_seconds: float = 2700.0,
    started: bool = True,
) -> uuid.UUID:
    async with session_scope() as db:
        from app.db.models import Interview

        interview = await db.get(Interview, interview_id)
        assert interview is not None and interview.round_id is not None
        now = datetime.now(UTC)
        ended = status in ("completing", "completed", "aborted", "failed")
        row = InterviewSession(
            interview_id=interview.id,
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=interview.round_id,
            status=status,
            round_config={},
            started_at=now - timedelta(minutes=50) if started else None,
            ended_at=now if ended else None,
            end_reason=end_reason or ("time_limit" if ended else None),
            elapsed_seconds=elapsed_seconds,
        )
        db.add(row)
        await db.flush()
        return row.id


async def insert_evaluation(
    intake_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    band: str | None = "accept_with_notes",
    total: str | None = "3.6700",
    status: str = "ready",
    training_plan: dict[str, Any] | None = None,
    review_items: list[dict[str, Any]] | None = None,
    version: int = 1,
) -> uuid.UUID:
    async with session_scope() as db:
        intake = await db.get(IntakeInterview, intake_id)
        assert intake is not None
        row = IntakeEvaluation(
            organization_id=intake.organization_id,
            intake_id=intake.id,
            session_id=session_id,
            version=version,
            status=status,
            reason="session_completed",
            kit_version="v1",
            scoring_version="intake-scoring-1",
            weighted_total=Decimal(total) if total is not None else None,
            band=band,
            evidence_sufficiency="sufficient" if total is not None else "insufficient",
            training_plan=training_plan,
            review_items=review_items or [],
        )
        db.add(row)
        await db.flush()
        return row.id


async def insert_decision(
    intake_id: uuid.UUID,
    session_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    *,
    outcome: str = "accept",
    resolutions: list[dict[str, Any]] | None = None,
) -> uuid.UUID:
    mirror = {
        "accept": "advance",
        "accept_with_notes": "advance",
        "second_interview": "hold",
        "reject": "reject",
        "request_evidence": "needs_more_information",
    }[outcome]
    async with session_scope() as db:
        intake = await db.get(IntakeInterview, intake_id)
        assert intake is not None
        review = ReviewDecision(
            session_id=session_id, reviewer_id=reviewer_id, decision=mirror, notes="rationale"
        )
        db.add(review)
        await db.flush()
        row = IntakeDecision(
            organization_id=intake.organization_id,
            intake_id=intake.id,
            session_id=session_id,
            review_decision_id=review.id,
            reviewer_id=reviewer_id,
            outcome=outcome,
            rationale="A sufficiently long rationale.",
            review_items_resolution=resolutions or [],
        )
        db.add(row)
        await db.flush()
        return row.id


async def intake_row(intake_id: uuid.UUID | str) -> IntakeInterview:
    async with session_scope() as db:
        row = await db.get(IntakeInterview, uuid.UUID(str(intake_id)))
        assert row is not None
        return row


async def candidate_row(candidate_id: uuid.UUID | str) -> Candidate:
    async with session_scope() as db:
        row = await db.get(Candidate, uuid.UUID(str(candidate_id)))
        assert row is not None
        return row


class FakeSessions:
    """Session service stand-in for start-guard tests: creates a ``created`` session row, nothing else."""

    def __init__(self) -> None:
        self.created: list[uuid.UUID] = []

    async def create_or_resume(
        self, db: Any, *, interview_id: Any, round_id: Any | None = None
    ) -> InterviewSession:
        from app.db.models import Interview

        existing = await db.scalar(
            select(InterviewSession).where(
                InterviewSession.interview_id == interview_id,
                InterviewSession.status.in_(["created", "preparing", "ready", "live", "paused"]),
            )
        )
        if existing is not None:
            return existing
        interview = await db.get(Interview, interview_id)
        row = InterviewSession(
            interview_id=interview.id,
            organization_id=interview.organization_id,
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            round_id=round_id,
            status="created",
            round_config={},
        )
        db.add(row)
        await db.flush()
        self.created.append(row.id)
        return row

    async def candidate_view(self, db: Any, session_id: Any) -> dict[str, Any]:
        return {"status": "created"}


@contextmanager
def fake_sessions() -> Iterator[FakeSessions]:
    sessions = FakeSessions()
    with realtime_deps.override_providers(session_service=sessions):
        yield sessions


def onboarding_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "full_name": "Asha Rao",
        "target_role": "ai_ml",
        "open_to_ai_ml": "yes",
        "technical_background": "Two years of Python scripting for data cleaning.",
        "primary_language": "python",
        "self_rating": 6,
        "scripting": "regular",
        "years_hands_on": 2.5,
        "experience_level_comfort": "unsure",
        "training_commitment": "full",
        "weekly_hours": 30,
        "availability": "within_2_weeks",
        "accuracy_confirmed": True,
    }
    body.update(overrides)
    return body
