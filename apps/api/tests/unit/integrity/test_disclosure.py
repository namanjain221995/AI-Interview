"""Consent disclosure: determinism, hash sensitivity, item content per policy."""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

from app.domain.enums import IntegrityAction, IntegrityEventType
from app.schemas.policies import PolicySnapshot
from app.services.integrity.contracts import DisclosureBuilder
from app.services.integrity.disclosure import (
    ITEM_KEYS,
    LIMITATIONS_CORE,
    DefaultDisclosureBuilder,
    build_disclosure,
    consent_items,
    disclosure_payload,
    missing_required_items,
    render_disclosure_markdown,
)

ORG, JOB = "Demo Corp", "Senior DevOps Engineer"


def snapshot(**sections: dict[str, Any]) -> dict[str, Any]:
    data = PolicySnapshot().model_dump(mode="json")
    for name, values in sections.items():
        data[name].update(values)
    return data


def build(snap: dict[str, Any] | None = None, org: str = ORG, job: str = JOB):
    return build_disclosure(snap if snap is not None else snapshot(), organization_name=org, job_title=job)


def items(disclosure) -> dict[str, Any]:
    return {item.key: item for item in disclosure.items}


def test_deterministic_text_and_hash() -> None:
    first, second = build(), build(copy.deepcopy(snapshot()))
    assert first == second
    assert render_disclosure_markdown(first) == render_disclosure_markdown(second)
    assert re.fullmatch(r"[0-9a-f]{64}", first.policy_hash)
    assert [item.key for item in first.items] == list(ITEM_KEYS)


def test_partial_and_full_snapshot_hash_identically() -> None:
    assert build({}).policy_hash == build(snapshot()).policy_hash  # normalised snapshot


def test_unknown_snapshot_keys_are_ignored() -> None:
    snap = snapshot()
    snap["future_section"] = {"x": 1}
    snap["integrity"]["future_flag"] = True
    assert build(snap).policy_hash == build().policy_hash


def test_invalid_snapshot_raises() -> None:
    with pytest.raises(ValueError):
        build({"integrity": {"camera_frame_interval_seconds": 1}})


@pytest.mark.parametrize(
    "change",
    [
        {"integrity": {"monitor_clipboard": False}},
        {"integrity": {"camera_frame_analysis": False}},
        {"integrity": {"camera_frame_interval_seconds": 25}},
        {"integrity": {"store_evidence_frames": False}},
        {"integrity": {"screen_share_required": True}},
        {"integrity": {"terminate_on_required_screen_share_stop": True}},
        {"integrity": {"warn_candidate": False}},
        {"integrity": {"max_reconnects": 3}},
        {"recording": {"record_video": False}},  # on by default
        {"recording": {"record_answer_audio": False}},
        {"retention": {"frame_days": 7}},
        {"retention": {"transcript_days": 90}},
        {"policy_version": "2027-01-01"},
    ],
)
def test_hash_changes_when_policy_changes(change: dict[str, Any]) -> None:
    base = snapshot()
    changed = copy.deepcopy(base)
    for key, value in change.items():
        if isinstance(value, dict):
            changed[key].update(value)
        else:
            changed[key] = value
    assert build(changed).policy_hash != build(base).policy_hash


def test_hash_changes_with_org_and_job() -> None:
    base = build().policy_hash
    assert build(org="Other Corp").policy_hash != base
    assert build(job="Backend Engineer").policy_hash != base


def test_hash_changes_with_rule_override_even_when_text_is_identical() -> None:
    snap = snapshot()
    snap["integrity"]["rules"] = [{"event_type": "copy_event", "enabled": False}]
    assert build(snap).policy_hash != build().policy_hash


def test_default_policy_items() -> None:
    d = build()
    by_key = items(d)
    assert by_key["microphone"].required and by_key["microphone"].enabled
    assert by_key["camera"].required and "must stay on" in by_key["camera"].description
    transcript = by_key["transcript"].description
    assert by_key["transcript"].required and "until they delete it" in transcript and "days" not in transcript
    integrity = by_key["integrity_monitoring"]
    assert integrity.enabled and integrity.required
    for phrase in (
        "leaving fullscreen",
        "switching to another tab",
        "never what was copied",
        "keyboard shortcuts",
    ):
        assert phrase in integrity.description
    assert "never pause or end the interview" in integrity.description
    frames = by_key["camera_frames"]
    assert frames.enabled and "About every 15 seconds" in frames.description
    assert "never identifies you" in frames.description
    assert "never treated as a concern on its own" in frames.description
    assert "kept for 30 days" in frames.description
    assert not by_key["screen_share"].enabled
    assert by_key["audio_recording"].enabled and "90 days" in by_key["audio_recording"].description
    # The whole interview is recorded by default (both voices), with consent.
    video = by_key["video_recording"]
    assert video.enabled and video.required
    assert "both sides of the conversation" in video.description and "30 days" in video.description
    assert ORG in by_key["ai_assessment"].description and JOB in by_key["ai_assessment"].description
    assert "uploaded resume file: 365 days" in d.retention_summary and "730 days" not in d.retention_summary
    assert (
        "Your transcript, the written assessment and the details read from your resume stay"
        in d.retention_summary
    )


def test_limitations_statement_is_honest() -> None:
    d = build()
    assert d.limitations_statement.startswith(LIMITATIONS_CORE)
    assert (
        "A web browser cannot lock your computer; fullscreen and focus are monitored, not enforced."
        in d.limitations_statement
    )
    assert "can be wrong" in d.limitations_statement
    assert "makes every hiring decision" in d.human_review_statement
    assert "never conclusions" in d.human_review_statement
    assert JOB in d.purpose and ORG in d.purpose


@pytest.mark.parametrize(
    ("integrity", "expected"),
    [
        (
            {"screen_share_required": True, "terminate_on_required_screen_share_stop": True},
            "the interview ends",
        ),
        (
            {"screen_share_required": True, "terminate_on_required_screen_share_stop": False},
            "the interview pauses until you share",
        ),
    ],
)
def test_screen_share_consequences_follow_policy(integrity, expected) -> None:
    share = items(build(snapshot(integrity=integrity)))["screen_share"]
    assert share.enabled and share.required
    assert expected in share.description
    assert "Stop sharing" in share.description
    assert "What is captured" in share.description


def test_screen_share_overridden_action_is_disclosed() -> None:
    snap = snapshot(integrity={"screen_share_required": True})
    snap["integrity"]["rules"] = [{"event_type": "screen_share_stopped", "action": "flag_for_human_review"}]
    share = items(build(snap))["screen_share"]
    assert "asked to share again" in share.description


def test_screen_analysis_disclosed_with_interval() -> None:
    share = items(
        build(snapshot(integrity={"screen_share_analysis": True, "screen_frame_interval_seconds": 30}))
    )["screen_share"]
    assert "about every 30 seconds a still image of your screen" in share.description
    assert "never pause or end the interview" in share.description


def test_monitoring_disabled_is_stated_plainly() -> None:
    d = build(snapshot(integrity={"enabled": False}, recording={"record_video": False}))
    by_key = items(d)
    assert not by_key["integrity_monitoring"].enabled
    assert "turned off" in by_key["integrity_monitoring"].description
    assert not by_key["camera_frames"].enabled and not by_key["camera"].enabled
    assert LIMITATIONS_CORE in d.limitations_statement
    # A recorded interview still uses (and discloses) the camera without monitoring.
    recorded = items(build(snapshot(integrity={"enabled": False})))
    assert recorded["camera"].enabled and "recorded" in recorded["camera"].description


def test_recruiter_policy_action_rules_are_listed() -> None:
    snap = snapshot()
    snap["integrity"]["rules"] = [{"event_type": "fullscreen_exited", "action": "terminate_interview"}]
    integrity = items(build(snap))["integrity_monitoring"]
    assert "left fullscreen: the interview ends" in integrity.description


def test_vision_terminate_override_is_not_disclosed_as_ending() -> None:
    snap = snapshot()
    snap["integrity"]["rules"] = [{"event_type": "multiple_persons_visible", "action": "terminate_interview"}]
    d = build(snap)
    assert "the interview ends" not in items(d)["integrity_monitoring"].description
    assert "never pause or end the interview" in items(d)["camera_frames"].description


def test_no_frame_storage_disclosed() -> None:
    frames = items(build(snapshot(integrity={"store_evidence_frames": False})))["camera_frames"]
    assert "none are kept" in frames.description


def test_video_recording_disclosed() -> None:
    d = build(snapshot(recording={"record_video": True}, retention={"video_days": 14}))
    video = items(d)["video_recording"]
    assert video.enabled and video.required and "14 days" in video.description
    assert "recorded" in items(d)["camera"].description


def test_consent_items_and_required_checks() -> None:
    d = build(snapshot(recording={"record_video": True}))
    values = consent_items(d, snapshot(recording={"record_video": True}))
    assert (
        values["microphone"] is True and values["video_recording"] is True and values["screen_share"] is False
    )
    assert values["retention_days"] == 730
    accepted = {key: True for key in values}
    assert missing_required_items(d, accepted) == []
    accepted["camera"] = False
    assert missing_required_items(d, accepted) == ["camera"]


def test_payload_and_markdown() -> None:
    d = build(org="ACME *Labs* <script>", job="Eng_ineer [x]")
    payload = disclosure_payload(d, snapshot())
    assert payload["policy_hash"] == d.policy_hash
    assert payload["text_markdown"].startswith("# Before you start")
    assert "<script>" not in payload["text_markdown"] and "\\<script\\>" in payload["text_markdown"]
    assert set(payload["consent_items"]) >= {
        "microphone",
        "camera",
        "transcript",
        "integrity_monitoring",
        "retention_days",
    }
    assert payload["required_items"]
    assert LIMITATIONS_CORE.replace(";", ";") in payload["text_markdown"]


def test_names_are_normalised() -> None:
    d = build(org="  Demo\n\tCorp  ", job="")
    assert d.intro.startswith("Demo Corp uses an AI interviewer for this role.")
    assert "the  role" not in d.purpose and "for this role at Demo Corp" in d.purpose
    assert "required for this role." in items(d)["ai_assessment"].description


def test_builder_class_satisfies_protocol() -> None:
    builder: DisclosureBuilder = DefaultDisclosureBuilder()
    assert builder.build(snapshot(), organization_name=ORG, job_title=JOB) == build()


def test_disclosure_mentions_no_monitoring_that_is_off() -> None:
    d = build(snapshot(integrity={"monitor_clipboard": False, "monitor_shortcuts": False}))
    text = items(d)["integrity_monitoring"].description
    assert "copy and paste" not in text and "keyboard shortcuts" not in text
    assert "leaving fullscreen" in text


def test_action_rank_consistency() -> None:
    assert IntegrityAction.TERMINATE_INTERVIEW.value == "terminate_interview"
    assert IntegrityEventType.SCREEN_SHARE_STOPPED.value == "screen_share_stopped"


def test_reload_and_reconnect_policy_is_disclosed() -> None:
    default = items(build())["integrity_monitoring"].description
    assert "more than 10 times, the interview may not be resumable" in default
    assert "cannot be resumed" not in default
    strict = items(build(snapshot(integrity={"allow_page_reload_resume": False, "max_reconnects": 1})))
    text = strict["integrity_monitoring"].description
    assert "If you reload or close the page, the interview cannot be resumed." in text
    assert "more than 1 time, the interview may not be resumable" in text
    off = items(build(snapshot(integrity={"enabled": False, "allow_page_reload_resume": False})))
    assert "cannot be resumed" in off["integrity_monitoring"].description


# ------------------------------------------------------------- on-device face check


def test_face_check_item_says_plainly_what_runs_on_the_device_and_what_is_sent() -> None:
    disclosure = build()
    item = items(disclosure)["face_check"]
    assert item.enabled and item.required and item.title == "On-device face check"
    text = item.description
    assert "analysed only on your device" in text
    assert "check that one face is visible" in text
    assert "about 5 seconds" in text and "about 3 seconds" in text  # the effective thresholds
    assert "one snapshot from your camera is sent" in text
    assert "photo, a poster or a face on a screen" in text
    assert "never pause or end the interview" in text and ORG in text
    assert "never identifies you" in text and "where you are looking is never treated as a concern" in text
    assert "kept for 30 days" in text
    assert "On-device face check" in items(disclosure)["camera"].description
    assert "face detector also counts the faces" in items(disclosure)["camera_frames"].description
    assert consent_items(disclosure, snapshot())["face_check"] is True
    assert "face_check" in disclosure_payload(disclosure, snapshot())["required_items"]
    assert missing_required_items(disclosure, {key: True for key in ITEM_KEYS if key != "face_check"}) == [
        "face_check"
    ]


@pytest.mark.parametrize(
    "integrity",
    [
        {"face_presence_check": False},
        {"camera_required": False, "camera_frame_analysis": False},
        {"enabled": False},
    ],
)
def test_face_check_item_is_not_used_when_the_policy_or_camera_turns_it_off(integrity) -> None:
    disclosure = build(snapshot(integrity=integrity))
    item = items(disclosure)["face_check"]
    assert not item.enabled and not item.required
    assert item.description == "Your camera picture is not checked for faces for this interview."
    assert "On-device face check" not in items(disclosure)["camera"].description


def test_face_check_changes_the_policy_hash_and_follows_thresholds_and_storage() -> None:
    assert build(snapshot(integrity={"face_presence_check": False})).policy_hash != build().policy_hash
    rules = [{"event_type": "face_not_visible", "min_duration_seconds": 8}]
    assert "about 8 seconds" in items(build(snapshot(integrity={"rules": rules})))["face_check"].description
    transient = items(build(snapshot(integrity={"store_evidence_frames": False})))["face_check"]
    assert "Snapshots are deleted right after the check; none are kept." in transient.description
    face_only = build(snapshot(integrity={"camera_frame_analysis": False}))
    assert items(face_only)["face_check"].enabled
    assert "Camera snapshot analysis" not in items(face_only)["face_check"].description
    assert "images linked to an integrity observation: 30 days" in face_only.retention_summary


# ------------------------------------------------------------------ G3: promise only what is enforced


def _everything_on() -> dict[str, Any]:
    return snapshot(
        recording={"record_video": True, "record_screen": True, "record_answer_audio": True},
        integrity={
            "camera_frame_analysis": True,
            "screen_share_analysis": True,
            "store_evidence_frames": True,
        },
    )


def test_every_automatic_deletion_promise_is_a_stored_file_the_sweep_deletes() -> None:
    """Fails when the consent text promises a deletion nothing enforces (audit E1)."""
    from app.domain.enums import MediaKind
    from app.services.integrity.disclosure import auto_deleted_items
    from app.services.integrity.retention_classes import AUTO_DELETED, KEPT_UNTIL_DELETED
    from app.services.storage.media import retention_deadline

    snap = PolicySnapshot.model_validate(_everything_on())
    promised = auto_deleted_items(snap)
    assert {key for key, _label, _days in promised} == {"resume_file", "answer_audio", "video", "frames"}
    for key, _label, days in promised:
        assert key in AUTO_DELETED and key not in KEPT_UNTIL_DELETED, key
        for kind in AUTO_DELETED[key]:
            assert isinstance(kind, MediaKind)
            assert retention_deadline(kind, days) is not None  # every stored file gets a sweep deadline


def test_rows_without_a_sweep_are_never_given_a_deletion_deadline() -> None:
    d = build(_everything_on())
    lowered = d.retention_summary.lower()
    # The list after "deleted automatically after these periods:" up to the end of that sentence.
    deadline_list = lowered.split("deleted automatically after these periods:", 1)[1].split(". ", 1)[0]
    for phrase in ("transcript", "assessment", "report", "details read"):
        assert phrase not in deadline_list, phrase
    assert "your transcript, the written assessment and the details read from your resume stay" in lowered
    for item in d.items:
        if item.key in ("transcript", "ai_assessment"):
            assert not re.search(r"kept for \d+ days?", item.description), item.key
