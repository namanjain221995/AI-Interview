"""Enums, limits and the built-in registration field catalog for open links.

These enums live here, not in ``app.domain.enums``: the web ``types.test.ts``
parses that file and fails on drift. They are mirrored in
``apps/web/src/features/links/types.ts``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class LinkStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class LinkState(StrEnum):
    OPEN = "open"
    SCHEDULED = "scheduled"
    PAUSED = "paused"
    CLOSED = "closed"
    EXPIRED = "expired"
    FULL = "full"


class Progression(StrEnum):
    IMMEDIATE = "immediate"
    AFTER_REVIEW = "after_review"


class RegistrationStatus(StrEnum):
    REGISTERED = "registered"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class RoundState(StrEnum):
    LOCKED = "locked"
    INVITED = "invited"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class UnlockedBy(StrEnum):
    REGISTRATION = "registration"
    AUTO = "auto"
    RECRUITER = "recruiter"
    RETAKE = "retake"


class FieldType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    SELECT = "select"
    MULTISELECT = "multiselect"
    CHECKBOX = "checkbox"
    DATE = "date"


class FlagCode(StrEnum):
    SAME_DEVICE = "same_device"
    SAME_RESUME = "same_resume"
    SAME_NETWORK = "same_network"


class FlagReview(StrEnum):
    NONE = "none"
    OPEN = "open"
    CLEARED = "cleared"
    CONFIRMED = "confirmed"


# ------------------------------------------------------------------ limits

MAX_FIELDS = 30
MAX_OPTIONS = 50
MAX_ROUNDS = 10
LABEL_MAX = 120
HELP_TEXT_MAX = 300
PLACEHOLDER_MAX = 120
OPTION_MAX = 120
CUSTOM_KEY_PATTERN = r"^c_[a-z0-9_]{1,40}$"
TEXT_MAX_LENGTH = (200, 500)  # (default, ceiling)
TEXTAREA_MAX_LENGTH = (1000, 2000)
EMAIL_MAX = 320
URL_MAX = 500
DATE_MAX_AHEAD_DAYS = 730
LINK_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{20,128}$"  # noqa: S105 - a format check, not a secret
NETWORK_WINDOW_SECONDS = 15 * 60
HONEYPOT_FIELD = "website"
DEVICE_COOKIE = "ol_device"
DEVICE_COOKIE_PATH = "/api/v1/link-access"
DEVICE_COOKIE_MAX_AGE = 180 * 24 * 3600

# Registration statuses that mean "has a live attempt" (resumable on the same device).
ACTIVE_STATUSES = frozenset({RegistrationStatus.REGISTERED.value, RegistrationStatus.IN_PROGRESS.value})
# Session end reasons that route an immediate-progression registration to a person.
REVIEW_END_REASONS = frozenset({"policy_termination", "technical_failure", "disconnect_timeout"})

DEFAULT_DUPLICATE_POLICY: dict[str, str] = {"scope": "job", "device": "flag", "network": "flag"}

NOTICE_PERIOD_OPTIONS = ["Immediately", "15 days", "30 days", "60 days", "90 days", "More than 90 days"]


def _builtin(
    key: str,
    type_: FieldType,
    label: str,
    *,
    required: bool = False,
    enabled: bool = False,
    options: list[str] | None = None,
    min_: float | None = None,
    max_: float | None = None,
    max_length: int | None = None,
    maps_to: str | None = None,
    locked: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "builtin": True,
        "type": type_.value,
        "label": label,
        "help_text": None,
        "placeholder": None,
        "required": required,
        "enabled": enabled,
        "options": list(options or []),
        "min": min_,
        "max": max_,
        "max_length": max_length,
        "_maps_to": maps_to,
        "_locked": locked,
    }


# Built-in field catalog (``GET /open-links/form/catalog``). ``_locked`` fields
# cannot be disabled or made optional; ``_maps_to`` names the candidate column.
BUILTIN_FIELDS: list[dict[str, Any]] = [
    _builtin(
        "full_name",
        FieldType.TEXT,
        "Full name",
        required=True,
        enabled=True,
        max_length=200,
        maps_to="full_name",
        locked=True,
    ),
    _builtin("email", FieldType.EMAIL, "Email", required=True, enabled=True, maps_to="email", locked=True),
    _builtin("phone", FieldType.PHONE, "Mobile number", required=True, enabled=True, maps_to="phone"),
    _builtin("location", FieldType.TEXT, "City", max_length=120, maps_to="location"),
    _builtin("linkedin_url", FieldType.URL, "LinkedIn profile"),
    _builtin("portfolio_url", FieldType.URL, "Portfolio or GitHub"),
    _builtin("current_title", FieldType.TEXT, "Current job title", max_length=200),
    _builtin("current_company", FieldType.TEXT, "Current company", max_length=200),
    _builtin("years_experience", FieldType.NUMBER, "Years of experience", min_=0, max_=60),
    _builtin("notice_period", FieldType.SELECT, "Notice period", options=NOTICE_PERIOD_OPTIONS),
    _builtin("available_from", FieldType.DATE, "Available from"),
]
BUILTIN_BY_KEY: dict[str, dict[str, Any]] = {f["key"]: f for f in BUILTIN_FIELDS}


def public_builtin(field: dict[str, Any]) -> dict[str, Any]:
    """Catalog entry without the private ``_`` keys."""
    return {k: v for k, v in field.items() if not k.startswith("_")}
