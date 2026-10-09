"""Organization settings (``OrganizationSettings``): defaults that are frozen
into every new interview's policy snapshot.

Any staff member may read them (they shape job and invite forms); changing
them requires ``settings:manage`` and is audited with the changed sections.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import DbSession, StaffPrincipal, require_permission, resolve_org_id
from app.core.errors import ServiceUnavailable, ValidationFailed
from app.core.ratelimit import rate_limit
from app.core.rbac import Permission, Principal
from app.schemas.api.common import ERROR_RESPONSES
from app.schemas.api.settings import OrganizationSettings
from app.services.identity.organizations import (
    get_organization,
    load_organization_settings,
    save_organization_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"], responses=ERROR_RESPONSES)

SettingsManager = Annotated[Principal, Depends(require_permission(Permission.SETTINGS_MANAGE))]
OrgQuery = Annotated[uuid.UUID | None, Query(description="SUPER_ADMIN only: target organization")]


@router.get("", response_model=OrganizationSettings, summary="Get organization settings")
async def get_settings_endpoint(principal: StaffPrincipal, db: DbSession, organization_id: OrgQuery = None) -> OrganizationSettings:
    org = await get_organization(db, resolve_org_id(principal, organization_id))
    return load_organization_settings(org.settings)


@router.put("", response_model=OrganizationSettings, summary="Replace organization settings")
async def put_settings(
    body: OrganizationSettings,
    request: Request,
    principal: SettingsManager,
    db: DbSession,
    organization_id: OrgQuery = None,
) -> OrganizationSettings:
    org = await get_organization(db, resolve_org_id(principal, organization_id))
    changed = await save_organization_settings(db, org, body)
    if changed:
        await audit(
            db, principal, "settings.update", resource_type="organization", resource_id=str(org.id),
            organization_id=org.id, request=request, details={"changed_sections": changed},
        )
    return body


# --------------------------------------------------------------------- voices
# The interviewer voice is chosen in settings; these endpoints proxy the local
# TTS service so recruiters can list voices and hear a short sample.

_PREVIEW_MAX_CHARS = 200


class VoicePreviewRequest(BaseModel):
    voice: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    text: str = Field(
        "Hi, I'm your interviewer. Thanks for joining me today — let's get started.",
        min_length=1,
        max_length=_PREVIEW_MAX_CHARS,
    )


@router.get("/voices", summary="List interviewer voices from the local TTS service")
async def list_voices(principal: StaffPrincipal) -> dict[str, Any]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.tts_base_url}/v1/voices")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ServiceUnavailable("The voice service is unavailable.", code="tts_unavailable", retry_after=10) from exc
    voices = [
        {
            "id": v.get("id"),
            "language_name": v.get("language_name"),
            "gender": v.get("gender"),
            "default": bool(v.get("default")),
        }
        for v in payload.get("voices", [])
        if isinstance(v, dict) and v.get("id")
    ]
    return {"default": payload.get("default") or settings.tts_voice, "voices": voices}


@router.post(
    "/voice-preview",
    summary="Synthesize a short voice sample (audio/wav)",
    dependencies=[Depends(rate_limit("voice_preview", limit=10, window_s=60))],
    response_class=Response,
)
async def voice_preview(body: VoicePreviewRequest, principal: SettingsManager) -> Response:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{settings.tts_base_url}/v1/audio/speech",
                json={"model": settings.tts_model, "input": body.text, "voice": body.voice, "response_format": "wav"},
            )
    except httpx.HTTPError as exc:
        raise ServiceUnavailable("The voice service is unavailable.", code="tts_unavailable", retry_after=10) from exc
    if response.status_code == 422:
        raise ValidationFailed("Unknown voice or invalid text.", code="invalid_voice")
    if response.status_code != 200:
        raise ServiceUnavailable("The voice service is unavailable.", code="tts_unavailable", retry_after=10)
    return Response(
        content=response.content,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
