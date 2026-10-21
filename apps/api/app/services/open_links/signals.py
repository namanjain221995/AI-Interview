"""Soft duplicate signals (OPEN_LINKS.md section 4.2).

Only keyed HMACs are stored: the ``ol_device`` cookie value, the client IP
and IP + User-Agent. Raw IPs and user agents never reach the open-link tables.
``signal_key`` = ``OPEN_LINK_SIGNAL_SECRET`` or, when unset,
``HMAC_SHA256(MEDIA_URL_SECRET, "open-link-signals-v1")``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response

from app.core.config import get_settings
from app.core.ratelimit import client_ip_key
from app.services.open_links.constants import (
    DEVICE_COOKIE,
    DEVICE_COOKIE_MAX_AGE,
    DEVICE_COOKIE_PATH,
    FlagCode,
)

_DEVICE_VALUE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

FLAG_MESSAGES: dict[str, str] = {
    FlagCode.SAME_DEVICE.value: "Registered from the same browser as another registration on this link.",
    FlagCode.SAME_RESUME.value: "Uploaded the same resume file as another registration.",
    FlagCode.SAME_NETWORK.value: (
        "Registered from the same network and browser type as another registration within 15 minutes."
    ),
}
FLAG_SEVERITY: dict[str, str] = {
    FlagCode.SAME_DEVICE.value: "high",
    FlagCode.SAME_RESUME.value: "high",
    FlagCode.SAME_NETWORK.value: "low",
}


def signal_key() -> bytes:
    settings = get_settings()
    if settings.open_link_signal_secret is not None and settings.open_link_signal_secret.get_secret_value():
        return settings.open_link_signal_secret.get_secret_value().encode("utf-8")
    base = settings.media_url_secret.get_secret_value().encode("utf-8")
    return hmac.new(base, b"open-link-signals-v1", hashlib.sha256).digest()


def signal_hash(kind: str, value: str) -> str:
    return hmac.new(signal_key(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def ip_hash(ip: str) -> str:
    return signal_hash("ip", ip)


def ip_ua_hash(ip: str, user_agent: str) -> str:
    return signal_hash("ipua", f"{ip}\n{user_agent}")


def device_hash(value: str) -> str:
    return signal_hash("device", value)


def read_device_cookie(request: Request) -> str | None:
    value = request.cookies.get(get_settings().cookie_name_prefix + DEVICE_COOKIE)
    return value if value and _DEVICE_VALUE.match(value) else None


def new_device_value() -> str:
    return secrets.token_urlsafe(32)


def set_device_cookie(response: Response, value: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.cookie_name_prefix + DEVICE_COOKIE,
        value,
        max_age=DEVICE_COOKIE_MAX_AGE,
        path=DEVICE_COOKIE_PATH,
        domain=settings.cookie_domain or None,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


class RequestSignals:
    """Hashes for one registration request (computed once)."""

    def __init__(self, request: Request) -> None:
        ip = client_ip_key(request)  # IPv6 by /64: one network, however its addresses rotate (S15)
        user_agent = (request.headers.get("user-agent") or "")[:512]
        existing = read_device_cookie(request)
        self.device_value = existing or new_device_value()
        self.device_is_new = existing is None
        self.device_hash = device_hash(self.device_value)
        self.ip_hash = ip_hash(ip)
        self.ip_ua_hash = ip_ua_hash(ip, user_agent)


def make_flag(
    code: str, related_registration_id: uuid.UUID | None, *, at: datetime | None = None
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": FLAG_SEVERITY[code],
        "message": FLAG_MESSAGES[code],
        "related_registration_id": str(related_registration_id) if related_registration_id else None,
        "at": (at or datetime.now(UTC)).isoformat(),
    }


def has_high_flag(flags: list[dict[str, Any]]) -> bool:
    return any(f.get("severity") == "high" for f in flags)
