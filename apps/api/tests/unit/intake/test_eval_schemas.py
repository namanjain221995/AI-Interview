"""Evaluator wire schemas (INTAKE_INTERVIEW.md 8.4): every field required, aliases constrained, fixtures valid."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.structured import schema_for
from app.services.intake.demo_content import demo_evaluator_outputs
from app.services.intake.eval_schemas import (
    EVALUATOR_SCHEMAS,
    ClaimVerdict,
    CompetencyJudgement,
    EvidenceCite,
    TrainingPlanDraft,
)
from app.services.intake.evidence import assign_aliases

from .eval_fixtures import demo_inputs


def _assert_all_required(node: dict, defs: dict, path: str) -> None:
    if "$ref" in node:
        _assert_all_required(defs[node["$ref"].split("/")[-1]], defs, path)
        return
    for key in ("anyOf", "allOf"):
        for option in node.get(key, []):
            _assert_all_required(option, defs, path)
    if "properties" in node:
        missing = set(node["properties"]) - set(node.get("required", []))
        assert not missing, f"{path}: optional {sorted(missing)}"
        for name, prop in node["properties"].items():
            _assert_all_required(prop, defs, f"{path}.{name}")
    if "items" in node:
        _assert_all_required(node["items"], defs, f"{path}[]")


def _has_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False


@pytest.mark.parametrize("schema", [*EVALUATOR_SCHEMAS.values(), TrainingPlanDraft], ids=lambda s: s.__name__)
def test_every_field_is_required_on_the_wire(schema) -> None:  # noqa: ANN001
    json_schema = schema_for(schema)
    _assert_all_required(json_schema, json_schema.get("$defs", {}), schema.__name__)
    assert not _has_key(json_schema, "propertyNames")  # no dict-typed fields for guided decoding


def test_aliases_and_ranges_are_constrained() -> None:
    # aliases are bounded strings on the wire (no regex keyword); malformed ones are dropped by the validator
    assert not _has_key(schema_for(EvidenceCite), "pattern")
    with pytest.raises(ValidationError):
        EvidenceCite(answer="A1234567", quote="valid quote", supports="positive")
    with pytest.raises(ValidationError):
        EvidenceCite(answer="A3", quote="ok", supports="positive")  # quote too short
    with pytest.raises(ValidationError):
        EvidenceCite(answer="A3", quote="valid quote", supports="neutral")
    base = {
        "competency": "communication", "sufficient": True, "score": 6, "reasoning": "r", "evidence": [],
        "missing_evidence": [], "strengths": [], "improvements": [],
    }  # fmt: skip
    with pytest.raises(ValidationError):
        CompetencyJudgement.model_validate(base)
    with pytest.raises(ValidationError):
        CompetencyJudgement.model_validate({**base, "score": 3, "competency": "technical_reading"})
    with pytest.raises(ValidationError):
        CompetencyJudgement.model_validate(
            {k: v for k, v in base.items() if k != "missing_evidence"} | {"score": 3}
        )
    with pytest.raises(ValidationError):
        ClaimVerdict.model_validate(
            {"claim": "C1", "verdict": "dishonest", "rung_answers": {"ownership": None, "mechanics": None, "stress": None},
             "evidence": [], "note": "", "unresolved": []}
        )  # fmt: skip


def test_fairness_vocabulary_has_no_trait_judgements() -> None:
    text = " ".join(str(schema_for(s)) for s in EVALUATOR_SCHEMAS.values()).lower()
    for banned in ("honest", "deceptive", "nervous", "confident", "emotion", "accent", "energy"):
        if banned == "honest":
            # only the structured answer's gap handling may say "honest" (it describes the answer, not the person)
            assert text.count("honest") == 1
            continue
        assert banned not in text


def test_demo_fixture_outputs_validate() -> None:
    _rows, inputs = demo_inputs()
    assign_aliases(inputs)
    outputs = demo_evaluator_outputs(inputs)
    assert set(outputs) == set(EVALUATOR_SCHEMAS)
    for key, value in outputs.items():
        EVALUATOR_SCHEMAS[key].model_validate(value)
