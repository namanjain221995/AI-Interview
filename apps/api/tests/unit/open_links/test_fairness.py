"""Protected-trait guard for registration forms (OPEN_LINKS.md 2.3)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ValidationFailed
from app.services.open_links.fairness import check_form, enforce_form
from app.services.open_links.form_schema import default_schema, normalize_schema


def _form(*custom: dict[str, Any], **builtin_overrides: dict[str, Any]) -> dict[str, Any]:
    fields = default_schema()["fields"]
    for field in fields:
        field.update(builtin_overrides.get(field["key"], {}))
    return normalize_schema({"fields": [*fields, *custom]})


def _text(key: str, label: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "type": "text", "label": label, **extra}


@pytest.mark.parametrize(
    ("field", "topic"),
    [
        (_text("c_age", "Age"), "age"),
        (_text("c_x", "Date of birth"), "age"),
        (_text("c_dob", "When were you born?"), "age"),  # key "c dob"
        (_text("c_x", "Graduation year"), "age"),
        (_text("c_x", "Passing year"), "age"),
        (_text("c_x", "Gender"), "gender"),
        ({"key": "c_title", "type": "select", "label": "Title", "options": ["Mr", "Mrs", "Ms"]}, "gender"),
        (_text("c_x", "Marital status"), "family_status"),
        (_text("c_x", "Father's name"), "family_status"),
        (_text("c_x", "Number of children"), "family_status"),
        (_text("c_x", "Religion"), "religion"),
        (_text("c_x", "Caste"), "religion"),
        (_text("c_x", "Nationality"), "nationality"),
        (_text("c_x", "Passport number"), "nationality"),
        (_text("c_x", "Upload a photo link"), "appearance"),
        (_text("c_x", "Height"), "appearance"),
        (_text("c_x", "Any medical conditions?"), "health"),
        (_text("c_x", "Disability"), "health"),
        (_text("c_x", "Current CTC"), "salary_history"),
        (_text("c_x", "Last drawn salary"), "salary_history"),
        (
            _text("c_x", "Anything else?", help_text="Tell us your sexual orientation"),
            "gender",
        ),  # catalog lint
        (
            _text("c_x", "Where are you from originally?", placeholder="where are you originally from"),
            "nationality",
        ),
        (_text("c_x", "Family plans"), "family_status"),  # catalog lint phrase
    ],
)
def test_blocked_fields(field: dict[str, Any], topic: str) -> None:
    report = check_form(_form(field))
    assert [e["topic"] for e in report.errors] == [topic]
    with pytest.raises(ValidationFailed) as exc:
        enforce_form(_form(field), [f"{field['key']}:anything"])
    assert exc.value.code == "protected_field"
    assert exc.value.details["fields"][0]["key"] == field["key"]


@pytest.mark.parametrize(
    ("field", "code"),
    [
        (_text("c_x", "Expected CTC"), "expected_pay"),
        (_text("c_x", "Do you have work authorization for India?"), "right_to_work"),
        (_text("c_x", "Are you legally authorized to work in Germany?"), "right_to_work"),
        (_text("c_x", "Are you eligible to work in the UK?"), "right_to_work"),
        (_text("c_x", "Postal address"), "address"),
        (_text("c_x", "Pin code"), "address"),
        (_text("c_x", "Explain any career break"), "employment_gap"),
        (_text("c_x", "Mother tongue"), "languages"),
    ],
)
def test_warnings_need_acknowledgement(field: dict[str, Any], code: str) -> None:
    form = _form(field)
    report = check_form(form)
    assert report.errors == []
    assert [(w["key"], w["code"], w["acknowledged"]) for w in report.warnings] == [("c_x", code, False)]
    with pytest.raises(ValidationFailed) as exc:
        enforce_form(form, [])
    assert exc.value.code == "form_warnings_unacknowledged"
    enforce_form(form, [f"c_x:{code}"])  # acknowledged -> accepted


@pytest.mark.parametrize(
    "label",
    [
        "Preferred shift",
        "Years with Kubernetes",
        "Which programming languages do you use at work?",
        "Notice period in days",
        "LinkedIn profile",
        "Are you comfortable with on-call rotations?",
        "Expected joining date",
        "Highest qualification",
    ],
)
def test_ordinary_questions_pass(label: str) -> None:
    report = check_form(_form(_text("c_q", label)))
    assert report.errors == [] and report.warnings == []


def test_disabled_custom_fields_are_scanned_and_builtins_only_when_reworded() -> None:
    assert check_form(_form(_text("c_x", "Gender", enabled=False))).errors
    assert check_form(_form()).errors == []  # default built-ins are safe
    reworded = check_form(_form(current_title={"label": "Your age"}))
    assert [e["key"] for e in reworded.errors] == ["current_title"]
