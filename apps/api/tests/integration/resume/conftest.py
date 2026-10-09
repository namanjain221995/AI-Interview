"""Fixtures for the resume pipeline integration tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


@pytest.fixture
def pipeline_worker(worker_factory: Callable[..., Any]) -> Any:
    """Worker consuming only the resume pipeline streams (handlers registered by import)."""
    import app.workers.resume  # noqa: F401 - registers resume.process / profile.generate

    return worker_factory(job_types=["resume.process", "profile.generate"])
