"""Task -> endpoint routing (CONTRACTS.md §9) with circuit-aware fallback.

* Turn-critical and deep-reasoning tasks run on ``main`` (35B, thinking off
  for turn tasks); vision and background classification tasks run on
  ``vision`` (8B VL).
* ``MODEL_ROUTING_OVERRIDES`` (JSON task -> endpoint) replaces any row.
* When the primary endpoint's circuit is open, turn-critical tasks degrade
  from ``main`` to ``vision``; text-only ``vision`` tasks may move to
  ``main``. Image requests never go to ``main`` (text-only model). Deep,
  asynchronous tasks have no fallback: their background job retries later
  instead of producing a lower-quality result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from app.ai.base import ChatMessage, Endpoint
from app.ai.breaker import CircuitBreaker
from app.core.logging import get_logger
from app.domain.enums import ModelTask

log = get_logger(__name__)

ENDPOINTS: tuple[Endpoint, ...] = ("main", "vision")

TASK_ROUTES: dict[ModelTask, Endpoint] = {
    # Turn-critical (latency budget measured in hundreds of milliseconds).
    ModelTask.TURN_DECISION: "main",
    ModelTask.ANSWER_ASSESSMENT: "main",
    ModelTask.CODE_OBSERVATION: "main",
    ModelTask.HINT_GENERATION: "main",
    ModelTask.OPENING: "main",
    # Deep reasoning, asynchronous.
    ModelTask.RESUME_EXTRACTION: "main",
    ModelTask.PROFILE_ANALYSIS: "main",
    ModelTask.JOB_MATCH: "main",
    ModelTask.INTERVIEW_PLANNING: "main",
    ModelTask.ANSWER_EVALUATION: "main",
    ModelTask.CODING_EVALUATION: "main",
    ModelTask.REPORT_GENERATION: "main",
    ModelTask.QUESTION_GENERATION: "main",
    ModelTask.COMPETENCY_SUMMARY: "main",
    # Vision, asynchronous and low frequency.
    ModelTask.FRAME_ANALYSIS: "vision",
    ModelTask.SCREEN_ANALYSIS: "vision",
    # Background classification keeps load off the main model.
    ModelTask.DOCUMENT_CLASSIFICATION: "vision",
    ModelTask.ROUTING_CLASSIFICATION: "vision",
    ModelTask.MEMORY_SUMMARY: "vision",
}

TURN_CRITICAL_TASKS: frozenset[ModelTask] = frozenset(
    {
        ModelTask.TURN_DECISION,
        ModelTask.ANSWER_ASSESSMENT,
        ModelTask.CODE_OBSERVATION,
        ModelTask.HINT_GENERATION,
        ModelTask.OPENING,
    }
)

NON_LLM_TASKS: frozenset[ModelTask] = frozenset(
    {ModelTask.EMBEDDING, ModelTask.RERANK, ModelTask.STT, ModelTask.OCR, ModelTask.TTS}
)


@dataclass(slots=True, frozen=True)
class RouteDecision:
    endpoint: Endpoint
    fallback: Endpoint | None  # tried when ``endpoint`` turns out to be unavailable
    reason: str  # table | override | explicit | primary_circuit_open


def has_image_content(messages: Sequence[ChatMessage]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, Mapping) and part.get("type") in ("image_url", "input_image", "image"):
                    return True
    return False


def parse_overrides(raw: Mapping[str, Any] | None) -> dict[ModelTask, Endpoint]:
    """Validate ``MODEL_ROUTING_OVERRIDES``; invalid entries are logged and ignored."""
    result: dict[ModelTask, Endpoint] = {}
    for key, value in (raw or {}).items():
        try:
            task = ModelTask(str(key))
        except ValueError:
            log.warning("ai.router.override_ignored", task=str(key), reason="unknown task")
            continue
        if task in NON_LLM_TASKS:
            log.warning("ai.router.override_ignored", task=task.value, reason="not an LLM task")
            continue
        if value not in ENDPOINTS:
            log.warning("ai.router.override_ignored", task=task.value, reason=f"unknown endpoint {value!r}")
            continue
        result[task] = cast(Endpoint, value)
    return result


class ModelRouter:
    def __init__(
        self,
        breakers: Mapping[str, CircuitBreaker] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self._breakers = dict(breakers or {})
        self._overrides = parse_overrides(overrides)

    @property
    def overrides(self) -> dict[ModelTask, Endpoint]:
        return dict(self._overrides)

    def primary(self, task: ModelTask) -> Endpoint:
        if task in NON_LLM_TASKS:
            raise ValueError(f"{task.value} is not routed through the LLM router")
        if task in self._overrides:
            return self._overrides[task]
        return TASK_ROUTES.get(task, "main")

    def fallback_for(self, task: ModelTask, primary: Endpoint, *, has_images: bool) -> Endpoint | None:
        if primary == "main" and task in TURN_CRITICAL_TASKS:
            return "vision"
        if primary == "vision" and not has_images:
            return "main"
        return None

    def select(
        self,
        task: ModelTask,
        *,
        has_images: bool = False,
        explicit: Endpoint | None = None,
    ) -> RouteDecision:
        if explicit is not None:
            if explicit not in ENDPOINTS:
                raise ValueError(f"unknown endpoint {explicit!r}")
            if explicit == "main" and has_images:
                log.warning("ai.router.images_to_text_model", task=task.value)
            return RouteDecision(explicit, None, "explicit")
        primary = self.primary(task)
        reason = "override" if task in self._overrides else "table"
        if primary == "main" and has_images:
            # Image input cannot be served by the text-only model.
            primary, reason = "vision", "images_require_vision"
        fallback = self.fallback_for(task, primary, has_images=has_images)
        if fallback is not None and self._rejects(primary) and not self._rejects(fallback):
            log.info("ai.router.fallback", task=task.value, primary=primary, fallback=fallback)
            return RouteDecision(fallback, None, "primary_circuit_open")
        return RouteDecision(primary, fallback, reason)

    def _rejects(self, endpoint: Endpoint) -> bool:
        breaker = self._breakers.get(endpoint)
        return breaker is not None and breaker.would_reject()
