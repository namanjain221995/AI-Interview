"""Candidate onboarding form (``details`` journey step; INTAKE_INTERVIEW.md 10.4, 15.3).

* The form answers are stored in ``intake_interviews.onboarding`` (:class:`IntakeOnboarding`); stage 3 of the
  live interview *confirms* them instead of re-asking ("On the form you said ...").
* Work authorisation (MQ1) is form-only (decision 6): asked only when the frozen policy enables it, stored
  separately in ``intake_interviews.eligibility`` (:class:`IntakeEligibility`) for human review, and **never**
  part of :func:`onboarding_digest` (the only onboarding data that may reach a prompt, always fenced as
  untrusted by its consumer) - never sent to any model or used in any score.
* A staff preset ``target_role`` wins over the candidate's choice (the form shows it read-only).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.db.models import IntakeInterview
from app.schemas.api.intake_admin import PortalIntakeEligibility
from app.schemas.intake import (
    DIGEST_SCRIPTING_DETAILS_MAX,
    DIGEST_TECHNICAL_BACKGROUND_MAX,
    ONBOARDING_DIGEST_KEYS,
    PRIMARY_LANGUAGE_LABELS,
    IntakeEligibility,
    IntakeInviteSettings,
    IntakeOnboarding,
    IntakeOnboardingIn,
    OnboardingDigest,
)

log = get_logger(__name__)

#: Keys that identify work-authorisation (MQ1) data; none may appear in anything sent to a model.
ELIGIBILITY_KEYS: frozenset[str] = frozenset(
    {"eligibility", "authorized", "needs_sponsorship", "work_authorized", "work_authorization"}
)


def _truncate(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    cut = value[: limit - 1].rstrip()
    return f"{cut}…"


def onboarding_digest(onboarding: IntakeOnboarding | BaseModel | Mapping[str, Any] | None) -> dict[str, Any]:
    """Prompt-safe subset of the onboarding answers (15.3): whitelisted keys only, long texts truncated, the
    language as a label ("Python", "C#") or the candidate's own text for "other". Never eligibility, never the
    name. ``{}`` when there is nothing (or nothing readable) to share."""
    if onboarding is None:
        return {}
    data = onboarding.model_dump(mode="json") if isinstance(onboarding, BaseModel) else dict(onboarding)
    language_key = data.get("primary_language")
    if language_key == "other":
        language = data.get("primary_language_other") or "Other"
    else:
        language = PRIMARY_LANGUAGE_LABELS.get(str(language_key), language_key)
    digest: dict[str, Any] = {key: data.get(key) for key in ONBOARDING_DIGEST_KEYS}
    digest["primary_language"] = language
    digest["technical_background"] = _truncate(
        digest.get("technical_background") or "", DIGEST_TECHNICAL_BACKGROUND_MAX
    )
    digest["scripting_details"] = _truncate(digest.get("scripting_details"), DIGEST_SCRIPTING_DETAILS_MAX)
    try:
        return OnboardingDigest.model_validate(digest).model_dump(mode="json")
    except ValidationError:
        log.warning("intake.onboarding_digest_invalid")
        return {}


def contains_eligibility(value: Any) -> bool:
    """True when ``value`` (nested mappings/sequences) carries a work-authorisation key."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return any(str(k) in ELIGIBILITY_KEYS or contains_eligibility(v) for k, v in value.items())
    if isinstance(value, list | tuple | set | frozenset):
        return any(contains_eligibility(v) for v in value)
    return False


def assert_no_eligibility(value: Any) -> None:
    """Guard for prompt builders (T1/T2): raise when MQ1 data would reach a model."""
    if contains_eligibility(value):
        raise AssertionError("work-authorisation (MQ1) data must never reach a model")


def stored_onboarding(intake: IntakeInterview) -> IntakeOnboarding | None:
    raw = intake.onboarding
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return IntakeOnboarding.model_validate(raw)
    except ValidationError:
        log.warning("intake.onboarding_unreadable", intake_id=str(intake.id))
        return None


def stored_eligibility(intake: IntakeInterview) -> IntakeEligibility | None:
    raw = intake.eligibility
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return IntakeEligibility.model_validate(raw)
    except ValidationError:
        log.warning("intake.eligibility_unreadable", intake_id=str(intake.id))
        return None


def eligibility_view(intake: IntakeInterview, settings: IntakeInviteSettings) -> PortalIntakeEligibility:
    """MQ1 form state for the candidate (their own answers are echoed back for editing)."""
    policy = settings.work_authorization
    answers = stored_eligibility(intake) if policy.enabled else None
    return PortalIntakeEligibility(
        enabled=policy.enabled,
        country=policy.country if policy.enabled else None,
        ask_sponsorship=policy.ask_sponsorship,
        answered=answers is not None,
        authorized=answers.authorized if answers else None,
        needs_sponsorship=answers.needs_sponsorship if answers else None,
    )


@dataclass(slots=True, frozen=True)
class OnboardingResult:
    onboarding: IntakeOnboarding
    eligibility: IntakeEligibility | None
    resubmitted: bool


def build_onboarding(
    intake: IntakeInterview, settings: IntakeInviteSettings, body: IntakeOnboardingIn, *, now: datetime
) -> OnboardingResult:
    """Validate the form against the interview's policy (preset role, MQ1) without writing anything."""
    data = body.model_dump(mode="json", exclude={"eligibility"})
    preset = intake.target_role
    if preset:
        data["target_role"] = preset
        if preset != "other":
            data["target_role_other"] = None
        elif not data.get("target_role_other"):
            raise ValidationFailed(
                "Tell us which role you are targeting.",
                code="target_role_other_required",
                details={"field": "target_role_other"},
            )
    try:
        onboarding = IntakeOnboarding.model_validate({**data, "version": 1, "submitted_at": now})
    except ValidationError as exc:  # pragma: no cover - the request model already validated the same fields
        from app.services.intake.access import intake_validation_error  # noqa: PLC0415

        raise intake_validation_error(exc.errors()) from None
    eligibility: IntakeEligibility | None = None
    policy = settings.work_authorization
    if policy.enabled:
        if body.eligibility is None:
            raise ValidationFailed(
                "Please answer the work-authorisation questions (you can choose to discuss them with the "
                "hiring team).",
                code="eligibility_required",
                details={"field": "eligibility"},
            )
        eligibility = IntakeEligibility(
            country=policy.country or "",
            authorized=body.eligibility.authorized,
            needs_sponsorship=body.eligibility.needs_sponsorship if policy.ask_sponsorship else None,
            answered_at=now,
        )
    return OnboardingResult(
        onboarding=onboarding,
        eligibility=eligibility,
        resubmitted=intake.onboarding_submitted_at is not None,
    )


def apply_onboarding(intake: IntakeInterview, result: OnboardingResult) -> None:
    intake.onboarding = result.onboarding.model_dump(mode="json")
    intake.eligibility = result.eligibility.model_dump(mode="json") if result.eligibility else None
    intake.onboarding_submitted_at = result.onboarding.submitted_at


__all__ = [
    "ELIGIBILITY_KEYS",
    "OnboardingResult",
    "apply_onboarding",
    "assert_no_eligibility",
    "build_onboarding",
    "contains_eligibility",
    "eligibility_view",
    "onboarding_digest",
    "stored_eligibility",
    "stored_onboarding",
]
