"""Recording integration fixtures: interviews with a recording policy + consent,
signed-in candidate clients, segment uploads and ffmpeg switches.

Tests that need a real ffmpeg skip unless one is reachable through
``RECORDING_FFMPEG_PATH`` / ``RECORDING_FFPROBE_PATH`` (or on ``PATH``).
"""

from __future__ import annotations

import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from app.core.config import Settings
from app.core.security import generate_invite_token
from app.db.models import Candidate, Consent, Interview, InterviewSession, Job, Round
from app.db.session import session_scope
from app.domain.enums import RoundType
from app.schemas.policies import PolicySnapshot
from app.schemas.rounds import RoundConfig
from app.services.recordings import ffmpeg

pytestmark = pytest.mark.integration

FULL_CONSENT: dict[str, Any] = {
    "microphone": True,
    "camera": True,
    "transcript": True,
    "integrity_monitoring": True,
    "camera_frames": True,
    "audio_recording": True,
    "video_recording": True,
    "ai_assessment": True,
}
WEBM_HEADER = b"\x1a\x45\xdf\xa3" + bytes(range(60))


@dataclass(slots=True)
class RecSession:
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_email: str
    interview_id: uuid.UUID
    session_id: uuid.UUID
    invite_token: str


def policy_snapshot(
    *, record_video: bool = True, record_screen: bool = False, video_days: int = 30
) -> dict[str, Any]:
    snap = PolicySnapshot().model_dump(mode="json")
    snap["recording"].update(record_video=record_video, record_screen=record_screen)
    snap["retention"]["video_days"] = video_days
    return snap


async def make_session(
    organization_id: uuid.UUID,
    *,
    policy: dict[str, Any] | None = None,
    consent: dict[str, Any] | None = FULL_CONSENT,
    status: str = "live",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> RecSession:
    token, token_hash = generate_invite_token()
    config = RoundConfig(round_type=RoundType.TECHNICAL_SCREENING, name="Technical Screening")
    async with session_scope() as db:
        job = Job(
            organization_id=organization_id, title="Platform Engineer", description="Run it.", status="open"
        )
        candidate = Candidate(
            organization_id=organization_id,
            full_name="Jane Doe",
            email=f"jane-{secrets.token_hex(4)}@example.test",
        )
        db.add_all([job, candidate])
        await db.flush()
        round_ = Round(
            job_id=job.id,
            name="Technical Screening",
            round_type=RoundType.TECHNICAL_SCREENING.value,
            config=config.model_dump(mode="json"),
        )
        db.add(round_)
        await db.flush()
        interview = Interview(
            organization_id=organization_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_.id,
            status="in_progress" if status in ("live", "paused") else "invited",
            invite_token_hash=token_hash,
            invite_expires_at=datetime.now(UTC) + timedelta(days=7),
            policy_snapshot=policy or policy_snapshot(),
        )
        db.add(interview)
        await db.flush()
        if consent is not None:
            db.add(
                Consent(
                    interview_id=interview.id,
                    candidate_id=candidate.id,
                    policy_version="2026-10-01",
                    policy_hash="0" * 64,
                    items=consent,
                )
            )
        row = InterviewSession(
            interview_id=interview.id,
            organization_id=organization_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_.id,
            status=status,
            round_config=config.model_dump(mode="json"),
            started_at=started_at or datetime.now(UTC) - timedelta(minutes=5),
            ended_at=ended_at,
        )
        db.add(row)
        await db.flush()
        return RecSession(
            organization_id=organization_id,
            candidate_id=candidate.id,
            candidate_email=candidate.email,
            interview_id=interview.id,
            session_id=row.id,
            invite_token=token,
        )


async def set_session(session_id: uuid.UUID, **values: Any) -> None:
    async with session_scope() as db:
        row = await db.get(InterviewSession, session_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)


def _transport(app: Any) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app, client=("127.0.0.1", 50001))


async def candidate_client(app: Any, rec: RecSession) -> httpx.AsyncClient:
    http = httpx.AsyncClient(transport=_transport(app), base_url="http://testserver")
    response = await http.post(
        f"/api/v1/candidate-access/{rec.invite_token}/verify", json={"email": rec.candidate_email}
    )
    assert response.status_code == 200, response.text
    http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return http


async def upload(
    http: httpx.AsyncClient,
    rec: RecSession,
    *,
    segment: int,
    data: bytes,
    part_id: uuid.UUID | str | None = None,
    kind: str = "camera",
    content_type: str = "video/webm;codecs=vp8,opus",
    **fields: Any,
) -> httpx.Response:
    form: dict[str, str] = {"segment": str(segment), "kind": kind}
    if part_id is not None:
        form["part_id"] = str(part_id)
    for key, value in fields.items():
        form[key] = str(value).lower() if isinstance(value, bool) else str(value)
    return await http.post(
        f"/api/v1/portal/sessions/{rec.session_id}/recording",
        files={"file": (f"segment-{segment:05d}.webm", data, content_type)},
        data=form,
    )


@pytest_asyncio.fixture
async def org(org_factory: Any) -> AsyncIterator[uuid.UUID]:
    yield await org_factory()


@pytest.fixture
def no_ffmpeg(settings: Settings) -> Iterator[None]:
    settings.recording_ffmpeg_path = "/nonexistent/ffmpeg"
    settings.recording_ffprobe_path = "/nonexistent/ffprobe"
    ffmpeg.reset_tools_cache()
    yield
    ffmpeg.reset_tools_cache()


@pytest.fixture
def real_ffmpeg() -> Iterator[ffmpeg.Tools]:
    ffmpeg.reset_tools_cache()
    found = ffmpeg.tools()
    if found.ffmpeg is None or found.ffprobe is None:
        pytest.skip("ffmpeg/ffprobe not available (set RECORDING_FFMPEG_PATH / RECORDING_FFPROBE_PATH)")
    yield found
    ffmpeg.reset_tools_cache()


def make_live_webm(tools: ffmpeg.Tools, target: Path, *, seconds: int = 6, video: bool = True) -> bytes:
    """A MediaRecorder-like WebM (live: no cues, unknown sizes) made with ffmpeg."""
    assert tools.ffmpeg is not None
    argv = [tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        argv += ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=15"]
    argv += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    argv += ["-t", str(seconds)]
    argv += ["-c:v", "libvpx", "-b:v", "300k"] if video else []
    argv += ["-c:a", "libopus", "-f", "webm", "-live", "1", "-cluster_time_limit", "1000", str(target)]
    subprocess.run(argv, check=True, timeout=120)
    return target.read_bytes()


def split_bytes(data: bytes, parts: int) -> list[bytes]:
    size = -(-len(data) // parts)
    return [data[i : i + size] for i in range(0, len(data), size)]
