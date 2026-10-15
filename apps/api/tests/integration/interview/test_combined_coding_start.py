"""Combined round: the coding exercise opens on its own (no force-coding hook)
through the real turn loop, at a turn boundary inside the first ~40-50 % of
the round, announced by the interviewer ("the editor is opening now"), and
whatever the model plan says about where the coding segment goes."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.ai.base import AIUnavailable
from app.db.models import InterviewEvent
from app.db.session import session_scope
from app.domain.enums import ModelTask, RoundType, SegmentKind
from app.seed.loaders import load_round_templates
from app.services.interview.adapters import CodingPort
from app.services.interview.contracts import CodingStarted, UtteranceBegin
from app.services.interview.planner import GuidedPlan

from .harness import FakeCodingService, build_world, create_session, make_observer
from .test_engine_interviews import Driver

pytestmark = pytest.mark.integration

ANSWERS = [
    "[adequate] I owned the ingestion service and cut p99 latency from 900 to 250 milliseconds.",
    "[partial] We used a queue between the services, I think it was mostly for retries.",
    "[adequate] I split the monolith's billing module out behind a versioned API.",
    "[vague] It depends, we usually just tried things until it worked.",
    "[adequate] I'd shard by tenant id and keep a lookup table for the hot tenants.",
    "[strong] I measured the cache hit ratio before and after: 62 to 91 percent over two weeks.",
    "[partial] I wasn't directly involved in that part, the platform team handled it.",
    "[adequate] I wrote the runbook and led the postmortem for that outage.",
    "[adequate] We picked Postgres over Dynamo because we needed transactions across two tables.",
    "[partial] I would probably add an index, I am not fully sure which one.",
    "[adequate] I paired with the new engineer for two weeks and then handed over the on-call.",
    "[adequate] The trade-off was latency against cost; we accepted 40 ms more for half the bill.",
]


def combined_config() -> dict[str, Any]:
    """The shipped ``combined`` round template (samples/catalog/round_templates.yaml)."""
    template = next(t for t in load_round_templates() if t.round_type == RoundType.COMBINED)
    return template.config.model_dump(mode="json")


def _late_plan() -> GuidedPlan:
    """A model plan that puts the coding exercise at the end (minute 21 of 30)."""
    seg = lambda key, kind, minutes, keys=(): {  # noqa: E731
        "key": key,
        "kind": kind.value,
        "competency_keys": list(keys),
        "time_budget_minutes": minutes,
    }
    return GuidedPlan.model_validate(
        {
            "opening_focus": "Recent backend work",
            "segments": [
                seg("intro", SegmentKind.INTRO, 1),
                seg("resume", SegmentKind.RESUME_VALIDATION, 5),
                seg("technical", SegmentKind.TECHNICAL, 9, ["problem_solving", "system_design"]),
                seg("behavioral", SegmentKind.BEHAVIORAL, 6, ["ownership", "collaboration"]),
                seg("coding", SegmentKind.CODING, 8, ["algorithms", "data_structures"]),
                seg("closing", SegmentKind.CLOSING, 1),
            ],
            "competency_targets": [],
            "claims_priority": [],
            "initial_difficulty": "medium",
            "difficulty_strategy": "Start medium",
            "risks_to_probe": [],
        }
    )


async def _run_until_coding(env: dict[str, Any], *, evaluate: bool = True, **turn_fields: Any):
    world = await build_world(env["mock_ai"], config=combined_config(), with_coding_question=True)
    sid = await create_session(env, world)
    fake = FakeCodingService(str(world.coding_question_id))
    engine = env["make_engine"](coding=CodingPort(service=fake, observer=make_observer(set()), source="test"))
    await engine.prepare_session(sid)
    driver = Driver(env, engine, sid)
    await driver.start()
    for turn, text in enumerate(ANSWERS, start=1):
        events = await driver.answer(text, advance=90.0, evaluate=evaluate, **turn_fields)
        if any(isinstance(e, CodingStarted) for e in events):
            return sid, driver, events, turn
        assert driver.closing is None, f"closed at turn {turn} before coding started"
    pytest.fail("coding never started")


@pytest.mark.parametrize(
    "plan",
    [
        pytest.param(_late_plan, id="model_plan_coding_last"),
        pytest.param(AIUnavailable, id="plan_fallback"),
        pytest.param(None, id="mock_default_plan"),
    ],
)
async def test_combined_round_reaches_coding_naturally(interview_env, plan) -> None:
    if plan is not None:
        interview_env["mock_ai"].llm.script(
            ModelTask.INTERVIEW_PLANNING, plan() if plan is _late_plan else plan
        )
    sid, driver, events, turn = await _run_until_coding(interview_env)
    state = await driver.state()
    limit = state.time_limit_s
    started = state.coding.started_at_s
    # The interviewer talks first (introduction + a few questions), then the editor opens by ~45 %.
    assert 0.2 * limit <= started <= 0.45 * limit, (started, turn)
    assert state.coding.active and not state.force_coding

    # Turn boundary: the transition is the reply to the candidate's answer, then the editor opens.
    names = [type(e).__name__ for e in events]
    assert names[-3:] == ["UtteranceBegin", "UtteranceMeta", "CodingStarted"]
    transition = [e for e in events if isinstance(e, UtteranceBegin)][-1]
    assert transition.kind == "transition"
    assert "editor is opening" in transition.text and "Most Frequent Error Service" in transition.text
    assert driver.decisions[-1]["action"] == "start_coding"

    async with session_scope() as db:
        types = list(
            (await db.execute(select(InterviewEvent.type).where(InterviewEvent.session_id == uuid.UUID(sid))))
            .scalars()
            .all()
        )
    assert "coding.started" in types and "test.force_coding" not in types


async def test_combined_round_coding_starts_without_evaluations(interview_env) -> None:
    """No technical evidence at all (evaluations never ran): the round default still opens coding."""
    interview_env["mock_ai"].llm.script(ModelTask.INTERVIEW_PLANNING, _late_plan())
    sid, driver, events, turn = await _run_until_coding(interview_env, evaluate=False)
    state = await driver.state()
    limit, started = state.time_limit_s, state.coding.started_at_s
    # The first turn boundary at or after 40 % (turns are 90 s apart here).
    assert 0.4 * limit <= started < 0.4 * limit + 90.0, (started, turn)


async def test_coding_transition_skips_its_thanks_after_a_spoken_ack(interview_env) -> None:
    """The realtime layer already said "Okay." after the answer: the transition starts with the exercise."""
    interview_env["mock_ai"].llm.script(ModelTask.INTERVIEW_PLANNING, _late_plan())
    _sid, _driver, events, _turn = await _run_until_coding(interview_env, acknowledged="Okay.")
    transition = [e for e in events if isinstance(e, UtteranceBegin)][-1]
    assert transition.kind == "transition"
    assert transition.text.startswith("Let's move to a short coding exercise"), transition.text
