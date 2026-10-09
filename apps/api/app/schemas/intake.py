"""Intake Interview cross-track contracts (``docs/design/INTAKE_INTERVIEW.md`` sections 4.1 and 15.1-15.10).

Owned by T3 (day 0); every other track imports these models. Changes after day 0 need the coordinator's
approval (they are contracts).

* Round configuration: :class:`IntakeRoundConfig` (``RoundConfig.intake``) with :class:`WorkAuthorizationPolicy`.
* Organisation settings content: :class:`ApprovedInfoItem`, :class:`IntakeOrgDefaults`.
* Stored documents: :class:`IntakeInviteSettings` (``intake_interviews.settings``), :class:`IntakeOnboarding` and
  :class:`IntakeEligibility`, résumé review (:class:`ResumeReviewDoc`, :class:`ResumeReviewRecord`), stage items
  (:class:`StageItemResult`), activity captures (:class:`ActivityCaptureRecord`), evidence references
  (:class:`EvidenceRef`), review items (:class:`ReviewItem`), question metadata (:class:`IntakeQuestionMeta`,
  ``questions.decision.intake``) and training plans (:class:`TrainingPlan`).
* Decimals (scores, weights, totals) serialise as **strings** (:data:`DecimalStr`, e.g. ``"3.6700"``).

Privacy: work-authorisation (MQ1) answers live only in :class:`IntakeEligibility`. They are never part of the
onboarding digest (:data:`ONBOARDING_DIGEST_KEYS`) and never reach a model, a score, a report or analytics.

Validation errors that the intake APIs surface with their own error code are raised as
:class:`~pydantic_core.PydanticCustomError` with that code as the error type (see :data:`INTAKE_ERROR_CODES`).
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.services.intake.scoring import DecisionBand

# ============================================================================================ decimals


def _decimal_to_str(value: Decimal) -> str:
    return format(value, "f")


#: Decimal serialised as a plain string ("3.6", "0.20", "3.6700"); the web formats it for display only.
DecimalStr = Annotated[Decimal, PlainSerializer(_decimal_to_str, return_type=str)]


# ======================================================================================= 15.1 enums

TargetRole = Literal["ai_ml", "data_analyst", "product_manager_technical", "product_manager_growth", "other"]
OpenToAiMl = Literal["yes", "maybe", "no"]
PrimaryLanguage = Literal["python", "java", "javascript", "csharp", "sql", "other"]
ScriptingLevel = Literal["none", "some", "regular"]
ExperienceComfort = Literal["yes", "unsure", "no"]
TrainingCommitment = Literal["full", "partial", "cannot"]
Availability = Literal["immediately", "within_2_weeks", "within_1_month", "later"]
PresenceMode = Literal["orb", "none"]
IntakeStatus = Literal["draft", "active", "cancelled"]
IntakeBucket = Literal[
    "all", "drafts", "active", "scheduled", "in_progress", "completed", "awaiting_review", "accepted",
    "borderline", "rejected", "training", "expired", "cancelled",
]  # fmt: skip
StageKey = Literal["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"]
StageStatus = Literal["pending", "in_progress", "completed", "incomplete"]
ItemStatus = Literal[
    "pending", "asked", "answered", "prefilled", "skipped", "declined", "not_reached", "interrupted",
    "not_applicable",
]  # fmt: skip
SkipReason = Literal[
    "time_budget", "policy_disabled", "prefilled", "declined", "not_hands_on", "unclear", "activity_interrupted",
    "time_up",
]  # fmt: skip
ActivityKind = Literal["reading_aloud", "read_relay", "topic_card"]
ActivityPhase = Literal["read", "relay", "prepare", "speak"]
CapturePhase = Literal["read", "relay", "speak"]
ActivityStatus = Literal["pending", "armed", "running", "completed", "interrupted", "skipped", "failed"]
ActivityEndReason = Literal[
    "finish_button", "deadline", "silence", "disconnect", "max_audio", "session_end", "skipped"
]
CaptureEndReason = Literal["finish_button", "deadline", "silence", "disconnect", "max_audio", "session_end"]
TranscriptStatus = Literal["pending", "final", "pending_worker", "live_only", "unavailable"]
TranscriptSource = Literal["final_pass_live", "final_pass_worker", "live_segments", "typed"]
EvaluationStatus = Literal["pending", "running", "ready", "failed"]
EvaluationReason = Literal["session_completed", "reevaluate"]
EvidenceSufficiency = Literal["sufficient", "partial", "insufficient"]
EvaluatorKey = Literal[
    "reading_metrics", "communication", "resume_evidence", "technical", "relay", "structured", "trainability",
    "composure", "training_plan",
]  # fmt: skip
EvaluatorRunStatus = Literal["pending", "running", "succeeded", "fallback", "failed"]
CompetencyScoreStatus = Literal["scored", "insufficient"]
#: ``scoring.DecisionBand`` values as a Literal (the StrEnum itself is re-exported as :class:`DecisionBand`).
DecisionBandValue = Literal[
    "strong_accept", "accept_with_notes", "borderline", "reject", "insufficient_evidence"
]
DecisionOutcome = Literal["accept", "accept_with_notes", "second_interview", "reject", "request_evidence"]
ReviewItemKind = Literal[
    "claims_not_supported", "reading_not_completed", "work_authorization_review", "mandatory_mismatch",
    "communication_unclear", "stage_incomplete", "mandatory_item_missing", "activity_interrupted",
    "asr_quality_low", "integrity_observation", "evaluator_fallback", "evaluator_disagreement",
]  # fmt: skip
ReviewItemSeverity = Literal["important", "attention", "info"]
ReviewItemResolutionValue = Literal["confirmed", "dismissed", "follow_up"]

TARGET_ROLES: tuple[str, ...] = get_args(TargetRole)
PRIMARY_LANGUAGES: tuple[str, ...] = get_args(PrimaryLanguage)
PRESENCE_MODES: tuple[str, ...] = get_args(PresenceMode)
INTAKE_STATUSES: tuple[str, ...] = get_args(IntakeStatus)
INTAKE_BUCKETS: tuple[str, ...] = get_args(IntakeBucket)
INTAKE_STAGE_KEYS: tuple[str, ...] = get_args(StageKey)
ACTIVITY_KINDS: tuple[str, ...] = get_args(ActivityKind)
DECISION_BANDS: tuple[str, ...] = get_args(DecisionBandValue)
DECISION_OUTCOMES: tuple[str, ...] = get_args(DecisionOutcome)
REVIEW_ITEM_KINDS: tuple[str, ...] = get_args(ReviewItemKind)

TARGET_ROLE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "ai_ml": "AI/ML",
        "data_analyst": "Data Analyst",
        "product_manager_technical": "Product Manager (technical)",
        "product_manager_growth": "Product Manager (growth)",
        "other": "Other",
    }
)
PRIMARY_LANGUAGE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "python": "Python",
        "java": "Java",
        "javascript": "JavaScript",
        "csharp": "C#",
        "sql": "SQL",
        "other": "Other",
    }
)
#: Candidate-safe stage titles (realtime ``session.state.intake``, portal "parts", staff config). The kit titles
#: (``IntakeKit.stage(key).title``) are staff wording.
INTAKE_CANDIDATE_STAGE_TITLES: Mapping[str, str] = MappingProxyType(
    {
        "greeting": "Introduction",
        "resume": "Your experience",
        "mandatory": "Standard questions",
        "reading": "Reading exercise",
        "topic": "Speaking exercise",
        "learning": "Learning and growth",
        "close": "Your questions",
    }
)

# Duration rules (decision 10; RoundConfig validator section 4.1).
INTAKE_MIN_MINUTES = 35
INTAKE_MAX_MINUTES = 50
INTAKE_DEFAULT_MINUTES = 45
INTAKE_MAX_EXTRA_MINUTES = 5
INTAKE_DEFAULT_TITLE = "Intake Interview"
INTAKE_DEFAULT_PROGRAM_NAME = "AI/ML Training Program"
APPROVED_INFO_MAX_ITEMS = 30

#: Error codes the intake APIs return as the top-level ``error.code`` (instead of ``validation_failed``) when a
#: request model raises a ``PydanticCustomError`` of that type.
INTAKE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "work_authorization_country_required",
        "target_role_other_required",
        "primary_language_other_required",
        "candidate_or_candidate_id_required",
        "document_required",
        "duplicate_ref",
        "ref_section_mismatch",
        "invalid_claim_limits",
    }
)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SINGLE_LINE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_text(value: Any, *, single_line: bool = False) -> Any:
    """Strip surrounding whitespace and control characters (newlines kept unless ``single_line``)."""
    if not isinstance(value, str):
        return value
    pattern = _SINGLE_LINE_CONTROL if single_line else _CONTROL
    return pattern.sub(" " if single_line else "", value).strip()


def _optional_text(value: Any, *, single_line: bool = True) -> Any:
    cleaned = _clean_text(value, single_line=single_line)
    return cleaned or None if isinstance(cleaned, str) else cleaned


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Lenient(BaseModel):
    """Runtime sub-records written by the live pipeline: unknown keys are dropped, never rejected."""

    model_config = ConfigDict(extra="ignore")


# ============================================================================ 4.1 round configuration


class WorkAuthorizationPolicy(_Model):
    """MQ1 policy (decision 6): form-only, default lawful wording, never sent to a model or scored."""

    enabled: bool = False
    country: str | None = Field(
        None, max_length=80, description='Spoken/printed as written, e.g. "the United States"'
    )
    ask_sponsorship: bool = True
    variant: Literal["default"] = "default"  # reviewed variants are out of scope in v1

    @field_validator("country", mode="before")
    @classmethod
    def _clean_country(cls, value: Any) -> Any:
        return _optional_text(value)

    @model_validator(mode="after")
    def _country_required(self) -> WorkAuthorizationPolicy:
        if self.enabled and not self.country:
            raise PydanticCustomError(
                "work_authorization_country_required",
                "Enter the country when the work-authorisation questions are enabled.",
            )
        return self


class IntakeFollowupLimits(_Model):
    greeting: int = Field(2, ge=0, le=3)  # follow-ups on the proud project
    resume_clarify_per_rung: int = Field(1, ge=0, le=2)
    mandatory: int = Field(1, ge=0, le=2)  # optional MQ follow-ups beyond the fixed items
    topic: int = Field(1, ge=0, le=2)  # beyond the mandatory weakest-bullet follow-up
    learning: int = Field(2, ge=0, le=3)


class IntakeClaimLimits(_Model):
    min_claims: int = Field(2, ge=1, le=3)
    max_claims: int = Field(3, ge=1, le=3)

    @model_validator(mode="after")
    def _ordered(self) -> IntakeClaimLimits:
        if self.min_claims > self.max_claims:
            raise PydanticCustomError("invalid_claim_limits", "min_claims must not exceed max_claims.")
        return self


class IntakeRoundConfig(_Model):
    """``RoundConfig.intake`` (only for ``round_type: intake``). Timers come from the kit, not from here."""

    kit_version: str = Field("v1", max_length=32)
    followup_limits: IntakeFollowupLimits = Field(default_factory=IntakeFollowupLimits)
    claims: IntakeClaimLimits = Field(default_factory=IntakeClaimLimits)
    scenarios: int = Field(2, ge=0, le=3)
    passage_drill_a: Literal["auto", "A", "B", "D", "E", "F"] = "auto"
    passage_drill_b: Literal["auto", "A", "B", "C", "E", "F"] = "auto"
    topic_card: Literal["auto", "1", "2", "3", "4", "5", "6", "7", "8"] = "auto"
    work_authorization: WorkAuthorizationPolicy | None = None  # None = organisation default
    closing_questions_max: int = Field(3, ge=0, le=5)


# =================================================================== organisation settings content


class ApprovedInfoItem(_Model):
    """One approved program fact; stage 7 may only state these."""

    topic: str = Field(..., min_length=1, max_length=120)
    answer: str = Field(..., min_length=1, max_length=1200)

    @field_validator("topic", mode="before")
    @classmethod
    def _clean_topic(cls, value: Any) -> Any:
        return _clean_text(value, single_line=True)

    @field_validator("answer", mode="before")
    @classmethod
    def _clean_answer(cls, value: Any) -> Any:
        return _clean_text(value)


class IntakeOrgDefaults(_Model):
    duration_minutes: int = Field(INTAKE_DEFAULT_MINUTES, ge=INTAKE_MIN_MINUTES, le=INTAKE_MAX_MINUTES)
    max_reconnects: int = Field(10, ge=0, le=20)
    expires_in_hours: int = Field(168, ge=1, le=1440)
    presence: PresenceMode = "orb"
    resume_required: bool = True


# ============================================================================ 15.2 invite settings


class IntakeInviteSettingsIn(_Model):
    """Staff input; fields left out take the organisation defaults. ``work_authorization: null`` = inherit
    (round configuration, else organisation default)."""

    duration_minutes: int = Field(INTAKE_DEFAULT_MINUTES, ge=INTAKE_MIN_MINUTES, le=INTAKE_MAX_MINUTES)
    presence: PresenceMode = "orb"
    max_reconnects: int = Field(10, ge=0, le=20)
    expires_in_hours: int = Field(168, ge=1, le=1440)
    scheduled_for: datetime | None = None
    require_access_code: bool = False
    resume_required: bool = True
    assigned_reviewer_id: uuid.UUID | None = None
    notify_on_completion: bool = True
    work_authorization: WorkAuthorizationPolicy | None = None

    @field_validator("scheduled_for", mode="after")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        """A schedule without a timezone is read as UTC."""
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class IntakeInviteSettings(IntakeInviteSettingsIn):
    """``intake_interviews.settings``: the effective settings, frozen when the invitation is sent."""

    work_authorization: WorkAuthorizationPolicy = Field(default_factory=WorkAuthorizationPolicy)  # effective
    program_name: str = Field(INTAKE_DEFAULT_PROGRAM_NAME, max_length=200)
    kit_version: str = Field("v1", max_length=32)
    kit_sha256: str = Field("", max_length=64)


# ===================================================================== 15.3 onboarding, eligibility

#: The only onboarding data that may reach a prompt (always fenced as untrusted): never eligibility, never the name.
ONBOARDING_DIGEST_KEYS: tuple[str, ...] = (
    "target_role",
    "target_role_other",
    "open_to_ai_ml",
    "technical_background",
    "primary_language",
    "self_rating",
    "scripting",
    "scripting_details",
    "data_ml_libraries",
    "years_hands_on",
    "experience_level_comfort",
    "training_commitment",
    "weekly_hours",
)
DIGEST_TECHNICAL_BACKGROUND_MAX = 600
DIGEST_SCRIPTING_DETAILS_MAX = 200

_SINGLE_LINE_FIELDS = (
    "full_name",
    "target_role_other",
    "primary_language_other",
    "data_ml_libraries",
)
_OPTIONAL_TEXT_FIELDS = (
    "target_role_other",
    "primary_language_other",
    "scripting_details",
    "data_ml_libraries",
)


class _OnboardingFields(_Model):
    full_name: str = Field(..., min_length=2, max_length=200)
    target_role: TargetRole
    target_role_other: str | None = Field(None, max_length=80)
    open_to_ai_ml: OpenToAiMl
    technical_background: str = Field(..., min_length=1, max_length=1500)
    primary_language: PrimaryLanguage
    primary_language_other: str | None = Field(None, max_length=40)
    self_rating: int = Field(..., ge=1, le=10)
    scripting: ScriptingLevel
    scripting_details: str | None = Field(None, max_length=500)
    data_ml_libraries: str | None = Field(None, max_length=300)
    years_hands_on: float | None = Field(None, ge=0, le=50, allow_inf_nan=False)
    experience_level_comfort: ExperienceComfort
    training_commitment: TrainingCommitment
    weekly_hours: int | None = Field(None, ge=0, le=80)
    availability: Availability
    available_from: date | None = None
    accuracy_confirmed: Literal[True]

    @field_validator(*_SINGLE_LINE_FIELDS, mode="before")
    @classmethod
    def _single_line(cls, value: Any, info: Any) -> Any:
        if info.field_name in _OPTIONAL_TEXT_FIELDS:
            return _optional_text(value)
        return _clean_text(value, single_line=True)

    @field_validator("technical_background", mode="before")
    @classmethod
    def _multi_line(cls, value: Any) -> Any:
        return _clean_text(value)

    @field_validator("scripting_details", mode="before")
    @classmethod
    def _details(cls, value: Any) -> Any:
        return _optional_text(value, single_line=False)

    @model_validator(mode="after")
    def _other_values(self) -> _OnboardingFields:
        if self.target_role == "other":
            if not self.target_role_other:
                raise PydanticCustomError(
                    "target_role_other_required", "Tell us which role you are targeting."
                )
        else:
            self.target_role_other = None
        if self.primary_language == "other":
            if not self.primary_language_other:
                raise PydanticCustomError(
                    "primary_language_other_required", "Tell us which language you know best."
                )
        else:
            self.primary_language_other = None
        if self.years_hands_on is not None and not math.isfinite(self.years_hands_on):
            raise ValueError("years_hands_on must be a finite number")
        return self


class IntakeOnboarding(_OnboardingFields):
    """``intake_interviews.onboarding`` (candidate's own answers; MQ1 is stored separately)."""

    version: Literal[1] = 1
    submitted_at: datetime


class IntakeEligibilityIn(_Model):
    """``null`` = "I'd rather discuss this with the hiring team"."""

    authorized: bool | None = None
    needs_sponsorship: bool | None = None


class IntakeOnboardingIn(_OnboardingFields):
    """Candidate input. ``eligibility`` is required when MQ1 is enabled for the interview (and ignored otherwise)."""

    eligibility: IntakeEligibilityIn | None = None


class IntakeEligibility(_Model):
    """``intake_interviews.eligibility`` (MQ1): human review only - never sent to any model, never scored, never in
    reports, exports or analytics. ``needs_sponsorship`` is null when the policy does not ask it."""

    version: Literal[1] = 1
    country: str = Field(..., max_length=80)
    authorized: bool | None = None
    needs_sponsorship: bool | None = None
    answered_at: datetime


class OnboardingDigest(_Model):
    """Shape of ``onboarding.onboarding_digest()`` (documentation and validation of the prompt-safe subset)."""

    target_role: TargetRole
    target_role_other: str | None = None
    open_to_ai_ml: OpenToAiMl
    technical_background: str = Field(..., max_length=DIGEST_TECHNICAL_BACKGROUND_MAX)
    primary_language: str  # label ("Python", "C#") or the candidate's own text for "other"
    self_rating: int
    scripting: ScriptingLevel
    scripting_details: str | None = Field(None, max_length=DIGEST_SCRIPTING_DETAILS_MAX)
    data_ml_libraries: str | None = None
    years_hands_on: float | None = None
    experience_level_comfort: ExperienceComfort
    training_commitment: TrainingCommitment
    weekly_hours: int | None = None


# ============================================================================== 15.4 résumé review

ROLE_REF = re.compile(r"^r\d{1,2}$")
PROJECT_REF = re.compile(r"^p\d{1,2}$")
EDUCATION_REF = re.compile(r"^e\d{1,2}$")
CANDIDATE_REF = re.compile(r"^n\d{1,3}$")
NEW_REF = "new"
_REF_PATTERN = r"^(?:[rpe]\d{1,2}|n\d{1,3}|new)$"

ResumeText = Annotated[str, Field(min_length=1, max_length=400)]
ShortText = Annotated[str, Field(min_length=1, max_length=80)]


def _clean_list(value: Any, *, single_line: bool = True) -> Any:
    """Strip items, drop empty ones and case-insensitive duplicates (order kept)."""
    if not isinstance(value, list):
        return value
    out: list[Any] = []
    seen: set[str] = set()
    for item in value:
        cleaned = _clean_text(item, single_line=single_line)
        if isinstance(cleaned, str):
            if not cleaned or cleaned.casefold() in seen:
                continue
            seen.add(cleaned.casefold())
        out.append(cleaned)
    return out


class _DocItem(_Model):
    @field_validator("*", mode="before")
    @classmethod
    def _clean_values(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _clean_text(value, single_line=True)
        if isinstance(value, list):
            return _clean_list(value)
        return value


class ResumeRole(_DocItem):
    ref: str = Field(..., pattern=_REF_PATTERN)
    title: str = Field(..., min_length=1, max_length=160)
    company: str = Field("", max_length=160)
    start: str | None = Field(None, max_length=20)
    end: str | None = Field(None, max_length=20)
    responsibilities: list[ResumeText] = Field(default_factory=list, max_length=6)
    technologies: list[ShortText] = Field(default_factory=list, max_length=15)
    achievements: list[ResumeText] = Field(default_factory=list, max_length=4)

    @field_validator("start", "end", mode="after")
    @classmethod
    def _empty_none(cls, value: str | None) -> str | None:
        return value or None


class ResumeProject(_DocItem):
    ref: str = Field(..., pattern=_REF_PATTERN)
    name: str = Field(..., min_length=1, max_length=160)
    role: str | None = Field(None, max_length=160)
    description: str = Field("", max_length=600)
    technologies: list[ShortText] = Field(default_factory=list, max_length=15)
    outcomes: list[ResumeText] = Field(default_factory=list, max_length=4)

    @field_validator("role", mode="after")
    @classmethod
    def _empty_none(cls, value: str | None) -> str | None:
        return value or None


class ResumeEducation(_DocItem):
    """No dates (age proxy)."""

    ref: str = Field(..., pattern=_REF_PATTERN)
    institution: str = Field(..., min_length=1, max_length=200)
    degree: str | None = Field(None, max_length=160)
    field: str | None = Field(None, max_length=160)

    @field_validator("degree", "field", mode="after")
    @classmethod
    def _empty_none(cls, value: str | None) -> str | None:
        return value or None


_SECTION_REFS: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {"roles": ROLE_REF, "projects": PROJECT_REF, "education": EDUCATION_REF}
)


class ResumeReviewDoc(_Model):
    """The candidate-safe résumé document the candidate confirms or corrects (refs ``r1``/``p1``/``e1`` for extracted
    items, ``n1``... for items the candidate added; send ``"new"`` for a new item)."""

    version: Literal[1] = 1
    headline: str | None = Field(None, max_length=200)
    experience_years: float | None = Field(None, ge=0, le=60, allow_inf_nan=False)
    roles: list[ResumeRole] = Field(default_factory=list, max_length=12)
    projects: list[ResumeProject] = Field(default_factory=list, max_length=10)
    skills: list[ShortText] = Field(default_factory=list, max_length=40)
    languages: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=15
    )
    tools: list[ShortText] = Field(default_factory=list, max_length=30)
    education: list[ResumeEducation] = Field(default_factory=list, max_length=5)
    certifications: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=10
    )

    @field_validator("headline", mode="before")
    @classmethod
    def _clean_headline(cls, value: Any) -> Any:
        return _optional_text(value)

    @field_validator("skills", "languages", "tools", "certifications", mode="before")
    @classmethod
    def _clean_lists(cls, value: Any) -> Any:
        return _clean_list(value)

    @model_validator(mode="after")
    def _refs(self) -> ResumeReviewDoc:
        for section, pattern in _SECTION_REFS.items():
            seen: set[str] = set()
            for item in getattr(self, section):
                ref = item.ref
                if ref == NEW_REF or CANDIDATE_REF.match(ref):
                    continue
                if not pattern.match(ref):
                    raise PydanticCustomError(
                        "ref_section_mismatch",
                        "Item reference {ref} does not belong to {section}.",
                        {"ref": ref, "section": section},
                    )
                if ref in seen:
                    raise PydanticCustomError(
                        "duplicate_ref", "Item reference {ref} is used twice.", {"ref": ref}
                    )
                seen.add(ref)
        return self


class ResumeCorrection(_Model):
    path: str = Field(..., max_length=120)  # e.g. "roles[r1].title", "skills"
    op: Literal["changed", "added", "removed"]
    before: Any | None = None
    after: Any | None = None


class ResumeReviewSource(_Model):
    resume_id: uuid.UUID
    profile_id: uuid.UUID | None = None
    profile_status: Literal["quick", "ready", "partial"] | None = None


class ResumeReviewRecord(_Model):
    """``intake_interviews.resume_review``. ``original``/``document`` are null only for a skip without any profile."""

    action: Literal["confirmed", "skipped"]
    source: ResumeReviewSource
    original: ResumeReviewDoc | None = None
    document: ResumeReviewDoc | None = None
    corrections: list[ResumeCorrection] = Field(default_factory=list)
    confirmed_at: datetime


class ResumeReviewSection(_Model):
    key: str
    label: str | None = None
    state: str | None = None


class ResumeReviewOut(_Model):
    status: Literal["waiting", "ready"]
    profile_stage: Literal["none", "quick", "full"] = "none"
    sections: list[ResumeReviewSection] = Field(default_factory=list)
    document: ResumeReviewDoc | None = None
    confirmed: bool = False
    skipped: bool = False
    confirmed_at: datetime | None = None
    can_skip: bool = False
    skip_available_in_s: int | None = None


class ResumeReviewIn(_Model):
    action: Literal["confirm", "skip"]
    document: ResumeReviewDoc | None = None

    @model_validator(mode="after")
    def _document_for_confirm(self) -> ResumeReviewIn:
        if self.action == "confirm" and self.document is None:
            raise PydanticCustomError("document_required", "Send the reviewed résumé document to confirm it.")
        return self


# ========================================================================= 15.5 stage item results


class StageItemResult(_Model):
    """``intake_stage_results.items[]`` (T1 writes)."""

    item_key: str = Field(..., max_length=60)
    title: str = Field("", max_length=300)
    mandatory: bool = False
    compressible: bool = False
    status: ItemStatus = "pending"
    question_ids: list[uuid.UUID] = Field(default_factory=list)
    answer_ids: list[uuid.UUID] = Field(default_factory=list)
    followups_used: int = Field(0, ge=0)
    followup_limit: int = Field(0, ge=0)
    clarifications: int = Field(0, ge=0)
    skip_reason: SkipReason | None = None
    started_s: float | None = None
    ended_s: float | None = None


# ========================================================================= 15.6 activity captures


class CaptureSegment(_Lenient):
    start_ms: int
    end_ms: int
    speech_ms: int | None = None
    vad_prob: float | None = None
    no_speech_prob: float | None = None
    text: str = ""


class CapturePause(_Lenient):
    start_ms: int
    end_ms: int
    duration_ms: int


class CaptureWindow(_Lenient):
    start_ms: int
    end_ms: int
    text: str = ""


class ActivityCaptureRecord(_Model):
    """``intake_activity_results.capture`` for the scored attempt (session clock; F0 ``align_reading`` input)."""

    attempt: int = Field(1, ge=1)
    phase: CapturePhase
    started_ms: int
    ended_ms: int
    voiced_ms: int = 0
    end_reason: CaptureEndReason
    segments: list[CaptureSegment] = Field(default_factory=list)
    pauses: list[CapturePause] = Field(default_factory=list)  # VAD pauses >= 300 ms
    stt_confidence: float | None = None
    stt_degraded: bool = False
    typed: bool = False
    windows: list[CaptureWindow] = Field(default_factory=list)  # final pass
    audio_stored: bool = False


# ============================================================================== 15.7 evidence refs


class EvidenceRef(_Model):
    source: Literal["answer", "activity", "onboarding", "resume"]
    stage: StageKey | None = None
    item_key: str | None = Field(None, max_length=60)
    question_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None
    activity_id: uuid.UUID | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    quote: str | None = Field(None, max_length=300)  # verbatim
    supports: Literal["positive", "negative", "context"]


# =============================================================================== 15.8 review items

REVIEW_ITEM_ID = re.compile(r"^ri_[0-9a-f]{10}$")


class ReviewItem(_Model):
    """Never an automatic outcome; each item carries evidence. Ids are deterministic (``ri_`` + 10 hex)."""

    id: str = Field(..., pattern=REVIEW_ITEM_ID.pattern)
    kind: ReviewItemKind
    severity: ReviewItemSeverity
    title: str = Field(..., max_length=200)
    detail: str = Field("", max_length=2000)
    competency_keys: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    source: Literal["rule", "evaluator", "reading", "validator"]
    kit_flag: str | None = None  # kit review flag key
    status: Literal["open"] = "open"


class ReviewerRef(_Model):
    id: uuid.UUID
    full_name: str


class ReviewItemResolution(_Model):
    """Added to a review item by the staff detail endpoint (latest decision's resolution)."""

    resolution: ReviewItemResolutionValue
    note: str | None = Field(None, max_length=1000)
    reviewer: ReviewerRef
    at: datetime


# ================================================================= 15.9 questions.decision.intake


class IntakeQuestionMeta(_Model):
    stage: StageKey
    item_key: str = Field(..., max_length=60)
    item_kind: Literal[
        "prompt", "followup", "clarify", "ladder", "mq", "verification", "activity", "answer_question", "redirect",
        "transition", "closing",
    ]  # fmt: skip
    mandatory: bool = False
    template: bool = True
    claim_ref: str | None = Field(None, pattern=r"^C\d{1,2}$")
    rung: Literal["ownership", "mechanics", "stress"] | None = None
    mq: Literal["mq2", "mq3", "mq4"] | None = None
    language: str | None = Field(None, max_length=40)
    activity_id: uuid.UUID | None = None
    followup_index: int = Field(0, ge=0)
    answer_mode: Literal["standard", "long"] = "standard"
    soft_limit_s: int | None = None
    move: Literal["ask_item", "follow_up", "clarify", "answer_question", "redirect"] | None = None
    deviations: list[str] = Field(default_factory=list)


# ============================================================================== 15.10 training plan


class TrainingMilestone(_Model):
    week: Literal[1, 2, 3]
    milestone: str = Field(..., min_length=1, max_length=300)
    measure: str = Field(..., min_length=1, max_length=300)


class TrainingModule(_Model):
    key: str = Field(..., max_length=60)
    title: str = Field(..., max_length=160)
    priority: int = Field(..., ge=1, le=5)
    competencies: list[str] = Field(default_factory=list)
    why: str = Field(..., max_length=400)
    objectives: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        ..., min_length=2, max_length=4
    )
    milestones: list[TrainingMilestone] = Field(..., min_length=3, max_length=6)


class TrainingPlan(_Model):
    generated_by: Literal["model", "deterministic"]
    summary: str = Field(..., max_length=600)
    modules: list[TrainingModule] = Field(..., min_length=1, max_length=5)


__all__ = [
    "ACTIVITY_KINDS",
    "APPROVED_INFO_MAX_ITEMS",
    "CANDIDATE_REF",
    "DECISION_BANDS",
    "DECISION_OUTCOMES",
    "DIGEST_SCRIPTING_DETAILS_MAX",
    "DIGEST_TECHNICAL_BACKGROUND_MAX",
    "EDUCATION_REF",
    "INTAKE_BUCKETS",
    "INTAKE_CANDIDATE_STAGE_TITLES",
    "INTAKE_DEFAULT_MINUTES",
    "INTAKE_DEFAULT_PROGRAM_NAME",
    "INTAKE_DEFAULT_TITLE",
    "INTAKE_ERROR_CODES",
    "INTAKE_MAX_EXTRA_MINUTES",
    "INTAKE_MAX_MINUTES",
    "INTAKE_MIN_MINUTES",
    "INTAKE_STAGE_KEYS",
    "INTAKE_STATUSES",
    "NEW_REF",
    "ONBOARDING_DIGEST_KEYS",
    "PRESENCE_MODES",
    "PRIMARY_LANGUAGES",
    "PRIMARY_LANGUAGE_LABELS",
    "PROJECT_REF",
    "REVIEW_ITEM_ID",
    "REVIEW_ITEM_KINDS",
    "ROLE_REF",
    "TARGET_ROLES",
    "TARGET_ROLE_LABELS",
    "ActivityCaptureRecord",
    "ActivityEndReason",
    "ActivityKind",
    "ActivityPhase",
    "ActivityStatus",
    "ApprovedInfoItem",
    "Availability",
    "CaptureEndReason",
    "CapturePause",
    "CapturePhase",
    "CaptureSegment",
    "CaptureWindow",
    "CompetencyScoreStatus",
    "DecimalStr",
    "DecisionBand",
    "DecisionBandValue",
    "DecisionOutcome",
    "EvaluationReason",
    "EvaluationStatus",
    "EvaluatorKey",
    "EvaluatorRunStatus",
    "EvidenceRef",
    "EvidenceSufficiency",
    "ExperienceComfort",
    "IntakeBucket",
    "IntakeClaimLimits",
    "IntakeEligibility",
    "IntakeEligibilityIn",
    "IntakeFollowupLimits",
    "IntakeInviteSettings",
    "IntakeInviteSettingsIn",
    "IntakeOnboarding",
    "IntakeOnboardingIn",
    "IntakeOrgDefaults",
    "IntakeQuestionMeta",
    "IntakeRoundConfig",
    "IntakeStatus",
    "ItemStatus",
    "OnboardingDigest",
    "OpenToAiMl",
    "PresenceMode",
    "PrimaryLanguage",
    "ResumeCorrection",
    "ResumeEducation",
    "ResumeProject",
    "ResumeReviewDoc",
    "ResumeReviewIn",
    "ResumeReviewOut",
    "ResumeReviewRecord",
    "ResumeReviewSection",
    "ResumeReviewSource",
    "ResumeRole",
    "ReviewItem",
    "ReviewItemKind",
    "ReviewItemResolution",
    "ReviewItemResolutionValue",
    "ReviewItemSeverity",
    "ReviewerRef",
    "ScriptingLevel",
    "SkipReason",
    "StageItemResult",
    "StageKey",
    "StageStatus",
    "TargetRole",
    "TrainingCommitment",
    "TrainingMilestone",
    "TrainingModule",
    "TrainingPlan",
    "TranscriptSource",
    "TranscriptStatus",
    "WorkAuthorizationPolicy",
]
