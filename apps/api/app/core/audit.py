"""Audit trail.

``await audit(db, principal, "job.create", resource_type="job", resource_id=str(job.id), details={...})``
adds an ``audit_logs`` row to the caller's transaction (it is committed with
the request). Details are made JSON-safe, size-capped, and any key that looks
like a credential is redacted.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, get_request_id
from app.core.rbac import Principal
from app.db.models import AuditLog
from app.domain.enums import ActorType, SubjectType

log = get_logger(__name__)

_SENSITIVE = re.compile(
    r"^(?:.*_)?(?:password|passwd|secret|token|authorization|cookies?|api_?key|access_code|credentials?|password_hash)$",
    re.IGNORECASE,
)
_MAX_DEPTH = 6
_MAX_ITEMS = 100
_MAX_STRING = 2000


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "[truncated]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "...[truncated]"
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value, depth + 1)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for i, (key, item) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["_truncated"] = True
                break
            name = str(key)
            out[name] = "[redacted]" if _SENSITIVE.match(name) else _json_safe(item, depth + 1)
        return out
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        return [_json_safe(item, depth + 1) for item in items[:_MAX_ITEMS]]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"), depth + 1)
    return _json_safe(str(value), depth + 1)


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    safe = _json_safe(details)
    return safe if isinstance(safe, dict) else {"value": safe}


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    try:
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


async def audit(
    db: AsyncSession,
    principal: Principal | None,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    details: Mapping[str, Any] | None = None,
    request: Request | None = None,
    organization_id: uuid.UUID | None = None,
) -> AuditLog:
    """Record an auditable action in the caller's transaction.

    ``principal=None`` records a system action. ``organization_id`` defaults to
    the principal's organization; pass it explicitly for cross-org actions by a
    SUPER_ADMIN so the entry lands in the affected organization's trail.
    """
    if principal is None:
        actor_type, actor_id = ActorType.SYSTEM, None
    elif principal.subject_type is SubjectType.CANDIDATE:
        actor_type, actor_id = ActorType.CANDIDATE, principal.candidate_id
    else:
        actor_type, actor_id = ActorType.USER, principal.user_id
    org_id = organization_id if organization_id is not None else (principal.organization_id if principal else None)
    request_id = None
    user_agent = None
    if request is not None:
        request_id = getattr(request.state, "request_id", None)
        user_agent = request.headers.get("user-agent")
    entry = AuditLog(
        organization_id=org_id,
        actor_type=actor_type.value,
        actor_id=actor_id,
        action=action[:100],
        resource_type=resource_type[:64] if resource_type else None,
        resource_id=str(resource_id)[:64] if resource_id is not None else None,
        ip_address=_client_ip(request),
        user_agent=user_agent[:512] if user_agent else None,
        request_id=(str(request_id or get_request_id() or "")[:64] or None),
        details=sanitize_details(details),
    )
    db.add(entry)
    log.info(
        "audit",
        action=action,
        actor_type=actor_type.value,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
    )
    return entry
