"""Face presence end to end: the server-side face detector on received frames,
face-check snapshots confirmed in the request and linked to the on-device
interval, consent/policy gates, grouping of intervals and summaries, and the
fused vision analysis."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ai.base import AIUnavailable
from app.core.errors import Forbidden, ValidationFailed
from app.db.models import BackgroundJob, IntegrityEvent, MediaAsset
from app.db.session import session_scope
from app.domain.enums import IntegrityAction, IntegrityEventType, ModelTask
from app.schemas.ai import FrameObservation
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity.service import ANALYZE_FRAME_JOB

pytestmark = pytest.mark.integration

T = IntegrityEventType
A = IntegrityAction
FACES = Path(__file__).resolve().parents[2] / "fixtures" / "faces"
CONSENT = {
    "microphone": True,
    "camera": True,
    "camera_frames": True,
    "face_check": True,
    "transcript": True,
    "integrity_monitoring": True,
    "screen_share": False,
    "audio_recording": True,
    "video_recording": False,
    "ai_assessment": True,
    "retention_days": 365,
}


def image(name: str) -> bytes:
    return (FACES / f"{name}.jpg").read_bytes()


def iso(seconds_ago: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def face_event(
    event_type: str, episode: str, duration_ms: int, phase: str = "start", **extra
) -> ClientIntegrityEvent:
    return ClientIntegrityEvent(
        event_type=event_type,
        occurred_at=iso(duration_ms / 1000),
        duration_ms=duration_ms,
        details={"episode": episode, "phase": phase, **extra},
    )


async def snapshot(service, refs, name: str, reason: str, episode: str, duration_ms: int, **extra) -> str:
    hint = {
        "reason": reason,
        "episode": episode,
        "duration_ms": duration_ms,
        "started_at": iso(duration_ms / 1000),
    }
    hint.update(extra)
    return await service.submit_frame(
        refs.sid,
        kind="camera",
        image=image(name),
        content_type="image/jpeg",
        captured_at=iso(),
        trigger="face_check",
        face_check=json.dumps(hint),
    )


async def rows(session_id: uuid.UUID, event_type: T | None = None) -> list[IntegrityEvent]:
    async with session_scope() as db:
        stmt = select(IntegrityEvent).where(IntegrityEvent.session_id == session_id)
        if event_type is not None:
            stmt = stmt.where(IntegrityEvent.type == event_type.value)
        return list((await db.scalars(stmt.order_by(IntegrityEvent.created_at))).all())


async def media(mid: str) -> MediaAsset | None:
    async with session_scope() as db:
        return await db.get(MediaAsset, uuid.UUID(mid))


async def analyze_jobs(media_id: str) -> int:
    async with session_scope() as db:
        jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == ANALYZE_FRAME_JOB))).all()
    return sum(1 for job in jobs if job.payload.get("media_id") == media_id)


# ---------------------------------------------------------------- frames


async def test_every_camera_frame_is_checked_by_the_face_detector(service, make_session) -> None:
    refs = await make_session(consent=CONSENT)
    mid = await service.submit_frame(
        refs.sid, kind="camera", image=image("one_front"), content_type="image/jpeg", captured_at=iso()
    )
    details = (await media(mid)).details
    assert details["trigger"] == "periodic" and details["analysis"] == "pending"
    faces = details["faces"]
    assert (faces["face_count"], faces["counted_faces"], faces["detector"]) == (1, 1, "yunet-2023mar")
    assert 0 < faces["latency_ms"] < 2000
    assert set(faces["faces"][0]) == {"box", "score", "static", "moving"}
    assert await analyze_jobs(mid) == 1


async def test_confirmed_multiple_faces_are_flagged_with_the_snapshot_as_evidence(
    service, make_session
) -> None:
    refs = await make_session(consent=CONSENT)
    [warned] = await service.record_client_events(
        refs.sid, [face_event("multiple_faces_visible", "mf_000001", 3400, faces=2)]
    )
    assert warned.event_type is T.MULTIPLE_FACES_VISIBLE and warned.action is A.WARN_CANDIDATE
    assert warned.candidate_message == "Please make sure only you are visible on camera during the interview."
    mid = await snapshot(service, refs, "two_side_by_side", "multiple_faces", "mf_000001", 3600, faces=2)
    [row] = await rows(refs.session_id, T.MULTIPLE_FACES_VISIBLE)
    assert row.action == A.FLAG_FOR_HUMAN_REVIEW.value and row.severity == "high"
    assert row.source == "browser" and str(row.evidence_media_id) == mid
    check = row.details["server_check"]
    assert check["status"] == "confirmed" and check["faces"] == 2 and check["media_id"] == mid
    assert row.details["episodes"] == {"mf_000001": 3600} and row.count == 1 and row.duration_ms == 3600
    assert row.details["decision"] == "confirmed"
    # The rest of the interval (and its end) lands on the same row.
    await service.record_client_events(
        refs.sid, [face_event("multiple_faces_visible", "mf_000001", 12_000, phase="end", faces=2)]
    )
    [row] = await rows(refs.session_id, T.MULTIPLE_FACES_VISIBLE)
    assert row.duration_ms == 12_000 and row.count == 1 and row.action == A.FLAG_FOR_HUMAN_REVIEW.value
    assert row.details["phase"] == "end" and row.details["server_checks"] == {"mf_000001": "confirmed"}
    asset = await media(mid)
    assert asset.deleted_at is None and asset.details["face_check"] == {
        "reason": "multiple_faces",
        "episode": "mf_000001",
    }
    assert await analyze_jobs(mid) == 1  # the snapshot is also a camera frame for the vision model


async def test_snapshot_arriving_first_and_a_contradicting_check(service, make_session) -> None:
    refs = await make_session(consent=CONSENT, integrity={"camera_frame_analysis": False})
    mid = await snapshot(service, refs, "one_front", "multiple_faces", "mf_000002", 3500)
    [row] = await rows(refs.session_id, T.MULTIPLE_FACES_VISIBLE)
    assert row.details["server_check"]["status"] == "not_confirmed"
    assert row.action == A.WARN_CANDIDATE.value and row.evidence_media_id is None
    assert (await media(mid)).deleted_at is not None  # not evidence: deleted right away
    assert await analyze_jobs(mid) == 0
    # A long interval is not flagged when the server's check contradicted the device.
    await service.record_client_events(
        refs.sid, [face_event("multiple_faces_visible", "mf_000002", 40_000, "end")]
    )
    [row] = await rows(refs.session_id, T.MULTIPLE_FACES_VISIBLE)
    assert row.action == A.WARN_CANDIDATE.value and row.duration_ms == 40_000


async def test_no_face_snapshot_and_a_long_absence(service, make_session) -> None:
    refs = await make_session(consent=CONSENT)
    mid = await snapshot(service, refs, "none_empty_room", "no_face", "nf_000001", 5200)
    [row] = await rows(refs.session_id, T.FACE_NOT_VISIBLE)
    assert row.details["server_check"]["status"] == "confirmed" and str(row.evidence_media_id) == mid
    assert row.action == A.WARN_CANDIDATE.value and row.severity == "low"
    [outcome] = await service.record_client_events(
        refs.sid, [face_event("face_not_visible", "nf_000001", 31_000, "update")]
    )
    assert outcome.action is A.FLAG_FOR_HUMAN_REVIEW
    [row] = await rows(refs.session_id, T.FACE_NOT_VISIBLE)
    assert (
        row.action == A.FLAG_FOR_HUMAN_REVIEW.value and row.severity == "medium" and row.duration_ms == 31_000
    )
    # A separate short interval a minute later joins the same burst row.
    await service.record_client_events(refs.sid, [face_event("face_not_visible", "nf_000002", 6000)])
    [row] = await rows(refs.session_id, T.FACE_NOT_VISIBLE)
    assert row.count == 2 and row.duration_ms == 37_000 and row.action == A.FLAG_FOR_HUMAN_REVIEW.value


async def test_face_check_needs_a_consent_that_described_it(service, make_session) -> None:
    consent = {k: v for k, v in CONSENT.items() if k != "face_check"}
    refs = await make_session(consent=consent)
    assert (
        await service.record_client_events(refs.sid, [face_event("face_not_visible", "nf_000003", 6000)])
        == []
    )
    with pytest.raises(Forbidden) as caught:
        await snapshot(service, refs, "none_empty_room", "no_face", "nf_000003", 6000)
    assert caught.value.code == "consent_required"
    assert await rows(refs.session_id) == []
    declined = await make_session(consent={**CONSENT, "camera": False})
    assert (
        await service.record_client_events(declined.sid, [face_event("face_not_visible", "nf_4444", 6000)])
        == []
    )


async def test_face_check_follows_the_policy_toggle(service, make_session) -> None:
    refs = await make_session(consent=CONSENT, integrity={"face_presence_check": False})
    assert (
        await service.record_client_events(
            refs.sid, [face_event("multiple_faces_visible", "mf_000005", 4000)]
        )
        == []
    )
    with pytest.raises(Forbidden) as caught:
        await snapshot(service, refs, "two_side_by_side", "multiple_faces", "mf_000005", 4000)
    assert caught.value.code == "frames_not_enabled"
    # Periodic frames are still checked by the face detector for the vision analysis.
    mid = await service.submit_frame(
        refs.sid, kind="camera", image=image("two_side_by_side"), content_type="image/jpeg", captured_at=iso()
    )
    assert (await media(mid)).details["faces"]["counted_faces"] == 2


async def test_face_check_only_policy_accepts_snapshots_but_not_periodic_frames(
    service, make_session
) -> None:
    refs = await make_session(consent=CONSENT, integrity={"camera_frame_analysis": False})
    with pytest.raises(Forbidden):
        await service.submit_frame(
            refs.sid, kind="camera", image=image("one_front"), content_type="image/jpeg", captured_at=iso()
        )
    mid = await snapshot(service, refs, "two_side_by_side", "multiple_faces", "mf_000006", 3200)
    assert (await media(mid)).deleted_at is None  # confirmed: kept as evidence
    assert await analyze_jobs(mid) == 0


async def test_invalid_face_check_snapshots_are_rejected(service, make_session) -> None:
    refs = await make_session(consent=CONSENT)
    for trigger, hint, kind in (
        ("face_check", None, "camera"),
        ("face_check", json.dumps({"reason": "smile", "episode": "ep_9999"}), "camera"),
        ("face_check", json.dumps({"reason": "no_face"}), "camera"),
        ("face_check", json.dumps({"reason": "no_face", "episode": "ep_9999"}), "screen"),
        ("burst", None, "camera"),
    ):
        with pytest.raises(ValidationFailed):
            await service.submit_frame(
                refs.sid,
                kind=kind,
                image=image("one_front"),
                content_type="image/jpeg",
                captured_at=iso(),
                trigger=trigger,
                face_check=hint,
            )


async def test_summaries_group_per_page_run_and_static_faces_are_logged(service, make_session) -> None:
    refs = await make_session(consent=CONSENT)

    def summary(run: str, monitored: int, one: int) -> ClientIntegrityEvent:
        return ClientIntegrityEvent(
            event_type="face_check_summary",
            occurred_at=iso(),
            details={"run": run, "monitored_ms": monitored, "one_face_ms": one, "frames": monitored // 250},
        )

    await service.record_client_events(refs.sid, [summary("run_aaaa", 60_000, 58_000)])
    await service.record_client_events(refs.sid, [summary("run_aaaa", 120_000, 117_000)])
    await service.record_client_events(refs.sid, [summary("run_bbbb", 30_000, 30_000)])
    [row] = await rows(refs.session_id, T.FACE_CHECK_SUMMARY)
    assert row.action == A.LOG_ONLY.value and row.count == 2
    assert row.details["monitored_ms"] == 150_000 and row.details["one_face_ms"] == 147_000
    assert row.details["runs"]["run_aaaa"]["monitored_ms"] == 120_000
    [static] = await service.record_client_events(
        refs.sid,
        [
            ClientIntegrityEvent(
                event_type="static_face_ignored", occurred_at=iso(), details={"still_seconds": 31}
            )
        ],
    )
    assert static.action is A.LOG_ONLY
    async with session_scope() as db:
        from app.services.integrity.queries import face_presence_summary

        presence = await face_presence_summary(db, refs.session_id)
    assert presence["monitored_ms"] == 150_000 and presence["static_faces_ignored"] == 1


async def test_face_signals_never_pause_or_end_the_interview(service, make_session) -> None:
    rules = [
        {"event_type": "multiple_faces_visible", "action": "terminate_interview"},
        {"event_type": "face_not_visible", "action": "pause_interview"},
    ]
    refs = await make_session(consent=CONSENT, integrity={"rules": rules})
    outcomes = await service.record_client_events(
        refs.sid,
        [
            face_event("multiple_faces_visible", "mf_000007", 5000),
            face_event("face_not_visible", "nf_000007", 9000),
        ],
    )
    assert {o.action for o in outcomes} == {A.FLAG_FOR_HUMAN_REVIEW}
    await snapshot(service, refs, "two_side_by_side", "multiple_faces", "mf_000007", 5000)
    for row in await rows(refs.session_id):
        assert row.action not in ("pause_interview", "terminate_interview")
        assert row.details["downgraded_from"] in ("terminate_interview", "pause_interview")


# ---------------------------------------------------------------- analysis


async def test_a_poster_is_not_a_second_person_for_the_vision_analysis(
    service, clock, make_session, mock_ai
) -> None:
    refs = await make_session(consent=CONSENT)
    # The vision model counts the framed print as a person, with fairly high confidence.
    mock_ai.llm.set_default(
        ModelTask.FRAME_ANALYSIS, FrameObservation(person_count=2, primary_face_visible=True, confidence=0.85)
    )
    ids = []
    for index, seconds_ago in enumerate((45, 30, 15, 0)):
        clock.at(seconds_ago)
        ids.append(
            await service.submit_frame(
                refs.sid,
                kind="camera",
                image=image(f"poster_seq_{index}"),
                content_type="image/jpeg",
                captured_at=iso(seconds_ago),
            )
        )
    statics = [(await media(mid)).details["faces"]["static_faces"] for mid in ids]
    assert statics == [0, 0, 1, 1]
    clock.at(0)
    for mid in ids:
        assert await service.analyze_frame(refs.sid, mid) == []
    assert await rows(refs.session_id, T.MULTIPLE_PERSONS_VISIBLE) == []
    observation = (await media(ids[-1])).details
    assert observation.get("deleted_reason") == "not_evidence"


async def test_two_people_are_found_by_the_face_detector(service, clock, make_session, mock_ai) -> None:
    refs = await make_session(consent=CONSENT)
    mock_ai.llm.set_default(
        ModelTask.FRAME_ANALYSIS, FrameObservation(person_count=1, primary_face_visible=True, confidence=0.9)
    )
    ids = []
    for index, seconds_ago in enumerate((30, 15, 0)):
        clock.at(seconds_ago)
        ids.append(
            await service.submit_frame(
                refs.sid,
                kind="camera",
                image=image(f"two_move_{index}"),
                content_type="image/jpeg",
                captured_at=iso(seconds_ago),
            )
        )
    lives = [(await media(mid)).details["faces"]["live_faces"] for mid in ids]
    assert lives == [0, 2, 2]  # both faces are seen moving from the second frame on
    clock.at(0)
    outcomes = []
    for mid in ids:
        outcomes += await service.analyze_frame(refs.sid, mid)
    # Two consecutive frames with two live faces (the 2nd and 3rd): flagged for review.
    assert [(o.event_type, o.action) for o in outcomes] == [
        (T.MULTIPLE_PERSONS_VISIBLE, A.FLAG_FOR_HUMAN_REVIEW)
    ]
    [row] = await rows(refs.session_id, T.MULTIPLE_PERSONS_VISIBLE)
    assert row.details["basis"] == "face_detector" and row.details["observation"]["faces"]["live_faces"] == 2
    assert row.evidence_media_id is not None


async def test_last_attempt_falls_back_to_the_face_detector_when_vision_is_down(
    service, clock, make_session, mock_ai
) -> None:
    refs = await make_session(consent=CONSENT)
    mock_ai.llm.set_default(ModelTask.FRAME_ANALYSIS, AIUnavailable("vision down", endpoint="vision"))
    ids = []
    for seconds_ago in (40, 20, 0):
        clock.at(seconds_ago)
        ids.append(
            await service.submit_frame(
                refs.sid,
                kind="camera",
                image=image("none_empty_room"),
                content_type="image/jpeg",
                captured_at=iso(seconds_ago),
            )
        )
    clock.at(0)
    with pytest.raises(AIUnavailable):
        await service.analyze_frame(refs.sid, ids[0])  # earlier attempts still retry
    outcomes = []
    for mid in ids:
        outcomes += await service.analyze_frame(refs.sid, mid, vision_optional=True)
    assert outcomes and {o.event_type for o in outcomes} == {T.NO_PERSON_VISIBLE}
    [row] = await rows(refs.session_id, T.NO_PERSON_VISIBLE)
    assert row.details["basis"] == "face_detector" and row.details["observation"]["vision"] == "unavailable"
