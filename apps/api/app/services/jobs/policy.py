"""Effective interview policy: organization defaults + job overrides.

``build_policy_snapshot(org_settings, job)`` produces the
:class:`~app.schemas.policies.PolicySnapshot` frozen into
``interviews.policy_snapshot`` at invite time, so a candidate is evaluated
under exactly the policy disclosed to them.

Merge rules
    * ``integrity``: job ``integrity_settings`` keys override the
      organization's ``IntegrityPolicy`` field by field; ``rules`` given by the
      job replace the organization's rules for the same ``event_type`` (other
      organization rules are kept).
    * ``recording``: job ``recording_settings`` keys override
      ``RecordingPolicy``; a nested ``retention`` mapping overrides
      ``RetentionPolicy`` field by field.
    * ``candidate_report_mode``: the job's value (jobs default to the
      organization's mode when created).
    * ``avatar``: organization settings.
    * Vision- and screen-derived events can never terminate an interview:
      such rules are downgraded to ``flag_for_human_review``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.core.errors import ValidationFailed
from app.domain.enums import CandidateReportMode, IntegrityAction, IntegrityEventType
from app.schemas.policies import (
    IntegrityPolicy,
    OrganizationSettings,
    PolicySnapshot,
    RecordingPolicy,
    RetentionPolicy,
)

# Events produced by the vision / screen analysis models (never objective enough to terminate).
MODEL_DERIVED_EVENTS: frozenset[IntegrityEventType] = frozenset(
    {
        IntegrityEventType.NO_PERSON_VISIBLE,
        IntegrityEventType.MULTIPLE_PERSONS_VISIBLE,
        IntegrityEventType.PERSON_LEFT_FRAME,
        IntegrityEventType.ANOTHER_PERSON_ENTERED,
        IntegrityEventType.POSSIBLE_PHONE_VISIBLE,
        IntegrityEventType.POSSIBLE_PHOTO_TAKING,
        IntegrityEventType.LOOKING_AWAY_WITH_OTHER_SIGNALS,
        IntegrityEventType.SCREEN_CONTEXT_CHANGE,
    }
)


def _get(job: Any, name: str) -> Any:
    if job is None:
        return None
    if isinstance(job, Mapping):
        return job.get(name)
    return getattr(job, name, None)


def merge_integrity(base: IntegrityPolicy, overrides: Mapping[str, Any] | None) -> IntegrityPolicy:
    data = base.model_dump(mode="json")
    overrides = dict(overrides or {})
    job_rules = overrides.pop("rules", None)
    data.update(overrides)
    if job_rules is not None:
        replaced = {str(rule.get("event_type")) for rule in job_rules if isinstance(rule, Mapping)}
        kept = [rule for rule in data.get("rules", []) if str(rule.get("event_type")) not in replaced]
        data["rules"] = [*kept, *job_rules]
    policy = IntegrityPolicy.model_validate(data)
    return enforce_integrity_guards(policy)


def enforce_integrity_guards(policy: IntegrityPolicy) -> IntegrityPolicy:
    rules = []
    changed = False
    for rule in policy.rules:
        if rule.event_type in MODEL_DERIVED_EVENTS and rule.action is IntegrityAction.TERMINATE_INTERVIEW:
            rules.append(rule.model_copy(update={"action": IntegrityAction.FLAG_FOR_HUMAN_REVIEW}))
            changed = True
        else:
            rules.append(rule)
    return policy.model_copy(update={"rules": rules}) if changed else policy


def merge_recording(
    base_recording: RecordingPolicy, base_retention: RetentionPolicy, overrides: Mapping[str, Any] | None
) -> tuple[RecordingPolicy, RetentionPolicy]:
    overrides = dict(overrides or {})
    retention_overrides = overrides.pop("retention", None) or {}
    recording = RecordingPolicy.model_validate({**base_recording.model_dump(mode="json"), **overrides})
    retention = RetentionPolicy.model_validate(
        {**base_retention.model_dump(mode="json"), **dict(retention_overrides)}
    )
    return recording, retention


def build_policy_snapshot(org_settings: OrganizationSettings, job: Any = None) -> PolicySnapshot:
    """Effective policy for an interview of ``job`` (ORM row, mapping or None)."""
    integrity = merge_integrity(org_settings.integrity, _get(job, "integrity_settings"))
    recording, retention = merge_recording(
        org_settings.recording, org_settings.retention, _get(job, "recording_settings")
    )
    mode = _get(job, "candidate_report_mode") or org_settings.candidate_report_mode
    return PolicySnapshot(
        integrity=integrity,
        recording=recording,
        retention=retention,
        avatar=org_settings.avatar,
        candidate_report_mode=CandidateReportMode(mode),
    )


def validate_job_policy_overrides(
    integrity_settings: Mapping[str, Any] | None, recording_settings: Mapping[str, Any] | None
) -> None:
    """Raise ``ValidationFailed`` (422) when job overrides are not valid partial
    policies (unknown keys, wrong types, out-of-range values)."""
    errors: list[dict[str, Any]] = []
    try:
        merge_integrity(IntegrityPolicy(), integrity_settings)
    except ValidationError as exc:
        errors.extend(_errors("integrity_settings", exc))
    try:
        merge_recording(RecordingPolicy(), RetentionPolicy(), recording_settings)
    except ValidationError as exc:
        errors.extend(_errors("recording_settings", exc))
    if errors:
        raise ValidationFailed("The job policy settings are invalid.", details={"errors": errors[:20]})


def _errors(prefix: str, exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "loc": [prefix, *[str(part) for part in err.get("loc", ())]],
            "msg": err.get("msg", "invalid"),
            "type": err.get("type"),
        }
        for err in exc.errors()
    ]
