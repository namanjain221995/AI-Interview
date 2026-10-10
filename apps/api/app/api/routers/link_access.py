"""Public open-link access (OPEN_LINKS.md sections 3.2, 3.3, 9.3; unauthenticated).

``GET  /link-access/{token}``           landing info (no internal ids, no counts)
``POST /link-access/{token}/register``  registration -> candidate cookies

Malformed and unknown tokens both answer 404 ``link_not_found``. The POST has
no cookie principal (like ``candidate-access``): it relies on an
Origin/Referer allowlist, JSON-only bodies, ``SameSite=Lax`` cookies and the
fact that it only creates the caller's own session. Registrations never bind to an
existing candidate with history from an unknown browser (S4; OPEN_LINKS.md section on
candidate reuse): verified reuse arrives with email verification (G4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.config import get_settings
from app.core.deps import DbSession
from app.core.errors import NotFound
from app.core.pow import create_challenge
from app.core.ratelimit import client_ip_key, enforce_rate_limit, rate_limit
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.open_links import LinkInfo, PowChallengeOut, RegisterRequest, RegisterResponse
from app.services.identity.cookies import set_auth_cookies
from app.services.open_links.links import find_link_by_token, public_info
from app.services.open_links.registration import register
from app.services.open_links.signals import set_device_cookie

router = APIRouter(prefix="/link-access", tags=["link-access"], responses=ERROR_RESPONSES)

INFO_IP_LIMIT = 60  # per minute


def _register_ip_limit() -> int:
    return get_settings().open_link_register_ip_limit_per_hour


async def _register_ip_dependency(request: Request) -> None:
    await enforce_rate_limit(
        "link_register_ip", client_ip_key(request), limit=_register_ip_limit(), window_s=3600
    )


CHALLENGE_IP_LIMIT = 30  # per minute: one per page load, a few retries


@router.get(
    "/{token}/challenge",
    response_model=PowChallengeOut,
    dependencies=[Depends(rate_limit("link_access_challenge", CHALLENGE_IP_LIMIT, 60, client_ip_key))],
    summary="Proof-of-work challenge for registering through this link",
)
async def link_challenge(token: str, response: Response, db: DbSession) -> PowChallengeOut:
    link = await find_link_by_token(db, token)
    if link is None:
        raise NotFound("This link isn't valid. Check you copied the whole link.", code="link_not_found")
    response.headers["Cache-Control"] = "no-store"
    return PowChallengeOut(**create_challenge(f"link:{link.id}").as_dict())


@router.get(
    "/{token}",
    response_model=LinkInfo,
    dependencies=[Depends(rate_limit("link_access_info", INFO_IP_LIMIT, 60, client_ip_key))],
    summary="Open link landing information",
)
async def link_info(token: str, response: Response, db: DbSession) -> LinkInfo:
    link = await find_link_by_token(db, token)
    if link is None:
        raise NotFound("This link isn't valid. Check you copied the whole link.", code="link_not_found")
    response.headers["Cache-Control"] = "no-store"
    return await public_info(db, link)


@router.post(
    "/{token}/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_register_ip_dependency)],
    summary="Register through an open link",
)
async def register_endpoint(
    token: str, body: RegisterRequest, request: Request, response: Response, db: DbSession
) -> RegisterResponse:
    result = await register(db, token, body, request)
    set_auth_cookies(response, result.tokens)
    if result.device_cookie:
        set_device_cookie(response, result.device_cookie)
    response.headers["Cache-Control"] = "no-store"
    if result.outcome == "resumed":
        response.status_code = status.HTTP_200_OK
    return RegisterResponse(
        outcome=result.outcome,  # type: ignore[arg-type]
        interview_id=result.interview_id,
        next_step=result.next_step,  # type: ignore[arg-type]
        csrf_token=result.tokens.csrf_token,
        personal_url=result.personal_url,
    )
