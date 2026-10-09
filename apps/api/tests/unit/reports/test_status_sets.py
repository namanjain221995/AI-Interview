"""The report API and the report worker agree on which sessions are reportable."""

from __future__ import annotations

from app.domain.enums import SessionStatus
from app.services.reports.queries import REPORTABLE_STATUSES, REVIEWABLE_STATUSES
from app.workers.reports import REPORTABLE


def test_regenerate_endpoint_and_worker_accept_the_same_sessions() -> None:
    assert REPORTABLE_STATUSES == REPORTABLE
    assert SessionStatus.FAILED.value in REPORTABLE_STATUSES
    # Anything a reviewer can decide on can also get a report.
    assert REVIEWABLE_STATUSES <= REPORTABLE_STATUSES
