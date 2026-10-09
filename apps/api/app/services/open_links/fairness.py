"""Protected-trait guard for registration forms (OPEN_LINKS.md section 2.3).

``check_form(schema, acknowledged)`` scans every custom field (enabled or not)
- key, label, help text, placeholder and options, lower-cased with ``_``/``-``
turned into spaces - plus built-in fields whose wording a recruiter changed:

1. short-label rules (form labels are terse: "Age", "Gender"), mirrored by the
   web builder in ``features/links/fairness.ts``;
2. the catalog lint ``app.seed.fairness.fairness_hits`` for specific phrases.

Blocked topics can never be saved (422 ``protected_field``). Warnings need an
acknowledgement ``"<key>:<code>"`` (422 ``form_warnings_unacknowledged``).
Warning phrases are blanked before the catalog lint runs, so a question the
guard only warns about (e.g. "work authorization") is not also blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationFailed
from app.seed.fairness import fairness_hits
from app.services.open_links.constants import BUILTIN_BY_KEY


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in parts))


TOPIC_LABELS: dict[str, str] = {
    "age": "age or date of birth",
    "gender": "gender",
    "family_status": "marital or family status",
    "religion": "religion, caste or ethnicity",
    "nationality": "nationality or citizenship",
    "appearance": "photos or physical appearance",
    "health": "health or disability",
    "salary_history": "pay history",
}

BLOCKED_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "age",
        _rx(
            r"\bage\b",
            r"\bdob\b",
            r"\bdates? of birth\b",
            r"\bbirth ?dates?\b",
            r"\bbirthdays?\b",
            r"\byear of birth\b",
            r"\bgraduation year\b",
            r"\bpassing year\b",
            r"\bbatch year\b",
        ),
    ),
    ("gender", _rx(r"\bgender\b", r"\bsex\b", r"\bpronouns?\b")),
    (
        "family_status",
        _rx(
            r"\bmarital\b",
            r"\bmarried\b",
            r"\bspouses?\b",
            r"\bhusbands?\b",
            r"\bwi(?:fe|ves)\b",
            r"\bchildren\b",
            r"\bkids\b",
            r"\bdependents?\b",
            r"\bpregnan",
            r"\bfather(?:'?s)? name\b",
            r"\bmother(?:'?s)? name\b",
            r"\bparents?(?:'?s)? name\b",
            r"\bguardian",
        ),
    ),
    (
        "religion",
        _rx(
            r"\breligio",
            r"\bfaith\b",
            r"\bcaste\b",
            r"\bsub caste\b",
            r"\bcommunity\b",
            r"\btribes?\b",
            r"\brace\b",
            r"\bethnic",
        ),
    ),
    (
        "nationality",
        _rx(
            r"\bnationality\b",
            r"\bcitizenship\b",
            r"\bcitizens?\b",
            r"\bpassports?\b",
            r"\bcountry of birth\b",
            r"\bplace of birth\b",
            r"\bvisa (?:status|type)\b",
        ),
    ),
    (
        "appearance",
        _rx(
            r"\bphotos?\b",
            r"\bphotograph",
            r"\bpictures?\b",
            r"\bselfies?\b",
            r"\bheadshots?\b",
            r"\bheight\b",
            r"\bweight\b",
            r"\bcomplexion\b",
        ),
    ),
    (
        "health",
        _rx(r"\bhealth", r"\bmedical\b", r"\bdisab", r"\bhandicap", r"\bblood group\b", r"\billness"),
    ),
    (
        "salary_history",
        _rx(
            r"\bcurrent (?:salary|ctc|pay|compensation)\b",
            r"\blast drawn\b",
            r"\bprevious (?:salary|ctc)\b",
            r"\bsalary history\b",
        ),
    ),
)
HONORIFIC = re.compile(r"^(?:mr|mrs|ms|miss)\.?$")

WARNING_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "expected_pay",
        _rx(r"\bexpected (?:salary|ctc|pay)\b"),
        "Ask only for expectations, never pay history.",
    ),
    (
        "right_to_work",
        _rx(
            r"\bwork authori[sz]ation\b",
            r"\bright to work\b",
            r"\blegally (?:authori[sz]ed|allowed)\b(?: to work\b)?",
            r"\b(?:authori[sz]ed|eligible) to work\b",
        ),
        "Ask only a yes/no about the job's location; never ask for nationality or visa type.",
    ),
    (
        "address",
        _rx(r"\baddress", r"\bpin ?code\b", r"\bpostal\b", r"\bzip\b"),
        "A city is enough; a full address is personal data you probably don't need.",
    ),
    (
        "employment_gap",
        _rx(r"\bgaps?\b", r"\bcareer break"),
        "Gaps can reveal health, caregiving or family status.",
    ),
    (
        "languages",
        _rx(r"\bmother tongue\b", r"\bnative language\b"),
        "Can reveal national or ethnic origin; ask about job-required languages instead.",
    ),
)

# Catalog-lint topics -> form topics.
_SEED_TOPICS: dict[str, str] = {
    "age": "age",
    "date of birth": "age",
    "marital or family status": "family_status",
    "pregnancy": "family_status",
    "children": "family_status",
    "health": "health",
    "disability": "health",
    "religion": "religion",
    "race or ethnicity": "religion",
    "national origin": "nationality",
    "citizenship or visa status": "nationality",
    "accent": "nationality",
    "sexual orientation": "gender",
    "gender identity": "gender",
    "salary history": "salary_history",
    "appearance": "appearance",
}


def normalize_text(text: str | None) -> str:
    return " ".join(re.sub(r"[_-]+", " ", (text or "").lower()).split())


def blocked_message(topic: str) -> str:
    return f"This asks for {TOPIC_LABELS.get(topic, topic)}, which we never collect. Remove the field or reword it."


def warning_key(field_key: str, code: str) -> str:
    return f"{field_key}:{code}"


@dataclass(slots=True)
class FairnessReport:
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]

    @property
    def unacknowledged(self) -> list[dict[str, Any]]:
        return [w for w in self.warnings if not w["acknowledged"]]


def _field_texts(field: dict[str, Any]) -> list[str]:
    if field.get("builtin"):
        catalog = BUILTIN_BY_KEY.get(field.get("key", ""), {})
        # Built-in keys/options are fixed and safe; only reworded text is scanned.
        values = [
            v
            for name in ("label", "help_text", "placeholder")
            if (v := field.get(name)) and v != catalog.get(name)
        ]
    else:
        values = [field.get("key"), field.get("label"), field.get("help_text"), field.get("placeholder")]
        values += list(field.get("options") or [])
    return [t for t in (normalize_text(v) for v in values if isinstance(v, str)) if t]


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def check_form(schema: dict[str, Any], acknowledged: list[str] | None = None) -> FairnessReport:
    acked = set(acknowledged or [])
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for field in schema.get("fields", []):
        texts = _field_texts(field)
        if not texts:
            continue
        key, label = field.get("key", ""), field.get("label", "")
        topic = next((t for t, rule in BLOCKED_RULES if any(rule.search(x) for x in texts)), None)
        if topic is None and not field.get("builtin"):
            if any(HONORIFIC.match(normalize_text(o)) for o in field.get("options") or []):
                topic = "gender"
        field_warnings = [
            (code, rule, message)
            for code, rule, message in WARNING_RULES
            if any(rule.search(x) for x in texts)
        ]
        if topic is None:
            for text in texts:
                for _, rule, _ in WARNING_RULES:
                    text = _blank(text, rule)
                hits = fairness_hits(text, field=key)
                if hits:
                    topic = _SEED_TOPICS.get(hits[0].topic, "family_status")
                    break
        if topic is not None:
            errors.append({"key": key, "label": label, "topic": topic, "message": blocked_message(topic)})
            continue
        for code, _, message in field_warnings:
            warnings.append(
                {
                    "key": key,
                    "code": code,
                    "message": message,
                    "acknowledged": warning_key(key, code) in acked,
                }
            )
    return FairnessReport(errors=errors, warnings=warnings)


def enforce_form(schema: dict[str, Any], acknowledged: list[str] | None = None) -> FairnessReport:
    """Raise 422 ``protected_field`` / ``form_warnings_unacknowledged``; return the report."""
    report = check_form(schema, acknowledged)
    if report.errors:
        topics = sorted({TOPIC_LABELS.get(e["topic"], e["topic"]) for e in report.errors})
        raise ValidationFailed(
            "This form asks for information we never collect: " + ", ".join(topics) + ".",
            code="protected_field",
            details={"fields": report.errors},
        )
    if report.unacknowledged:
        raise ValidationFailed(
            "Some form questions need your confirmation before they can be used.",
            code="form_warnings_unacknowledged",
            details={"warnings": report.unacknowledged},
        )
    return report
