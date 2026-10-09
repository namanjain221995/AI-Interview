"""Integrity REST API: RBAC, organization isolation, filters, review + audit,
bulk review, session timeline with signed evidence URLs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import AuditLog, IntegrityEvent, MediaAsset
from app.db.session import session_scope
from app.domain.enums import IntegrityEventType, ModelTask, Role
from app.schemas.ai import FrameObservation
from app.schemas.realtime import ClientIntegrityEvent

pytestmark = pytest.mark.integration

T = IntegrityEventType


def ev(event_type: T, seconds_ago: float = 0, **kw) -> ClientIntegrityEvent:
    return ClientIntegrityEvent(
        event_type=event_type,
        occurred_at=(datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat(),
        **kw,
    )


@pytest.fixture
async def world(service, clock, make_session, org_factory, mock_ai, camera_frame):
    """Org A: two sessions with events (one with an evidence frame); org B: one session."""
    org_a, org_b = await org_factory(name="Org A"), await org_factory(name="Org B")
    a1 = await make_session(organization_id=org_a, candidate_name="Alice Able")
    a2 = await make_session(organization_id=org_a, candidate_name="Bob Brown", job_title="Backend Engineer")
    b1 = await make_session(organization_id=org_b, candidate_name="Carol Other")
    await service.record_client_events(
        a1.sid, [ev(T.COPY_EVENT, 30), ev(T.CAMERA_DISABLED, 20), ev(T.FULLSCREEN_EXITED, 10)]
    )
    await service.record_client_events(a2.sid, [ev(T.LARGE_PASTE_IN_CODING, 5)])
    await service.record_client_events(b1.sid, [ev(T.CAMERA_DISABLED, 5)])
    two = FrameObservation(person_count=2, primary_face_visible=True, confidence=0.9)
    mock_ai.llm.set_default(ModelTask.FRAME_ANALYSIS, two)
    frames = []
    for seconds in (15, 5):
        clock.at(seconds)
        mid = await service.submit_frame(
            a1.sid,
            kind="camera",
            image=camera_frame,
            content_type="image/jpeg",
            captured_at=(datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(),
        )
        await service.analyze_frame(a1.sid, mid)
        frames.append(mid)
    clock.real()
    return {"org_a": org_a, "org_b": org_b, "a1": a1, "a2": a2, "b1": b1, "evidence": frames[1]}


async def test_queue_is_org_scoped_with_refs(auth_client, world) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=world["org_a"])
    response = await recruiter.get("/api/v1/integrity/events")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 5 and body["limit"] == 50 and body["offset"] == 0
    assert {item["organization_id"] for item in body["items"]} == {str(world["org_a"])}
    assert all(item["session_id"] != world["b1"].sid for item in body["items"])
    by_type = {item["type"]: item for item in body["items"]}
    assert by_type["large_paste_in_coding"]["candidate"]["full_name"] == "Bob Brown"
    assert by_type["large_paste_in_coding"]["job"]["title"] == "Backend Engineer"
    assert by_type["multiple_persons_visible"]["label"] == "More than one person visible"
    occurred = [item["occurred_at"] for item in body["items"]]
    assert occurred == sorted(occurred, reverse=True)


@pytest.mark.parametrize(
    ("query", "expected_types"),
    [
        ("type=camera_disabled", {"camera_disabled"}),
        ("type=camera_disabled&type=copy_event", {"camera_disabled", "copy_event"}),
        ("severity=high", {"multiple_persons_visible"}),
        (
            "actionable=true",
            {"camera_disabled", "fullscreen_exited", "large_paste_in_coding", "multiple_persons_visible"},
        ),
        ("actionable=false", {"copy_event"}),
        (
            "action=flag_for_human_review",
            {"camera_disabled", "large_paste_in_coding", "multiple_persons_visible"},
        ),
        ("source=vision", {"multiple_persons_visible"}),
        ("status=dismissed", set()),
    ],
)
async def test_queue_filters(auth_client, world, query, expected_types) -> None:
    admin = await auth_client(Role.ADMIN, organization_id=world["org_a"])
    response = await admin.get(f"/api/v1/integrity/events?{query}")
    assert response.status_code == 200, response.text
    assert {item["type"] for item in response.json()["items"]} == expected_types


async def test_queue_session_candidate_date_filters_and_pagination(auth_client, world) -> None:
    admin = await auth_client(Role.ADMIN, organization_id=world["org_a"])
    by_session = (await admin.get(f"/api/v1/integrity/events?session_id={world['a2'].sid}")).json()
    assert [i["type"] for i in by_session["items"]] == ["large_paste_in_coding"]
    by_candidate = (
        await admin.get(f"/api/v1/integrity/events?candidate_id={world['a2'].candidate_id}")
    ).json()
    assert by_candidate["total"] == 1
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    since = await admin.get("/api/v1/integrity/events", params={"since": future})
    assert since.json()["total"] == 0
    until = await admin.get("/api/v1/integrity/events", params={"until": future})
    assert until.json()["total"] == 5
    page = (await admin.get("/api/v1/integrity/events?limit=2&offset=2")).json()
    assert page["total"] == 5 and len(page["items"]) == 2 and page["offset"] == 2
    assert (await admin.get("/api/v1/integrity/events?type=nonsense")).status_code == 422


async def test_rbac(auth_client, candidate_auth_client, client, world) -> None:
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=world["org_a"])
    assert (await interviewer.get("/api/v1/integrity/events")).status_code == 403
    event_id = await _first_event_id(world["a1"].session_id)
    patch = await interviewer.patch(f"/api/v1/integrity/events/{event_id}", json={"review_status": "noted"})
    assert patch.status_code == 403
    # interviewers may read a session's integrity timeline (sessions:read)
    assert (await interviewer.get(f"/api/v1/sessions/{world['a1'].sid}/integrity")).status_code == 200
    candidate = await candidate_auth_client(organization_id=world["org_a"])
    assert (await candidate.get("/api/v1/integrity/events")).status_code == 403
    assert (await candidate.get(f"/api/v1/sessions/{world['a1'].sid}/integrity")).status_code == 403
    assert (await client.get("/api/v1/integrity/events")).status_code == 401


async def _first_event_id(session_id: uuid.UUID, event_type: str | None = None) -> str:
    async with session_scope() as db:
        stmt = select(IntegrityEvent.id).where(IntegrityEvent.session_id == session_id)
        if event_type:
            stmt = stmt.where(IntegrityEvent.type == event_type)
        return str(await db.scalar(stmt.limit(1)))


async def test_review_sets_reviewer_and_is_audited(auth_client, world) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=world["org_a"])
    event_id = await _first_event_id(world["a1"].session_id, "camera_disabled")
    response = await recruiter.patch(
        f"/api/v1/integrity/events/{event_id}",
        json={"review_status": "dismissed", "review_note": "  Camera driver crashed; candidate explained.  "},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["review_status"] == "dismissed"
        and body["review_note"] == "Camera driver crashed; candidate explained."
    )
    assert body["reviewed_by_id"] == str(recruiter.user.id) and body["reviewed_at"]
    assert body["candidate"]["full_name"] == "Alice Able"
    async with session_scope() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "integrity.review"))
    assert (
        log is not None
        and log.resource_id == event_id
        and log.details["from"] == "unreviewed"
        and log.details["to"] == "dismissed"
    )
    assert log.organization_id == world["org_a"]
    reverted = await recruiter.patch(
        f"/api/v1/integrity/events/{event_id}", json={"review_status": "unreviewed"}
    )
    assert reverted.json()["reviewed_by_id"] is None and reverted.json()["review_note"] is None
    bad = await recruiter.patch(f"/api/v1/integrity/events/{event_id}", json={"review_status": "guilty"})
    assert bad.status_code == 422
    too_long = await recruiter.patch(
        f"/api/v1/integrity/events/{event_id}", json={"review_status": "noted", "review_note": "x" * 2001}
    )
    assert too_long.status_code == 422


async def test_cross_org_review_and_read_look_like_404(auth_client, world) -> None:
    recruiter_b = await auth_client(Role.RECRUITER, organization_id=world["org_b"])
    event_id = await _first_event_id(world["a1"].session_id)
    assert (
        await recruiter_b.patch(f"/api/v1/integrity/events/{event_id}", json={"review_status": "noted"})
    ).status_code == 404
    assert (await recruiter_b.get(f"/api/v1/integrity/events/{event_id}")).status_code == 404
    assert (await recruiter_b.get(f"/api/v1/sessions/{world['a1'].sid}/integrity")).status_code == 404
    assert (
        await recruiter_b.get(f"/api/v1/integrity/events?organization_id={world['org_a']}")
    ).status_code == 403
    async with session_scope() as db:
        untouched = await db.get(IntegrityEvent, uuid.UUID(event_id))
    assert untouched.review_status == "unreviewed"


async def test_super_admin_sees_all_orgs_and_can_filter(auth_client, world) -> None:
    root = await auth_client(Role.SUPER_ADMIN)
    everything = (await root.get("/api/v1/integrity/events")).json()
    assert {item["organization_id"] for item in everything["items"]} == {
        str(world["org_a"]),
        str(world["org_b"]),
    }
    only_b = (await root.get(f"/api/v1/integrity/events?organization_id={world['org_b']}")).json()
    assert only_b["total"] == 1
    assert (await root.get(f"/api/v1/sessions/{world['b1'].sid}/integrity")).status_code == 200


async def test_bulk_review(auth_client, world) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=world["org_a"])
    own = [
        await _first_event_id(world["a1"].session_id, "copy_event"),
        await _first_event_id(world["a2"].session_id),
    ]
    foreign = await _first_event_id(world["b1"].session_id)
    response = await recruiter.post(
        "/api/v1/integrity/events/review",
        json={
            "event_ids": [*own, foreign, str(uuid.uuid4())],
            "review_status": "noted",
            "review_note": "Seen",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] == 2 and [i["id"] for i in body["items"]] == own
    assert set(body["not_found"]) >= {foreign}
    assert all(i["review_status"] == "noted" for i in body["items"])
    async with session_scope() as db:
        audits = (await db.scalars(select(AuditLog).where(AuditLog.action == "integrity.review"))).all()
        assert (await db.get(IntegrityEvent, uuid.UUID(foreign))).review_status == "unreviewed"
    assert len(audits) == 2
    empty = await recruiter.post(
        "/api/v1/integrity/events/review", json={"event_ids": [], "review_status": "noted"}
    )
    assert empty.status_code == 422


async def test_session_timeline_counts_and_evidence_urls(auth_client, world) -> None:
    admin = await auth_client(Role.ADMIN, organization_id=world["org_a"])
    response = await admin.get(f"/api/v1/sessions/{world['a1'].sid}/integrity")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "not determinations" in body["notice"]
    assert body["counts"] == {
        "camera_disabled": 1,
        "copy_event": 1,
        "fullscreen_exited": 1,
        "multiple_persons_visible": 1,
    }
    assert body["counts_by_severity"]["high"] == 1
    assert body["unreviewed_flags"] == 2 and body["total_events"] == 4 and body["truncated"] is False
    assert body["policy"]["camera_frame_analysis"] is True
    times = [e["occurred_at"] for e in body["events"]]
    assert times == sorted(times)
    [vision] = [e for e in body["events"] if e["type"] == "multiple_persons_visible"]
    assert vision["evidence_media_id"] == world["evidence"] and vision["evidence_available"] is True
    assert vision["evidence_url"].startswith("/api/v1/media/signed/")
    image = await admin.get(vision["evidence_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.content[:3] == b"\xff\xd8\xff"
    async with session_scope() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "session.integrity.view"))
    assert log is not None and log.details["evidence_urls"] == 1


async def test_single_event_with_evidence_is_audited(auth_client, world) -> None:
    recruiter = await auth_client(Role.RECRUITER, organization_id=world["org_a"])
    event_id = await _first_event_id(world["a1"].session_id, "multiple_persons_visible")
    body = (await recruiter.get(f"/api/v1/integrity/events/{event_id}")).json()
    assert body["evidence_url"] and body["evidence_available"] is True
    async with session_scope() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "media.view"))
    assert log is not None and log.details["integrity_event_id"] == event_id


async def test_expired_evidence_is_reported_unavailable(auth_client, world) -> None:
    async with session_scope() as db:
        await db.execute(
            update(MediaAsset)
            .where(MediaAsset.id == uuid.UUID(world["evidence"]))
            .values(retention_until=datetime.now(UTC) - timedelta(minutes=1))
        )
    admin = await auth_client(Role.ADMIN, organization_id=world["org_a"])
    body = (await admin.get(f"/api/v1/sessions/{world['a1'].sid}/integrity")).json()
    [vision] = [e for e in body["events"] if e["type"] == "multiple_persons_visible"]
    assert vision["evidence_available"] is False and vision["evidence_url"] is None


async def test_unknown_ids_404(auth_client, world) -> None:
    admin = await auth_client(Role.ADMIN, organization_id=world["org_a"])
    assert (await admin.get(f"/api/v1/sessions/{uuid.uuid4()}/integrity")).status_code == 404
    assert (
        await admin.patch(f"/api/v1/integrity/events/{uuid.uuid4()}", json={"review_status": "noted"})
    ).status_code == 404
    assert (await admin.get("/api/v1/integrity/events/not-a-uuid")).status_code == 422


async def test_openapi_wording(app) -> None:
    spec = app.openapi()
    queue = spec["paths"]["/api/v1/integrity/events"]["get"]
    assert "not determinations" in queue["description"]
    assert "observations" in queue["summary"]
