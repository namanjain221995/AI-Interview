"""Recording is on by default and the consent text says exactly what is recorded,
who can watch it and for how long."""

from __future__ import annotations

from typing import Any

from app.realtime.policy import session_policy
from app.schemas.policies import OrganizationSettings, PolicySnapshot, RecordingPolicy
from app.services.integrity.disclosure import build_disclosure, consent_items, missing_required_items


def _snapshot(**recording: Any) -> dict[str, Any]:
    snap = PolicySnapshot().model_dump(mode="json")
    snap["recording"].update(recording)
    return snap


def _items(snap: dict[str, Any]) -> dict[str, Any]:
    disclosure = build_disclosure(snap, organization_name="Acme", job_title="Platform Engineer")
    return {item.key: item for item in disclosure.items}


def test_video_recording_is_the_default() -> None:
    assert RecordingPolicy().record_video is True
    assert OrganizationSettings().recording.record_video is True
    assert PolicySnapshot().recording.record_video is True


def test_consent_text_names_both_voices_viewers_and_retention() -> None:
    snap = _snapshot()
    snap["retention"]["video_days"] = 21
    video = _items(snap)["video_recording"]
    assert video.enabled and video.required
    text = video.description
    for phrase in (
        "recorded on video",
        "both sides of the conversation",
        "the AI interviewer's voice",
        "If your camera cannot be recorded, the sound is still recorded",
        "People at Acme who review this interview (interviewers, recruiters and administrators)",
        "only administrators can download it",
        "every viewing and download is logged",
        "kept for 21 days and then deleted automatically",
    ):
        assert phrase in text, phrase


def test_recording_needs_its_own_consent() -> None:
    snap = _snapshot()
    disclosure = build_disclosure(snap, organization_name="Acme", job_title="Platform Engineer")
    accepted = {key: True for key in consent_items(disclosure, snap)}
    assert missing_required_items(disclosure, accepted) == []
    accepted["video_recording"] = False
    assert missing_required_items(disclosure, accepted) == ["video_recording"]
    # Policy on but no consent item: nothing may be recorded.
    assert not session_policy(snap, None).video_recording_allowed


def test_recording_off_says_so() -> None:
    video = _items(_snapshot(record_video=False))["video_recording"]
    assert not video.enabled and not video.required and video.description == "No video is recorded."


def test_recording_alone_never_makes_the_camera_mandatory() -> None:
    snap = _snapshot()
    snap["integrity"]["camera_required"] = False
    camera = _items(snap)["camera"]
    assert camera.enabled and not camera.required
    assert "only the sound of the interview is recorded" in camera.description
    snap["integrity"]["camera_required"] = True
    required = _items(snap)["camera"]
    assert required.required and "only the sound" not in required.description
