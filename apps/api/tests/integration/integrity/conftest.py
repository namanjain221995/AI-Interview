"""Fixtures for integrity integration tests: sessions with a frozen policy,
consent rows, a fresh service instance and synthetic camera frames."""

from __future__ import annotations

import io
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageDraw

import app.workers.integrity  # noqa: F401  (registers the job handlers)
from app.core.security import generate_invite_token
from app.db.models import Candidate, Consent, Interview, InterviewSession, Job, Round
from app.db.session import session_scope
from app.schemas.policies import PolicySnapshot
from app.services.integrity.service import DefaultIntegrityService, set_integrity_service


@dataclass(slots=True)
class SessionRefs:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    interview_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    snapshot: dict[str, Any]

    @property
    def sid(self) -> str:
        return str(self.session_id)


def policy_snapshot(
    integrity: dict[str, Any] | None = None,
    recording: dict[str, Any] | None = None,
    retention: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = PolicySnapshot().model_dump(mode="json")
    data["integrity"].update(integrity or {})
    data["recording"].update(recording or {})
    data["retention"].update(retention or {})
    return data


SessionFactory = Callable[..., Awaitable[SessionRefs]]


@pytest_asyncio.fixture
async def make_session(org_factory: Callable[..., Awaitable[uuid.UUID]]) -> SessionFactory:
    async def make(
        *,
        organization_id: uuid.UUID | None = None,
        integrity: dict[str, Any] | None = None,
        recording: dict[str, Any] | None = None,
        retention: dict[str, Any] | None = None,
        status: str = "live",
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        consent: dict[str, Any] | None | bool = True,
        candidate_name: str = "Jane Doe",
        job_title: str = "Senior DevOps Engineer",
    ) -> SessionRefs:
        org_id = organization_id or await org_factory()
        snapshot = policy_snapshot(integrity, recording, retention)
        _, token_hash = generate_invite_token()
        async with session_scope() as db:
            job = Job(organization_id=org_id, title=job_title, description="Test job", status="open")
            candidate = Candidate(
                organization_id=org_id,
                full_name=candidate_name,
                email=f"cand-{secrets.token_hex(4)}@example.test",
            )
            db.add_all([job, candidate])
            await db.flush()
            round_ = Round(job_id=job.id, name="Technical", round_type="technical_screening", config={})
            db.add(round_)
            await db.flush()
            interview = Interview(
                organization_id=org_id,
                candidate_id=candidate.id,
                job_id=job.id,
                round_id=round_.id,
                status="in_progress",
                invite_token_hash=token_hash,
                invite_expires_at=datetime.now(UTC) + timedelta(days=7),
                policy_snapshot=snapshot,
            )
            db.add(interview)
            await db.flush()
            session = InterviewSession(
                interview_id=interview.id,
                organization_id=org_id,
                candidate_id=candidate.id,
                job_id=job.id,
                round_id=round_.id,
                status=status,
                started_at=started_at if started_at is not None else datetime.now(UTC) - timedelta(minutes=5),
                ended_at=ended_at,
            )
            db.add(session)
            if consent is not False:
                items = (
                    consent
                    if isinstance(consent, dict)
                    else {
                        "microphone": True,
                        "camera": True,
                        "camera_frames": True,
                        "transcript": True,
                        "integrity_monitoring": True,
                        "screen_share": True,
                        "audio_recording": True,
                        "video_recording": False,
                        "retention_days": 365,
                        "ai_assessment": True,
                    }
                )
                db.add(
                    Consent(
                        interview_id=interview.id,
                        candidate_id=candidate.id,
                        policy_version="2026-10-01",
                        policy_hash="0" * 64,
                        items=items,
                    )
                )
            await db.flush()
            return SessionRefs(org_id, session.id, interview.id, candidate.id, job.id, snapshot)

    return make


class TestClock:
    """Real time by default; ``at(seconds_ago)`` freezes it relative to now."""

    __test__ = False

    def __init__(self) -> None:
        self.frozen: datetime | None = None
        self._base = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.frozen or datetime.now(UTC)

    def at(self, seconds_ago: float) -> datetime:
        self.frozen = self._base - timedelta(seconds=seconds_ago)
        return self.frozen

    def later(self, seconds: float) -> datetime:
        self.frozen = self._base + timedelta(seconds=seconds)
        return self.frozen

    def real(self) -> None:
        self.frozen = None


@pytest.fixture
def clock() -> TestClock:
    return TestClock()


@pytest_asyncio.fixture
async def service(db_session, redis, storage, clock) -> AsyncIterator[DefaultIntegrityService]:
    """A fresh service bound to the per-test database, Redis DB and storage
    (its clock is the ``clock`` fixture)."""
    svc = DefaultIntegrityService(clock=clock)
    previous = set_integrity_service(svc)
    try:
        yield svc
    finally:
        set_integrity_service(previous)


def frame_bytes(kind: str = "person", size: tuple[int, int] = (1280, 720), fmt: str = "JPEG") -> bytes:
    """Synthetic webcam frame: an empty room, or simple drawn figures."""
    width, height = size
    image = Image.new("RGB", size, (182, 170, 150))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, int(height * 0.7), width, height], fill=(120, 96, 70))  # floor
    draw.rectangle(
        [int(width * 0.6), int(height * 0.15), int(width * 0.85), int(height * 0.45)], fill=(90, 120, 160)
    )  # window
    figures = {"empty": 0, "person": 1, "two": 2}.get(kind, 1)
    for index in range(figures):
        cx = int(width * (0.35 + 0.3 * index))
        draw.ellipse([cx - 60, int(height * 0.18), cx + 60, int(height * 0.18) + 120], fill=(224, 172, 140))
        draw.rectangle([cx - 90, int(height * 0.18) + 130, cx + 90, int(height * 0.8)], fill=(40, 70, 120))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=85) if fmt == "JPEG" else image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def camera_frame() -> bytes:
    return frame_bytes("person")
