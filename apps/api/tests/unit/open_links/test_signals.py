"""Signal hashing: keyed, kind-separated, never the raw value; flags; CSV cell escaping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app.services.open_links import signals
from app.services.open_links.export import safe_cell


def test_hashes_are_keyed_and_kind_separated(settings) -> None:
    settings.open_link_signal_secret = None
    a = signals.ip_hash("10.0.0.1")
    assert a != signals.device_hash("10.0.0.1")  # kinds never collide
    assert "10.0.0.1" not in a and len(a) == 64
    settings.open_link_signal_secret = SecretStr("another-secret")
    assert signals.ip_hash("10.0.0.1") != a  # depends on the key


def test_default_key_derives_from_media_secret(settings) -> None:
    settings.open_link_signal_secret = None
    first = signals.signal_key()
    settings.media_url_secret = SecretStr("rotated-media-secret-value-0123456789")
    assert signals.signal_key() != first


def test_flags() -> None:
    flag = signals.make_flag("same_device", None, at=datetime(2026, 10, 8, tzinfo=UTC))
    assert flag["severity"] == "high" and flag["related_registration_id"] is None
    assert signals.make_flag("same_network", None)["severity"] == "low"
    assert signals.has_high_flag([flag]) and not signals.has_high_flag(
        [signals.make_flag("same_network", None)]
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("x")', '\'=HYPERLINK("x")'),
        ("+1 555", "'+1 555"),
        ("-2", "'-2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("\rcmd", "'\rcmd"),
        ("Jane", "Jane"),
        (None, ""),
        (True, "yes"),
        (["Day", "Night"], "Day; Night"),
    ],
)
def test_csv_cells_are_formula_safe(value, expected) -> None:
    assert safe_cell(value) == expected
