"""Onboarding form service (app/services/intake/onboarding.py): validation against the frozen policy, the
prompt-safe digest (never MQ1, never the name) and the MQ1 privacy guard (INTAKE_INTERVIEW.md 10.4, 15.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import ValidationFailed
from app.db.models import IntakeInterview
from app.schemas.intake import (
    DIGEST_SCRIPTING_DETAILS_MAX,
    DIGEST_TECHNICAL_BACKGROUND_MAX,
    ONBOARDING_DIGEST_KEYS,
    IntakeInviteSettings,
    IntakeOnboarding,
    IntakeOnboardingIn,
    WorkAuthorizationPolicy,
)
from app.services.intake.onboarding import (
    apply_onboarding,
    assert_no_eligibility,
    build_onboarding,
    contains_eligibility,
    eligibility_view,
    onboarding_digest,
    stored_eligibility,
    stored_onboarding,
)

from .test_intake_schemas import onboarding_payload

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def intake_row(**overrides: object) -> IntakeInterview:
    values = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "status": "active",
        "title": "Intake Interview",
        "settings": {},
    }
    values.update(overrides)
    return IntakeInterview(**values)


def settings(**policy: object) -> IntakeInviteSettings:
    return IntakeInviteSettings(
        work_authorization=WorkAuthorizationPolicy(**policy) if policy else WorkAuthorizationPolicy()
    )


def body(**overrides: object) -> IntakeOnboardingIn:
    return IntakeOnboardingIn.model_validate(onboarding_payload(**overrides))


# ------------------------------------------------------------------------------------------ digest


def test_digest_is_a_whitelist_without_name_or_eligibility() -> None:
    result = build_onboarding(intake_row(), settings(), body(), now=NOW)
    digest = onboarding_digest(result.onboarding)
    assert tuple(digest) == ONBOARDING_DIGEST_KEYS
    assert "full_name" not in digest and "Asha" not in str(digest)
    assert not contains_eligibility(digest)
    assert digest["primary_language"] == "Python" and digest["target_role"] == "ai_ml"
    assert digest["years_hands_on"] == 2.5 and digest["weekly_hours"] == 30


def test_digest_truncates_long_texts_and_uses_language_labels() -> None:
    raw = {
        **onboarding_payload(
            primary_language="csharp",
            technical_background="b" * 1500,
            scripting_details="s" * 500,
        ),
        "submitted_at": NOW.isoformat(),
        "eligibility": {"authorized": False},  # a raw mapping with MQ1 data: ignored by the whitelist
        "authorized": False,
    }
    digest = onboarding_digest(raw)
    assert digest["primary_language"] == "C#"
    assert len(digest["technical_background"]) == DIGEST_TECHNICAL_BACKGROUND_MAX
    assert digest["technical_background"].endswith("…")
    assert len(digest["scripting_details"]) == DIGEST_SCRIPTING_DETAILS_MAX
    assert not contains_eligibility(digest)
    other = onboarding_digest(body(primary_language="other", primary_language_other="Rust"))
    assert other["primary_language"] == "Rust"
    assert onboarding_digest(None) == {} and onboarding_digest({"target_role": "astronaut"}) == {}


def test_eligibility_guard() -> None:
    assert contains_eligibility({"a": [{"b": {"needs_sponsorship": None}}]})
    assert contains_eligibility({"eligibility": None})
    assert not contains_eligibility(
        {"onboarding_digest": {"target_role": "ai_ml"}, "work_authorization_enabled": True}
    )
    with pytest.raises(AssertionError):
        assert_no_eligibility({"context": {"authorized": True}})
    assert_no_eligibility({"context": "nothing to hide"})


# ------------------------------------------------------------------------------------- validation


def test_mq1_disabled_ignores_eligibility_answers() -> None:
    result = build_onboarding(
        intake_row(), settings(), body(eligibility={"authorized": True, "needs_sponsorship": True}), now=NOW
    )
    assert result.eligibility is None and result.resubmitted is False
    assert result.onboarding.submitted_at == NOW and result.onboarding.version == 1


def test_mq1_enabled_requires_answers_and_respects_the_policy() -> None:
    policy = {"enabled": True, "country": "Canada", "ask_sponsorship": True}
    with pytest.raises(ValidationFailed) as exc:
        build_onboarding(intake_row(), settings(**policy), body(), now=NOW)
    assert exc.value.code == "eligibility_required" and exc.value.details["field"] == "eligibility"
    result = build_onboarding(
        intake_row(),
        settings(**policy),
        body(eligibility={"authorized": None, "needs_sponsorship": False}),
        now=NOW,
    )
    assert result.eligibility is not None
    assert result.eligibility.model_dump(mode="json") == {
        "version": 1, "country": "Canada", "authorized": None, "needs_sponsorship": False,
        "answered_at": NOW.isoformat().replace("+00:00", "Z"),
    }  # fmt: skip
    no_sponsorship = build_onboarding(
        intake_row(),
        settings(enabled=True, country="Canada", ask_sponsorship=False),
        body(eligibility={"authorized": True, "needs_sponsorship": True}),
        now=NOW,
    )
    assert no_sponsorship.eligibility is not None and no_sponsorship.eligibility.needs_sponsorship is None


def test_staff_preset_target_role_wins() -> None:
    preset = build_onboarding(
        intake_row(target_role="data_analyst"), settings(), body(target_role="ai_ml"), now=NOW
    )
    assert preset.onboarding.target_role == "data_analyst" and preset.onboarding.target_role_other is None
    with pytest.raises(ValidationFailed) as exc:
        build_onboarding(intake_row(target_role="other"), settings(), body(target_role="ai_ml"), now=NOW)
    assert exc.value.code == "target_role_other_required"
    other = build_onboarding(
        intake_row(target_role="other"),
        settings(),
        body(target_role="other", target_role_other="QA lead"),
        now=NOW,
    )
    assert other.onboarding.target_role_other == "QA lead"


def test_apply_stores_documents_and_marks_resubmission() -> None:
    row = intake_row()
    first = build_onboarding(
        row,
        settings(enabled=True, country="Canada"),
        body(eligibility={"authorized": True, "needs_sponsorship": False}),
        now=NOW,
    )
    apply_onboarding(row, first)
    assert row.onboarding_submitted_at == NOW and "eligibility" not in row.onboarding
    assert stored_onboarding(row) == first.onboarding and isinstance(stored_onboarding(row), IntakeOnboarding)
    assert stored_eligibility(row) is not None and stored_eligibility(row).authorized is True
    second = build_onboarding(row, settings(), body(self_rating=8), now=NOW)
    assert second.resubmitted is True
    apply_onboarding(row, second)
    assert row.eligibility is None and stored_onboarding(row).self_rating == 8


def test_eligibility_view_for_the_candidate() -> None:
    row = intake_row()
    view = eligibility_view(row, settings())
    assert view.model_dump() == {
        "enabled": False, "country": None, "ask_sponsorship": True, "answered": False, "authorized": None,
        "needs_sponsorship": None,
    }  # fmt: skip
    enabled = settings(enabled=True, country="Canada")
    apply_onboarding(row, build_onboarding(row, enabled, body(eligibility={"authorized": False}), now=NOW))
    view = eligibility_view(row, enabled)
    assert view.enabled and view.country == "Canada" and view.answered and view.authorized is False
    assert stored_onboarding(intake_row(onboarding={"broken": True})) is None
