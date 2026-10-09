"""Helpers of the intake evaluation integration tests (T2): demo sessions in the throwaway test database and a
mock-AI resolver that answers each evaluator by its ``prompt_version`` (``intake-eval-<key>@…``).

Every model call goes through the mock registry (``mock_ai`` fixture) or a :class:`FixtureBackend`; nothing here
reaches a real model.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select

from app.ai.mock import MockAIServices, MockLLMCall
from app.db.models import InterviewSession
from app.db.models.intake import IntakeActivityResult
from app.db.session import session_scope
from app.domain.enums import ModelTask
from app.services.intake import demo
from app.services.intake.demo_content import demo_evaluator_outputs
from app.services.intake.evidence import assign_aliases, load_evaluation_inputs


async def make_demo_rows(
    organization_id: uuid.UUID, *, variant: str = "complete", with_eligibility: bool = True
) -> demo.DemoIntake:
    """The demo intake session (status ``completing``) without running the pipeline."""
    async with session_scope() as db:
        return await demo.create_demo_rows(
            db, organization_id, variant=variant, with_eligibility=with_eligibility
        )


async def demo_outputs(session_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """Canned evaluator outputs with the aliases the pipeline assigns to this session."""
    async with session_scope() as db:
        inputs = await load_evaluation_inputs(db, session_id)
    assign_aliases(inputs)
    return demo_evaluator_outputs(inputs)


def evaluator_of(call: MockLLMCall) -> str | None:
    version = (call.context.prompt_version if call.context else None) or ""
    if version.startswith("intake-eval-"):
        return version[len("intake-eval-") :].split("@", 1)[0]
    return None


def install_resolver(
    mock_ai: MockAIServices,
    outputs: Mapping[str, Any],
    *,
    overrides: Mapping[str, Callable[[MockLLMCall], Any] | Any] | None = None,
) -> dict[str, int]:
    """Answer every ``ANSWER_EVALUATION`` call from ``outputs`` (by evaluator); ``overrides`` win (a callable, an
    exception class/instance or an output). Returns live call counts per evaluator."""
    counts: dict[str, int] = {}
    extra = dict(overrides or {})

    def resolve(call: MockLLMCall) -> Any:
        key = evaluator_of(call) or "unknown"
        counts[key] = counts.get(key, 0) + 1
        if key in extra:
            item = extra[key]
            if callable(item) and not isinstance(item, type):
                return item(call)
            if isinstance(item, type) and issubclass(item, BaseException):
                return item("scripted failure")
            return item
        return outputs.get(key)

    mock_ai.llm.set_default(ModelTask.ANSWER_EVALUATION, resolve)
    return counts


def calls_for(mock_ai: MockAIServices, evaluator: str) -> list[MockLLMCall]:
    return [c for c in mock_ai.llm.calls if evaluator_of(c) == evaluator]


async def set_transcript_status(
    session_id: uuid.UUID, kind: str, status: str, *, final: str | None = "keep"
) -> None:
    async with session_scope() as db:
        row = await db.scalar(
            select(IntakeActivityResult).where(
                IntakeActivityResult.session_id == session_id, IntakeActivityResult.kind == kind
            )
        )
        assert row is not None
        row.transcript_status = status
        if final != "keep":
            row.transcript_final = final


async def session_status(session_id: uuid.UUID) -> str:
    async with session_scope() as db:
        session = await db.get(InterviewSession, session_id)
        assert session is not None
        return session.status
