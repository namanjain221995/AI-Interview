"""Gap-targeted follow-ups through the real turn loop: the turn prompt names what the answer left out."""

from __future__ import annotations

import pytest

from app.domain.enums import ModelTask

from .test_engine_interviews import Driver, _setup

pytestmark = pytest.mark.integration

TEAM_ONLY = (
    "We moved the services to EKS over two quarters. Our platform group wrote the Terraform modules and we "
    "migrated forty services, and the team ran the cutover in three waves with our on-call watching."
)


def _last_turn_prompt(env) -> str:
    call = env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION)[-1]
    return "\n".join(str(m["content"]) for m in call.messages)


@pytest.mark.parametrize("enabled", [True, False])
async def test_the_turn_prompt_asks_for_what_the_answer_left_out(interview_env, enabled) -> None:
    _world, sid, engine = await _setup(interview_env, quick_assessment_enabled=False, gap_probes=enabled)
    driver = Driver(interview_env, engine, sid)
    await driver.start()
    await driver.answer(TEAM_ONLY, advance=60.0)
    prompt = _last_turn_prompt(interview_env)
    assert ("derived_answer_gap" in prompt and "personally did" in prompt) is enabled


async def test_a_complete_answer_gets_no_gap_line(interview_env) -> None:
    _world, sid, engine = await _setup(interview_env, quick_assessment_enabled=False)
    driver = Driver(interview_env, engine, sid)
    await driver.start()
    await driver.answer(
        "I split the fleet into three waves and ran canaries at five percent first, because a regional failure "
        "was our biggest risk; I chose Argo Rollouts since it gave us automated analysis, and rollback time fell "
        "from forty minutes to six.",
        advance=60.0,
    )
    assert "derived_answer_gap" not in _last_turn_prompt(interview_env)
