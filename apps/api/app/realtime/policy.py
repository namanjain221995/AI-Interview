"""Effective candidate-facing policy = frozen policy snapshot + recorded consent.

Used by the realtime runtime (subtitles, typed answers, answer-audio
recording, integrity flags) and by the portal API (frame upload, recording
upload, disclosure). Anything that records or analyses the candidate requires
BOTH the recruiter policy and the candidate's consent item.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Consent
from app.schemas.policies import (
    AvatarSettings,
    IntegrityPolicy,
    PolicySnapshot,
    RecordingPolicy,
    RetentionPolicy,
)

log = get_logger(__name__)

_SECTIONS: dict[str, type[BaseModel]] = {
    "integrity": IntegrityPolicy,
    "recording": RecordingPolicy,
    "retention": RetentionPolicy,
    "avatar": AvatarSettings,
}


def _lenient(model: type[BaseModel], raw: Any) -> BaseModel:
    if not isinstance(raw, dict):
        return model()
    try:
        return model.model_validate(raw)
    except ValidationError:
        known = {k: v for k, v in raw.items() if k in model.model_fields}
        try:
            return model.model_validate(known)
        except ValidationError:
            log.warning("realtime.policy.invalid_section", section=model.__name__)
            return model()


def parse_policy_snapshot(raw: dict[str, Any] | None) -> PolicySnapshot:
    """Parse ``interviews.policy_snapshot`` tolerantly (unknown keys ignored,
    invalid sections fall back to safe defaults)."""
    raw = raw or {}
    data: dict[str, Any] = {name: _lenient(model, raw.get(name)) for name, model in _SECTIONS.items()}
    if "policy_version" in raw and isinstance(raw["policy_version"], str):
        data["policy_version"] = raw["policy_version"][:32]
    if "candidate_report_mode" in raw:
        data["candidate_report_mode"] = raw["candidate_report_mode"]
    try:
        return PolicySnapshot.model_validate(data)
    except ValidationError:
        data.pop("candidate_report_mode", None)
        return PolicySnapshot.model_validate(data)


async def latest_consent(db: AsyncSession, interview_id: uuid.UUID) -> Consent | None:
    return await db.scalar(
        select(Consent)
        .where(Consent.interview_id == interview_id, Consent.withdrawn_at.is_(None))
        .order_by(Consent.accepted_at.desc(), Consent.created_at.desc())
        .limit(1)
    )


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    snapshot: PolicySnapshot
    consent: dict[str, Any] = field(default_factory=dict)
    consented: bool = False

    def _consent(self, key: str) -> bool:
        return bool(self.consent.get(key, False))

    # ------------------------------------------------------------ recording
    @property
    def show_subtitles(self) -> bool:
        return self.snapshot.recording.show_live_subtitles

    @property
    def allow_typed_answers(self) -> bool:
        return self.snapshot.recording.allow_typed_answers

    @property
    def record_answer_audio(self) -> bool:
        return self.snapshot.recording.record_answer_audio and self._consent("audio_recording")

    @property
    def video_recording_allowed(self) -> bool:
        return self.snapshot.recording.record_video and self._consent("video_recording")

    @property
    def screen_recording_allowed(self) -> bool:
        return self.snapshot.recording.record_screen and (
            self._consent("screen_share") or self._consent("video_recording")
        )

    # ------------------------------------------------------------ integrity
    def _declined(self, *keys: str) -> bool:
        return any(self.consent.get(key) is False for key in keys)

    @property
    def camera_frames_allowed(self) -> bool:
        """Mirrors ``IntegrityService.submit_frame``: policy enables camera frame
        analysis, a consent exists, and neither ``camera`` nor ``camera_frames``
        was explicitly declined."""
        integrity = self.snapshot.integrity
        if not integrity.enabled or not integrity.camera_frame_analysis or not self.consented:
            return False
        return not self._declined("camera", "camera_frames")

    @property
    def face_check_allowed(self) -> bool:
        """On-device face check: policy on while the camera is used, a consent
        that described it (item ``face_check``) and the camera not declined.
        Mirrors ``IntegrityService`` (face events and face-check snapshots)."""
        integrity = self.snapshot.integrity
        if not integrity.enabled or not integrity.face_presence_check or not self.consented:
            return False
        if not (integrity.camera_required or integrity.camera_frame_analysis):
            return False
        return self.consent.get("face_check") is True and not self._declined("camera")

    @property
    def screen_frames_allowed(self) -> bool:
        integrity = self.snapshot.integrity
        if not integrity.enabled or not integrity.screen_share_analysis or not self.consented:
            return False
        return not self._declined("screen_share")

    def integrity_flags(self) -> dict[str, Any]:
        """Active monitoring flags for the UI (``session.state.integrity``)."""
        p = self.snapshot.integrity
        enabled = p.enabled
        return {
            "enabled": enabled,
            "require_fullscreen": enabled and p.require_fullscreen,
            "monitor_focus": enabled and p.monitor_focus,
            "monitor_clipboard": enabled and p.monitor_clipboard,
            "block_paste_in_coding": enabled and p.block_paste_in_coding,
            "monitor_shortcuts": enabled and p.monitor_shortcuts,
            "camera_required": enabled and p.camera_required,
            "camera_frames": self.camera_frames_allowed,
            "face_presence_check": self.face_check_allowed,
            "camera_frame_interval_seconds": p.camera_frame_interval_seconds,
            "screen_share_required": enabled and p.screen_share_required,
            "screen_frames": self.screen_frames_allowed,
            "screen_frame_interval_seconds": p.screen_frame_interval_seconds,
            "warn_candidate": enabled and p.warn_candidate,
            "record_video": self.video_recording_allowed,
            "record_screen": self.screen_recording_allowed,
            "record_answer_audio": self.record_answer_audio,
        }

    # --------------------------------------------------------------- avatar
    @property
    def voice(self) -> str | None:
        return self.snapshot.avatar.voice or None

    @property
    def interviewer_name(self) -> str:
        return self.snapshot.avatar.interviewer_name

    @property
    def max_reconnects(self) -> int:
        return self.snapshot.integrity.max_reconnects

    @property
    def allow_page_reload_resume(self) -> bool:
        return self.snapshot.integrity.allow_page_reload_resume

    def candidate_safe(self) -> dict[str, Any]:
        """Policy subset shown to the candidate in the portal (no thresholds or rule internals)."""
        s = self.snapshot
        return {
            "policy_version": s.policy_version,
            "recording": s.recording.model_dump(mode="json"),
            "integrity": {
                "enabled": s.integrity.enabled,
                "require_fullscreen": s.integrity.require_fullscreen,
                "monitor_focus": s.integrity.monitor_focus,
                "monitor_clipboard": s.integrity.monitor_clipboard,
                "block_paste_in_coding": s.integrity.block_paste_in_coding,
                "camera_required": s.integrity.camera_required,
                "camera_frame_analysis": s.integrity.camera_frame_analysis,
                "face_presence_check": s.integrity.face_presence_check,
                "camera_frame_interval_seconds": s.integrity.camera_frame_interval_seconds,
                "screen_share_required": s.integrity.screen_share_required,
                "screen_share_analysis": s.integrity.screen_share_analysis,
                "screen_frame_interval_seconds": s.integrity.screen_frame_interval_seconds,
                "allow_page_reload_resume": s.integrity.allow_page_reload_resume,
                "max_reconnects": s.integrity.max_reconnects,
            },
            "retention": s.retention.model_dump(mode="json"),
            "avatar": {
                "model_url": s.avatar.model_url,
                "model_format": s.avatar.model_format,
                "interviewer_name": s.avatar.interviewer_name,
                "voice": s.avatar.voice,
                "background": s.avatar.background,
            },
            "candidate_report_mode": s.candidate_report_mode.value,
        }


def session_policy(snapshot_raw: dict[str, Any] | None, consent: Consent | None) -> SessionPolicy:
    return SessionPolicy(
        snapshot=parse_policy_snapshot(snapshot_raw),
        consent=dict(consent.items) if consent is not None else {},
        consented=consent is not None,
    )
