"""Voice list / preview proxies to the local TTS service (mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.domain.enums import Role

pytestmark = pytest.mark.integration


async def test_list_voices_proxies_tts(auth_client):
    staff = await auth_client(Role.RECRUITER)
    base = get_settings().tts_base_url
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{base}/v1/voices").respond(
            200,
            json={
                "default": "af_heart",
                "voices": [
                    {"id": "af_heart", "language": "en-us", "language_name": "American English", "gender": "female", "default": True},
                    {"id": "am_michael", "language": "en-us", "language_name": "American English", "gender": "male", "default": False},
                ],
            },
        )
        res = await staff.get("/api/v1/settings/voices")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["default"] == "af_heart"
    assert [v["id"] for v in body["voices"]] == ["af_heart", "am_michael"]


async def test_voice_preview_requires_settings_manage_and_returns_wav(auth_client):
    recruiter = await auth_client(Role.RECRUITER)
    denied = await recruiter.post("/api/v1/settings/voice-preview", json={"voice": "af_heart"})
    assert denied.status_code == 403

    admin = await auth_client(Role.ADMIN)
    base = get_settings().tts_base_url
    wav = b"RIFF" + b"\x00" * 40
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{base}/v1/audio/speech").respond(200, content=wav, headers={"content-type": "audio/wav"})
        res = await admin.post("/api/v1/settings/voice-preview", json={"voice": "af_heart", "text": "Hello there."})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "audio/wav"
    assert res.content == wav
    sent = route.calls.last.request
    assert b'"voice":"af_heart"' in sent.content.replace(b" ", b"")


async def test_voice_preview_validates_voice_and_maps_outage(auth_client):
    admin = await auth_client(Role.ADMIN)
    bad = await admin.post("/api/v1/settings/voice-preview", json={"voice": "../etc/passwd"})
    assert bad.status_code == 422
    base = get_settings().tts_base_url
    with respx.mock() as mock:
        mock.post(f"{base}/v1/audio/speech").mock(side_effect=httpx.ConnectError("down"))
        res = await admin.post("/api/v1/settings/voice-preview", json={"voice": "af_heart"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "tts_unavailable"
