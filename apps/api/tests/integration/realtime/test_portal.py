"""Candidate access + portal REST API (CONTRACTS section 4)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, Consent, Interview, MediaAsset, Report
from app.db.session import session_scope
from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity
from app.services.integrity.contracts import IntegrityOutcome

from .conftest import DEFAULT_POLICY, create_interview

pytestmark = pytest.mark.integration

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 300
SAMPLE_TXT_RESUME = next((Path(__file__).resolve().parents[5] / "samples" / "resumes").glob("*.txt"))


def jpeg(width: int = 64, height: int = 48) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def next_event(sub, timeout: float = 2.0):  # noqa: ASYNC109
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = await sub.get(timeout=0.1)
        if event is not None:
            return event
    return None


def transport(app):
    return httpx.ASGITransport(app=app, client=("10.1.2.3", 50000))


async def verified_client(app, data: dict, *, email: str | None = None) -> httpx.AsyncClient:
    http = httpx.AsyncClient(transport=transport(app), base_url="https://192.168.1.50:3443")
    response = await http.post(
        f"/api/v1/candidate-access/{data['invite_token']}/verify",
        json={"email": email or data["candidate_email"]},
    )
    assert response.status_code == 200, response.text
    http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return http


# ----------------------------------------------------------- candidate access


async def test_invite_info_and_unknown_token(app, client, org_factory) -> None:
    data = await create_interview(await org_factory(name="Acme"), with_session=False)
    info = await client.get(f"/api/v1/candidate-access/{data['invite_token']}")
    assert info.status_code == 200
    body = info.json()
    assert body["organization_name"] == "Acme" and body["job_title"] == "Senior DevOps Engineer"
    assert body["candidate_first_name"] == "Jane" and body["requires_access_code"] is False
    assert body["expired"] is False and body["cancelled"] is False and body["status"] == "invited"
    assert body["interviewer_name"] == "Maya" and body["round_name"] == "Technical Screening"
    assert body["duration_minutes"] == 30  # "About 30 minutes" on the entry page
    assert "email" not in body
    selectable = await create_interview(await org_factory(), with_session=False, allow_round_selection=True)
    chosen_later = (await client.get(f"/api/v1/candidate-access/{selectable['invite_token']}")).json()
    assert (
        chosen_later["round_name"] is None and chosen_later["duration_minutes"] == 30
    )  # the selectable round's
    missing = await client.get("/api/v1/candidate-access/" + "x" * 43)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "invite_not_found"


async def test_expired_and_cancelled_invites(app, client, org_factory) -> None:
    org = await org_factory()
    expired = await create_interview(org, with_session=False, expires_in=timedelta(hours=-1))
    cancelled = await create_interview(org, with_session=False, interview_status="cancelled")
    info = (await client.get(f"/api/v1/candidate-access/{expired['invite_token']}")).json()
    assert info["expired"] is True and info["status"] == "expired"
    r = await client.post(
        f"/api/v1/candidate-access/{expired['invite_token']}/verify",
        json={"email": expired["candidate_email"]},
    )
    assert r.status_code == 410 and r.json()["error"]["code"] == "invite_expired"
    r = await client.post(
        f"/api/v1/candidate-access/{cancelled['invite_token']}/verify",
        json={"email": cancelled["candidate_email"]},
    )
    assert r.status_code == 410 and r.json()["error"]["code"] == "invite_cancelled"


async def test_verify_sets_cookies_and_marks_invite(app, org_factory) -> None:
    data = await create_interview(await org_factory(), with_session=False)
    async with httpx.AsyncClient(transport=transport(app), base_url="http://testserver") as http:
        r = await http.post(
            f"/api/v1/candidate-access/{data['invite_token']}/verify",
            json={"email": "  " + data["candidate_email"].upper() + " "},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["interview_id"] == str(data["interview_id"]) and body["next_step"] == "welcome"
        assert {"access_token", "csrf_token", "refresh_token"} <= set(r.cookies.keys())
        me = await http.get("/api/v1/portal/me")
        assert me.status_code == 200 and me.json()["candidate"]["first_name"] == "Jane"
    async with session_scope() as db:
        interview = await db.get(Interview, data["interview_id"])
        assert interview.verified_at is not None and interview.invite_opened_at is not None
        assert interview.failed_verification_count == 0
        actions = (
            await db.scalars(select(AuditLog.action).where(AuditLog.resource_id == str(data["interview_id"])))
        ).all()
        assert "candidate.verified" in actions


async def test_wrong_email_counts_failures_and_locks(app, client, org_factory, redis) -> None:
    data = await create_interview(await org_factory(), with_session=False)
    url = f"/api/v1/candidate-access/{data['invite_token']}/verify"
    for i in range(4):
        r = await client.post(url, json={"email": f"someone{i}@example.test"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    r = await client.post(url, json={"email": "fifth@example.test"})
    assert r.status_code == 423 and r.json()["error"]["code"] == "invite_locked"
    assert int(r.headers["Retry-After"]) > 0
    # Even the right email is refused while locked.
    r = await client.post(url, json={"email": data["candidate_email"]})
    assert r.status_code == 423
    async with session_scope() as db:
        interview = await db.get(Interview, data["interview_id"])
        assert interview.failed_verification_count == 5
        failed = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "candidate.verify_failed")
        )
        assert failed == 5


async def test_access_code_flow(app, client, org_factory) -> None:
    data = await create_interview(await org_factory(), with_session=False, access_code="ABCD-2345")
    url = f"/api/v1/candidate-access/{data['invite_token']}/verify"
    info = (await client.get(f"/api/v1/candidate-access/{data['invite_token']}")).json()
    assert info["requires_access_code"] is True
    r = await client.post(url, json={"email": data["candidate_email"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "access_code_required"
    r = await client.post(url, json={"email": data["candidate_email"], "access_code": "WRONG123"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    r = await client.post(url, json={"email": data["candidate_email"], "access_code": "abcd 2345"})
    assert r.status_code == 200


async def test_verify_rate_limited_per_invite(app, client, org_factory) -> None:
    data = await create_interview(await org_factory(), with_session=False, access_code="ABCD2345")
    url = f"/api/v1/candidate-access/{data['invite_token']}/verify"
    codes = []
    for _ in range(12):
        r = await client.post(url, json={"email": data["candidate_email"]})  # 422 does not count as a failure
        codes.append(r.status_code)
    assert codes[:10] == [422] * 10 and 429 in codes[10:]


# --------------------------------------------------------------------- portal


async def test_portal_journey_consent_resume_round_system_check_session_ticket(
    app, org_factory, portal_fakes
) -> None:
    data = await create_interview(
        await org_factory(name="Acme"), with_session=False, allow_round_selection=True
    )
    http = await verified_client(app, data)
    iid = data["interview_id"]
    detail = await http.get(f"/api/v1/portal/interviews/{iid}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["steps_completed"] == [] and body["allow_round_selection"] is True
    assert [r["name"] for r in body["rounds_offered"]] == [
        "Technical Screening"
    ]  # only candidate-selectable rounds
    assert "rules" not in body["policy"]["integrity"]

    # Disclosure + consent (real app.services.integrity disclosure)
    disclosure = (await http.get(f"/api/v1/portal/interviews/{iid}/disclosure")).json()
    assert len(disclosure["policy_hash"]) == 64 and disclosure["text_markdown"].startswith("# ")
    assert "Acme" in disclosure["intro"] and "Senior DevOps Engineer" in disclosure["intro"]
    assert {i["key"] for i in disclosure["items"]} >= {"microphone", "transcript", "ai_assessment"}
    required = disclosure["required_items"]
    assert {"microphone", "transcript", "ai_assessment"} <= set(required)
    assert disclosure["consent_items"]["retention_days"] > 0 and disclosure["consented"] is False
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 x", "application/pdf")},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "consent_required"
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/consent", json={"policy_hash": "f" * 64, "items": {}}
    )
    assert r.status_code == 409 and r.json()["error"]["details"]["policy_hash"] == disclosure["policy_hash"]
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/consent",
        json={"policy_hash": disclosure["policy_hash"], "items": {"microphone": True}},
    )
    assert r.status_code == 422
    assert set(r.json()["error"]["details"]["missing"]) == {
        k for k in required if k not in ("microphone", "retention")
    }
    items = {key: True for key in required}
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/consent",
        json={"policy_hash": disclosure["policy_hash"], "items": items},
    )
    assert r.status_code == 201, r.text
    stored = r.json()["items"]
    # Video recording is on by default (RecordingPolicy.record_video) and required to continue.
    assert stored["audio_recording"] is True and stored["video_recording"] is True
    assert stored["retention_days"] == disclosure["consent_items"]["retention_days"]
    assert (await http.get(f"/api/v1/portal/interviews/{iid}/disclosure")).json()["consented"] is True

    # Resume upload + status
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/resume",
        files={"file": ("cv.pdf", b"%PDF-1.4 resume", "application/pdf")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["resume"]["original_filename"] == "cv.pdf" and r.json()["resume"]["steps"]
    upload = portal_fakes["resume"].uploads[0]
    assert upload["uploaded_by_candidate"] is True and upload["interview_id"] == iid
    status = await http.get(f"/api/v1/portal/interviews/{iid}/resume")
    assert status.json()["resume"]["status"] == "extracting"

    # Session needs a round first (selection allowed)
    r = await http.post(f"/api/v1/portal/interviews/{iid}/session")
    assert r.status_code == 409 and r.json()["error"]["code"] == "round_required"
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/round", json={"round_id": str(data["other_round_id"])}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "round_not_selectable"
    r = await http.post(f"/api/v1/portal/interviews/{iid}/round", json={"round_id": str(uuid.uuid4())})
    assert r.status_code == 422
    r = await http.post(f"/api/v1/portal/interviews/{iid}/round", json={"round_id": str(data["round_id"])})
    assert r.status_code == 200 and r.json()["selected_round_id"] == str(data["round_id"])

    # System check
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/system-check",
        json={
            "camera": False,
            "microphone": True,
            "speakers": True,
            "webgl": False,
            "displays": 2,
            "network": {"rtt_ms": 40},
        },
    )
    check = r.json()
    assert (
        check["passed"] is False
        and "camera_required" in check["issues"]
        and "multiple_displays" in check["issues"]
    )
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/system-check",
        json={"camera": True, "microphone": True, "speakers": True, "webgl": True},
    )
    assert r.json()["passed"] is True

    steps = (await http.get(f"/api/v1/portal/interviews/{iid}")).json()["steps_completed"]
    assert {"consent", "resume", "round", "system-check"} <= set(steps)

    # Session + ws ticket
    r = await http.post(f"/api/v1/portal/interviews/{iid}/session")
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]
    again = await http.post(f"/api/v1/portal/interviews/{iid}/session")
    assert again.json()["session_id"] == session_id  # idempotent create-or-resume
    session = (await http.get(f"/api/v1/portal/sessions/{session_id}")).json()
    assert session["status"] == "created" and "recommendation" not in session["details"]
    ticket = await http.post(f"/api/v1/portal/sessions/{session_id}/ws-ticket")
    assert ticket.status_code == 200
    tb = ticket.json()
    assert tb["ws_url"] == f"wss://192.168.1.50:3443/ws/sessions/{session_id}"
    assert tb["ws_path"] == f"/ws/sessions/{session_id}" and tb["expires_in"] > 0 and len(tb["ticket"]) >= 32
    await http.aclose()


async def test_portal_session_reports_the_candidate_feedback_mode(app, org_factory, portal_fakes) -> None:
    """The portal hides every feedback element when the session's mode is "never" (the default)."""
    org = await org_factory()
    default = await create_interview(org, consent_items={"microphone": True})
    http = await verified_client(app, default)
    session = (await http.get(f"/api/v1/portal/sessions/{default['session_id']}")).json()
    interview = (await http.get(f"/api/v1/portal/interviews/{default['interview_id']}")).json()
    assert session["candidate_report_mode"] == "never" == interview["policy"]["candidate_report_mode"]
    assert session["candidate_report_available"] is False
    await http.aclose()

    immediate = await create_interview(
        org,
        consent_items={"microphone": True},
        policy={**DEFAULT_POLICY, "candidate_report_mode": "immediate"},
    )
    http = await verified_client(app, immediate)
    session = (await http.get(f"/api/v1/portal/sessions/{immediate['session_id']}")).json()
    assert session["candidate_report_mode"] == "immediate"  # an organisation that opts in keeps it
    await http.aclose()


async def test_portal_is_scoped_to_own_interview(app, org_factory, portal_fakes) -> None:
    org = await org_factory()
    mine = await create_interview(org, consent_items={"microphone": True})
    other = await create_interview(org, consent_items={"microphone": True})
    http = await verified_client(app, mine)
    for path in (
        f"/api/v1/portal/interviews/{other['interview_id']}",
        f"/api/v1/portal/interviews/{other['interview_id']}/disclosure",
        f"/api/v1/portal/sessions/{other['session_id']}",
        f"/api/v1/portal/sessions/{other['session_id']}/report",
    ):
        r = await http.get(path)
        assert r.status_code == 404, path
    r = await http.post(f"/api/v1/portal/sessions/{other['session_id']}/ws-ticket")
    assert r.status_code == 404
    anonymous = httpx.AsyncClient(transport=transport(app), base_url="http://testserver")
    assert (await anonymous.get(f"/api/v1/portal/interviews/{mine['interview_id']}")).status_code == 401
    await anonymous.aclose()
    await http.aclose()


async def test_csrf_required_for_portal_writes(app, org_factory, portal_fakes) -> None:
    data = await create_interview(await org_factory(), with_session=False)
    http = await verified_client(app, data)
    del http.headers["X-CSRF-Token"]
    r = await http.post(
        f"/api/v1/portal/interviews/{data['interview_id']}/system-check",
        json={"camera": True, "microphone": True, "speakers": True, "webgl": True},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    await http.aclose()


async def test_frames_go_through_the_real_integrity_service(app, org_factory, portal_fakes, storage) -> None:
    org = await org_factory()
    url = "/api/v1/portal/sessions/{}/frames"

    no_consent = await create_interview(org, consent_items=None, session_status="live")
    http = await verified_client(app, no_consent)
    r = await http.post(
        url.format(no_consent["session_id"]),
        files={"file": ("f.jpg", jpeg(), "image/jpeg")},
        data={"kind": "camera"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    await http.aclose()

    declined = await create_interview(
        org, consent_items={"microphone": True, "camera_frames": False}, session_status="live"
    )
    http = await verified_client(app, declined)
    r = await http.post(
        url.format(declined["session_id"]),
        files={"file": ("f.jpg", jpeg(), "image/jpeg")},
        data={"kind": "camera"},
    )
    assert (
        r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    )  # explicit decline blocks frames
    await http.aclose()

    ok = await create_interview(
        org, consent_items={"camera": True, "camera_frames": True}, session_status="live"
    )
    http = await verified_client(app, ok)
    target = url.format(ok["session_id"])
    r = await http.post(
        target,
        files={"file": ("f.jpg", jpeg(), "image/jpeg")},
        data={"kind": "camera", "captured_at": "2026-10-07T10:00:00Z"},
    )
    assert r.status_code == 202, r.text
    media_id = r.json()["media_id"]
    async with session_scope() as db:
        media = await db.get(MediaAsset, uuid.UUID(media_id))
        assert media is not None and media.kind == "camera_frame" and media.session_id == ok["session_id"]
    r = await http.post(
        target, files={"file": ("f.gif", b"GIF89a" + b"\x00" * 64, "image/gif")}, data={"kind": "camera"}
    )
    assert r.status_code == 415  # passed through from submit_frame
    r = await http.post(
        target,
        files={"file": ("f.jpg", b"\xff\xd8\xff" + b"\x00" * (2 * 1024 * 1024), "image/jpeg")},
        data={"kind": "camera"},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "frame_too_large"
    r = await http.post(target, files={"file": ("f.jpg", jpeg(), "image/jpeg")}, data={"kind": "screen"})
    assert (
        r.status_code == 403 and r.json()["error"]["code"] == "frames_not_enabled"
    )  # screen analysis off by default
    await http.aclose()

    ended = await create_interview(org, consent_items={"camera_frames": True}, session_status="completed")
    http = await verified_client(app, ended)
    r = await http.post(
        url.format(ended["session_id"]),
        files={"file": ("f.jpg", jpeg(), "image/jpeg")},
        data={"kind": "camera"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "session_ended"
    await http.aclose()


async def test_consent_explicit_optional_decline_is_stored_and_blocks_frames(
    app, org_factory, portal_fakes
) -> None:
    policy = {**DEFAULT_POLICY, "integrity": {**DEFAULT_POLICY["integrity"], "camera_required": False}}
    data = await create_interview(await org_factory(), policy=policy, session_status="live")
    http = await verified_client(app, data)
    iid = data["interview_id"]
    disclosure = (await http.get(f"/api/v1/portal/interviews/{iid}/disclosure")).json()
    camera = next(i for i in disclosure["items"] if i["key"] == "camera")
    assert camera["enabled"] and not camera["required"]  # optional when the camera is not required
    items = {key: True for key in disclosure["required_items"]} | {"camera": False}
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/consent",
        json={"policy_hash": disclosure["policy_hash"], "items": items},
    )
    assert r.status_code == 201, r.text
    assert r.json()["items"]["camera"] is False
    r = await http.post(
        f"/api/v1/portal/sessions/{data['session_id']}/frames",
        files={"file": ("f.jpg", jpeg(), "image/jpeg")},
        data={"kind": "camera"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    await http.aclose()


async def test_integrity_events_batch_returns_warnings_and_publishes_actions(
    app, org_factory, portal_fakes, fake_integrity, redis
) -> None:
    from app.core.queue import subscribe_session_events

    data = await create_interview(
        await org_factory(), consent_items={"microphone": True}, session_status="live"
    )
    http = await verified_client(app, data)
    fake_integrity.next_outcomes = [
        IntegrityOutcome(
            None,
            IntegrityEventType.FULLSCREEN_EXITED,
            IntegritySeverity.LOW,
            IntegrityAction.WARN_CANDIDATE,
            "Please return to fullscreen.",
        ),
        IntegrityOutcome(
            None,
            IntegrityEventType.SCREEN_SHARE_STOPPED,
            IntegritySeverity.HIGH,
            IntegrityAction.PAUSE_INTERVIEW,
            "Share your screen again.",
        ),
        IntegrityOutcome(
            None,
            IntegrityEventType.POSSIBLE_PHONE_VISIBLE,
            IntegritySeverity.HIGH,
            IntegrityAction.TERMINATE_INTERVIEW,
            None,
        ),
    ]
    events = [
        {"event_type": "fullscreen_exited", "occurred_at": "2026-10-07T10:00:00Z"},
        {"event_type": "screen_share_stopped", "occurred_at": "2026-10-07T10:00:01Z"},
    ]
    async with subscribe_session_events(data["session_id"]) as sub:
        r = await http.post(
            f"/api/v1/portal/sessions/{data['session_id']}/integrity-events", json={"events": events}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted"] == 2 and [w["event_type"] for w in body["warnings"]] == [
            "fullscreen_exited",
            "screen_share_stopped",
        ]
        published = await next_event(sub)
        assert published == {
            **published,
            "type": "integrity.action",
            "action": "pause_interview",
            "event_type": "screen_share_stopped",
        }
        assert await next_event(sub, timeout=0.3) is None  # the vision-derived terminate was not published
    r = await http.post(f"/api/v1/portal/sessions/{data['session_id']}/integrity-events", json={"events": []})
    assert r.status_code == 422
    await http.aclose()


async def test_integrity_events_batch_with_the_real_service(app, org_factory, portal_fakes) -> None:
    from app.db.models import IntegrityEvent

    data = await create_interview(
        await org_factory(), consent_items={"microphone": True}, session_status="live"
    )
    http = await verified_client(app, data)
    events = [{"event_type": "tab_hidden", "occurred_at": datetime.now(UTC).isoformat(), "duration_ms": 1500}]
    r = await http.post(
        f"/api/v1/portal/sessions/{data['session_id']}/integrity-events", json={"events": events}
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1
    async with session_scope() as db:
        rows = (
            await db.scalars(select(IntegrityEvent).where(IntegrityEvent.session_id == data["session_id"]))
        ).all()
        assert [row.type for row in rows] == ["tab_hidden"]
    await http.aclose()


async def test_recording_chunks_policy_idempotency_and_storage(
    app, org_factory, portal_fakes, storage
) -> None:
    org = await org_factory()
    policy = {**DEFAULT_POLICY, "recording": {**DEFAULT_POLICY["recording"], "record_video": True}}
    data = await create_interview(
        org, policy=policy, consent_items={"video_recording": True}, session_status="live"
    )
    http = await verified_client(app, data)
    url = f"/api/v1/portal/sessions/{data['session_id']}/recording"
    r = await http.post(url, files={"file": ("0.webm", b"not webm", "video/webm")}, data={"segment": "0"})
    assert r.status_code == 415 and r.json()["error"]["code"] == "invalid_recording"
    r = await http.post(url, files={"file": ("0.webm", WEBM, "video/webm;codecs=vp9")}, data={"segment": "0"})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["segment"] == 0 and first["duplicate"] is False
    again = await http.post(url, files={"file": ("0.webm", WEBM, "video/webm")}, data={"segment": "0"})
    assert again.status_code == 200 and again.json()["duplicate"] is True
    clash = await http.post(url, files={"file": ("0.webm", WEBM + b"x", "video/webm")}, data={"segment": "0"})
    assert clash.status_code == 409
    later = await http.post(
        url, files={"file": ("1.webm", b"\x00cluster-data", "video/webm")}, data={"segment": "1"}
    )
    assert later.status_code == 201  # continuation chunks have no container header
    r = await http.post(url, files={"file": ("x.ogg", WEBM, "audio/ogg")}, data={"segment": "2"})
    assert r.status_code == 415
    async with session_scope() as db:
        media = (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.session_id == data["session_id"])
                .order_by(MediaAsset.storage_key)
            )
        ).all()
        assert [m.storage_key.rsplit("/", 2)[-2:] for m in media] == [
            ["video", "00000.webm"],
            ["video", "00001.webm"],
        ]
        assert all(m.kind == "video_segment" and m.retention_until is not None for m in media)
        assert (media[0].retention_until - datetime.now(UTC)).days in (29, 30)
    assert await storage.exists(media[0].storage_key)
    await http.aclose()

    no_video = {**DEFAULT_POLICY, "recording": {**DEFAULT_POLICY["recording"], "record_video": False}}
    no_policy = await create_interview(
        org, policy=no_video, consent_items={"video_recording": True}, session_status="live"
    )
    http = await verified_client(app, no_policy)
    r = await http.post(
        f"/api/v1/portal/sessions/{no_policy['session_id']}/recording",
        files={"file": ("0.webm", WEBM, "video/webm")},
        data={"segment": "0"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "recording_not_enabled"
    await http.aclose()


async def test_candidate_report_only_when_released(app, org_factory, portal_fakes, storage) -> None:
    from app.services.storage.media import store_media

    data = await create_interview(
        await org_factory(), consent_items={"microphone": True}, session_status="completed"
    )
    http = await verified_client(app, data)
    url = f"/api/v1/portal/sessions/{data['session_id']}/report"
    r = await http.get(url)
    assert r.status_code == 404 and r.json()["error"]["details"]["status"] == "pending"
    async with session_scope() as db:
        key = f"orgs/{data['organization_id']}/sessions/{data['session_id']}/reports/candidate-v1.html"
        await store_media(
            db,
            organization_id=data["organization_id"],
            kind="report_artifact",
            key=key,
            data=b"<html>ok</html>",
            content_type="text/html",
            session_id=data["session_id"],
        )
        db.add(
            Report(
                session_id=data["session_id"],
                type="candidate",
                status="ready",
                version=1,
                content={
                    "summary": "Strong systems thinking.",
                    "recommendation": "proceed",
                    "integrity": {"flags": 2},
                },
                artifact_storage_key=key,
                released_to_candidate=False,
            )
        )
    r = await http.get(url)
    assert r.status_code == 404 and r.json()["error"]["details"]["status"] == "not_released"
    async with session_scope() as db:
        report = await db.scalar(select(Report).where(Report.session_id == data["session_id"]))
        report.released_to_candidate = True
        report.released_at = datetime.now(UTC)
    r = await http.get(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == {"summary": "Strong systems thinking."}  # recommendation/integrity stripped
    assert body["artifact_url"].startswith("/api/v1/media/signed/")
    artifact = await http.get(body["artifact_url"])
    assert artifact.status_code == 200 and artifact.content == b"<html>ok</html>"
    steps = (await http.get(f"/api/v1/portal/interviews/{data['interview_id']}")).json()["steps_completed"]
    assert "report" in steps and "complete" in steps
    await http.aclose()


async def test_ws_ticket_requires_open_session_and_consent(app, org_factory, portal_fakes) -> None:
    org = await org_factory()
    ended = await create_interview(org, consent_items={"microphone": True}, session_status="completed")
    http = await verified_client(app, ended)
    r = await http.post(f"/api/v1/portal/sessions/{ended['session_id']}/ws-ticket")
    assert r.status_code == 409 and r.json()["error"]["code"] == "session_ended"
    await http.aclose()
    no_consent = await create_interview(org, consent_items=None, session_status="ready")
    http = await verified_client(app, no_consent)
    r = await http.post(f"/api/v1/portal/sessions/{no_consent['session_id']}/ws-ticket")
    assert r.status_code == 409 and r.json()["error"]["code"] == "consent_required"
    await http.aclose()


async def test_disclosure_unavailable_without_integrity_module(app, org_factory, monkeypatch) -> None:
    from app.realtime import deps

    data = await create_interview(await org_factory(), with_session=False)
    http = await verified_client(app, data)
    monkeypatch.setitem(deps._overrides, "disclosure_builder", None)
    deps._overrides.pop("disclosure_builder")

    import importlib

    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "app.services.integrity":
            raise ImportError("not built yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(deps.importlib, "import_module", fake_import)
    r = await http.get(f"/api/v1/portal/interviews/{data['interview_id']}/disclosure")
    assert r.status_code == 503 and r.json()["error"]["code"] == "disclosure_unavailable"
    await http.aclose()
    assert io  # keep import used
    assert Consent


async def test_resume_upload_with_the_real_resume_service(app, org_factory, storage) -> None:
    sample = SAMPLE_TXT_RESUME
    data = await create_interview(await org_factory(), consent_items={"microphone": True}, with_session=False)
    http = await verified_client(app, data)
    iid = data["interview_id"]
    r = await http.post(
        f"/api/v1/portal/interviews/{iid}/resume",
        files={"file": (sample.name, sample.read_bytes(), "text/plain")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["resume"]["original_filename"] == sample.name and body["resume"]["status"]
    status = await http.get(f"/api/v1/portal/interviews/{iid}/resume")
    assert status.status_code == 200 and status.json()["resume"]["id"] == body["resume"]["id"]
    bad = await http.post(
        f"/api/v1/portal/interviews/{iid}/resume",
        files={"file": ("x.exe", b"MZ\x90\x00" + b"\x00" * 64, "application/octet-stream")},
    )
    assert bad.status_code in (415, 422), (
        bad.text
    )  # rejected by the resume service with a candidate-safe message
    await http.aclose()


async def test_system_check_accepts_the_avatar_probe_result(app, org_factory, portal_fakes) -> None:
    """The system-check page reports which interviewer view it chose (3d / 2d /
    none) and the measured frame rate; both are optional and stored."""
    from app.db.models import Interview
    from app.db.session import session_scope

    data = await create_interview(await org_factory(), with_session=False)
    http = await verified_client(app, data)
    url = f"/api/v1/portal/interviews/{data['interview_id']}/system-check"
    base = {"camera": True, "microphone": True, "speakers": True}
    r = await http.post(url, json={**base, "webgl": False, "avatar_mode": "2d", "avatar_fps": 24.5})
    assert r.status_code == 200, r.text
    assert r.json()["passed"] is True and "webgl_unavailable_2d_fallback" in r.json()["issues"]
    async with session_scope() as db:
        stored = (await db.get(Interview, data["interview_id"])).system_check
    assert stored["avatar_mode"] == "2d" and stored["avatar_fps"] == 24.5
    assert (await http.post(url, json={**base, "webgl": True})).status_code == 200  # still optional
    bad = await http.post(url, json={**base, "webgl": True, "avatar_mode": "vr"})
    assert bad.status_code == 422
    await http.aclose()
