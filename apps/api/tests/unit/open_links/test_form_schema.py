"""Form schema normalisation and answer validation (OPEN_LINKS.md 2.1, 2.2)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.core.errors import ValidationFailed
from app.services.open_links.form_schema import default_schema, normalize_schema, validate_answers

TODAY = date(2026, 10, 8)


def _schema(*custom: dict[str, Any], **builtin_overrides: dict[str, Any]) -> dict[str, Any]:
    fields = default_schema()["fields"]
    for field in fields:
        field.update(builtin_overrides.get(field["key"], {}))
    return normalize_schema({"fields": [*fields, *custom]}, version=1)


def _errors(exc: pytest.ExceptionInfo[ValidationFailed]) -> dict[str, str]:
    return exc.value.details["fields"]


# ------------------------------------------------------------------ schema


def test_default_schema_is_complete_and_locked_fields_forced() -> None:
    raw = default_schema()
    for field in raw["fields"]:
        if field["key"] in ("full_name", "email"):
            field["enabled"] = False
            field["required"] = False
    schema = normalize_schema(raw, version=4)
    by_key = {f["key"]: f for f in schema["fields"]}
    assert schema["version"] == 4
    assert by_key["full_name"]["enabled"] and by_key["full_name"]["required"]
    assert by_key["email"]["enabled"] and by_key["email"]["required"]
    assert by_key["phone"]["enabled"] and by_key["phone"]["required"]
    assert by_key["notice_period"]["options"][0] == "Immediately"


def test_missing_builtins_are_added() -> None:
    schema = normalize_schema(
        {"fields": [{"key": "c_shift", "type": "select", "label": "Shift", "options": ["Day"]}]}
    )
    keys = [f["key"] for f in schema["fields"]]
    assert keys[:2] == ["email", "full_name"] or keys[:2] == ["full_name", "email"]
    assert "c_shift" in keys and "available_from" in keys
    assert next(f for f in schema["fields"] if f["key"] == "phone")["enabled"] is False


def test_builtin_type_and_options_cannot_be_changed() -> None:
    raw = default_schema()
    for field in raw["fields"]:
        if field["key"] == "notice_period":
            field.update(type="text", options=["Never"], enabled=True)
    by_key = {f["key"]: f for f in normalize_schema(raw)["fields"]}
    assert by_key["notice_period"]["type"] == "select"
    assert "Never" not in by_key["notice_period"]["options"]


@pytest.mark.parametrize(
    ("field", "fragment"),
    [
        ({"key": "shift", "type": "text", "label": "Shift"}, "c_"),
        ({"key": "c_x", "type": "file", "label": "Upload"}, "Unknown field type"),
        ({"key": "c_x", "type": "select", "label": "X", "options": []}, "options"),
        ({"key": "c_x", "type": "select", "label": "X", "options": ["A", "a"]}, "more than once"),
        ({"key": "c_x", "type": "text", "label": ""}, "Label"),
        ({"key": "c_x", "type": "text", "label": "x" * 121}, "Label"),
        ({"key": "c_x", "type": "number", "label": "X", "min": 5, "max": 1}, "min"),
        ({"key": "c_x", "type": "multiselect", "label": "X", "options": ["A"], "max": 3}, "max selections"),
        ({"key": "c_x", "type": "textarea", "label": "X", "max_length": 5000}, "max_length"),
    ],
)
def test_invalid_custom_fields(field: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        _schema(field)
    assert any(fragment in e["message"] for e in exc.value.details["errors"])


def test_duplicate_keys_and_field_limit() -> None:
    with pytest.raises(ValidationFailed):
        _schema({"key": "c_a", "type": "text", "label": "A"}, {"key": "c_a", "type": "text", "label": "B"})
    many = [{"key": f"c_f{i}", "type": "text", "label": f"F{i}"} for i in range(20)]
    with pytest.raises(ValidationFailed) as exc:
        _schema(*many)  # 11 built-ins + 20 > 30
    assert "at most 30" in exc.value.details["errors"][-1]["message"]
    assert len(_schema(*many[:19])["fields"]) == 30


# ------------------------------------------------------------------ answers


def test_valid_answers_normalised() -> None:
    schema = _schema(
        {"key": "c_shift", "type": "select", "label": "Shift", "options": ["Day", "Night"], "required": True},
        {"key": "c_langs", "type": "multiselect", "label": "Languages", "options": ["Go", "Rust", "C"]},
        {"key": "c_ok", "type": "checkbox", "label": "I confirm my details", "required": True},
        location={"enabled": True},
        years_experience={"enabled": True},
        available_from={"enabled": True},
        portfolio_url={"enabled": True},
    )
    result = validate_answers(
        schema,
        {
            "full_name": "  José Kumar ",
            "email": " Jose.Kumar+x@GMAIL.com ",
            "phone": "+91 98765 43210",
            "location": "Pune",
            "years_experience": "4.5",
            "available_from": (TODAY + timedelta(days=10)).isoformat(),
            "portfolio_url": "https://github.com/jose",
            "c_shift": "Night",
            "c_langs": ["Rust", "Go"],
            "c_ok": True,
        },
        today=TODAY,
    )
    assert result.full_name == "José Kumar"  # NFC + trimmed
    assert result.email == "jose.kumar+x@gmail.com" and result.email_key == "josekumar@gmail.com"
    assert result.phone_key == "9876543210" and result.location == "Pune"
    values = {f["key"]: f["value"] for f in result.snapshot["fields"]}
    assert values["years_experience"] == 4.5 and values["c_langs"] == ["Go", "Rust"]
    assert result.snapshot["form_version"] == 1
    assert {f["label"] for f in result.snapshot["fields"]} >= {"Shift", "Languages"}


def test_unknown_required_and_disabled_fields() -> None:
    schema = _schema()
    with pytest.raises(ValidationFailed) as exc:
        validate_answers(
            schema, {"full_name": "A", "email": "a@b.co", "c_new": "x", "location": "x"}, today=TODAY
        )
    errors = _errors(exc)
    assert errors["c_new"] == "This field is not part of the form."
    assert errors["phone"] == "This field is required."
    assert "location" not in errors  # disabled fields are ignored


@pytest.mark.parametrize(
    ("override", "custom", "answers", "key", "fragment"),
    [
        ({}, None, {"email": "not-an-email"}, "email", "valid email"),
        ({}, None, {"phone": "98765"}, "phone", "7 to 15"),
        ({}, None, {"phone": "98765abc12"}, "phone", "digits"),
        ({}, None, {"full_name": "x" * 201}, "full_name", "under 200"),
        ({}, None, {"full_name": "bad\x00name"}, "full_name", "characters"),
        (
            {"portfolio_url": {"enabled": True}},
            None,
            {"portfolio_url": "javascript:alert(1)"},
            "portfolio_url",
            "https",
        ),
        (
            {"years_experience": {"enabled": True}},
            None,
            {"years_experience": 61},
            "years_experience",
            "at most",
        ),
        (
            {"years_experience": {"enabled": True}},
            None,
            {"years_experience": "1.255"},
            "years_experience",
            "2 decimal",
        ),
        (
            {"years_experience": {"enabled": True}},
            None,
            {"years_experience": "nan"},
            "years_experience",
            "number",
        ),
        (
            {"available_from": {"enabled": True}},
            None,
            {"available_from": "1990-01-01"},
            "available_from",
            "today",
        ),
        (
            {"available_from": {"enabled": True}},
            None,
            {"available_from": "2030-01-01"},
            "available_from",
            "two years",
        ),
        (
            {"notice_period": {"enabled": True}},
            None,
            {"notice_period": "Tomorrow"},
            "notice_period",
            "options",
        ),
        (
            {},
            {"key": "c_ok", "type": "checkbox", "label": "Confirm", "required": True},
            {"c_ok": False},
            "c_ok",
            "tick",
        ),
        (
            {},
            {"key": "c_m", "type": "multiselect", "label": "M", "options": ["A", "B"], "max": 1},
            {"c_m": ["A", "B"]},
            "c_m",
            "at most 1",
        ),
        (
            {},
            {"key": "c_m", "type": "multiselect", "label": "M", "options": ["A", "B"]},
            {"c_m": ["A", "A"]},
            "c_m",
            "options",
        ),
    ],
)
def test_invalid_answers(override, custom, answers, key, fragment) -> None:
    schema = _schema(*([custom] if custom else []), **override)
    base = {"full_name": "Jane", "email": "jane@example.com", "phone": "9876543210"}
    with pytest.raises(ValidationFailed) as exc:
        validate_answers(schema, {**base, **answers}, today=TODAY)
    assert fragment in _errors(exc)[key]


def test_optional_checkbox_and_empty_optional_values() -> None:
    schema = _schema(
        {"key": "c_relocate", "type": "checkbox", "label": "Open to relocation"}, location={"enabled": True}
    )
    result = validate_answers(
        schema,
        {"full_name": "Jane", "email": "jane@example.com", "phone": "9876543210", "location": "  "},
        today=TODAY,
    )
    values = {f["key"]: f["value"] for f in result.snapshot["fields"]}
    assert values["c_relocate"] is False and "location" not in values and result.location is None
