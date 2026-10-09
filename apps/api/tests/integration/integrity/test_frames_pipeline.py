"""Frames: submit (validation, policy, consent, storage per policy, enqueue)
and analyze (mock vision -> sustained-signal rules -> evidence-linked events)."""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from app.ai.base import AIOutputInvalid, AIUnavailable
from app.core.errors import Conflict, Forbidden, PayloadTooLarge, RateLimited, UnsupportedMediaType
from app.core.queue import subscribe_session_events
from app.db.models import BackgroundJob, IntegrityEvent, MediaAsset
from app.db.session import session_scope
from app.domain.enums import IntegrityAction, IntegrityEventType, ModelTask
from app.schemas.ai import FrameObservation, ScreenObservation
from app.services.integrity.service import ANALYZE_FRAME_JOB

from .conftest import frame_bytes

pytestmark = pytest.mark.integration

T = IntegrityEventType
A = IntegrityAction


def at(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


async def submit_at(service, clock, refs, seconds_ago: float, image: bytes, kind: str = "camera") -> str:
    """Submit a frame received ``seconds_ago`` (server receive time drives timing)."""
    clock.at(seconds_ago)
    return await service.submit_frame(
        refs.sid, kind=kind, image=image, content_type="image/jpeg", captured_at=at(seconds_ago)
    )


def observation(**kw) -> FrameObservation:
    base = {"person_count": 1, "primary_face_visible": True, "confidence": 0.9}
    base.update(kw)
    return FrameObservation(**base)


async def media(mid: str) -> MediaAsset | None:
    async with session_scope() as db:
        return await db.get(MediaAsset, uuid.UUID(mid))


async def events(session_id: uuid.UUID) -> list[IntegrityEvent]:
    async with session_scope() as db:
        return list(
            (await db.scalars(select(IntegrityEvent).where(IntegrityEvent.session_id == session_id))).all()
        )


async def next_event(sub, wait: float = 2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while loop.time() < deadline:
        event = await sub.get(timeout=0.1)
        if event is not None:
            return event
    return None


# ------------------------------------------------------------------- submit


async def test_submit_stores_evidence_frame_with_retention_and_enqueues(
    service, make_session, storage, camera_frame
) -> None:
    refs = await make_session(retention={"frame_days": 7})
    media_id = await service.submit_frame(
        refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(1)
    )
    asset = await media(media_id)
    assert asset is not None and asset.kind == "camera_frame" and asset.content_type == "image/jpeg"
    assert asset.storage_key.startswith(
        f"orgs/{refs.organization_id}/sessions/{refs.session_id}/frames/camera/"
    )
    assert asset.session_id == refs.session_id and asset.candidate_id == refs.candidate_id
    remaining = asset.retention_until - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)
    stored = await storage.read(asset.storage_key)
    assert Image.open(io.BytesIO(stored)).size == (640, 360)
    async with session_scope() as db:
        job = await db.scalar(select(BackgroundJob).where(BackgroundJob.type == ANALYZE_FRAME_JOB))
    assert job is not None and job.payload["media_id"] == media_id and job.payload["session_id"] == refs.sid


async def test_submit_transient_when_frames_not_stored(service, make_session, redis, camera_frame) -> None:
    refs = await make_session(integrity={"store_evidence_frames": False})
    media_id = await service.submit_frame(
        refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(1)
    )
    assert await media(media_id) is None
    ttl = await redis.ttl(f"integrity:frame:{media_id}")
    assert 0 < ttl <= 600
    frame = await service.store.get_frame(media_id)
    assert frame is not None and frame[1]["kind"] == "camera" and frame[1]["stored"] is False


@pytest.mark.parametrize(
    ("integrity", "kind", "code"),
    [
        ({"camera_frame_analysis": False}, "camera", "frames_not_enabled"),
        ({"enabled": False}, "camera", "frames_not_enabled"),
        ({}, "screen", "frames_not_enabled"),
    ],
)
async def test_submit_rejected_when_policy_disables_analysis(
    service, make_session, camera_frame, integrity, kind, code
) -> None:
    refs = await make_session(integrity=integrity)
    with pytest.raises(Forbidden) as info:
        await service.submit_frame(
            refs.sid, kind=kind, image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )
    assert info.value.code == code


async def test_submit_requires_consent(service, make_session, camera_frame) -> None:
    no_consent = await make_session(consent=False)
    with pytest.raises(Forbidden) as info:
        await service.submit_frame(
            no_consent.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )
    assert info.value.code == "consent_required"
    declined = await make_session(consent={"camera": False, "microphone": True})
    with pytest.raises(Forbidden):
        await service.submit_frame(
            declined.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )


async def test_submit_rejects_ended_session_bad_type_and_size(
    service, make_session, camera_frame, settings
) -> None:
    ended = await make_session(status="completed", ended_at=datetime.now(UTC))
    with pytest.raises(Conflict):
        await service.submit_frame(
            ended.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )
    refs = await make_session()
    with pytest.raises(UnsupportedMediaType):
        await service.submit_frame(
            refs.sid, kind="camera", image=b"GIF89a" + b"0" * 64, content_type="image/gif", captured_at=at(0)
        )
    with pytest.raises(UnsupportedMediaType):
        await service.submit_frame(
            refs.sid,
            kind="camera",
            image=frame_bytes(fmt="PNG"),
            content_type="image/jpeg",
            captured_at=at(0),
        )
    settings.max_frame_bytes = 1024
    with pytest.raises(PayloadTooLarge):
        await service.submit_frame(
            refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )


async def test_submit_rate_limited_per_session(service, make_session, camera_frame) -> None:
    refs = await make_session(integrity={"camera_frame_interval_seconds": 120})  # limit = 1*2+2 = 4/min
    for _ in range(4):
        await service.submit_frame(
            refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )
    with pytest.raises(RateLimited):
        await service.submit_frame(
            refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(0)
        )


# ------------------------------------------------------------------ analyze


async def test_sustained_absence_links_evidence_and_deletes_other_frames(
    service, clock, make_session, mock_ai, storage, camera_frame
) -> None:
    refs = await make_session()
    empty = observation(person_count=0, primary_face_visible=False, notes="empty chair")
    mock_ai.llm.script(ModelTask.FRAME_ANALYSIS, empty, empty, empty)
    ids = [await submit_at(service, clock, refs, s, camera_frame) for s in (25, 15, 5)]
    clock.at(0)
    async with subscribe_session_events(refs.session_id) as sub:
        await asyncio.sleep(0.05)
        assert await service.analyze_frame(refs.sid, ids[0]) == []
        assert await service.analyze_frame(refs.sid, ids[1]) == []
        [outcome] = await service.analyze_frame(refs.sid, ids[2])
        published = [await next_event(sub), await next_event(sub)]
    assert outcome.event_type is T.NO_PERSON_VISIBLE and outcome.action is A.FLAG_FOR_HUMAN_REVIEW
    assert outcome.candidate_message == "We can't see you on camera. Please make sure you're in view."
    assert [p["type"] for p in published] == [
        "integrity.event",
        "integrity.warning",
    ]  # worker path publishes warnings
    assert published[0]["delivered_to_caller"] is False and published[0]["evidence_media_id"] == ids[2]
    [row] = await events(refs.session_id)
    assert row.source == "vision" and str(row.evidence_media_id) == ids[2]
    assert row.duration_ms == 20_000 and row.confidence == 0.9
    assert row.details["observation"]["person_count"] == 0 and "streak_id" in row.details
    for mid in ids[:2]:  # not evidence -> deleted (data minimisation)
        asset = await media(mid)
        assert asset.deleted_at is not None and asset.details["deleted_reason"] == "not_evidence"
        assert not await storage.exists(asset.storage_key)
    evidence = await media(ids[2])
    assert evidence.deleted_at is None and evidence.details["analysis"] == "done"
    assert evidence.details["observation"]["notes"] == "empty chair"
    call = mock_ai.llm.calls_for(ModelTask.FRAME_ANALYSIS)[0]
    assert call.endpoint == "vision" and call.schema is FrameObservation
    assert call.messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "Fairness and evidence rules" in call.messages[0]["content"]


async def test_phone_seen_twice_matches_example_event_shape(
    service, clock, make_session, mock_ai, camera_frame
) -> None:
    refs = await make_session()
    phone = observation(phone_like_object_visible=True, phone_confidence=0.74)
    mock_ai.llm.script(ModelTask.FRAME_ANALYSIS, phone, phone)
    first = await submit_at(service, clock, refs, 20, camera_frame)
    second = await submit_at(service, clock, refs, 5, camera_frame)
    clock.at(0)
    [log_only] = await service.analyze_frame(refs.sid, first)
    [flag] = await service.analyze_frame(refs.sid, second)
    assert log_only.action is A.LOG_ONLY and flag.action is A.FLAG_FOR_HUMAN_REVIEW
    assert flag.candidate_message is None
    rows = {r.action: r for r in await events(refs.session_id)}
    flagged = rows["flag_for_human_review"]
    example = {
        "type": flagged.type,
        "confidence": flagged.confidence,
        "evidence_media_id": str(flagged.evidence_media_id),
        "action": flagged.action,
    }
    assert example == {
        "type": "possible_phone_visible",
        "confidence": 0.74,
        "evidence_media_id": second,
        "action": "flag_for_human_review",
    }


async def test_vision_never_terminates_even_when_configured(
    service, clock, make_session, mock_ai, camera_frame
) -> None:
    refs = await make_session(
        integrity={"rules": [{"event_type": "multiple_persons_visible", "action": "terminate_interview"}]}
    )
    # Drawn figures have no detectable face: the vision model alone needs high confidence.
    two = observation(person_count=2, confidence=0.95)
    mock_ai.llm.script(ModelTask.FRAME_ANALYSIS, two, two)
    ids = [await submit_at(service, clock, refs, s, camera_frame) for s in (15, 5)]
    clock.at(0)
    assert await service.analyze_frame(refs.sid, ids[0]) == []
    [outcome] = await service.analyze_frame(refs.sid, ids[1])
    assert outcome.event_type is T.MULTIPLE_PERSONS_VISIBLE
    assert outcome.action is A.FLAG_FOR_HUMAN_REVIEW
    [row] = await events(refs.session_id)
    assert row.action == "flag_for_human_review" and row.details["downgraded_from"] == "terminate_interview"
    assert row.details["consecutive_frames"] == 2


async def test_gaze_alone_never_creates_events(service, make_session, mock_ai, camera_frame) -> None:
    refs = await make_session()
    away = observation(primary_person_looking_away=True, confidence=0.99)
    mock_ai.llm.set_default(ModelTask.FRAME_ANALYSIS, away)
    for s in (40, 30, 20, 10, 0):
        mid = await service.submit_frame(
            refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(s)
        )
        assert await service.analyze_frame(refs.sid, mid) == []
        assert (await media(mid)).deleted_at is not None
    assert await events(refs.session_id) == []


async def test_screen_frames_flag_prohibited_context_only(
    service, make_session, mock_ai, camera_frame
) -> None:
    refs = await make_session(integrity={"screen_share_analysis": True})
    clean = ScreenObservation(active_context="code editor", interview_window_visible=True, confidence=0.9)
    bad = ScreenObservation(
        active_context="chat application",
        interview_window_visible=True,
        other_applications_visible=["AI chat assistant"],
        possible_prohibited_context=True,
        description="AI chat assistant beside the interview tab",
        confidence=0.8,
    )
    weak = bad.model_copy(update={"confidence": 0.5})
    mock_ai.llm.script(ModelTask.SCREEN_ANALYSIS, clean, weak, bad)
    outcomes = []
    for s in (30, 20, 10):
        mid = await service.submit_frame(
            refs.sid, kind="screen", image=camera_frame, content_type="image/jpeg", captured_at=at(s)
        )
        outcomes.append(await service.analyze_frame(refs.sid, mid))
    assert outcomes[0] == [] and outcomes[1] == []
    [outcome] = outcomes[2]
    assert outcome.event_type is T.SCREEN_CONTEXT_CHANGE and outcome.action is A.FLAG_FOR_HUMAN_REVIEW
    [row] = await events(refs.session_id)
    assert row.source == "screen" and row.details["observation"]["active_context"] == "chat application"


async def test_transient_frame_analysis_has_no_evidence_and_is_purged(
    service, make_session, mock_ai, redis, camera_frame
) -> None:
    refs = await make_session(integrity={"store_evidence_frames": False})
    photo = observation(phone_like_object_visible=True, phone_confidence=0.9, phone_held_toward_screen=True)
    mock_ai.llm.script(ModelTask.FRAME_ANALYSIS, photo)
    mid = await service.submit_frame(
        refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(1)
    )
    outcomes = await service.analyze_frame(refs.sid, mid)
    assert {o.event_type for o in outcomes} == {T.POSSIBLE_PHONE_VISIBLE, T.POSSIBLE_PHOTO_TAKING}
    assert all(r.evidence_media_id is None for r in await events(refs.session_id))
    assert await redis.exists(f"integrity:frame:{mid}") == 0


async def test_stale_frames_are_skipped_without_model_call(
    service, clock, make_session, mock_ai, camera_frame
) -> None:
    refs = await make_session()
    mid = await submit_at(service, clock, refs, 0, camera_frame)
    clock.later(600)  # the job only ran 10 minutes after the frame arrived
    assert await service.analyze_frame(refs.sid, mid) == []
    assert mock_ai.llm.calls_for(ModelTask.FRAME_ANALYSIS) == []
    assert (await media(mid)).details["deleted_reason"] == "stale"


async def test_model_outage_propagates_for_retry_and_invalid_output_is_skipped(
    service, make_session, mock_ai, camera_frame
) -> None:
    refs = await make_session()
    mock_ai.llm.script(ModelTask.FRAME_ANALYSIS, AIUnavailable("vision down", endpoint="vision"))
    mid = await service.submit_frame(
        refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(1)
    )
    with pytest.raises(AIUnavailable):
        await service.analyze_frame(refs.sid, mid)
    assert (await media(mid)).deleted_at is None  # kept for the retry
    mock_ai.llm.script(ModelTask.FRAME_ANALYSIS, AIOutputInvalid("garbage"))
    assert await service.analyze_frame(refs.sid, mid) == []
    assert (await media(mid)).details["deleted_reason"] == "model_output_invalid"


async def test_missing_or_foreign_frames_are_noops(service, make_session, camera_frame) -> None:
    refs = await make_session()
    other = await make_session()
    assert await service.analyze_frame(refs.sid, str(uuid.uuid4())) == []
    mid = await service.submit_frame(
        other.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(1)
    )
    assert await service.analyze_frame(refs.sid, mid) == []  # belongs to another session


async def test_worker_runs_analyze_frame_job(
    service, make_session, mock_ai, worker_factory, camera_frame
) -> None:
    refs = await make_session()
    two = observation(person_count=2, confidence=0.9)
    mock_ai.llm.set_default(ModelTask.FRAME_ANALYSIS, two)
    worker = worker_factory(job_types=[ANALYZE_FRAME_JOB])
    processed = 0
    for s in (15, 5):  # frames arrive ~10 s apart in production, so analyses finish in order
        await service.submit_frame(
            refs.sid, kind="camera", image=camera_frame, content_type="image/jpeg", captured_at=at(s)
        )
        processed += await worker.drain(timeout=20)
    assert processed == 2
    async with session_scope() as db:
        jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == ANALYZE_FRAME_JOB))).all()
    assert {j.status for j in jobs} == {"succeeded"}
    rows = await events(refs.session_id)
    assert [r.type for r in rows] == ["multiple_persons_visible"]
