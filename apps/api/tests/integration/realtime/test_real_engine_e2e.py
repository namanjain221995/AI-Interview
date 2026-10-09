"""End to end with the REAL interview engine, session service, coding,
integrity and disclosure modules (mock AI): candidate access -> portal ->
session -> WebSocket -> intro -> spoken answer -> follow-up -> end."""

from __future__ import annotations

import asyncio

import httpx
import numpy as np
import pytest

from app.realtime.registry import get_registry

from .conftest import AsgiWebSocket, create_interview, load_clip, silence

pytestmark = pytest.mark.integration


async def test_full_candidate_flow_with_real_engine(app, org_factory, mock_ai, storage) -> None:
    mock_ai.tts.ms_per_char = 15
    data = await create_interview(await org_factory(name="Acme"), with_session=False)
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.9.8.7", 5000)), base_url="http://testserver"
    )
    try:
        r = await http.post(
            f"/api/v1/candidate-access/{data['invite_token']}/verify", json={"email": data["candidate_email"]}
        )
        assert r.status_code == 200, r.text
        http.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        iid = data["interview_id"]

        disclosure = (await http.get(f"/api/v1/portal/interviews/{iid}/disclosure")).json()
        consent = await http.post(
            f"/api/v1/portal/interviews/{iid}/consent",
            json={
                "policy_hash": disclosure["policy_hash"],
                "items": {k: True for k in disclosure["required_items"]},
            },
        )
        assert consent.status_code == 201, consent.text
        created = await http.post(f"/api/v1/portal/interviews/{iid}/session")
        assert created.status_code == 200, created.text
        session_id = created.json()["session_id"]

        # The session.prepare job would run in the worker; run it inline.
        from app.services.interview.engine import get_engine

        await asyncio.wait_for(get_engine().prepare_session(session_id), 60)

        ticket = (await http.post(f"/api/v1/portal/sessions/{session_id}/ws-ticket")).json()
        ws = AsgiWebSocket(
            app,
            f"/ws/sessions/{session_id}",
            query=f"ticket={ticket['ticket']}",
            headers=[(b"origin", b"http://testserver")],
        )
        await ws.connect()
        assert ws.accepted
        await ws.hello(user_agent="pytest-e2e")
        state = await ws.wait_type("session.state")
        assert state["status"] in ("ready", "preparing", "created")

        await ws.send_json({"type": "control.start"})
        intro = await ws.wait_type("ai.utterance.start", timeout=30)
        intro_end = await ws.wait_type("ai.utterance.end", utterance_id=intro["utterance_id"], timeout=30)
        assert intro_end["text"]
        assert ws.of_type("tts.chunk"), "no interviewer audio"
        await ws.send_json(
            {"type": "playback.started", "utterance_id": intro["utterance_id"], "played_ms": 0}
        )
        await ws.send_json(
            {"type": "playback.finished", "utterance_id": intro["utterance_id"], "played_ms": 99999}
        )
        await ws.wait_type("turn.state", state="listening", timeout=20)

        mark = len(ws.messages)
        mock_ai.stt.default_text = ""
        mock_ai.stt.script(
            "I ran EKS with three managed node groups across three availability zones,",
            "and Karpenter handled autoscaling.",
        )
        await ws.send_audio(np.concatenate((silence(300), load_clip("cost_vague"), silence(2000))))
        final = await ws.wait_type("transcript.final", after=mark, timeout=30)
        assert final["text"].startswith("I ran EKS")
        reply = await ws.wait_for(
            lambda m: m.get("type") == "ai.utterance.start" and m.get("kind") != "acknowledgment",
            after=mark,
            timeout=60,
        )
        reply_end = await ws.wait_type(
            "ai.utterance.end", utterance_id=reply["utterance_id"], after=mark, timeout=60
        )
        assert reply_end["text"]
        runtime = get_registry().get(session_id)
        record = runtime.latency[-1]
        assert record["segments"] >= 2 and "first_audio_ms" in record, record

        await ws.send_json(
            {"type": "playback.finished", "utterance_id": reply["utterance_id"], "played_ms": 99999}
        )
        await ws.send_json({"type": "control.end_interview", "reason": "e2e"})
        ended = await ws.wait_type("session.ended", timeout=60)
        assert ended["reason"]
        await ws.disconnect()

        session = (await http.get(f"/api/v1/portal/sessions/{session_id}")).json()
        assert session["status"] in ("completing", "completed")
    finally:
        await get_registry().shutdown_all()
        await http.aclose()
