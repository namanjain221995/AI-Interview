"""Portal surface of the on-device face check: the policy flag the browser
reads, the consent item, and face-check snapshots through the frames endpoint
(real integrity service and face detector)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.db.models import IntegrityEvent, MediaAsset
from app.db.session import session_scope

from .conftest import DEFAULT_POLICY, create_interview

pytestmark = pytest.mark.integration

FACES = Path(__file__).resolve().parents[2] / "fixtures" / "faces"
CONSENT = {"microphone": True, "camera": True, "camera_frames": True, "face_check": True}


def transport(app):
    return httpx.ASGITransport(app=app, client=("10.1.2.3", 50000))


async def verified_client(app, data: dict) -> httpx.AsyncClient:
    http = httpx.AsyncClient(transport=transport(app), base_url="https://192.168.1.50:3443")
    response = await http.post(
        f"/api/v1/candidate-access/{data['invite_token']}/verify", json={"email": data["candidate_email"]}
    )
    assert response.status_code == 200, response.text
    http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return http


def snapshot_form(reason: str, episode: str, duration_ms: int) -> dict[str, str]:
    return {
        "kind": "camera",
        "captured_at": datetime.now(UTC).isoformat(),
        "trigger": "face_check",
        "face_check": json.dumps(
            {"reason": reason, "episode": episode, "duration_ms": duration_ms, "faces": 2, "static_boxes": []}
        ),
    }


async def test_face_check_policy_flag_and_consent_item(app, org_factory, portal_fakes) -> None:
    data = await create_interview(await org_factory(), session_status="live")
    http = await verified_client(app, data)
    interview = (await http.get(f"/api/v1/portal/interviews/{data['interview_id']}")).json()
    assert interview["policy"]["integrity"]["face_presence_check"] is True
    disclosure = (await http.get(f"/api/v1/portal/interviews/{data['interview_id']}/disclosure")).json()
    item = next(i for i in disclosure["items"] if i["key"] == "face_check")
    assert item["enabled"] and item["required"] and "face_check" in disclosure["required_items"]
    assert disclosure["consent_items"]["face_check"] is True
    await http.aclose()

    off = {**DEFAULT_POLICY, "integrity": {**DEFAULT_POLICY["integrity"], "face_presence_check": False}}
    data = await create_interview(await org_factory(), policy=off, session_status="live")
    http = await verified_client(app, data)
    interview = (await http.get(f"/api/v1/portal/interviews/{data['interview_id']}")).json()
    assert interview["policy"]["integrity"]["face_presence_check"] is False
    disclosure = (await http.get(f"/api/v1/portal/interviews/{data['interview_id']}/disclosure")).json()
    assert not next(i for i in disclosure["items"] if i["key"] == "face_check")["enabled"]
    await http.aclose()


async def test_face_check_snapshot_through_the_frames_endpoint(
    app, org_factory, portal_fakes, storage
) -> None:
    org = await org_factory()
    data = await create_interview(org, consent_items=CONSENT, session_status="live")
    http = await verified_client(app, data)
    url = f"/api/v1/portal/sessions/{data['session_id']}/frames"
    image = (FACES / "two_side_by_side.jpg").read_bytes()
    r = await http.post(
        url,
        files={"file": ("f.jpg", image, "image/jpeg")},
        data=snapshot_form("multiple_faces", "mf_portal1", 3500),
    )
    assert r.status_code == 202, r.text
    media_id = r.json()["media_id"]
    async with session_scope() as db:
        [event] = (
            await db.scalars(select(IntegrityEvent).where(IntegrityEvent.session_id == data["session_id"]))
        ).all()
        media = await db.get(MediaAsset, uuid.UUID(media_id))
    assert event.type == "multiple_faces_visible" and event.action == "flag_for_human_review"
    assert str(event.evidence_media_id) == media_id and event.details["server_check"]["status"] == "confirmed"
    assert media.details["faces"]["counted_faces"] == 2 and media.deleted_at is None

    r = await http.post(
        url, files={"file": ("f.jpg", image, "image/jpeg")}, data={"kind": "camera", "trigger": "x"}
    )
    assert r.status_code == 422
    r = await http.post(
        url,
        files={"file": ("f.jpg", image, "image/jpeg")},
        data={"kind": "camera", "trigger": "face_check", "face_check": "{}"},
    )
    assert r.status_code == 422
    await http.aclose()

    no_face_consent = await create_interview(
        org, consent_items={k: v for k, v in CONSENT.items() if k != "face_check"}, session_status="live"
    )
    http = await verified_client(app, no_face_consent)
    r = await http.post(
        f"/api/v1/portal/sessions/{no_face_consent['session_id']}/frames",
        files={"file": ("f.jpg", image, "image/jpeg")},
        data=snapshot_form("multiple_faces", "mf_portal2", 3500),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    await http.aclose()
