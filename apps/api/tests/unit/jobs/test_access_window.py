"""Access windows (S7): the invite expiry is the deadline to start, never a cut-off mid-interview."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Interview, InterviewSession
from app.services.jobs import access_window as aw

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def interview(status: str = "invited", *, expires_in: timedelta = timedelta(hours=1)) -> Interview:
    return Interview(status=status, invite_expires_at=NOW + expires_in)


def session(status: str, *, started: bool) -> InterviewSession:
    return InterviewSession(status=status, started_at=NOW - timedelta(minutes=10) if started else None)


def test_open_invitation_starts_and_continues() -> None:
    inv = interview()
    assert aw.start_refusal(inv, now=NOW) is None
    assert aw.continue_refusal(inv, None, now=NOW) is None


def test_cancelled_invitation_never_starts_or_continues() -> None:
    inv = interview("cancelled")
    assert aw.start_refusal(inv, now=NOW) == aw.REFUSAL_CANCELLED
    assert aw.continue_refusal(inv, session("live", started=True), now=NOW) == aw.REFUSAL_CANCELLED


@pytest.mark.parametrize("status", ["live", "paused"])
def test_expired_invitation_continues_while_a_started_session_is_open(status: str) -> None:
    inv = interview("in_progress", expires_in=timedelta(minutes=-5))
    assert aw.start_refusal(inv, now=NOW) == aw.REFUSAL_EXPIRED
    assert aw.continue_refusal(inv, session(status, started=True), now=NOW) is None


@pytest.mark.parametrize(
    ("status", "started"),
    [("ready", False), ("created", False), ("live", False), ("completed", True), ("aborted", True)],
)
def test_expired_invitation_without_an_open_started_session_is_refused(status: str, started: bool) -> None:
    inv = interview("invited", expires_in=timedelta(minutes=-5))
    assert aw.continue_refusal(inv, session(status, started=started), now=NOW) == aw.REFUSAL_EXPIRED
    assert aw.continue_refusal(inv, None, now=NOW) == aw.REFUSAL_EXPIRED


def test_completed_interviews_never_count_as_expired() -> None:
    inv = interview("completed", expires_in=timedelta(days=-30))
    assert not aw.invite_expired(inv, NOW)
    assert aw.start_refusal(inv, now=NOW) is None


def test_naive_expiry_timestamps_are_read_as_utc() -> None:
    inv = Interview(status="invited", invite_expires_at=(NOW - timedelta(seconds=1)).replace(tzinfo=None))
    assert aw.invite_expired(inv, NOW)
