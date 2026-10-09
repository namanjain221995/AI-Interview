"""Client signal normalisation: clock clamping, details sanitising, large-paste derivation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import IntegrityEventType, IntegritySource
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity.normalize import (
    MAX_CLIENT_LAG,
    clamp_duration,
    clamp_time,
    client_signal,
    derived_large_paste,
    is_large_coding_paste,
    parse_client_time,
    sanitize_details,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-07T11:59:58Z", datetime(2026, 10, 7, 11, 59, 58, tzinfo=UTC)),
        ("2026-10-07T13:59:58+02:00", datetime(2026, 10, 7, 11, 59, 58, tzinfo=UTC)),
        ("2026-10-07T11:59:58", datetime(2026, 10, 7, 11, 59, 58, tzinfo=UTC)),
        (
            str(int(datetime(2026, 10, 7, 11, 0, tzinfo=UTC).timestamp() * 1000)),
            datetime(2026, 10, 7, 11, 0, tzinfo=UTC),
        ),
        ("not a date", None),
        ("", None),
        (None, None),
        ("nan", None),
        ("x" * 100, None),
    ],
)
def test_parse_client_time(raw, expected) -> None:
    assert parse_client_time(raw) == expected


def test_clamp_time() -> None:
    assert clamp_time(NOW - timedelta(seconds=1), now=NOW) == (NOW - timedelta(seconds=1), False)
    future, adjusted = clamp_time(NOW + timedelta(hours=3), now=NOW)
    assert future == NOW and adjusted
    old, adjusted = clamp_time(NOW - timedelta(days=2), now=NOW)
    assert old == NOW - MAX_CLIENT_LAG and adjusted
    missing, adjusted = clamp_time(None, now=NOW)
    assert missing == NOW and adjusted


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (1500, 1500),
        (-5, 0),
        (1e12, 6 * 3600 * 1000),
        ("12", 12),
        ("abc", None),
        (True, None),
        (float("inf"), None),
    ],
)
def test_clamp_duration(value, expected) -> None:
    assert clamp_duration(value) == expected


def test_sanitize_details_drops_content_and_nested_values() -> None:
    cleaned = sanitize_details(
        {
            "length": 1200,
            "lines": 30,
            "context": "coding",
            "text": "def solve(): return 42",
            "clipboard": "secret",
            "Content": "x",
            "nested": {"a": 1},
            "list": [1, 2],
            "bad key!": 1,
            "long": "y" * 500,
            "ratio": float("nan"),
            "ok": True,
        }
    )
    assert cleaned == {"length": 1200, "lines": 30, "context": "coding", "long": "y" * 200, "ok": True}
    assert sanitize_details(None) == {}
    assert len(sanitize_details({f"k{i}": i for i in range(50)})) == 20


@pytest.mark.parametrize(
    ("details", "large"),
    [
        ({"length": 250, "context": "coding"}, True),
        ({"chars": 199, "context": "coding"}, False),
        ({"lines": 8, "target": "code_editor"}, True),
        ({"length": 5000, "context": "chat"}, False),
        ({"length": 5000, "in_coding": True}, True),
        ({"length": True, "context": "coding"}, False),
        ({}, False),
    ],
)
def test_large_coding_paste_detection(details, large) -> None:
    assert is_large_coding_paste(details) is large


def test_client_signal_and_derived_paste() -> None:
    event = ClientIntegrityEvent(
        event_type=IntegrityEventType.PASTE_EVENT,
        occurred_at=(NOW + timedelta(minutes=5)).isoformat(),
        duration_ms=None,
        details={"length": 800, "lines": 20, "context": "coding", "text": "pasted code"},
    )
    signal = client_signal(event, now=NOW)
    assert signal.source is IntegritySource.BROWSER
    assert signal.occurred_at == NOW
    assert signal.details["clock_adjusted"] is True and "text" not in signal.details
    derived = derived_large_paste(signal)
    assert derived.event_type is IntegrityEventType.LARGE_PASTE_IN_CODING
    assert derived.details == {"derived_from": "paste_event", "chars": 800, "lines": 20}
