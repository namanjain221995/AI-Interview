"""Candidate portal DTOs (``/api/v1/candidate-access/*`` and ``/api/v1/portal/*``).

Candidate-facing responses never include recruiter-only data: no scores,
recommendations, integrity details, claims-to-verify, or policy thresholds.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import IntegrityEventType, InterviewStatus, SessionEndReason, SessionStatus
from app.schemas.api.common import Email
from app.schemas.intake import PresenceMode
from app.schemas.realtime import ClientIntegrityEvent

# "details" and "resume-review" are intake-only journey steps (INTAKE_INTERVIEW.md 10.4).
CandidateStepKey = Literal[
    "welcome", "instructions", "consent", "resume", "details", "resume-review", "round", "system-check",
    "interview", "complete", "report",
]  # fmt: skip


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------- candidate access


class InviteInfo(_Out):
    organization_name: str
    job_title: str
    round_name: str | None = None
    candidate_first_name: str | None = None
    requires_access_code: bool
    expires_at: datetime
    expired: bool
    cancelled: bool
    completed: bool = False
    status: InterviewStatus
    interviewer_name: str | None = None
    # Planned length of the round ("About 30 minutes"); None when the candidate
    # still chooses between rounds of different lengths.
    duration_minutes: int | None = None
    # Additive (intake): the invited round's type, so /i/:token can show intake copy
    # before verification; None while the candidate still chooses a round.
    round_type: str | None = None


class CandidateVerifyRequest(_In):
    email: Email
    access_code: str | None = Field(None, max_length=64)


class CandidateVerifyResponse(_Out):
    interview_id: uuid.UUID
    next_step: CandidateStepKey
    csrf_token: str


# ------------------------------------------------------------------- portal


class PortalCandidate(_Out):
    id: uuid.UUID
    full_name: str
    first_name: str | None = None
    email: str


class PortalOrganization(_Out):
    name: str
    slug: str | None = None


class PortalMe(_Out):
    candidate: PortalCandidate
    interview_id: uuid.UUID
    organization: PortalOrganization


class PortalRound(_Out):
    id: uuid.UUID
    name: str
    round_type: str
    duration_minutes: int
    expected_difficulty: str
    instructions: str | None = None
    coding_enabled: bool = False
    candidate_selectable: bool = False


class PortalJob(_Out):
    id: uuid.UUID
    title: str
    department: str | None = None
    location: str | None = None
    description: str | None = None


class PortalSessionBrief(_Out):
    id: uuid.UUID
    status: SessionStatus


class PortalIntakeBrief(_Out):
    """``PortalInterview.intake``: present for intake interviews (switches the candidate journey)."""

    intake_id: uuid.UUID
    title: str
    estimated_minutes: int
    presence: PresenceMode


class PortalInterview(_Out):
    id: uuid.UUID
    status: InterviewStatus
    organization: PortalOrganization
    job: PortalJob
    rounds_offered: list[PortalRound]
    selected_round_id: uuid.UUID | None = None
    allow_round_selection: bool
    policy: dict[str, Any]
    steps_completed: list[CandidateStepKey]
    expires_at: datetime
    interviewer_name: str | None = None
    latest_session: PortalSessionBrief | None = None
    requires_resume: bool = True
    # Additive (intake): set for intake interviews only.
    intake: PortalIntakeBrief | None = None


class DisclosureItemOut(_Out):
    key: str
    title: str
    description: str
    required: bool
    enabled: bool


class DisclosureOut(_Out):
    """Contract ``Disclosure`` fields plus ``text_markdown`` (exactly the hashed
    text), ``consent_items`` (the map stored with the consent) and
    ``required_items`` (``app.services.integrity.disclosure_payload``)."""

    policy_version: str
    policy_hash: str
    title: str
    intro: str
    items: list[DisclosureItemOut]
    retention_summary: str
    purpose: str
    human_review_statement: str
    limitations_statement: str
    text_markdown: str
    consent_items: dict[str, Any] = Field(default_factory=dict)
    required_items: list[str] = Field(default_factory=list)
    consented: bool = False


class ConsentRequest(_In):
    policy_hash: str = Field(..., min_length=16, max_length=64)
    items: dict[str, bool | int] = Field(default_factory=dict)

    @field_validator("items")
    @classmethod
    def _bounded(cls, value: dict[str, bool | int]) -> dict[str, bool | int]:
        if len(value) > 40:
            raise ValueError("too many consent items")
        for key in value:
            if len(key) > 64:
                raise ValueError("consent item keys are at most 64 characters")
        return value


class ConsentResponse(_Out):
    consent_id: uuid.UUID
    policy_version: str
    policy_hash: str
    accepted_at: datetime
    items: dict[str, Any]


class PortalResumeInfo(_Out):
    id: uuid.UUID
    original_filename: str
    size_bytes: int
    status: str
    status_detail: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    profile_ready: bool = False
    created_at: datetime | None = None


class PortalResume(_Out):
    resume: PortalResumeInfo | None = None
    profile_summary: dict[str, Any] | None = None
    # Additive (resume speed): "quick" = the deterministic read shown right after
    # text extraction, "full" = the model profile, "none" = nothing to show yet.
    profile_stage: str = "none"
    # Per-section state of the profile ([{key, label, state}]; state quick |
    # active | done | fallback | pending) - never what the analysis found.
    sections: list[dict[str, Any]] = Field(default_factory=list)


class RoundSelectionRequest(_In):
    round_id: uuid.UUID


class SystemCheckNetwork(_In):
    rtt_ms: float | None = Field(None, ge=0, le=60_000)
    downlink_mbps: float | None = Field(None, ge=0, le=100_000)


class SystemCheckBrowser(_In):
    name: str | None = Field(None, max_length=80)
    version: str | None = Field(None, max_length=40)
    user_agent: str | None = Field(None, max_length=512)


class SystemCheckRequest(_In):
    camera: bool
    microphone: bool
    speakers: bool
    webgl: bool
    fullscreen: bool | None = None
    screen_share: bool | None = None
    network: SystemCheckNetwork | None = None
    browser: SystemCheckBrowser | None = None
    displays: int | None = Field(None, ge=0, le=16)
    microphone_level_dbfs: float | None = Field(None, ge=-160, le=10)
    # Interviewer view chosen by the client's rendering probe (3D avatar, 2D
    # fallback or audio-only) and the frame rate it measured.
    avatar_mode: Literal["3d", "2d", "none"] | None = None
    avatar_fps: float | None = Field(None, ge=0, le=1000)


class SystemCheckResponse(_Out):
    passed: bool
    issues: list[str]
    recorded_at: datetime


class SessionCreateRequest(_In):
    round_id: uuid.UUID | None = None


class PortalSessionCreateResponse(_Out):
    session_id: uuid.UUID
    status: SessionStatus


class PortalSession(_Out):
    id: uuid.UUID
    interview_id: uuid.UUID
    status: SessionStatus
    round_name: str | None = None
    round_type: str | None = None
    duration_minutes: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: SessionEndReason | str | None = None
    candidate_report_available: bool = False
    # Additive: the session's candidate feedback policy (immediate | after_review |
    # never); the portal hides every feedback element when it is "never".
    candidate_report_mode: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PortalSessionEndRequest(_In):
    # The candidate can only end their own interview as themselves; other reasons are the server's.
    reason: Literal["candidate_ended"] = "candidate_ended"


class PortalSessionEndResponse(_Out):
    status: Literal["ending", "already_ended"]


class WsTicketResponse(_Out):
    ticket: str
    ws_url: str
    ws_path: str
    expires_in: int


class FrameUploadAccepted(_Out):
    accepted: bool
    media_id: str | None = None


class IntegrityEventsRequest(_In):
    events: list[ClientIntegrityEvent] = Field(..., min_length=1, max_length=50)


class IntegrityWarningOut(_Out):
    event_type: IntegrityEventType
    message: str


class IntegrityEventsResponse(_Out):
    accepted: int
    warnings: list[IntegrityWarningOut] = Field(default_factory=list)


class RecordingChunkResponse(_Out):
    media_id: uuid.UUID
    segment: int
    kind: Literal["camera", "screen"]
    size_bytes: int
    duplicate: bool = False
    # The recording part the segment belongs to (None for uploads without one).
    part_id: uuid.UUID | None = None


class CandidateReportOut(_Out):
    session_id: uuid.UUID
    report_id: uuid.UUID
    version: int
    generated_at: datetime | None = None
    released_at: datetime | None = None
    content: dict[str, Any]
    artifact_url: str | None = None
    artifact_expires_at: datetime | None = None
