"""Auth cookies (CONTRACTS.md section 3).

Staff and candidate sessions use separate cookie sets (G2), so a recruiter who opens a candidate link in the
same browser stays signed in:

* staff:     ``access_token`` (HttpOnly, path ``/api``), ``refresh_token`` (HttpOnly, path ``/api/v1/auth``),
  ``csrf_token`` (readable, path ``/``).
* candidate: ``cand_access_token``, ``cand_refresh_token``, ``cand_csrf_token`` (same paths and flags).

``COOKIE_NAME_PREFIX`` is prepended to every name (``cookie_names``): browsers share cookies across ports, so the
dev copy on :3201 (``dev_``) never reads or overwrites production's cookies on :3200.

Which set a request uses (``request_audience``): the candidate-only API areas (portal, candidate-access,
link-access, link-return) always mean the candidate; the shared endpoints (``/auth/refresh``, ``/auth/logout``,
``/media``) mean the candidate when the SPA sends ``X-Session-Audience: candidate`` from a candidate page;
everything else is staff. Migration window: candidate sessions started before G2 still carry the staff names;
for the candidate audience those are accepted as a fallback (a refresh from the current SPA moves them to the
new names; a refresh from a tab still running the pre-G2 bundle, which sends no audience header, keeps them where
that bundle reads them). The fallback only ever yields a candidate's session: refresh and logout ignore a staff
session found that way. It can go once every pre-G2 candidate session has expired
(CANDIDATE_SESSION_TTL_SECONDS).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import Request, Response

from app.core.config import get_settings
from app.domain.enums import SubjectType
from app.services.identity.sessions import IssuedTokens

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CANDIDATE_ACCESS_COOKIE = "cand_access_token"
CANDIDATE_REFRESH_COOKIE = "cand_refresh_token"
CANDIDATE_CSRF_COOKIE = "cand_csrf_token"
AUDIENCE_HEADER = "X-Session-Audience"
CANDIDATE_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/portal/",
    "/api/v1/candidate-access/",
    "/api/v1/link-access/",
    "/api/v1/link-return/",
)

Audience = Literal["staff", "candidate"]


@dataclass(frozen=True, slots=True)
class CookieNames:
    access: str
    refresh: str
    csrf: str


STAFF_COOKIES = CookieNames(ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
CANDIDATE_COOKIES = CookieNames(CANDIDATE_ACCESS_COOKIE, CANDIDATE_REFRESH_COOKIE, CANDIDATE_CSRF_COOKIE)


def cookie_names(audience: Audience) -> CookieNames:
    """The audience's cookie names with ``COOKIE_NAME_PREFIX`` applied."""
    names = CANDIDATE_COOKIES if audience == "candidate" else STAFF_COOKIES
    prefix = get_settings().cookie_name_prefix
    if not prefix:
        return names
    return CookieNames(prefix + names.access, prefix + names.refresh, prefix + names.csrf)


def request_audience(request: Request) -> Audience:
    """Whose cookies this request uses (see the module docstring)."""
    path = request.url.path
    if path.startswith(CANDIDATE_PATH_PREFIXES) or path == "/api/v1/portal":
        return "candidate"
    if (request.headers.get(AUDIENCE_HEADER) or "").strip().lower() == "candidate":
        return "candidate"
    return "staff"


def _cookie(request: Request, kind: Literal["access", "refresh", "csrf"]) -> tuple[str | None, bool]:
    """``(value, legacy)``: the audience's cookie, else (candidate audience only, migration window) the staff
    name a pre-G2 candidate session still carries."""
    audience = request_audience(request)
    value = request.cookies.get(getattr(cookie_names(audience), kind))
    if value or audience == "staff":
        return value or None, False
    legacy = request.cookies.get(getattr(cookie_names("staff"), kind))
    return (legacy, True) if legacy else (None, False)


def access_cookie(request: Request) -> tuple[str | None, bool]:
    """``(token, legacy)``; a legacy token only counts when it is a candidate's (checked by the caller)."""
    return _cookie(request, "access")


def csrf_cookie(request: Request) -> str | None:
    return _cookie(request, "csrf")[0]


def refresh_cookie(request: Request) -> tuple[str | None, bool]:
    return _cookie(request, "refresh")


ACCESS_COOKIE_PATH = "/api"
REFRESH_COOKIE_PATH = "/api/v1/auth"
CSRF_COOKIE_PATH = "/"


def _max_age(expires_at: datetime) -> int:
    return max(0, int((expires_at - datetime.now(UTC)).total_seconds()))


def set_auth_cookies(response: Response, tokens: IssuedTokens, names: CookieNames | None = None) -> None:
    """Sets the cookie set of the session's audience (staff or candidate). ``names`` overrides it: a tab still
    running the pre-G2 bundle keeps a candidate session under the staff names, which is where it reads them."""
    settings = get_settings()
    names = names or cookie_names("candidate" if tokens.subject_type is SubjectType.CANDIDATE else "staff")
    common = {
        "domain": settings.cookie_domain or None,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
    }
    response.set_cookie(
        names.access,
        tokens.access_token,
        max_age=_max_age(tokens.access_expires_at),
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        **common,  # type: ignore[arg-type]
    )
    csrf_expiry = tokens.refresh_expires_at or tokens.session_expires_at
    response.set_cookie(
        names.csrf,
        tokens.csrf_token,
        max_age=_max_age(csrf_expiry),
        path=CSRF_COOKIE_PATH,
        httponly=False,
        **common,  # type: ignore[arg-type]
    )
    if tokens.refresh_token is not None and tokens.refresh_expires_at is not None:
        response.set_cookie(
            names.refresh,
            tokens.refresh_token,
            max_age=_max_age(tokens.refresh_expires_at),
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            **common,  # type: ignore[arg-type]
        )


def clear_auth_cookies(response: Response, audience: Audience = "staff") -> None:
    """Clears one audience's cookie set (never the other one: a candidate signing out keeps the staff session)."""
    settings = get_settings()
    names = cookie_names(audience)
    for name, path, httponly in (
        (names.access, ACCESS_COOKIE_PATH, True),
        (names.refresh, REFRESH_COOKIE_PATH, True),
        (names.csrf, CSRF_COOKIE_PATH, False),
    ):
        response.delete_cookie(
            name,
            path=path,
            domain=settings.cookie_domain or None,
            secure=settings.cookie_secure,
            httponly=httponly,
            samesite=settings.cookie_samesite,
        )
