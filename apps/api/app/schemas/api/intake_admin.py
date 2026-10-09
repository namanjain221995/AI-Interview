"""Intake Interview DTOs: staff admin (``/api/v1/intake``) and candidate portal (``/api/v1/portal/intake``).

Contract: ``docs/design/INTAKE_INTERVIEW.md`` sections 10.1, 10.4, 15.12 and 15.14. Shared document shapes
(settings, onboarding, résumé review) come from :mod:`app.schemas.intake`. Decimals serialise as strings.
Invite links and access codes appear exactly once (create / send / batch / regenerate-link responses).
Candidate-facing models never include scores, bands, reviewer data or claims-to-verify.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.api.common import Email
from app.schemas.intake import (
    APPROVED_INFO_MAX_ITEMS,
    INTAKE_MAX_MINUTES,
    INTAKE_MIN_MINUTES,
    ApprovedInfoItem,
    DecimalStr,
    DecisionBandValue,
    DecisionOutcome,
    EvaluationStatus,
    EvidenceSufficiency,
    IntakeBucket,
    IntakeInviteSettingsIn,
    IntakeOnboarding,
    IntakeOrgDefaults,
    IntakeStatus,
    PresenceMode,
    ResumeReviewIn,
    ResumeReviewOut,
    TargetRole,
    WorkAuthorizationPolicy,
)

BATCH_MAX_ROWS = 200


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRef(_Out):
    id: uuid.UUID
    full_name: str


# ================================================================================ GET /intake/config


class MinutesRange(_Out):
    min: int
    max: int


class IntakeKitDuration(_Out):
    min: int
    max: int
    target: int


class IntakeKitStageOut(_Out):
    key: str
    number: int
    title: str  # kit (staff) title
    candidate_title: str
    minutes: MinutesRange


class IntakeKitPassageOut(_Out):
    key: str
    title: str
    level: str
    drills: list[str]


class IntakeKitCardOut(_Out):
    number: int
    title: str
    type: str
    type_label: str


class IntakeKitInfo(_Out):
    version: str
    sha256: str
    title: str
    duration: IntakeKitDuration
    stages: list[IntakeKitStageOut]
    passages: list[IntakeKitPassageOut]  # index only: never the passage text
    topic_cards: list[IntakeKitCardOut]


class IntakeJobOption(_Out):
    job_id: uuid.UUID
    title: str
    status: str
    round_id: uuid.UUID
    round_name: str


class IntakeConfigOut(_Out):
    enabled: bool = True
    kit: IntakeKitInfo
    defaults: IntakeOrgDefaults
    work_authorization_default: WorkAuthorizationPolicy
    jobs: list[IntakeJobOption]


# ========================================================================= /intake/settings


class IntakeOrgSettingsIn(_In):
    program_name: str = Field(..., min_length=1, max_length=200)
    work_authorization: WorkAuthorizationPolicy = Field(default_factory=WorkAuthorizationPolicy)
    approved_info: list[ApprovedInfoItem] = Field(default_factory=list, max_length=APPROVED_INFO_MAX_ITEMS)
    defaults: IntakeOrgDefaults = Field(default_factory=IntakeOrgDefaults)


class IntakeOrgSettingsOut(_Out):
    program_name: str
    work_authorization: WorkAuthorizationPolicy
    approved_info: list[ApprovedInfoItem]
    defaults: IntakeOrgDefaults
    updated_at: datetime | None = None
    updated_by: UserRef | None = None


# ========================================================================== summary / list


class IntakeCandidateRef(_Out):
    id: uuid.UUID
    full_name: str
    email: str


class IntakeJobRef(_Out):
    id: uuid.UUID
    title: str


class IntakeRoundRef(_Out):
    id: uuid.UUID
    name: str


class IntakeInterviewRef(_Out):
    id: uuid.UUID
    status: str
    invite_expires_at: datetime
    scheduled_for: datetime | None = None
    verified_at: datetime | None = None


class IntakeSessionRef(_Out):
    id: uuid.UUID
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_seconds: float = 0.0


class IntakeEvaluationRef(_Out):
    id: uuid.UUID
    status: EvaluationStatus
    weighted_total: DecimalStr | None = None
    band: DecisionBandValue | None = None
    evidence_sufficiency: EvidenceSufficiency | None = None


class IntakeDecisionRef(_Out):
    outcome: DecisionOutcome
    created_at: datetime
    reviewer: UserRef


class IntakeSummaryOut(_Out):
    id: uuid.UUID
    status: IntakeStatus
    bucket: IntakeBucket  # single badge (precedence in INTAKE_INTERVIEW.md 10.3)
    title: str
    batch_id: uuid.UUID | None = None
    candidate: IntakeCandidateRef
    job: IntakeJobRef
    round: IntakeRoundRef | None = None
    target_role: TargetRole | None = None
    interview: IntakeInterviewRef | None = None
    session: IntakeSessionRef | None = None
    onboarding_submitted: bool = False
    resume_confirmed: bool = False
    evaluation: IntakeEvaluationRef | None = None
    decision: IntakeDecisionRef | None = None
    assigned_reviewer: UserRef | None = None
    has_training_plan: bool = False
    review_items_open: int = 0
    integrity_flags: int = 0
    created_at: datetime
    updated_at: datetime


class IntakeStatsBuckets(_Out):
    drafts: int = 0
    active: int = 0
    scheduled: int = 0
    in_progress: int = 0
    completed: int = 0
    awaiting_review: int = 0
    accepted: int = 0
    borderline: int = 0
    rejected: int = 0
    training: int = 0
    expired: int = 0
    cancelled: int = 0


class IntakeStatsTotals(_Out):
    invitations: int = 0
    started: int = 0
    completed: int = 0
    incomplete: int = 0
    awaiting_review: int = 0
    decided: int = 0
    average_weighted_total: DecimalStr | None = None
    average_duration_s: float | None = None


class IntakeStatsOut(_Out):
    buckets: IntakeStatsBuckets
    totals: IntakeStatsTotals


# ============================================================================ create / batch


class IntakeCandidateIn(_In):
    full_name: str = Field(..., min_length=1, max_length=200)
    email: Email
    phone: str | None = Field(None, max_length=40)


class IntakeCreate(_In):
    """Exactly one of ``candidate_id`` (existing candidate) and ``candidate`` (create, or reuse by e-mail)."""

    candidate_id: uuid.UUID | None = None
    candidate: IntakeCandidateIn | None = None
    job_id: uuid.UUID
    round_id: uuid.UUID | None = None
    target_role: TargetRole | None = None
    title: str | None = Field(None, max_length=200)
    settings: IntakeInviteSettingsIn = Field(default_factory=IntakeInviteSettingsIn)
    notes: str | None = Field(None, max_length=4000)
    send: bool = True

    @model_validator(mode="after")
    def _one_candidate(self) -> IntakeCreate:
        if (self.candidate_id is None) == (self.candidate is None):
            raise PydanticCustomError(
                "candidate_or_candidate_id_required",
                "Choose an existing candidate or enter a new candidate's name and e-mail (exactly one).",
            )
        return self


class IntakeCreateResponse(_Out):
    intake: IntakeSummaryOut
    invite_url: str | None = None  # shown once; null for drafts
    access_code: str | None = None  # shown once
    expires_at: datetime | None = None


class IntakeBatchRow(_In):
    full_name: str = Field(..., min_length=1, max_length=200)
    email: Email
    target_role: TargetRole | None = None
    notes: str | None = Field(None, max_length=4000)


class IntakeBatchCreate(_In):
    job_id: uuid.UUID
    round_id: uuid.UUID | None = None
    settings: IntakeInviteSettingsIn = Field(default_factory=IntakeInviteSettingsIn)
    send: bool = True
    rows: list[IntakeBatchRow] = Field(..., min_length=1, max_length=BATCH_MAX_ROWS)


class IntakeBatchCreated(_Out):
    row: int  # 0-based index in the request
    intake_id: uuid.UUID
    candidate_id: uuid.UUID
    email: str
    candidate_reused: bool
    invite_url: str | None = None
    access_code: str | None = None
    expires_at: datetime | None = None


class IntakeBatchFailed(_Out):
    row: int
    email: str
    code: str
    message: str


class IntakeBatchResponse(_Out):
    batch_id: uuid.UUID
    created: list[IntakeBatchCreated]
    failed: list[IntakeBatchFailed]


# ========================================================================= update / actions


class IntakeUpdate(_In):
    """Drafts: every field. Active: ``assigned_reviewer_id``, ``notify_on_completion``, ``notes`` and
    ``target_role`` (until the candidate submitted the onboarding form)."""

    title: str | None = Field(None, min_length=1, max_length=200)
    target_role: TargetRole | None = None
    settings: IntakeInviteSettingsIn | None = None
    assigned_reviewer_id: uuid.UUID | None = None
    notify_on_completion: bool | None = None
    notes: str | None = Field(None, max_length=4000)
    job_id: uuid.UUID | None = None
    round_id: uuid.UUID | None = None
    candidate: IntakeCandidateIn | None = None


class IntakeSendRequest(_In):
    expires_in_hours: int | None = Field(None, ge=1, le=24 * 60)


class IntakeCancelRequest(_In):
    reason: str | None = Field(None, max_length=500)


class IntakeRegenerateLinkRequest(_In):
    expires_in_hours: int | None = Field(None, ge=1, le=24 * 60)


# ================================================================================== portal


class PortalIntakePart(_Out):
    number: int
    key: str
    title: str  # candidate-safe title
    minutes: int


class PortalDurationRange(_Out):
    min: int = INTAKE_MIN_MINUTES
    max: int = INTAKE_MAX_MINUTES


class PortalIntakeEligibility(_Out):
    """MQ1 form state. The candidate's own answers are echoed back so the form can be edited; staff and
    reports never show them to a model."""

    enabled: bool
    country: str | None = None
    ask_sponsorship: bool = True
    answered: bool = False
    authorized: bool | None = None
    needs_sponsorship: bool | None = None


class PortalIntakeResumeReview(_Out):
    required: bool
    confirmed: bool = False
    skipped: bool = False


PortalIntakeMissing = Literal["consent", "resume", "onboarding", "resume_review"]


class PortalIntakeOut(_Out):
    intake_id: uuid.UUID
    interview_id: uuid.UUID
    title: str
    program_name: str
    organization_name: str
    estimated_minutes: int
    duration_range: PortalDurationRange = Field(default_factory=PortalDurationRange)
    parts: list[PortalIntakePart]
    presence: PresenceMode
    # Additive (T3): a staff preset target role; the form shows it read-only and the server stores it.
    target_role_preset: TargetRole | None = None
    onboarding: IntakeOnboarding | None = None
    onboarding_submitted: bool = False
    onboarding_locked: bool = False
    eligibility: PortalIntakeEligibility
    resume_required: bool
    resume_uploaded: bool = False
    resume_review: PortalIntakeResumeReview
    ready_to_start: bool = False
    missing: list[PortalIntakeMissing] = Field(default_factory=list)

    @field_validator("missing")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


__all__ = [
    "BATCH_MAX_ROWS",
    "IntakeBatchCreate",
    "IntakeBatchCreated",
    "IntakeBatchFailed",
    "IntakeBatchResponse",
    "IntakeBatchRow",
    "IntakeCancelRequest",
    "IntakeCandidateIn",
    "IntakeCandidateRef",
    "IntakeConfigOut",
    "IntakeCreate",
    "IntakeCreateResponse",
    "IntakeDecisionRef",
    "IntakeEvaluationRef",
    "IntakeInterviewRef",
    "IntakeJobOption",
    "IntakeJobRef",
    "IntakeKitCardOut",
    "IntakeKitDuration",
    "IntakeKitInfo",
    "IntakeKitPassageOut",
    "IntakeKitStageOut",
    "IntakeOrgSettingsIn",
    "IntakeOrgSettingsOut",
    "IntakeRegenerateLinkRequest",
    "IntakeRoundRef",
    "IntakeSendRequest",
    "IntakeSessionRef",
    "IntakeStatsBuckets",
    "IntakeStatsOut",
    "IntakeStatsTotals",
    "IntakeSummaryOut",
    "IntakeUpdate",
    "MinutesRange",
    "PortalDurationRange",
    "PortalIntakeEligibility",
    "PortalIntakeMissing",
    "PortalIntakeOut",
    "PortalIntakePart",
    "PortalIntakeResumeReview",
    "ResumeReviewIn",
    "ResumeReviewOut",
    "UserRef",
]
