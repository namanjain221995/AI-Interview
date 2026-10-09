"""Intake contracts (app/schemas/intake.py), the intake RoundConfig validator, admin DTO rules and the intake
API validation-error mapping (INTAKE_INTERVIEW.md 4.1, 15)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.enums import RoundType
from app.schemas.api.intake_admin import IntakeCreate, IntakeOrgSettingsIn, PortalIntakeOut
from app.schemas.intake import (
    DECISION_BANDS,
    INTAKE_CANDIDATE_STAGE_TITLES,
    INTAKE_ERROR_CODES,
    INTAKE_STAGE_KEYS,
    ONBOARDING_DIGEST_KEYS,
    ActivityCaptureRecord,
    DecimalStr,
    DecisionBand,
    EvidenceRef,
    IntakeClaimLimits,
    IntakeEligibility,
    IntakeInviteSettings,
    IntakeInviteSettingsIn,
    IntakeOnboarding,
    IntakeOnboardingIn,
    IntakeQuestionMeta,
    IntakeRoundConfig,
    ResumeReviewDoc,
    ResumeReviewIn,
    ReviewItem,
    StageItemResult,
    TrainingPlan,
    WorkAuthorizationPolicy,
)
from app.schemas.rounds import RoundConfig
from app.services.intake import kit as kit_module
from app.services.intake.access import intake_validation_error
from app.services.intake.scoring import INTAKE_COMPETENCY_KEYS


def onboarding_payload(**overrides: object) -> dict:
    payload = {
        "full_name": "Asha Rao",
        "target_role": "ai_ml",
        "open_to_ai_ml": "yes",
        "technical_background": "Two years of Python scripting for data cleaning.",
        "primary_language": "python",
        "self_rating": 6,
        "scripting": "regular",
        "years_hands_on": 2.5,
        "experience_level_comfort": "unsure",
        "training_commitment": "full",
        "weekly_hours": 30,
        "availability": "within_2_weeks",
        "accuracy_confirmed": True,
    }
    payload.update(overrides)
    return payload


def error_type(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return exc.value.errors()[0]["type"]


# ------------------------------------------------------------------------------ round configuration


def test_intake_round_config_defaults_match_the_contract() -> None:
    cfg = IntakeRoundConfig()
    assert cfg.kit_version == "v1" and cfg.scenarios == 2 and cfg.closing_questions_max == 3
    assert cfg.followup_limits.model_dump() == {
        "greeting": 2, "resume_clarify_per_rung": 1, "mandatory": 1, "topic": 1, "learning": 2,
    }  # fmt: skip
    assert (cfg.claims.min_claims, cfg.claims.max_claims) == (2, 3)
    assert (cfg.passage_drill_a, cfg.passage_drill_b, cfg.topic_card) == ("auto", "auto", "auto")
    assert cfg.work_authorization is None
    with pytest.raises(ValidationError):
        IntakeRoundConfig(passage_drill_a="C")  # passage C is relay-only
    with pytest.raises(ValidationError):
        IntakeRoundConfig(passage_drill_b="D")  # passage D is read-aloud only
    with pytest.raises(ValidationError):
        IntakeRoundConfig(followup_limits={"greeting": 4})
    with pytest.raises(ValidationError) as exc:
        IntakeClaimLimits(min_claims=3, max_claims=2)
    assert error_type(exc) == "invalid_claim_limits"


def test_work_authorization_needs_a_country_when_enabled() -> None:
    assert WorkAuthorizationPolicy().enabled is False
    with pytest.raises(ValidationError) as exc:
        WorkAuthorizationPolicy(enabled=True)
    assert error_type(exc) == "work_authorization_country_required"
    with pytest.raises(ValidationError):
        WorkAuthorizationPolicy(enabled=True, country="   ")
    policy = WorkAuthorizationPolicy(enabled=True, country="  the United States ", ask_sponsorship=False)
    assert policy.country == "the United States" and policy.variant == "default"
    with pytest.raises(ValidationError):
        WorkAuthorizationPolicy(enabled=True, country="X", variant="citizenship")


def test_round_config_intake_defaults_and_limits() -> None:
    cfg = RoundConfig(round_type=RoundType.INTAKE, name="Intake")
    assert (cfg.duration_minutes, cfg.max_duration_minutes) == (45, 50)
    assert isinstance(cfg.intake, IntakeRoundConfig) and cfg.resume_validation.enabled is True
    assert (
        RoundConfig(
            round_type="intake", name="x", duration_minutes=50, max_duration_minutes=90
        ).max_duration_minutes
        == 55
    )
    assert (
        RoundConfig(
            round_type="intake", name="x", duration_minutes=35, max_duration_minutes=10
        ).max_duration_minutes
        == 35
    )
    assert RoundConfig(round_type="intake", name="x", max_duration_minutes=40).max_duration_minutes == 45
    forced = RoundConfig(round_type="intake", name="x", resume_validation={"enabled": False})
    assert forced.resume_validation.enabled is True
    for bad, message in (
        ({"duration_minutes": 30}, "Intake rounds last 35-50 minutes"),
        ({"duration_minutes": 55}, "Intake rounds last 35-50 minutes"),
        ({"coding": {"enabled": True}}, "coding"),
        ({"candidate_selectable": True}, "selected by the candidate"),
    ):
        with pytest.raises(ValidationError) as exc:
            RoundConfig(round_type="intake", name="x", **bad)
        assert message in str(exc.value)
    round_trip = RoundConfig.model_validate(json.loads(cfg.model_dump_json()))
    assert round_trip == cfg


def test_round_config_other_types_are_untouched() -> None:
    cfg = RoundConfig(round_type=RoundType.TECHNICAL_SCREENING, name="Tech", duration_minutes=20)
    assert cfg.intake is None and cfg.duration_minutes == 20
    assert RoundConfig(round_type="coding", name="c", coding={"enabled": True}).coding.enabled
    assert RoundType.INTAKE.value == "intake" and list(RoundType)[-1] is RoundType.INTAKE


# --------------------------------------------------------------------------------- invite settings


def test_invite_settings_ranges_and_defaults() -> None:
    settings = IntakeInviteSettingsIn()
    assert settings.model_dump(exclude={"scheduled_for", "assigned_reviewer_id", "work_authorization"}) == {
        "duration_minutes": 45, "presence": "orb", "max_reconnects": 10, "expires_in_hours": 168,
        "require_access_code": False, "resume_required": True, "notify_on_completion": True,
    }  # fmt: skip
    for bad in (
        {"duration_minutes": 34}, {"duration_minutes": 51}, {"max_reconnects": 21}, {"expires_in_hours": 0},
        {"expires_in_hours": 1441}, {"presence": "avatar"}, {"unknown": 1},
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            IntakeInviteSettingsIn(**bad)
    stored = IntakeInviteSettings()
    assert stored.work_authorization == WorkAuthorizationPolicy() and stored.program_name
    assert IntakeInviteSettingsIn(presence="none").model_fields_set == {"presence"}


# ------------------------------------------------------------------------------------- onboarding


def test_onboarding_input_rules() -> None:
    body = IntakeOnboardingIn.model_validate(onboarding_payload(full_name="  Asha\tRao  "))
    assert body.full_name == "Asha Rao" and body.eligibility is None
    with pytest.raises(ValidationError) as exc:
        IntakeOnboardingIn.model_validate(onboarding_payload(target_role="other"))
    assert error_type(exc) == "target_role_other_required"
    with pytest.raises(ValidationError) as exc:
        IntakeOnboardingIn.model_validate(
            onboarding_payload(primary_language="other", primary_language_other=" ")
        )
    assert error_type(exc) == "primary_language_other_required"
    cleared = IntakeOnboardingIn.model_validate(
        onboarding_payload(target_role_other="ignored", primary_language_other="Rust")
    )
    assert cleared.target_role_other is None and cleared.primary_language_other is None
    other = IntakeOnboardingIn.model_validate(
        onboarding_payload(
            target_role="other",
            target_role_other="QA",
            primary_language="other",
            primary_language_other="Rust",
        )
    )
    assert (other.target_role_other, other.primary_language_other) == ("QA", "Rust")
    for bad in (
        {"accuracy_confirmed": False}, {"self_rating": 0}, {"self_rating": 11}, {"years_hands_on": 51},
        {"years_hands_on": float("nan")}, {"weekly_hours": 81}, {"technical_background": ""},
        {"technical_background": "x" * 1501}, {"full_name": "A"}, {"target_role": "pm"},
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            IntakeOnboardingIn.model_validate(onboarding_payload(**bad))
    with_eligibility = IntakeOnboardingIn.model_validate(
        onboarding_payload(eligibility={"authorized": None, "needs_sponsorship": False})
    )
    assert with_eligibility.eligibility is not None and with_eligibility.eligibility.authorized is None


def test_stored_onboarding_and_eligibility_documents() -> None:
    now = datetime.now(UTC)
    stored = IntakeOnboarding.model_validate(
        {**onboarding_payload(available_from="2026-11-01"), "submitted_at": now.isoformat()}
    )
    assert stored.version == 1 and stored.available_from == date(2026, 11, 1)
    assert "eligibility" not in IntakeOnboarding.model_fields
    with pytest.raises(ValidationError):
        IntakeOnboarding.model_validate({**onboarding_payload(), "submitted_at": now, "eligibility": {}})
    eligibility = IntakeEligibility(
        country="Canada", authorized=True, needs_sponsorship=None, answered_at=now
    )
    assert eligibility.model_dump(mode="json")["version"] == 1
    assert "full_name" not in ONBOARDING_DIGEST_KEYS
    assert not {"eligibility", "authorized", "needs_sponsorship", "work_authorization"} & set(
        ONBOARDING_DIGEST_KEYS
    )


# ---------------------------------------------------------------------------------- résumé review


def test_resume_review_document_refs_and_cleaning() -> None:
    doc = ResumeReviewDoc.model_validate(
        {
            "roles": [
                {
                    "ref": "r1",
                    "title": " Data analyst ",
                    "company": "Acme",
                    "technologies": ["SQL", "sql", " "],
                },
                {"ref": "new", "title": "Intern", "company": "Beta"},
                {"ref": "n4", "title": "Volunteer", "company": ""},
            ],
            "skills": ["pandas", "Pandas", "numpy"],
            "education": [{"ref": "e1", "institution": "City College", "degree": "", "field": None}],
        }
    )
    assert doc.roles[0].title == "Data analyst" and doc.roles[0].technologies == ["SQL"]
    assert doc.skills == ["pandas", "numpy"] and doc.education[0].degree is None
    with pytest.raises(ValidationError) as exc:
        ResumeReviewDoc.model_validate({"roles": [{"ref": "p1", "title": "x"}]})
    assert error_type(exc) == "ref_section_mismatch"
    with pytest.raises(ValidationError) as exc:
        ResumeReviewDoc.model_validate({"projects": [{"ref": "p1", "name": "a"}, {"ref": "p1", "name": "b"}]})
    assert error_type(exc) == "duplicate_ref"
    for bad in (
        {"roles": [{"ref": "r1", "title": ""}]},
        {"roles": [{"ref": "x1", "title": "t"}]},
        {"skills": ["s"] * 41 + [f"s{i}" for i in range(41)]},
        {"education": [{"ref": f"e{i}", "institution": "u"} for i in range(1, 7)]},
        {"experience_years": 61},
    ):
        with pytest.raises(ValidationError):
            ResumeReviewDoc.model_validate(bad)
    assert "start" not in ResumeReviewDoc.model_fields["education"].annotation.__args__[0].model_fields
    with pytest.raises(ValidationError) as exc:
        ResumeReviewIn(action="confirm")
    assert error_type(exc) == "document_required"
    assert ResumeReviewIn(action="skip").document is None


# ------------------------------------------------------------------------- cross-track documents


def test_cross_track_documents_round_trip() -> None:
    qid, aid = uuid.uuid4(), uuid.uuid4()
    item = StageItemResult(item_key="greeting.intro", mandatory=True, status="answered", question_ids=[qid])
    assert json.loads(item.model_dump_json())["question_ids"] == [str(qid)]
    with pytest.raises(ValidationError):
        StageItemResult(item_key="x", status="done")
    capture = ActivityCaptureRecord.model_validate(
        {
            "phase": "read",
            "started_ms": 1000,
            "ended_ms": 61000,
            "end_reason": "finish_button",
            "segments": [{"start_ms": 1000, "end_ms": 4000, "text": "hello", "extra_runtime_key": 1}],
            "pauses": [{"start_ms": 4000, "end_ms": 5000, "duration_ms": 1000}],
        }
    )
    assert capture.segments[0].text == "hello" and capture.attempt == 1
    assert "extra_runtime_key" not in capture.model_dump()["segments"][0]
    ref = EvidenceRef(source="answer", stage="resume", answer_id=aid, quote="I built it", supports="positive")
    review = ReviewItem(
        id="ri_0123456789",
        kind="claims_not_supported",
        severity="important",
        title="t",
        source="rule",
        evidence=[ref],
    )
    assert review.status == "open"
    with pytest.raises(ValidationError):
        ReviewItem(id="ri_xyz", kind="claims_not_supported", severity="important", title="t", source="rule")
    meta = IntakeQuestionMeta(stage="mandatory", item_key="mandatory.mq3", item_kind="mq", mq="mq3")
    assert meta.answer_mode == "standard" and meta.deviations == []
    with pytest.raises(ValidationError):
        IntakeQuestionMeta(
            stage="mandatory", item_key="mandatory.mq1", item_kind="mq", mq="mq1"
        )  # never live
    module = {
        "key": "python_fundamentals",
        "title": "Python fundamentals",
        "priority": 1,
        "why": "w",
        "objectives": ["a", "b"],
        "milestones": [{"week": w, "milestone": "m", "measure": "x"} for w in (1, 2, 3)],
    }
    assert TrainingPlan(generated_by="deterministic", summary="s", modules=[module]).modules[0].priority == 1
    with pytest.raises(ValidationError):
        TrainingPlan(generated_by="model", summary="s", modules=[{**module, "objectives": ["only one"]}])
    with pytest.raises(ValidationError):
        TrainingPlan(
            generated_by="model", summary="s", modules=[{**module, "milestones": module["milestones"][:2]}]
        )


def test_decimals_serialise_as_strings() -> None:
    class Probe(BaseModel):
        total: DecimalStr
        partial: DecimalStr | None = None

    assert Probe(total=Decimal("3.6700")).model_dump() == {"total": "3.6700", "partial": None}
    assert json.loads(Probe(total="0.20", partial=Decimal("4.2")).model_dump_json()) == {
        "total": "0.20",
        "partial": "4.2",
    }


def test_enumerations_agree_with_the_foundations() -> None:
    assert INTAKE_STAGE_KEYS == kit_module.STAGE_KEYS
    assert tuple(INTAKE_CANDIDATE_STAGE_TITLES) == INTAKE_STAGE_KEYS
    assert set(DECISION_BANDS) == {b.value for b in DecisionBand}
    assert len(INTAKE_COMPETENCY_KEYS) == 7


# ------------------------------------------------------------------------------------ admin DTOs


def test_create_requires_exactly_one_candidate() -> None:
    job_id = uuid.uuid4()
    with pytest.raises(ValidationError) as exc:
        IntakeCreate(job_id=job_id)
    assert error_type(exc) == "candidate_or_candidate_id_required"
    with pytest.raises(ValidationError):
        IntakeCreate(
            job_id=job_id, candidate_id=uuid.uuid4(), candidate={"full_name": "A", "email": "a@x.io"}
        )
    created = IntakeCreate(job_id=job_id, candidate={"full_name": "A B", "email": " A@Example.COM "})
    assert (
        created.candidate is not None and created.candidate.email == "a@example.com" and created.send is True
    )
    with pytest.raises(ValidationError):
        IntakeCreate(job_id=job_id, candidate={"full_name": "A", "email": "not-an-email"})


def test_org_settings_input_limits() -> None:
    body = IntakeOrgSettingsIn(program_name="AI/ML Training Program")
    assert body.work_authorization.enabled is False and body.defaults.duration_minutes == 45
    with pytest.raises(ValidationError):
        IntakeOrgSettingsIn(program_name="p", approved_info=[{"topic": "t", "answer": "a"}] * 31)
    with pytest.raises(ValidationError):
        IntakeOrgSettingsIn(program_name="p", approved_info=[{"topic": "t" * 121, "answer": "a"}])
    with pytest.raises(ValidationError):
        IntakeOrgSettingsIn(program_name="p", defaults={"duration_minutes": 60})
    assert "target_role_preset" in PortalIntakeOut.model_fields


# --------------------------------------------------------------------------- validation mapping


def test_intake_validation_error_maps_codes_and_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        IntakeOrgSettingsIn.model_validate(
            {"program_name": "p", "work_authorization": {"enabled": True, "country": None}}
        )
    error = intake_validation_error([{**e, "loc": ("body", *e["loc"])} for e in exc.value.errors()])
    assert error.status_code == 422 and error.code == "work_authorization_country_required"
    assert error.details["field"] == "work_authorization.country"
    with pytest.raises(ValidationError) as exc:
        IntakeOnboardingIn.model_validate(onboarding_payload(target_role="other"))
    error = intake_validation_error([{**e, "loc": ("body", *e["loc"])} for e in exc.value.errors()])
    assert error.code == "target_role_other_required" and error.details["field"] == "target_role_other"
    with pytest.raises(ValidationError) as exc:
        IntakeOnboardingIn.model_validate(onboarding_payload(self_rating=12))
    error = intake_validation_error([{**e, "loc": ("body", *e["loc"])} for e in exc.value.errors()])
    assert error.code == "validation_failed" and error.details["field"] == "self_rating"
    assert all("input" not in e for e in error.details["errors"])
    with pytest.raises(ValidationError) as exc:
        ResumeReviewDoc.model_validate({"roles": [{"ref": "r1", "title": "t", "technologies": ["x" * 81]}]})
    error = intake_validation_error(
        [{**e, "loc": ("body", "document", *e["loc"])} for e in exc.value.errors()]
    )
    assert error.details["field"] == "document.roles[0].technologies[0]"
    assert "work_authorization_country_required" in INTAKE_ERROR_CODES
