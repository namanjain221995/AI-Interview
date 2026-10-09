"""Policy snapshot merging and round-config helpers."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailed
from app.domain.enums import CandidateReportMode, IntegrityAction, IntegrityEventType, RoundType
from app.schemas.policies import IntegrityRule, OrganizationSettings, PolicySnapshot
from app.services.jobs.catalog import deep_merge, overlay_columns, parse_round_config, round_type_label
from app.services.jobs.policy import build_policy_snapshot, validate_job_policy_overrides


def test_snapshot_defaults_without_job() -> None:
    settings = OrganizationSettings(candidate_report_mode=CandidateReportMode.IMMEDIATE)
    snapshot = build_policy_snapshot(settings, None)
    assert isinstance(snapshot, PolicySnapshot)
    assert snapshot.candidate_report_mode is CandidateReportMode.IMMEDIATE
    assert snapshot.integrity == settings.integrity
    assert snapshot.retention == settings.retention
    assert snapshot.avatar == settings.avatar


def test_job_overrides_win_field_by_field() -> None:
    settings = OrganizationSettings()
    settings.integrity.rules = [
        IntegrityRule(event_type=IntegrityEventType.TAB_HIDDEN, action=IntegrityAction.WARN_CANDIDATE),
        IntegrityRule(event_type=IntegrityEventType.COPY_EVENT, action=IntegrityAction.LOG_ONLY),
    ]
    job = {
        "integrity_settings": {
            "camera_required": False,
            "screen_share_required": True,
            "rules": [{"event_type": "tab_hidden", "action": "flag_for_human_review", "min_occurrences": 3}],
        },
        "recording_settings": {"record_video": True, "retention": {"video_days": 7}},
        "candidate_report_mode": "never",
    }
    snapshot = build_policy_snapshot(settings, job)
    assert snapshot.integrity.camera_required is False
    assert snapshot.integrity.screen_share_required is True
    assert snapshot.integrity.monitor_focus is True  # untouched org default
    rules = {r.event_type: r for r in snapshot.integrity.rules}
    assert rules[IntegrityEventType.TAB_HIDDEN].action is IntegrityAction.FLAG_FOR_HUMAN_REVIEW
    assert rules[IntegrityEventType.TAB_HIDDEN].min_occurrences == 3
    assert rules[IntegrityEventType.COPY_EVENT].action is IntegrityAction.LOG_ONLY  # org rule kept
    assert snapshot.recording.record_video is True and snapshot.recording.record_answer_audio is True
    assert (
        snapshot.retention.video_days == 7
        and snapshot.retention.resume_days == settings.retention.resume_days
    )
    assert snapshot.candidate_report_mode is CandidateReportMode.NEVER


def test_vision_events_can_never_terminate() -> None:
    job = {
        "integrity_settings": {
            "rules": [
                {"event_type": "possible_phone_visible", "action": "terminate_interview"},
                {"event_type": "screen_share_stopped", "action": "terminate_interview"},
            ]
        }
    }
    rules = {
        r.event_type: r.action for r in build_policy_snapshot(OrganizationSettings(), job).integrity.rules
    }
    assert rules[IntegrityEventType.POSSIBLE_PHONE_VISIBLE] is IntegrityAction.FLAG_FOR_HUMAN_REVIEW
    assert (
        rules[IntegrityEventType.SCREEN_SHARE_STOPPED] is IntegrityAction.TERMINATE_INTERVIEW
    )  # objective signal


@pytest.mark.parametrize(
    ("integrity", "recording"),
    [
        ({"camera_frame_interval_seconds": 1}, None),
        ({"unknown_flag": True}, None),
        (None, {"record_video": "sometimes"}),
        (None, {"retention": {"audio_days": 0}}),
        ({"rules": [{"event_type": "not_an_event"}]}, None),
    ],
)
def test_invalid_job_overrides(integrity, recording) -> None:
    with pytest.raises(ValidationFailed) as info:
        validate_job_policy_overrides(integrity, recording)
    assert info.value.details["errors"]


def test_valid_job_overrides() -> None:
    validate_job_policy_overrides(
        {"require_fullscreen": False}, {"show_live_subtitles": False, "retention": {"audio_days": 30}}
    )
    validate_job_policy_overrides(None, None)


def test_deep_merge_and_overlay() -> None:
    base = {
        "name": "Default",
        "coding": {"enabled": False, "languages": ["python", "go"]},
        "duration_minutes": 30,
        "max_duration_minutes": 40,
    }
    merged = deep_merge(base, {"coding": {"enabled": True, "languages": ["java"]}})
    assert merged["coding"] == {"enabled": True, "languages": ["java"]}  # nested dict merged, list replaced
    assert base["coding"]["enabled"] is False  # input untouched
    out = overlay_columns(merged, {"name": "Custom", "duration_minutes": 60, "round_type": RoundType.CODING})
    assert out["name"] == "Custom" and out["duration_minutes"] == 60
    assert out["max_duration_minutes"] == 60  # raised to fit the new duration
    assert out["round_type"] == "coding"


def test_parse_round_config_errors_are_422() -> None:
    config = parse_round_config({"round_type": "coding", "name": "Coding"})
    assert config.round_type is RoundType.CODING
    with pytest.raises(ValidationFailed) as info:
        parse_round_config(
            {"round_type": "coding", "name": "X", "rubric": [{"key": "a", "label": "A", "weight": 40}]}
        )
    assert any("rubric" in err["loc"] for err in info.value.details["errors"])
    assert round_type_label(RoundType.COMBINED) == "Combined AI round"
