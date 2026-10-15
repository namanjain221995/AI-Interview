"""Open interview link DTOs (OPEN_LINKS.md section 9 is the contract).

Staff: ``/open-links``; public: ``/link-access`` and ``/link-return``;
candidate portal: ``/portal/link``. Public payloads carry no internal ids
(no link, job, round or organization ids) and no counts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.api.common import Email
from app.schemas.api.portal import CandidateStepKey

LinkStatusLiteral = Literal["active", "paused", "closed"]
LinkStateLiteral = Literal["open", "scheduled", "paused", "closed", "expired", "full"]
ProgressionLiteral = Literal["immediate", "after_review"]
RegistrationStatusLiteral = Literal[
    "registered", "in_progress", "awaiting_review", "completed", "expired", "cancelled", "blocked"
]
RoundStateLiteral = Literal["locked", "invited", "in_progress", "completed", "expired", "cancelled"]
FieldTypeLiteral = Literal[
    "text", "textarea", "number", "email", "phone", "url", "select", "multiselect", "checkbox", "date"
]
FlagReviewLiteral = Literal["none", "open", "cleared", "confirmed"]
NextActionLiteral = Literal["continue_current", "start_next", "wait_for_review", "all_done", "closed"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ================================================================= shared


class FormField(BaseModel):
    key: str
    builtin: bool = False
    type: FieldTypeLiteral
    label: str
    help_text: str | None = None
    placeholder: str | None = None
    required: bool = False
    enabled: bool = True
    options: list[str] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    max_length: int | None = None


class FormSchemaOut(BaseModel):
    version: int
    fields: list[FormField]


class FormSchemaIn(_Lenient):
    """``version`` is server-owned and ignored; fields are validated by
    ``app.services.open_links.form_schema.normalize_schema``."""

    version: int | None = None
    fields: list[dict[str, Any]] = Field(..., max_length=100)


class DuplicatePolicy(_In):
    scope: Literal["job", "link"] = "job"
    device: Literal["flag", "block"] = "flag"
    network: Literal["off", "flag"] = "flag"


class LinkRound(BaseModel):
    id: uuid.UUID
    name: str
    round_type: str
    sequence: int
    duration_minutes: int
    missing: bool = False


class RetentionDays(BaseModel):
    resume: int
    audio: int
    video: int
    transcript: int
    report: int


class PolicySummary(BaseModel):
    video_recording: bool
    audio_recording: bool
    screen_recording: bool
    camera_monitoring: bool
    screen_monitoring: bool
    candidate_report_mode: str
    retention_days: RetentionDays


class LinkCounts(BaseModel):
    registrations: int = 0
    in_progress: int = 0
    awaiting_review: int = 0
    completed: int = 0
    flagged: int = 0
    blocked: int = 0


class Flag(BaseModel):
    code: Literal["same_device", "same_resume", "same_network"]
    severity: Literal["high", "low"]
    message: str
    related_registration_id: str | None = None
    at: str


class IdName(BaseModel):
    id: uuid.UUID
    full_name: str


class JobBrief(BaseModel):
    id: uuid.UUID
    title: str
    status: str


# ================================================================== staff


class _LinkFields(BaseModel):
    description: str | None = Field(None, max_length=4000)
    round_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    progression: ProgressionLiteral = "immediate"
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    max_registrations: int | None = Field(None, ge=1, le=10000)
    round_window_hours: int | None = Field(None, ge=1, le=720)
    require_resume: bool = True
    per_ip_hourly_limit: int = Field(120, ge=5, le=2000)
    duplicate_policy: DuplicatePolicy | None = None
    form_schema: FormSchemaIn | None = None
    acknowledged_warnings: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("opens_at", "closes_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class OpenLinkCreate(_LinkFields):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=120)
    require_access_code: bool = False


class OpenLinkUpdate(_Lenient):
    """Partial update; ``job_id`` and ``require_access_code`` are ignored
    (job is fixed after create; the access code has its own endpoint)."""

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=4000)
    round_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    progression: ProgressionLiteral | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    max_registrations: int | None = Field(None, ge=1, le=10000)
    round_window_hours: int | None = Field(None, ge=1, le=720)
    require_resume: bool | None = None
    per_ip_hourly_limit: int | None = Field(None, ge=5, le=2000)
    duplicate_policy: DuplicatePolicy | None = None
    form_schema: FormSchemaIn | None = None
    acknowledged_warnings: list[str] | None = Field(None, max_length=200)

    @field_validator("opens_at", "closes_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class OpenLinkSummary(BaseModel):
    id: uuid.UUID
    name: str
    job: JobBrief
    status: LinkStatusLiteral
    state: LinkStateLiteral
    url: str
    rounds: list[LinkRound]
    progression: ProgressionLiteral
    opens_at: datetime | None
    closes_at: datetime | None
    max_registrations: int | None
    counts: LinkCounts
    created_at: datetime
    updated_at: datetime


class OpenLink(OpenLinkSummary):
    description: str | None
    round_window_hours: int
    require_resume: bool
    requires_access_code: bool
    per_ip_hourly_limit: int
    duplicate_policy: DuplicatePolicy
    form_schema: FormSchemaOut
    acknowledged_warnings: list[str]
    policy_summary: PolicySummary
    warnings: list[str]
    created_by: IdName | None = None


class OpenLinkWithCode(BaseModel):
    link: OpenLink
    access_code: str | None = None


class FormIssue(BaseModel):
    key: str
    label: str
    topic: str
    message: str


class FormWarning(BaseModel):
    key: str
    code: str
    message: str
    acknowledged: bool


class FormValidateRequest(_Lenient):
    form_schema: FormSchemaIn
    acknowledged_warnings: list[str] = Field(default_factory=list, max_length=200)


class FormValidateResponse(BaseModel):
    ok: bool
    errors: list[FormIssue]
    warnings: list[FormWarning]
    normalized: FormSchemaOut


class FormLimits(BaseModel):
    max_fields: int
    max_options: int


class FormCatalog(BaseModel):
    builtins: list[FormField]
    types: list[FieldTypeLiteral]
    limits: FormLimits
    default_schema: FormSchemaOut


class LinkStatusRequest(_In):
    status: LinkStatusLiteral


class AccessCodeRequest(_In):
    enabled: bool


class RegistrationRound(BaseModel):
    sequence: int
    round_id: uuid.UUID
    name: str
    state: RoundStateLiteral
    attempt: int
    interview_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    session_status: str | None = None
    end_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None


class CandidateBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str


class Registration(BaseModel):
    id: uuid.UUID
    link_id: uuid.UUID
    candidate: CandidateBrief | None = None
    full_name: str
    email: str
    phone: str | None = None
    status: RegistrationStatusLiteral
    status_reason: str | None = None
    current_round_index: int
    rounds: list[RegistrationRound]
    flags: list[Flag]
    flag_review: FlagReviewLiteral
    created_at: datetime
    updated_at: datetime


class LinkRef(BaseModel):
    id: uuid.UUID
    name: str
    job_title: str


class RegistrationWithLink(Registration):
    link: LinkRef


class AnswerItem(BaseModel):
    key: str
    label: str
    type: FieldTypeLiteral
    value: Any = None


class RegistrationDetail(Registration):
    answers: list[AnswerItem]
    attempts: list[RegistrationRound]
    privacy_ack_at: datetime | None = None
    personal_link_issued_at: datetime | None = None
    resume_id: uuid.UUID | None = None
    flag_reviewed_at: datetime | None = None
    flag_review_note: str | None = None


class AdvanceRequest(_In):
    registration_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)
    note: str | None = Field(None, max_length=500)


class AdvancedItem(BaseModel):
    registration_id: uuid.UUID
    interview_id: uuid.UUID
    round_id: uuid.UUID


class SkippedItem(BaseModel):
    registration_id: uuid.UUID
    code: Literal[
        "not_awaiting_review",
        "last_round",
        "candidate_archived",
        "already_invited",
        "round_missing",
        "not_found",
    ]


class AdvanceResponse(BaseModel):
    advanced: list[AdvancedItem]
    skipped: list[SkippedItem]


class RetakeRequest(_In):
    round_index: int = Field(..., ge=0, le=20)
    reason: str = Field(..., min_length=3, max_length=500)


class CancelRegistrationRequest(_In):
    reason: str | None = Field(None, max_length=500)


class UnblockRequest(_In):
    reason: str = Field(..., min_length=3, max_length=500)


class UnblockResponse(BaseModel):
    registration: RegistrationDetail
    personal_url: str


class PersonalLinkResponse(BaseModel):
    personal_url: str
    issued_at: datetime


class FlagReviewRequest(_In):
    decision: Literal["cleared", "confirmed"]
    note: str | None = Field(None, max_length=1000)


# ================================================================= public


class PublicFormField(BaseModel):
    key: str
    type: FieldTypeLiteral
    label: str
    help_text: str | None = None
    placeholder: str | None = None
    required: bool = False
    options: list[str] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    max_length: int | None = None


class PublicForm(BaseModel):
    fields: list[PublicFormField]


class PublicRound(BaseModel):
    name: str
    round_type: str
    duration_minutes: int
    sequence: int


class DataUse(PolicySummary):
    notice_markdown: str
    ai_assessment: Literal[True] = True
    human_decision: Literal[True] = True


class LinkInfo(BaseModel):
    organization_name: str
    job_title: str
    job_location: str | None = None
    link_name: str
    description: str | None = None
    state: LinkStateLiteral
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    rounds: list[PublicRound]
    progression: ProgressionLiteral
    requires_access_code: bool
    require_resume: bool
    interviewer_name: str | None = None
    form: PublicForm | None = None
    data_use: DataUse
    honeypot_field: Literal["website"] = "website"


AnswerValue = str | int | float | bool | list[str] | None


class RegisterRequest(_Lenient):
    answers: dict[str, AnswerValue] = Field(default_factory=dict, max_length=60)
    privacy_ack: bool = False
    access_code: str | None = Field(None, max_length=64)
    website: str | None = Field(None, max_length=2000)
    # Proof-of-work solution for GET /link-access/{token}/challenge (S15; ALTCHA payload, base64 JSON).
    pow: str | None = Field(None, max_length=2000)


class PowChallengeOut(BaseModel):
    algorithm: str
    challenge: str
    salt: str
    signature: str
    maxnumber: int


class RegisterResponse(BaseModel):
    outcome: Literal["registered", "resumed"]
    interview_id: uuid.UUID
    next_step: CandidateStepKey
    csrf_token: str
    personal_url: str | None = None


class PersonalLinkInfo(BaseModel):
    organization_name: str
    job_title: str
    link_name: str
    state: Literal["active", "cancelled"]


class PersonalVerifyRequest(_In):
    email: Email


class LinkProgressRound(BaseModel):
    sequence: int
    name: str
    round_type: str
    duration_minutes: int
    state: RoundStateLiteral
    interview_id: uuid.UUID | None = None
    is_current: bool = False


class LinkProgress(BaseModel):
    organization_name: str
    job_title: str
    link_name: str
    progression: ProgressionLiteral
    rounds: list[LinkProgressRound]
    next_action: NextActionLiteral
    next_interview_id: uuid.UUID | None = None
    next_round_name: str | None = None
    deadline: datetime | None = None


class PersonalVerifyResponse(BaseModel):
    interview_id: uuid.UUID
    next_step: CandidateStepKey
    csrf_token: str
    progress: LinkProgress


class ContinueRequest(_In):
    interview_id: uuid.UUID


class ContinueResponse(BaseModel):
    interview_id: uuid.UUID
    next_step: CandidateStepKey
    csrf_token: str
