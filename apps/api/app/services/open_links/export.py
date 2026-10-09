"""Registrations CSV export (OPEN_LINKS.md sections 8.4, 9.2).

Columns: ``registered_at, full_name, email, phone, status, status_reason,
flags``, then per link round ``round_<n>_name, round_<n>_state,
round_<n>_completed_at, round_<n>_session_id``, then one column per form field
label (custom and optional built-ins). No scores, no signal hashes. Every cell
starting with ``= + - @ \\t \\r`` is prefixed with ``'`` (formula injection).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.db.models import InterviewLink, LinkRegistration
from app.schemas.api.open_links import Registration

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_IDENTITY_KEYS = frozenset({"full_name", "email", "phone"})


def safe_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, list):
        text = "; ".join(str(v) for v in value)
    elif isinstance(value, datetime):
        text = value.astimezone(UTC).isoformat()
    else:
        text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def filename(link: InterviewLink, now: datetime | None = None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", link.name.lower()).strip("-")[:60] or "link"
    return f"{slug}-registrations-{(now or datetime.now(UTC)).strftime('%Y%m%d')}.csv"


def build_csv(
    link: InterviewLink, registrations: Sequence[LinkRegistration], views: Sequence[Registration]
) -> tuple[str, int]:
    schema_fields = [
        f for f in (link.form_schema or {}).get("fields", []) if f.get("key") not in _IDENTITY_KEYS
    ]
    answer_columns = [(f["key"], f.get("label") or f["key"]) for f in schema_fields]
    # Answers for fields removed from the form since registration are kept too.
    known = {key for key, _ in answer_columns}
    for registration in registrations:
        for item in (registration.answers or {}).get("fields", []):
            key = item.get("key")
            if key and key not in known and key not in _IDENTITY_KEYS:
                answer_columns.append((key, item.get("label") or key))
                known.add(key)
    round_count = len(link.round_ids or [])
    header = ["registered_at", "full_name", "email", "phone", "status", "status_reason", "flags"]
    for n in range(1, round_count + 1):
        header += [f"round_{n}_name", f"round_{n}_state", f"round_{n}_completed_at", f"round_{n}_session_id"]
    header += [label for _, label in answer_columns]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([safe_cell(h) for h in header])
    by_id = {v.id: v for v in views}
    for registration in registrations:
        view = by_id.get(registration.id)
        answers = {
            item.get("key"): item.get("value") for item in (registration.answers or {}).get("fields", [])
        }
        row: list[Any] = [
            registration.created_at,
            registration.full_name,
            registration.email,
            registration.phone,
            registration.status,
            registration.status_reason,
            ", ".join(sorted({str(f.get("code")) for f in registration.flags or []})),
        ]
        rounds = view.rounds if view is not None else []
        for n in range(round_count):
            item = rounds[n] if n < len(rounds) else None
            row += [
                item.name if item else "",
                item.state if item else "",
                item.completed_at if item else None,
                item.session_id if item and item.session_id else None,
            ]
        row += [answers.get(key) for key, _ in answer_columns]
        writer.writerow([safe_cell(v) for v in row])
    return buffer.getvalue(), len(registrations)
