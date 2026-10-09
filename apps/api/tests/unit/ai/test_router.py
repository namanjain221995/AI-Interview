from __future__ import annotations

import pytest

from app.ai.breaker import CircuitBreaker
from app.ai.router import TASK_ROUTES, TURN_CRITICAL_TASKS, ModelRouter, has_image_content, parse_overrides
from app.domain.enums import ModelTask
from tests.unit.ai.conftest import FakeClock

IMAGE_MESSAGES = [
    {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
            {"type": "text", "text": "x"},
        ],
    }
]
TEXT_MESSAGES = [{"role": "user", "content": "hello"}]


def breakers(clock: FakeClock | None = None) -> dict[str, CircuitBreaker]:
    kwargs = {"clock": clock} if clock else {}
    return {
        "main": CircuitBreaker("main", failure_threshold=1, reset_seconds=30, **kwargs),
        "vision": CircuitBreaker("vision", failure_threshold=1, reset_seconds=30, **kwargs),
    }


def test_contract_table() -> None:
    router = ModelRouter()
    for task in (
        ModelTask.TURN_DECISION,
        ModelTask.ANSWER_ASSESSMENT,
        ModelTask.CODE_OBSERVATION,
        ModelTask.HINT_GENERATION,
        ModelTask.RESUME_EXTRACTION,
        ModelTask.PROFILE_ANALYSIS,
        ModelTask.JOB_MATCH,
        ModelTask.INTERVIEW_PLANNING,
        ModelTask.ANSWER_EVALUATION,
        ModelTask.CODING_EVALUATION,
        ModelTask.REPORT_GENERATION,
        ModelTask.QUESTION_GENERATION,
        ModelTask.COMPETENCY_SUMMARY,
    ):
        assert router.primary(task) == "main", task
    for task in (
        ModelTask.FRAME_ANALYSIS,
        ModelTask.SCREEN_ANALYSIS,
        ModelTask.DOCUMENT_CLASSIFICATION,
        ModelTask.ROUTING_CLASSIFICATION,
        ModelTask.MEMORY_SUMMARY,
    ):
        assert router.primary(task) == "vision", task
    assert set(TURN_CRITICAL_TASKS) <= set(TASK_ROUTES)


def test_non_llm_tasks_are_not_routed() -> None:
    with pytest.raises(ValueError):
        ModelRouter().primary(ModelTask.EMBEDDING)


def test_overrides_apply_and_invalid_entries_are_ignored() -> None:
    router = ModelRouter(
        overrides={"answer_assessment": "vision", "nope": "main", "turn_decision": "gpu9", "stt": "main"}
    )
    assert router.primary(ModelTask.ANSWER_ASSESSMENT) == "vision"
    assert router.primary(ModelTask.TURN_DECISION) == "main"
    assert router.select(ModelTask.ANSWER_ASSESSMENT).reason == "override"
    assert parse_overrides({"memory_summary": "main"}) == {ModelTask.MEMORY_SUMMARY: "main"}


def test_turn_critical_falls_back_to_vision_when_main_open() -> None:
    b = breakers()
    router = ModelRouter(breakers=b)
    decision = router.select(ModelTask.TURN_DECISION)
    assert (decision.endpoint, decision.fallback) == ("main", "vision")
    b["main"].on_failure()
    decision = router.select(ModelTask.TURN_DECISION)
    assert decision.endpoint == "vision"
    assert decision.fallback is None
    assert decision.reason == "primary_circuit_open"


def test_deep_tasks_have_no_fallback() -> None:
    b = breakers()
    router = ModelRouter(breakers=b)
    b["main"].on_failure()
    decision = router.select(ModelTask.ANSWER_EVALUATION)
    assert decision.endpoint == "main"
    assert decision.fallback is None


def test_vision_text_task_falls_back_to_main_but_images_never_do() -> None:
    b = breakers()
    router = ModelRouter(breakers=b)
    b["vision"].on_failure()
    text_route = router.select(ModelTask.MEMORY_SUMMARY, has_images=False)
    assert text_route.endpoint == "main"
    image_route = router.select(ModelTask.FRAME_ANALYSIS, has_images=True)
    assert image_route.endpoint == "vision"
    assert image_route.fallback is None


def test_no_fallback_when_both_open() -> None:
    b = breakers()
    router = ModelRouter(breakers=b)
    b["main"].on_failure()
    b["vision"].on_failure()
    assert router.select(ModelTask.TURN_DECISION).endpoint == "main"


def test_half_open_primary_is_probed_not_skipped() -> None:
    clock = FakeClock()
    b = breakers(clock)
    router = ModelRouter(breakers=b)
    b["main"].on_failure()
    clock.advance(31)
    assert router.select(ModelTask.TURN_DECISION).endpoint == "main"


def test_images_force_vision_and_explicit_endpoint_wins() -> None:
    router = ModelRouter()
    assert router.select(ModelTask.TURN_DECISION, has_images=True).endpoint == "vision"
    explicit = router.select(ModelTask.MEMORY_SUMMARY, explicit="main")
    assert (explicit.endpoint, explicit.fallback, explicit.reason) == ("main", None, "explicit")
    with pytest.raises(ValueError):
        router.select(ModelTask.MEMORY_SUMMARY, explicit="gpu")  # type: ignore[arg-type]


def test_has_image_content() -> None:
    assert has_image_content(IMAGE_MESSAGES)
    assert not has_image_content(TEXT_MESSAGES)
