"""Training plan (INTAKE_INTERVIEW.md 9.2, shape 15.10): deterministic selection, personalisation, fallback."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.base import AIOutputInvalid, AIUnavailable
from app.domain.enums import ModelTask
from app.schemas.intake import TrainingPlan
from app.services.intake.eval_schemas import TrainingPlanDraft
from app.services.intake.training import (
    CATALOG,
    deterministic_plan,
    generate_training_plan,
    merge_plan,
    select_modules,
    training_messages,
)

ALL_GOOD = {
    "communication": Decimal(5),
    "technical_reading": Decimal("4.4"),
    "structured_answer": Decimal(4),
    "technical": Decimal(4),
    "trainability": Decimal(5),
    "composure": Decimal(4),
    "adaptability": Decimal(5),
}


def _keys(**scores) -> list[str]:  # noqa: ANN003
    merged = {**ALL_GOOD, **{k: (Decimal(str(v)) if v is not None else None) for k, v in scores.items()}}
    return [
        m.key for m in select_modules(merged, drill_b=scores.get("drill_b"), language=scores.get("language"))
    ]


def test_ai_ml_fundamentals_is_always_included() -> None:
    assert _keys() == ["ai_ml_fundamentals"]


def test_low_or_insufficient_competencies_map_to_modules() -> None:
    assert set(_keys(communication=3)) == {"communication_practice", "ai_ml_fundamentals"}
    assert "structured_speaking" in _keys(structured_answer=None)  # insufficient counts as 3
    assert "learning_habits" in _keys(trainability=2)
    assert "interview_preparation" in _keys(composure=1)
    assert _keys(adaptability=2)[0] == "ai_ml_fundamentals"


def test_technical_maps_to_the_language_and_reading_to_explanations() -> None:
    merged = {**ALL_GOOD, "technical": Decimal(2), "technical_reading": Decimal(3)}
    keys = [m.key for m in select_modules(merged, drill_b=3, language="python")]
    assert {"python_fundamentals", "technical_reading_practice", "technical_explanations"} <= set(keys)
    keys = [m.key for m in select_modules(merged, drill_b=5, language="java")]
    assert "programming_fundamentals" in keys and "technical_explanations" not in keys
    assert "sql_fundamentals" in [m.key for m in select_modules(merged, drill_b=5, language="SQL")]


def test_at_most_five_ordered_by_weighted_gap_and_ai_ml_kept() -> None:
    low = {k: Decimal(1) for k in ALL_GOOD}
    modules = select_modules(low, drill_b=1, language="python")
    assert len(modules) == 5
    assert modules[0].key in {
        "communication_practice",
        "technical_reading_practice",
        "technical_explanations",
    }
    assert any(m.key == "ai_ml_fundamentals" for m in modules)
    gaps = [m.gap for m in modules[:-1]]
    assert gaps == sorted(gaps, reverse=True)


def test_deterministic_plan_matches_the_contract() -> None:
    modules = select_modules({**ALL_GOOD, "communication": Decimal(2)})
    plan = deterministic_plan(modules, improvements={"communication": ["Lead with the main point."]})
    TrainingPlan.model_validate(plan)
    assert plan["generated_by"] == "deterministic"
    assert plan["modules"][0]["priority"] == 1
    assert "Communication & Presentation" in plan["modules"][0]["why"]
    assert all(len(m["milestones"]) >= 3 for m in plan["modules"])
    for definition in CATALOG.values():
        assert 2 <= len(definition.objectives) <= 4 and {w for w, *_ in definition.milestones} == {1, 2, 3}


def test_merge_keeps_selection_and_neutralises_wording() -> None:
    modules = select_modules({**ALL_GOOD, "communication": Decimal(2)})
    base = deterministic_plan(modules)
    draft = TrainingPlanDraft.model_validate(
        {
            "summary": "Focus on structure.",
            "modules": [
                {
                    "key": "communication_practice",
                    "why": "The candidate failed to structure answers.",
                    "objectives": ["Lead with the point", "Summarise in a minute"],
                    "milestones": [
                        {"week": 1, "milestone": "Practice", "measure": "recording"},
                        {"week": 2, "milestone": "Summaries", "measure": "peer"},
                        {"week": 3, "milestone": "Mock", "measure": "mentor"},
                    ],
                },
                {"key": "unknown_module", "why": "x", "objectives": ["a", "b"], "milestones": [
                    {"week": 1, "milestone": "m", "measure": "x"}, {"week": 2, "milestone": "m", "measure": "x"},
                    {"week": 3, "milestone": "m", "measure": "x"}]},
            ],
        }
    )  # fmt: skip
    plan = merge_plan(base, draft)
    TrainingPlan.model_validate(plan)
    assert plan["generated_by"] == "model"
    assert [m["key"] for m in plan["modules"]] == [m["key"] for m in base["modules"]]
    assert "failed to" not in plan["modules"][0]["why"]


def test_prompt_never_carries_band_decision_or_eligibility() -> None:
    modules = select_modules({**ALL_GOOD, "technical": Decimal(2)}, language="python")
    messages = training_messages(
        modules, strengths={"technical": ["Solid SQL"]}, improvements={"technical": ["x"]}
    )
    text = " ".join(m["content"] for m in messages)
    assert "<interview_notes>" in text and "python_fundamentals" in text
    assert "accept" not in text.lower() and "eligib" not in text.lower() and "sponsor" not in text.lower()


async def test_generation_with_model_and_fallbacks(mock_ai) -> None:
    scores = {**ALL_GOOD, "composure": Decimal(2)}
    common = dict(
        strengths={}, improvements={}, drill_b=4, language="python", session_id="s", organization_id="o"
    )
    outcome = await generate_training_plan(scores=scores, use_model=False, **common)
    assert outcome.plan["generated_by"] == "deterministic" and outcome.error is None
    mock_ai.llm.script(ModelTask.REPORT_GENERATION, AIUnavailable("down"))
    outcome = await generate_training_plan(scores=scores, **common)
    assert outcome.plan["generated_by"] == "deterministic" and outcome.retryable
    mock_ai.llm.script(ModelTask.REPORT_GENERATION, AIOutputInvalid("bad"))
    outcome = await generate_training_plan(scores=scores, **common)
    assert outcome.error and not outcome.retryable
    call = mock_ai.llm.calls_for(ModelTask.REPORT_GENERATION)[-1]
    assert call.context is not None and call.context.prompt_version.startswith("intake-training@")


@pytest.mark.parametrize("language", [None, "rust"])
def test_unknown_language_uses_programming_fundamentals(language) -> None:  # noqa: ANN001
    keys = [m.key for m in select_modules({**ALL_GOOD, "technical": Decimal(3)}, language=language)]
    assert "programming_fundamentals" in keys
