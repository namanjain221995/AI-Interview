"""Human review items (INTAKE_INTERVIEW.md 8.8): every kit flag and platform item, deterministic ids, never an
automatic outcome, MQ1 never copied."""

from __future__ import annotations

import copy
import re

from app.schemas.intake import ReviewItem
from app.services.intake.demo_content import demo_evaluator_outputs
from app.services.intake.evidence import assign_aliases
from app.services.intake.review_items import KIT_FLAGS, report_safe_items, review_item_id

from .eval_fixtures import demo_inputs, evaluate_demo


def _raw() -> dict:
    _rows, inputs = demo_inputs()
    assign_aliases(inputs)
    return demo_evaluator_outputs(inputs)


def _kinds(items: list[dict]) -> dict[str, dict]:
    return {i["kind"]: i for i in items}


def test_items_validate_against_the_contract_and_never_decide() -> None:
    result = evaluate_demo()
    for item in result.review_items:
        ReviewItem.model_validate(item)
        assert item["status"] == "open"
        assert re.fullmatch(r"ri_[0-9a-f]{10}", item["id"])
        assert item["kit_flag"] in (KIT_FLAGS | {None})
    kinds = _kinds(result.review_items)
    assert set(kinds) == {"work_authorization_review", "integrity_observation"}
    assert kinds["work_authorization_review"]["severity"] == "info"


def test_ids_are_deterministic_across_runs() -> None:
    first = [i["id"] for i in evaluate_demo().review_items]
    second = [i["id"] for i in evaluate_demo().review_items]
    assert first == second
    assert review_item_id("x", None, "k", [{"answer_id": "b"}, {"answer_id": "a"}]) == review_item_id(
        "x", None, "k", [{"answer_id": "a"}, {"answer_id": "b"}]
    )


def test_claims_not_supported_from_claim_verdicts_and_self_rating() -> None:
    raw = _raw()
    resume = copy.deepcopy(raw["resume_evidence"])
    resume["claims"][1]["verdict"] = "unsupported"
    technical = copy.deepcopy(raw["technical"])
    technical["verification"]["quality"] = "incorrect"
    result = evaluate_demo(overrides={"resume_evidence": resume, "technical": technical})
    items = [i for i in result.review_items if i["kind"] == "claims_not_supported"]
    assert len(items) == 2
    claim = next(i for i in items if "C2" in i["detail"])
    assert claim["severity"] == "important" and claim["evidence"] and claim["source"] == "rule"
    rating = next(i for i in items if "Self-rating 7/10" in i["detail"])
    assert rating["competency_keys"] == ["technical"]


def test_unsupported_claim_without_valid_evidence_raises_no_item() -> None:
    raw = _raw()
    resume = copy.deepcopy(raw["resume_evidence"])
    resume["claims"][1]["verdict"] = "unsupported"
    resume["claims"][1]["evidence"] = [
        {"answer": "A7", "quote": "made up words never spoken", "supports": "negative"}
    ]
    result = evaluate_demo(overrides={"resume_evidence": resume})
    assert "claims_not_supported" not in _kinds(result.review_items)


def test_evaluator_drafts_need_valid_aliases() -> None:
    raw = _raw()
    resume = copy.deepcopy(raw["resume_evidence"])
    valid_alias = resume["claims"][1]["rung_answers"]["ownership"]
    resume["review_items"] = [
        {
            "kind": "claims_not_supported",
            "note": "The 40% figure was not explained.",
            "answers": [valid_alias],
        },
        {"kind": "claims_not_supported", "note": "Out of scope alias.", "answers": ["A1"]},
        {"kind": "evaluator_note", "note": "Context only.", "answers": [valid_alias]},
    ]
    result = evaluate_demo(overrides={"resume_evidence": resume})
    drafts = [i for i in result.review_items if i["source"] == "evaluator"]
    assert len(drafts) == 1 and drafts[0]["detail"] == "The 40% figure was not explained."


def test_reading_not_completed_and_asr_quality() -> None:
    result = evaluate_demo()
    reading = result.inputs.activities["reading_aloud"]
    reading.capture["stt_degraded"] = True
    reading.transcript_final = None  # only the live segments
    from app.services.intake.relay import drill_a_score, reading_metrics
    from app.services.intake.review_items import build_review_items

    reading.transcript_live = "Gradient descent is an optimization algorithm."
    metrics = reading_metrics(reading, result.kit.passage("B"))
    drill_a = drill_a_score(reading, metrics)
    items = build_review_items(
        result.inputs,
        result.aliases,
        competencies=result.validation.competencies,
        claims=result.validation.claims,
        findings=[],
        drill_a=drill_a,
        drill_b=result.drill_b,
        kit=result.kit,
    )
    kinds = _kinds(items)
    assert kinds["reading_not_completed"]["severity"] == "important"
    assert "Pronunciation and accent are never part" in kinds["reading_not_completed"]["detail"]
    assert kinds["asr_quality_low"]["severity"] == "attention"
    assert "live recognition segments" in kinds["asr_quality_low"]["detail"]


def test_work_authorization_severity_without_copying_answers() -> None:
    concern = evaluate_demo(
        eligibility={"country": "the United States", "authorized": False, "needs_sponsorship": True}
    )
    item = _kinds(concern.review_items)["work_authorization_review"]
    assert item["severity"] == "important"
    assert "False" not in item["detail"] and "United States" not in item["detail"]
    assert item["evidence"][0]["source"] == "onboarding" and item["evidence"][0]["quote"] is None
    disabled = evaluate_demo(eligibility_enabled=False)
    assert "work_authorization_review" not in _kinds(disabled.review_items)
    safe = report_safe_items(concern.review_items)
    wa = _kinds(safe)["work_authorization_review"]
    assert wa["severity"] == "info" and wa["evidence"] == [] and wa["id"] == item["id"]


def test_mandatory_mismatch_from_the_onboarding_form() -> None:
    result = evaluate_demo()
    from app.services.intake.review_items import build_review_items

    onboarding = dict(
        result.rows.onboarding, training_commitment="cannot", target_role="data_analyst", open_to_ai_ml="no"
    )
    items = build_review_items(
        result.inputs,
        result.aliases,
        competencies=result.validation.competencies,
        claims=[],
        findings=[],
        drill_a=result.drill_a,
        drill_b=result.drill_b,
        onboarding=onboarding,
        kit=result.kit,
    )
    item = _kinds(items)["mandatory_mismatch"]
    assert "cannot commit to the training" in item["detail"] and "non-AI/ML target" in item["detail"]


def test_communication_unclear_needs_two_stages() -> None:
    raw = _raw()
    communication = copy.deepcopy(raw["communication"])
    communication["judgement"]["score"] = 1
    communication["judgement"]["evidence"] = [
        dict(e, supports="negative") for e in communication["judgement"]["evidence"]
    ]
    result = evaluate_demo(overrides={"communication": communication})
    item = _kinds(result.review_items)["communication_unclear"]
    assert item["severity"] == "important" and len({e["stage"] for e in item["evidence"]}) >= 2
    assert "Accent, pronunciation and appearance" in item["detail"]


def test_platform_items_from_findings_and_activities() -> None:
    incomplete = evaluate_demo("incomplete")
    kinds = {i["kind"] for i in incomplete.review_items}
    assert {"stage_incomplete", "mandatory_item_missing"} <= kinds
    assert all(
        i["severity"] == "attention"
        for i in incomplete.review_items
        if i["kind"] in {"stage_incomplete", "mandatory_item_missing"}
    )
    fallback = evaluate_demo(drop=("composure",))
    assert "evaluator_fallback" in {i["kind"] for i in fallback.review_items}
    interrupted = evaluate_demo()
    interrupted.inputs.activities["topic_card"].status = "interrupted"
    from app.services.intake.review_items import build_review_items

    items = build_review_items(
        interrupted.inputs,
        interrupted.aliases,
        competencies=interrupted.validation.competencies,
        claims=[],
        findings=[],
        drill_a=interrupted.drill_a,
        drill_b=interrupted.drill_b,
        kit=interrupted.kit,
    )
    assert _kinds(items)["activity_interrupted"]["competency_keys"] == ["structured_answer"]


def test_items_are_sorted_by_severity() -> None:
    raw = _raw()
    resume = copy.deepcopy(raw["resume_evidence"])
    resume["claims"][1]["verdict"] = "inconsistent"
    result = evaluate_demo("incomplete", overrides={"resume_evidence": resume})
    order = {"important": 0, "attention": 1, "info": 2}
    severities = [order[i["severity"]] for i in result.review_items]
    assert severities == sorted(severities)
