"""Integrity, recording, retention and organization-level settings.

Policies are configured per organization (defaults) and per job (overrides),
then frozen into ``interviews.policy_snapshot`` at invite time so a candidate
is always evaluated under exactly the policy that was disclosed to them."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import CandidateReportMode, IntegrityAction, IntegrityEventType


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntegrityRule(_Model):
    """How one event type is handled. Vision-derived events can never map to
    TERMINATE_INTERVIEW; the policy validator downgrades them to a flag."""

    event_type: IntegrityEventType
    enabled: bool = True
    min_confidence: float = Field(0.6, ge=0, le=1)
    min_duration_seconds: float = Field(0, ge=0, description="Signal must persist this long (sustained)")
    min_occurrences: int = Field(1, ge=1, description="Occurrences inside window_seconds before acting")
    window_seconds: float = Field(300, ge=1)
    action: IntegrityAction = IntegrityAction.FLAG_FOR_HUMAN_REVIEW
    candidate_message: str | None = None


class IntegrityPolicy(_Model):
    enabled: bool = True
    require_fullscreen: bool = True
    monitor_focus: bool = True
    monitor_clipboard: bool = True
    block_paste_in_coding: bool = False
    monitor_shortcuts: bool = True
    camera_required: bool = True
    camera_frame_analysis: bool = True
    # ~10 s of 8B-vision time per frame on this host: 15 s keeps one worker able to serve ~5 live sessions.
    camera_frame_interval_seconds: int = Field(15, ge=3, le=120)
    store_evidence_frames: bool = True
    # On-device face check (browser): one face visible, with a snapshot only when a check
    # needs human review. Applies only while the camera is used (camera_required or
    # camera_frame_analysis); disclosed as its own consent item.
    face_presence_check: bool = True
    screen_share_required: bool = False
    screen_share_analysis: bool = False
    screen_frame_interval_seconds: int = Field(20, ge=5, le=300)
    terminate_on_required_screen_share_stop: bool = False
    warn_candidate: bool = True
    allow_page_reload_resume: bool = True
    max_reconnects: int = Field(10, ge=0)
    rules: list[IntegrityRule] = Field(default_factory=list, description="Overrides of built-in default rules")


class RecordingPolicy(_Model):
    record_answer_audio: bool = True
    # The whole interview is recorded by default (camera video with the audio
    # of both voices; audio only without a camera), disclosed in the consent
    # step and only with the candidate's consent. Staff watch it on the
    # session's Recording tab; it is deleted after retention.video_days.
    record_video: bool = True
    record_screen: bool = False
    show_live_subtitles: bool = True
    allow_typed_answers: bool = True


class RetentionPolicy(_Model):
    resume_days: int = Field(365, ge=1)
    audio_days: int = Field(90, ge=1)
    video_days: int = Field(30, ge=1)
    frame_days: int = Field(30, ge=1)
    transcript_days: int = Field(365, ge=1)
    report_days: int = Field(730, ge=1)


class AvatarSettings(_Model):
    # Default interviewer presence: an animated, audio-reactive orb
    # ("orb:<palette>", no human likeness). 3D avatars stay selectable.
    model_url: str = "orb:aurora"
    model_format: str = Field("orb", pattern="^(orb|cartoon|glb|vrm|procedural)$")
    interviewer_name: str = "Maya"
    voice: str = "af_heart"
    background: str = "studio"


class OrganizationSettings(_Model):
    integrity: IntegrityPolicy = Field(default_factory=IntegrityPolicy)
    recording: RecordingPolicy = Field(default_factory=RecordingPolicy)
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    avatar: AvatarSettings = Field(default_factory=AvatarSettings)
    candidate_report_mode: CandidateReportMode = CandidateReportMode.NEVER
    require_access_code: bool = False
    data_processing_notice: str = (
        "Your interview is conducted by an AI interviewer. Audio is transcribed to evaluate your answers. "
        "A human reviewer at the hiring organization makes every hiring decision."
    )


class PolicySnapshot(_Model):
    """Frozen copy stored in interviews.policy_snapshot."""

    policy_version: str = "2026-10-01"
    integrity: IntegrityPolicy = Field(default_factory=IntegrityPolicy)
    recording: RecordingPolicy = Field(default_factory=RecordingPolicy)
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    avatar: AvatarSettings = Field(default_factory=AvatarSettings)
    candidate_report_mode: CandidateReportMode = CandidateReportMode.NEVER
