"""Live interview-quality harness (opt-in: ``-m live``; shared production models).

Runs complete interviews against the real main model with scripted candidate
personas, scores every transcript (deterministic checks + LLM judge) and checks
scoring sanity across personas. Options are environment variables (see
``tests/quality/harness.py``); the CLI wraps this test::

    uv run python -m tests.quality.run --personas strong,weak --no-judge
    uv run pytest tests/quality/test_interview_quality.py -m live -s
"""

from __future__ import annotations

import pytest

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings

from .harness import HarnessOptions, run_harness
from .scorecard import write_outputs

pytestmark = [pytest.mark.live, pytest.mark.integration]


@pytest.mark.timeout(4 * 3600)
async def test_interview_quality(db_session, redis, storage) -> None:
    options = HarnessOptions.from_env()
    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    previous = set_ai(services)
    try:
        card = await run_harness(services, options)
    finally:
        set_ai(previous)
        await services.aclose()
    json_path, text_path = write_outputs(card, options.out_dir)
    print(card.summary())
    print(f"\nresults: {json_path}\ntranscripts: {text_path}")
    if options.enforce:
        failing = [p.run.persona for p in card.personas if not p.passed]
        global_failing = [
            c.key for c in card.global_checks if c.applicable and not c.passed and c.severity != "minor"
        ]
        assert not failing and not global_failing, (
            f"quality harness failed: personas {failing}, global {global_failing}"
        )
