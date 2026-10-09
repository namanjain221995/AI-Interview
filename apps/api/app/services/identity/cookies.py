"""Auth cookies (CONTRACTS.md section 3).

* ``access_token``  HttpOnly, path ``/api``, lifetime of the JWT.
* ``refresh_token`` HttpOnly, path ``/api/v1/auth`` (only sent to refresh/logout).
* ``csrf_token``    readable by JS, path ``/`` so the SPA can echo it in
  ``X-CSRF-Token`` (double submit, HMAC-bound to the auth session).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Response

from app.core.config import get_settings
from app.services.identity.sessions import IssuedTokens

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

ACCESS_COOKIE_PATH = "/api"
REFRESH_COOKIE_PATH = "/api/v1/auth"
CSRF_COOKIE_PATH = "/"


def _max_age(expires_at: datetime) -> int:
    return max(0, int((expires_at - datetime.now(UTC)).total_seconds()))


def set_auth_cookies(response: Response, tokens: IssuedTokens) -> None:
    settings = get_settings()
    common = {
        "domain": settings.cookie_domain or None,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
    }
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        max_age=_max_age(tokens.access_expires_at),
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        **common,  # type: ignore[arg-type]
    )
    csrf_expiry = tokens.refresh_expires_at or tokens.session_expires_at
    response.set_cookie(
        CSRF_COOKIE,
        tokens.csrf_token,
        max_age=_max_age(csrf_expiry),
        path=CSRF_COOKIE_PATH,
        httponly=False,
        **common,  # type: ignore[arg-type]
    )
    if tokens.refresh_token is not None and tokens.refresh_expires_at is not None:
        response.set_cookie(
            REFRESH_COOKIE,
            tokens.refresh_token,
            max_age=_max_age(tokens.refresh_expires_at),
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            **common,  # type: ignore[arg-type]
        )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name, path, httponly in (
        (ACCESS_COOKIE, ACCESS_COOKIE_PATH, True),
        (REFRESH_COOKIE, REFRESH_COOKIE_PATH, True),
        (CSRF_COOKIE, CSRF_COOKIE_PATH, False),
    ):
        response.delete_cookie(
            name,
            path=path,
            domain=settings.cookie_domain or None,
            secure=settings.cookie_secure,
            httponly=httponly,
            samesite=settings.cookie_samesite,
        )
