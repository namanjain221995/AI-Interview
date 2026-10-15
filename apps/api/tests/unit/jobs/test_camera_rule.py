"""Platform rule (owner, 2026-10-12): every interview type requires the camera, analyses frames and records video,
whatever the organisation or job settings say."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.policies import IntegrityPolicy, OrganizationSettings, RecordingPolicy
from app.services.jobs import policy as policy_module

CAMERA_OFF_JOB = SimpleNamespace(
    integrity_settings={"camera_required": False, "camera_frame_analysis": False, "monitor_focus": False},
    recording_settings={"record_video": False},
    candidate_report_mode=None,
)


def _snapshot(monkeypatch: pytest.MonkeyPatch, *, rule: bool, org: OrganizationSettings | None = None):
    monkeypatch.setattr(
        policy_module, "get_settings", lambda: SimpleNamespace(require_camera_for_all_interviews=rule)
    )
    return policy_module.build_policy_snapshot(org or OrganizationSettings(), CAMERA_OFF_JOB)


def test_the_platform_rule_overrides_camera_optional_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = _snapshot(monkeypatch, rule=True)
    assert snap.integrity.enabled and snap.integrity.camera_required and snap.integrity.camera_frame_analysis
    assert snap.recording.record_video
    assert snap.integrity.monitor_focus is False  # only the camera rule is forced; other job choices stay


def test_the_rule_also_overrides_an_organisation_that_switched_monitoring_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = OrganizationSettings(
        integrity=IntegrityPolicy(enabled=False, camera_required=False),
        recording=RecordingPolicy(record_video=False),
    )
    snap = _snapshot(monkeypatch, rule=True, org=org)
    assert snap.integrity.enabled and snap.integrity.camera_required and snap.recording.record_video


def test_without_the_rule_job_settings_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = _snapshot(monkeypatch, rule=False)
    assert not snap.integrity.camera_required and not snap.recording.record_video


def test_the_rule_is_on_by_default() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["require_camera_for_all_interviews"].default is True
