"""Duplicate keys for exact identity matching (OPEN_LINKS.md section 4.1).

* ``email_key``: lower-case, ``+tag`` removed from the local part, dots
  removed for gmail.com / googlemail.com (``googlemail.com`` folds into
  ``gmail.com``).
* ``phone_key``: digits only; the last 10 digits when there are 10 or more,
  so ``+91 98xxx xxxxx`` and ``098xxx xxxxx`` match.
"""

from __future__ import annotations

import re

_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_NON_DIGITS = re.compile(r"\D+")


def email_key(email: str) -> str:
    value = (email or "").strip().lower()
    local, sep, domain = value.rpartition("@")
    if not sep:
        return value
    local = local.split("+", 1)[0]
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"[:320]


def phone_digits(phone: str | None) -> str:
    return _NON_DIGITS.sub("", phone or "")


def phone_key(phone: str | None) -> str | None:
    digits = phone_digits(phone)
    if not digits:
        return None
    return digits[-10:] if len(digits) >= 10 else digits
