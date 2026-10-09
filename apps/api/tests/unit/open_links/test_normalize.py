"""Duplicate keys: e-mail and phone normalisation."""

from __future__ import annotations

import pytest

from app.services.open_links.normalize import email_key, phone_key


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Jane.Doe@Example.com", "jane.doe@example.com"),
        ("  jane+jobs@example.com ", "jane@example.com"),
        ("J.a.n.e@gmail.com", "jane@gmail.com"),
        ("jane.doe+drive@googlemail.com", "janedoe@gmail.com"),
        ("first.last@company.co.in", "first.last@company.co.in"),
    ],
)
def test_email_key(raw: str, key: str) -> None:
    assert email_key(raw) == key


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("+91 98765 43210", "9876543210"),
        ("098765-43210", "9876543210"),
        ("(987) 654-3210", "9876543210"),
        ("1234567", "1234567"),
        ("", None),
        (None, None),
        ("+1 (415) 555-0100", "4155550100"),
    ],
)
def test_phone_key(raw: str | None, key: str | None) -> None:
    assert phone_key(raw) == key


def test_same_person_different_formats_match() -> None:
    assert phone_key("+91 9876543210") == phone_key("09876543210") == phone_key("98765 43210")
    assert email_key("A.B+x@GMAIL.com") == email_key("ab@gmail.com")
