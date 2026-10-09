"""/api/v1/sessions/* — recruiter-facing session APIs (RBAC, org isolation, audit)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core.queue import publish_session_event
from app.db.models import AuditLog, InterviewSession
from app.db.session import session_scope
from app.domain.enums import Role
from app.services.reports.live import live_events
from app.services.scoring import recompute_session_scores

pytestmark = pytest.mark.integration


async def _audits(action: str) -> list[AuditLog]:
    async with session_scope() as db:
        return list((await db.scalars(select(AuditLog).where(AuditLog.action == action))).all())


async def test_list_filters_and_org_isolation(
    auth_client, session_factory, org_factory, candidate_auth_client
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    org = recruiter.user.organization_id
    mine = await session_factory(org, candidate_name="Jane Doe")
    live = await session_factory(org, status="live", candidate_name="Sam Lee")
    other = await session_factory(await org_factory(), candidate_name="Other Person")

    response = await recruiter.get("/api/v1/sessions")
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {str(mine.session_id), str(live.session_id)}
    assert body["total"] == 2 and body["limit"] == 50 and body["offset"] == 0
    item = next(i for i in body["items"] if i["id"] == str(mine.session_id))
    assert item["candidate"]["full_name"] == "Jane Doe"
    assert item["job"]["title"] == "Senior DevOps Engineer"
    assert item["round"]["name"] == "Combined AI Round"
    assert item["integrity_flags"] == 1 and item["question_count"] == 15
    assert item["overall_score"] is None and item["live"] is False

    assert {i["id"] for i in (await recruiter.get("/api/v1/sessions?status=live")).json()["items"]} == {
        str(live.session_id)
    }
    assert (await recruiter.get("/api/v1/sessions?status=completed,live")).json()["total"] == 2
    assert (await recruiter.get("/api/v1/sessions?live=true")).json()["total"] == 1
    assert (await recruiter.get("/api/v1/sessions?q=jane")).json()["total"] == 1
    assert (await recruiter.get(f"/api/v1/sessions?job_id={mine.job_id}")).json()["total"] == 1
    assert (await recruiter.get("/api/v1/sessions?status=bogus")).status_code == 422
    assert (await recruiter.get("/api/v1/sessions?limit=1")).json()["items"].__len__() == 1

    async with session_scope() as db:
        await recompute_session_scores(db, mine.session_id, final=True)
    filtered = (await recruiter.get("/api/v1/sessions?recommendation=human_review")).json()
    assert [i["id"] for i in filtered["items"]] == [str(mine.session_id)]
    assert filtered["items"][0]["recommendation"] == "human_review"
    assert filtered["items"][0]["overall_score"] > 60
    assert (await recruiter.get("/api/v1/sessions?recommendation=proceed")).json()["total"] == 0

    # Cross-organization access looks like a 404; organization_id of another org is forbidden.
    assert (await recruiter.get(f"/api/v1/sessions/{other.session_id}")).status_code == 404
    assert (
        await recruiter.get(f"/api/v1/sessions?organization_id={other.organization_id}")
    ).status_code == 403
    # SUPER_ADMIN sees every organization and can filter by one.
    admin = await auth_client(Role.SUPER_ADMIN)
    assert (await admin.get(f"/api/v1/sessions?organization_id={other.organization_id}")).json()["total"] == 1
    assert (await admin.get(f"/api/v1/sessions/{other.session_id}")).status_code == 200

    candidate = await candidate_auth_client()
    assert (await candidate.get("/api/v1/sessions")).status_code == 403
    assert (await candidate.get(f"/api/v1/sessions/{mine.session_id}")).status_code == 403


async def test_unauthenticated(client) -> None:
    assert (await client.get("/api/v1/sessions")).status_code == 401
    assert (await client.get(f"/api/v1/sessions/{uuid.uuid4()}/scores")).status_code == 401


async def test_detail(auth_client, session_factory) -> None:
    interviewer = await auth_client(Role.INTERVIEWER)
    fx = await session_factory(interviewer.user.organization_id)
    async with session_scope() as db:
        await recompute_session_scores(db, fx.session_id, final=True)
    response = await interviewer.get(f"/api/v1/sessions/{fx.session_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["round_config"]["name"] == "Combined AI Round"
    assert body["plan"]["opening_focus"] == "EKS platform scale"
    assert body["state_digest"]["turn_state"] == "ended"
    assert {c["id"] for c in body["claims"]} == {"c1", "c2"}
    assert body["durations"]["elapsed_seconds"] == 1720.0 and body["durations"]["scheduled_seconds"] == 1800.0
    assert body["durations"]["wall_seconds"] > 0
    assert body["recommendation"] == "human_review" and body["recommendation_rationale"]
    assert body["human_decision_note"].startswith("This recommendation is assistive")
    assert body["scores_final"] is True and len(body["rubric"]) == 7
    assert body["candidate_report_mode"] == "immediate"
    assert (await interviewer.get(f"/api/v1/sessions/{uuid.uuid4()}")).status_code == 404


async def test_transcript_and_questions_are_audited(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    response = await recruiter.get(f"/api/v1/sessions/{fx.session_id}/transcript")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 28 and {i["speaker"] for i in body["items"]} == {"candidate", "interviewer"}
    assert [i["seq"] for i in body["items"]] == sorted(i["seq"] for i in body["items"])
    assert body["interruptions"] == 1
    first_answer = next(i for i in body["items"] if i["answer_id"] == str(fx.answer_ids[0]))
    assert first_answer["audio_media_id"] == str(fx.audio_media_id)
    assert body["answer_audio"] == {str(fx.answer_ids[0]): str(fx.audio_media_id)}
    only = (
        await recruiter.get(f"/api/v1/sessions/{fx.session_id}/transcript?speaker=candidate&limit=3")
    ).json()
    assert only["total"] == 14 and len(only["items"]) == 3
    audits = await _audits("transcript.view")
    assert (
        audits
        and audits[0].resource_id == str(fx.session_id)
        and audits[0].organization_id == fx.organization_id
    )

    questions = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/questions")).json()
    assert questions["total"] == 15
    q1, q2 = questions["items"][0], questions["items"][1]
    assert q1["followup_ids"] == [q2["id"]] and q2["parent_question_id"] == q1["id"]
    assert q2["followup_depth"] == 1 and q2["chain_root_id"] == q1["id"]
    answer = q1["answers"][0]
    assert (
        answer["evaluation"]["overall_quality"] == "strong"
        and answer["evaluation"]["correctness"] is not None
    )
    assert len(answer["evidence"]) == 2 and answer["audio_media_id"] == str(fx.audio_media_id)
    assert q1["difficulty"] == "medium" and q2["difficulty"] == "hard"
    coding = questions["items"][-1]
    assert coding["coding_question_id"] and coding["answers"] == []
    assert len(await _audits("transcript.view")) == 3  # two transcript reads + one questions read


async def test_evidence_scores_and_recompute(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    evidence = (
        await recruiter.get(f"/api/v1/sessions/{fx.session_id}/evidence?competency=kubernetes")
    ).json()
    assert evidence["total"] == 5 and all(e["competency_key"] == "kubernetes" for e in evidence["items"])
    negatives = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/evidence?signal=negative")).json()
    assert {e["competency_key"] for e in negatives["items"]} == {"aws", "terraform", "data_structures"}
    assert (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/evidence?source=hint_usage")).json()[
        "total"
    ] == 1

    live = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/scores")).json()
    assert live["source"] == "live" and live["overall"]["score"] > 0
    response = await recruiter.post(f"/api/v1/sessions/{fx.session_id}/recompute-scores")
    assert response.status_code == 200, response.text
    scores = response.json()
    assert scores["source"] == "persisted" and scores["is_final"] is True
    assert scores["recommendation"] == "human_review" and scores["rationale"]
    k8s = next(c for c in scores["competencies"] if c["key"] == "kubernetes")
    why = k8s["why"]
    assert len(why["positive_evidence"]) == 5 and why["negative_evidence"] == []
    assert [q["sequence"] for q in why["supporting_questions"]] == [1, 2, 3]
    assert why["transcript_refs"][0]["quote"] and why["calculation"]["confidence"] == pytest.approx(
        k8s["confidence"], abs=1e-3
    )
    assert "upgrades" in why["unanswered_areas"]
    aws = next(c for c in scores["competencies"] if c["key"] == "aws")
    assert aws["why"]["negative_evidence"][0]["dimension"] == "iam"
    assert {r["key"] for r in scores["rubric"]} >= {"technical_knowledge", "coding"}
    assert await _audits("scores.recompute")
    # Persisted read path returns the same set.
    again = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/scores")).json()
    assert again["overall"]["id"] == scores["overall"]["id"]
    non_final = (
        await recruiter.post(f"/api/v1/sessions/{fx.session_id}/recompute-scores?final=false")
    ).json()
    assert non_final["is_final"] is False


async def test_recompute_requires_csrf_and_permission(
    auth_client, session_factory, candidate_auth_client
) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    del recruiter.headers["X-CSRF-Token"]
    assert (await recruiter.post(f"/api/v1/sessions/{fx.session_id}/recompute-scores")).status_code == 403
    candidate = await candidate_auth_client(organization_id=fx.organization_id)
    assert (await candidate.post(f"/api/v1/sessions/{fx.session_id}/recompute-scores")).status_code == 403


async def test_timeline_windows(auth_client, session_factory) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    replay = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/timeline")).json()
    assert replay["source"] == "replay" and replay["window_minutes"] == 10
    assert [w["label"] for w in replay["windows"]] == ["Minute 0-10", "Minute 10-20", "Minute 20-30"]
    first = replay["windows"][0]
    assert first["scores"]["kubernetes"] > 7 and first["summary"].startswith("Minute 0-10: ")
    assert (
        replay["series"]["kubernetes"][0]["elapsed_seconds"]
        < replay["series"]["kubernetes"][-1]["elapsed_seconds"]
    )
    assert [e["type"] for e in replay["events"]] == [
        "session.started",
        "question.asked",
        "coding.started",
        "session.ended",
    ]
    async with session_scope() as db:
        await recompute_session_scores(db, fx.session_id, final=True)
    snap = (await recruiter.get(f"/api/v1/sessions/{fx.session_id}/timeline?window_minutes=5")).json()
    assert snap["source"] == "snapshots" and len(snap["windows"]) == 6
    assert any(s["trigger"] == "final" for s in snap["snapshots"])
    assert (
        await recruiter.get(f"/api/v1/sessions/{fx.session_id}/timeline?window_minutes=0")
    ).status_code == 422


async def test_live_sse_for_finished_session(auth_client, session_factory, candidate_auth_client) -> None:
    recruiter = await auth_client(Role.RECRUITER)
    fx = await session_factory(recruiter.user.organization_id)
    response = await recruiter.get(f"/api/v1/sessions/{fx.session_id}/live")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    text = response.text
    assert "event: state" in text and "event: end" in text and '"session_ended"' in text
    assert await _audits("session.live.view")
    candidate = await candidate_auth_client(organization_id=fx.organization_id)
    assert (await candidate.get(f"/api/v1/sessions/{fx.session_id}/live")).status_code == 403


async def test_live_stream_forwards_bus_events_and_ends(session_factory, redis) -> None:
    fx = await session_factory(status="live")
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    chunks: list[str] = []

    async def consume() -> None:
        async for chunk in live_events(
            fx.session_id, is_disconnected=is_disconnected, interval=1.0, max_seconds=20
        ):
            chunks.append(chunk)

    task = asyncio.create_task(consume())
    for _ in range(50):
        if any("event: state" in c for c in chunks):
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)  # subscription established after the first digest
    await publish_session_event(fx.session_id, {"type": "evaluation.ready", "answer_id": "a1"})
    await publish_session_event(fx.session_id, {"type": "integrity.event", "event_type": "tab_hidden"})
    await asyncio.sleep(0.3)
    async with session_scope() as db:
        session = await db.get(InterviewSession, fx.session_id)
        session.status = "completed"
    await asyncio.wait_for(task, 10)
    text = "".join(chunks)
    assert text.startswith("retry: 3000")
    assert "event: evaluation.ready" in text and "event: integrity.event" in text
    assert '"conversation"' in text and "event: end" in text


async def test_needs_review_filter_is_the_review_queue(auth_client, session_factory, db_session) -> None:
    from app.db.models import Report, ReviewDecision

    recruiter = await auth_client(Role.RECRUITER)
    org = recruiter.user.organization_id
    waiting = await session_factory(org, candidate_name="Wanda Waiting")
    decided = await session_factory(org, candidate_name="Dana Decided")
    running = await session_factory(org, status="live", candidate_name="Rita Running")
    ended_early = await session_factory(org, status="aborted", candidate_name="Abe Aborted")
    db_session.add(
        ReviewDecision(session_id=decided.session_id, reviewer_id=recruiter.user.id, decision="advance")
    )
    await db_session.commit()

    async def ids(**params) -> set[str]:
        response = await recruiter.get("/api/v1/sessions", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(body["items"])
        return {item["id"] for item in body["items"]}

    assert await ids(needs_review="true") == {str(waiting.session_id)}
    assert await ids(needs_review="true", status="completed") == {str(waiting.session_id)}
    assert await ids(needs_review="true", status="live") == set()
    assert await ids(needs_review="false") == {
        str(decided.session_id),
        str(running.session_id),
        str(ended_early.session_id),
    }
    queue = (await recruiter.get("/api/v1/sessions", params={"needs_review": "true"})).json()["items"]
    assert queue[0]["review_decision"] is None and queue[0]["status"] == "completed"
    # Same definition as the dashboard KPI.
    overview = (await recruiter.get("/api/v1/analytics/overview")).json()
    assert overview["awaiting_review"] == 1

    # The recommendation filter matches the value the list shows: the newest
    # recruiter report's recommendation wins over the score's.
    async with session_scope() as db:
        await recompute_session_scores(db, waiting.session_id, final=True)
        await recompute_session_scores(db, decided.session_id, final=True)
    listed = {i["id"]: i for i in (await recruiter.get("/api/v1/sessions")).json()["items"]}
    assert listed[str(waiting.session_id)]["recommendation"] == "human_review"
    db_session.add(
        Report(
            session_id=decided.session_id,
            type="recruiter",
            status="ready",
            version=1,
            recommendation="proceed",
            content={},
        )
    )
    await db_session.commit()
    listed = {i["id"]: i for i in (await recruiter.get("/api/v1/sessions")).json()["items"]}
    assert listed[str(decided.session_id)]["recommendation"] == "proceed"
    assert await ids(recommendation="proceed") == {str(decided.session_id)}
    assert await ids(recommendation="human_review") == {str(waiting.session_id)}
