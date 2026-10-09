"""Lenient parsing of ``interviews.policy_snapshot``.

The snapshot is frozen at invite time (``PolicySnapshot`` model dump). Keys
this version does not know are ignored (a newer writer must not break event
recording or the disclosure), invalid recruiter rule overrides are dropped
with a warning, and any other invalid value raises ``ValueError``.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.domain.enums import IntegrityEventType
from app.schemas.policies import IntegrityPolicy, IntegrityRule, PolicySnapshot
from app.services.integrity.rules import EffectiveRule, resolve_rules

log = get_logger(__name__)


def _known(model: type[BaseModel], data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key in model.model_fields}


def _parse_rules(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rules: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        candidate = _known(IntegrityRule, entry)
        try:
            IntegrityRule.model_validate(candidate)
        except ValidationError as exc:
            log.warning(
                "integrity.policy.rule_dropped",
                event_type=str(entry.get("event_type"))[:48],
                errors=exc.error_count(),
            )
            continue
        rules.append(candidate)
    return rules


def parse_policy_snapshot(snapshot: Mapping[str, Any] | None) -> PolicySnapshot:
    """Validate a stored snapshot leniently (unknown keys ignored)."""
    data = dict(snapshot or {})
    cleaned: dict[str, Any] = {}
    for name, field_info in PolicySnapshot.model_fields.items():
        if name not in data:
            continue
        value = data[name]
        annotation = field_info.annotation
        if isinstance(value, Mapping) and isinstance(annotation, type) and issubclass(annotation, BaseModel):
            value = _known(annotation, value)
            if annotation is IntegrityPolicy and "rules" in value:
                value["rules"] = _parse_rules(value["rules"])
        cleaned[name] = value
    try:
        return PolicySnapshot.model_validate(cleaned)
    except ValidationError as exc:
        raise ValueError(f"invalid policy snapshot ({exc.error_count()} errors)") from exc


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    snapshot: PolicySnapshot
    rules: Mapping[IntegrityEventType, EffectiveRule]
    valid: bool  # False when the stored snapshot was invalid (monitoring then stays off)

    @property
    def integrity(self) -> IntegrityPolicy:
        return self.snapshot.integrity


_CACHE: OrderedDict[str, ResolvedPolicy] = OrderedDict()
_CACHE_SIZE = 256


def resolve_policy(snapshot: Mapping[str, Any] | None) -> ResolvedPolicy:
    """Parse + resolve rules, memoised by the snapshot's canonical JSON.

    An invalid snapshot never turns on monitoring the candidate was not told
    about: it resolves to a policy with integrity monitoring disabled."""
    key = hashlib.sha256(canonical_json(dict(snapshot or {})).encode("utf-8")).hexdigest()
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    try:
        parsed = parse_policy_snapshot(snapshot)
        valid = True
    except ValueError as exc:
        log.error("integrity.policy.invalid_snapshot", error=str(exc))
        parsed = PolicySnapshot(integrity=IntegrityPolicy(enabled=False))
        valid = False
    resolved = ResolvedPolicy(snapshot=parsed, rules=resolve_rules(parsed.integrity), valid=valid)
    _CACHE[key] = resolved
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return resolved
