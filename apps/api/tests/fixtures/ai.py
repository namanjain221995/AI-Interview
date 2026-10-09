"""AI-layer fixtures (loaded by tests/conftest.py when present).

``mock_ai`` installs a fresh :class:`~app.ai.mock.MockAIServices` through
``app.ai.registry.set_ai`` for the duration of one test and restores the
previous services afterwards::

    def test_turn(mock_ai):
        mock_ai.llm.script(ModelTask.TURN_DECISION, decision)
        ...
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.ai.mock import MockAIServices
from app.ai.registry import set_ai


@pytest.fixture
def mock_ai() -> Iterator[MockAIServices]:
    services = MockAIServices()
    previous = set_ai(services)
    try:
        yield services
    finally:
        set_ai(previous)
