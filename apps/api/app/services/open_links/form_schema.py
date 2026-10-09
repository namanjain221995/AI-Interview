"""Registration form schema: normalisation and answer validation
(OPEN_LINKS.md sections 2.1 and 2.2).

``normalize_schema(raw, version)`` turns recruiter input into the stored
schema (built-ins pinned to the catalog's type/options, full name and e-mail
always enabled and required, missing built-ins appended disabled) or raises
422 ``validation_failed`` with ``details.errors``.

``validate_answers(schema, answers)`` checks a registration against the
link's current schema and returns the normalised identity fields plus an
answers snapshot, or raises 422 ``validation_failed`` with
``details.fields = {key: candidate-safe message}``.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from app.core.errors import ValidationFailed
from app.services.open_links.constants import (
    BUILTIN_BY_KEY,
    BUILTIN_FIELDS,
    CUSTOM_KEY_PATTERN,
    DATE_MAX_AHEAD_DAYS,
    EMAIL_MAX,
    HELP_TEXT_MAX,
    LABEL_MAX,
    MAX_FIELDS,
    MAX_OPTIONS,
    OPTION_MAX,
    PLACEHOLDER_MAX,
    TEXT_MAX_LENGTH,
    TEXTAREA_MAX_LENGTH,
    URL_MAX,
    FieldType,
    public_builtin,
)
from app.services.open_links.normalize import email_key, phone_digits, phone_key

_CUSTOM_KEY = re.compile(CUSTOM_KEY_PATTERN)
_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_PHONE_CHARS = re.compile(r"^[0-9 +\-().]+$")
# Control characters (C0/C1 + DEL); newlines and tabs are allowed in textareas only.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_LINE_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_FIELD_TYPES = frozenset(t.value for t in FieldType)
_CHOICE_TYPES = frozenset({FieldType.SELECT.value, FieldType.MULTISELECT.value})


# =========================================================== schema


def default_schema() -> dict[str, Any]:
    return {"version": 1, "fields": [public_builtin(f) for f in BUILTIN_FIELDS]}


def _clean_text(value: Any, limit: int, *, allow_empty: bool = True) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be text")
    text = unicodedata.normalize("NFC", value).strip()
    if _LINE_CONTROL.search(text):
        raise ValueError("must not contain control characters")
    if len(text) > limit:
        raise ValueError(f"must be at most {limit} characters")
    if not text:
        if allow_empty:
            return None
        raise ValueError("is required")
    return text


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("must be a finite number")
    return int(number) if number.is_integer() else number


def _normalize_field(raw: Any, errors: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        errors.append({"index": index, "key": None, "message": "Each field must be an object."})
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        errors.append({"index": index, "key": None, "message": "Each field needs a key."})
        return None
    builtin = BUILTIN_BY_KEY.get(key)

    def fail(message: str) -> None:
        errors.append({"index": index, "key": key, "message": message})

    try:
        label = _clean_text(raw.get("label"), LABEL_MAX, allow_empty=builtin is not None)
    except ValueError as exc:
        fail(f"Label {exc}.")
        return None
    try:
        help_text = _clean_text(raw.get("help_text"), HELP_TEXT_MAX)
        placeholder = _clean_text(raw.get("placeholder"), PLACEHOLDER_MAX)
    except ValueError as exc:
        fail(f"Help text or placeholder {exc}.")
        return None
    required = bool(raw.get("required", False))
    enabled = bool(raw.get("enabled", True))

    if builtin is not None:
        out = public_builtin(builtin)
        out.update(
            label=label or builtin["label"],
            help_text=help_text,
            placeholder=placeholder,
            required=required,
            enabled=enabled,
        )
        if builtin.get("_locked"):
            out["required"] = True
            out["enabled"] = True
        if out["type"] == FieldType.TEXT.value and raw.get("max_length") is not None:
            try:
                requested = int(raw["max_length"])
            except (TypeError, ValueError):
                fail("max_length must be a whole number.")
                return None
            ceiling = builtin["max_length"] or TEXT_MAX_LENGTH[0]
            out["max_length"] = max(1, min(requested, ceiling))
        return out

    if not _CUSTOM_KEY.match(key):
        fail("Custom field keys must look like c_<letters_digits_underscores> (at most 40 characters).")
        return None
    type_ = raw.get("type")
    if type_ not in _FIELD_TYPES:
        fail("Unknown field type.")
        return None
    out = {
        "key": key,
        "builtin": False,
        "type": type_,
        "label": label,
        "help_text": help_text,
        "placeholder": placeholder,
        "required": required,
        "enabled": enabled,
        "options": [],
        "min": None,
        "max": None,
        "max_length": None,
    }
    if type_ in _CHOICE_TYPES:
        options_raw = raw.get("options") or []
        if not isinstance(options_raw, list) or not 1 <= len(options_raw) <= MAX_OPTIONS:
            fail(f"Choice fields need 1 to {MAX_OPTIONS} options.")
            return None
        seen: set[str] = set()
        options: list[str] = []
        for option in options_raw:
            try:
                text = _clean_text(option, OPTION_MAX, allow_empty=False)
            except ValueError as exc:
                fail(f"Each option {exc}.")
                return None
            assert text is not None
            folded = text.casefold()
            if folded in seen:
                fail(f"Option {text!r} appears more than once.")
                return None
            seen.add(folded)
            options.append(text)
        out["options"] = options
    try:
        min_value = _number_or_none(raw.get("min"))
        max_value = _number_or_none(raw.get("max"))
    except ValueError as exc:
        fail(f"min/max {exc}.")
        return None
    if type_ == FieldType.NUMBER.value:
        if min_value is not None and max_value is not None and min_value > max_value:
            fail("min must not be greater than max.")
            return None
        out["min"], out["max"] = min_value, max_value
    elif type_ == FieldType.MULTISELECT.value and max_value is not None:
        if not float(max_value).is_integer() or not 1 <= max_value <= len(out["options"]):
            fail("max selections must be a whole number between 1 and the number of options.")
            return None
        out["max"] = int(max_value)
    if type_ in (FieldType.TEXT.value, FieldType.TEXTAREA.value):
        default, ceiling = TEXT_MAX_LENGTH if type_ == FieldType.TEXT.value else TEXTAREA_MAX_LENGTH
        requested_length = raw.get("max_length")
        if requested_length is None:
            out["max_length"] = default
        else:
            try:
                value = int(requested_length)
            except (TypeError, ValueError):
                fail("max_length must be a whole number.")
                return None
            if not 1 <= value <= ceiling:
                fail(f"max_length must be between 1 and {ceiling}.")
                return None
            out["max_length"] = value
    return out


def normalize_schema(raw: Any, *, version: int = 1) -> dict[str, Any]:
    """Validated, normalised form schema (``version`` set by the caller)."""
    fields_raw = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(fields_raw, list):
        raise ValidationFailed(
            "The registration form is invalid.",
            details={"errors": [{"index": None, "key": None, "message": "fields must be a list."}]},
        )
    errors: list[dict[str, Any]] = []
    if len(fields_raw) > MAX_FIELDS:
        errors.append(
            {"index": None, "key": None, "message": f"A form can have at most {MAX_FIELDS} fields."}
        )
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(fields_raw[: MAX_FIELDS + 1]):
        normalized = _normalize_field(item, errors, index)
        if normalized is None:
            continue
        if normalized["key"] in seen:
            errors.append({"index": index, "key": normalized["key"], "message": "Field keys must be unique."})
            continue
        seen.add(normalized["key"])
        fields.append(normalized)
    # Name and e-mail are always collected; other missing built-ins are kept, disabled.
    for builtin in reversed(BUILTIN_FIELDS):
        if builtin["key"] in seen:
            continue
        entry = public_builtin(builtin)
        if builtin.get("_locked"):
            fields.insert(0, entry)
        else:
            entry["enabled"] = False
            fields.append(entry)
    if len(fields) > MAX_FIELDS and not any(e["index"] is None for e in errors):
        errors.append(
            {"index": None, "key": None, "message": f"A form can have at most {MAX_FIELDS} fields."}
        )
    if errors:
        raise ValidationFailed("The registration form is invalid.", details={"errors": errors[:50]})
    return {"version": int(version), "fields": fields}


def enabled_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in schema.get("fields", []) if f.get("enabled")]


# =========================================================== answers


@dataclass(slots=True)
class ValidatedAnswers:
    full_name: str
    email: str
    email_key: str
    phone: str | None
    phone_key: str | None
    location: str | None
    snapshot: dict[str, Any]
    values: dict[str, Any] = field(default_factory=dict)


class _AnswerError(ValueError):
    pass


def _string(value: Any, limit: int, *, multiline: bool = False) -> str:
    if isinstance(value, bool) or isinstance(value, int | float):
        value = str(value)
    if not isinstance(value, str):
        raise _AnswerError("Please enter text.")
    text = unicodedata.normalize("NFC", value).strip()
    if (_CONTROL if multiline else _LINE_CONTROL).search(text):
        raise _AnswerError("This contains characters we can't accept.")
    if len(text) > limit:
        raise _AnswerError(f"Please keep this under {limit} characters.")
    return text


def _is_empty(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, list) and not value)
    )


def _check_number(value: Any, spec: dict[str, Any]) -> float | int:
    if isinstance(value, bool):
        raise _AnswerError("Please enter a number.")
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            raise _AnswerError("Please enter a number.") from exc
    elif isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            raise _AnswerError("Please enter a number.")
        parsed = Decimal(str(value))
    else:
        raise _AnswerError("Please enter a number.")
    if not parsed.is_finite():
        raise _AnswerError("Please enter a number.")
    exponent = parsed.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2 and parsed != parsed.quantize(Decimal("0.01")):
        raise _AnswerError("Please use at most 2 decimal places.")
    number = float(parsed)
    if spec.get("min") is not None and number < float(spec["min"]):
        raise _AnswerError(f"Please enter a value of at least {spec['min']}.")
    if spec.get("max") is not None and number > float(spec["max"]):
        raise _AnswerError(f"Please enter a value of at most {spec['max']}.")
    return int(number) if number.is_integer() else round(number, 2)


def _check_email(value: Any) -> str:
    text = _string(value, EMAIL_MAX).lower()
    if not _EMAIL.match(text):
        raise _AnswerError("Please enter a valid email address.")
    return text


def _check_phone(value: Any) -> str:
    text = _string(value, 40)
    if not _PHONE_CHARS.match(text):
        raise _AnswerError("Please use only digits, spaces and + - ( ) .")
    digits = len(phone_digits(text))
    if not 7 <= digits <= 15:
        raise _AnswerError("Please enter a phone number with 7 to 15 digits.")
    return text


def _check_url(value: Any) -> str:
    text = _string(value, URL_MAX)
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise _AnswerError("Please enter a valid link starting with https://") from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or " " in text:
        raise _AnswerError("Please enter a valid link starting with https://")
    return text


def _check_date(value: Any, today: date) -> str:
    if not isinstance(value, str):
        raise _AnswerError("Please enter a date.")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise _AnswerError("Please enter a date as YYYY-MM-DD.") from exc
    if len(value.strip()) != 10:
        raise _AnswerError("Please enter a date as YYYY-MM-DD.")
    if parsed < today - timedelta(days=1):
        raise _AnswerError("Please choose today or a later date.")
    if parsed > today + timedelta(days=DATE_MAX_AHEAD_DAYS):
        raise _AnswerError("Please choose a date within the next two years.")
    return parsed.isoformat()


def _check_choice(value: Any, options: list[str]) -> str:
    if not isinstance(value, str) or value not in options:
        raise _AnswerError("Please choose one of the options.")
    return value


def _check_multi(value: Any, spec: dict[str, Any]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _AnswerError("Please choose from the options.")
    options = spec.get("options") or []
    if len(set(value)) != len(value) or any(v not in options for v in value):
        raise _AnswerError("Please choose from the options.")
    if spec.get("max") is not None and len(value) > int(spec["max"]):
        raise _AnswerError(f"Please choose at most {int(spec['max'])}.")
    return [o for o in options if o in value]  # option order


def _check_value(spec: dict[str, Any], value: Any, today: date) -> Any:
    type_ = spec["type"]
    if type_ == FieldType.TEXT.value:
        return _string(value, int(spec.get("max_length") or TEXT_MAX_LENGTH[0]))
    if type_ == FieldType.TEXTAREA.value:
        return _string(value, int(spec.get("max_length") or TEXTAREA_MAX_LENGTH[0]), multiline=True)
    if type_ == FieldType.NUMBER.value:
        return _check_number(value, spec)
    if type_ == FieldType.EMAIL.value:
        return _check_email(value)
    if type_ == FieldType.PHONE.value:
        return _check_phone(value)
    if type_ == FieldType.URL.value:
        return _check_url(value)
    if type_ == FieldType.SELECT.value:
        return _check_choice(value, spec.get("options") or [])
    if type_ == FieldType.MULTISELECT.value:
        return _check_multi(value, spec)
    if type_ == FieldType.CHECKBOX.value:
        if not isinstance(value, bool):
            raise _AnswerError("Please tick or untick this box.")
        return value
    if type_ == FieldType.DATE.value:
        return _check_date(value, today)
    raise _AnswerError("This field cannot be answered.")


def validate_answers(
    schema: dict[str, Any], answers: dict[str, Any], *, today: date | None = None
) -> ValidatedAnswers:
    today = today or datetime.now(UTC).date()
    specs = {f["key"]: f for f in schema.get("fields", [])}
    errors: dict[str, str] = {}
    if not isinstance(answers, dict):
        raise ValidationFailed("Please check the highlighted fields.", details={"fields": {}})
    for key in answers:
        if key not in specs:
            errors[str(key)[:64]] = "This field is not part of the form."
    values: dict[str, Any] = {}
    snapshot_fields: list[dict[str, Any]] = []
    for spec in schema.get("fields", []):
        if not spec.get("enabled"):
            continue
        key = spec["key"]
        raw = answers.get(key)
        if spec["type"] == FieldType.CHECKBOX.value and raw is None:
            raw = False
        if _is_empty(raw) or (
            spec["type"] == FieldType.CHECKBOX.value and raw is False and spec.get("required")
        ):
            if spec.get("required"):
                errors[key] = (
                    "Please tick this box to continue."
                    if spec["type"] == FieldType.CHECKBOX.value
                    else "This field is required."
                )
                continue
            if spec["type"] != FieldType.CHECKBOX.value:
                continue
        try:
            value = _check_value(spec, raw, today)
        except _AnswerError as exc:
            errors[key] = str(exc)
            continue
        if isinstance(value, str) and not value:
            if spec.get("required"):
                errors[key] = "This field is required."
            continue
        values[key] = value
        snapshot_fields.append({"key": key, "label": spec["label"], "type": spec["type"], "value": value})
    if errors:
        raise ValidationFailed("Please check the highlighted fields.", details={"fields": errors})
    full_name = values["full_name"]
    email = values["email"]
    phone = values.get("phone")
    return ValidatedAnswers(
        full_name=full_name,
        email=email,
        email_key=email_key(email),
        phone=phone,
        phone_key=phone_key(phone),
        location=values.get("location"),
        snapshot={"form_version": int(schema.get("version", 1)), "fields": snapshot_fields},
        values=values,
    )
