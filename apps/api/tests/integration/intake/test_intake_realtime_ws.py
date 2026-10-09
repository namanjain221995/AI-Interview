"""Intake activities over the real WebSocket runtime with the real engine (mock AI): message sequence, Drill B
passage hidden at 30 s of interview time (advance-clock hook), never in replay / Redis outbox / session.state,
reconnect during the read phase re-shows only the remaining time, ``activity.finish`` rules, test hooks."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from app.core.security import issue_ws_ticket
from app.realtime import deps as realtime_deps
from app.realtime.registry import get_registry
from app.services.intake.kit import load_intake_kit
from app.services.interview.contracts import ActivityEvent
from app.services.interview.engine import set_engine

from ..realtime.conftest import AsgiWebSocket
from . import live_helpers
from .live_helpers import (
    activity_rows,
    build_intake_world,
    create_session,
)

intake_env = live_helpers.intake_env  # the shared fixture

pytestmark = pytest.mark.integration

HOOKS = "/api/v1/test"


async def deliver(ws: AsgiWebSocket, utterance_id: str) -> None:
    await ws.send_json({"type": "playback.started", "utterance_id": utterance_id, "played_ms": 0})
    await ws.send_json({"type": "playback.finished", "utterance_id": utterance_id, "played_ms": 99_999})


async def deliver_all(ws: AsgiWebSocket, seen: set[str]) -> None:
    for message in list(ws.messages):
        if message.get("type") == "ai.utterance.end" and message["utterance_id"] not in seen:
            seen.add(message["utterance_id"])
            await deliver(ws, message["utterance_id"])


async def connect(app: Any, world: Any, sid: str, *, last_seq: int = 0) -> AsgiWebSocket:
    ticket = await issue_ws_ticket(uuid.UUID(sid), world.candidate_id, world.interview_id)
    ws = AsgiWebSocket(
        app, f"/ws/sessions/{sid}", query=f"ticket={ticket}", headers=[(b"origin", b"http://testserver")]
    )
    await ws.connect()
    assert ws.accepted, ws.close_code
    await ws.hello(last_seq=last_seq, user_agent="pytest-intake")
    return ws


@pytest.fixture
def live_engine(intake_env):
    engine = intake_env["make_engine"](real_clock=True)
    previous = set_engine(engine)
    with realtime_deps.override_providers(turn_engine=engine):
        yield engine
    set_engine(previous)


async def until_reading(app, client, intake_env, live_engine) -> tuple[Any, str, AsgiWebSocket, set[str]]:
    intake_env["mock_ai"].tts.ms_per_char = 5
    world = await build_intake_world()
    sid = await create_session(intake_env, world)
    await live_engine.prepare_session(sid)
    await live_engine.intake_jump(sid, "reading")
    ws = await connect(app, world, sid)
    state = await ws.wait_type("session.state")
    assert state["intake"]["stage"] == "reading" and state["intake"]["stage_title"] == "Reading exercise"
    await ws.send_json({"type": "control.start"})
    return world, sid, ws, set()


async def test_drill_b_passage_secrecy_and_server_hide_at_30_seconds(
    app, client, redis, intake_env, live_engine
) -> None:
    kit = load_intake_kit()
    world, sid, ws, delivered = await until_reading(app, client, intake_env, live_engine)
    start_a = await ws.wait_type("activity.start", kind="reading_aloud", timeout=20)
    assert (
        start_a["status"] == "armed" and "content" not in start_a and start_a["phases"][0]["phase"] == "read"
    )
    await ws.wait_type("ai.utterance.end", timeout=20)
    await deliver_all(ws, delivered)
    phase_a = await ws.wait_for(
        lambda m: m.get("type") == "activity.phase" and m.get("status") == "running", timeout=10
    )
    assert phase_a["capture"] is True and phase_a["can_finish"] is False and phase_a["content"]["paragraphs"]
    # Finishing before 5 s is refused; the capture continues.
    await ws.send_json({"type": "activity.finish", "activity_id": start_a["activity_id"], "phase": "read"})
    await ws.wait_type("error", code="finish_not_available", timeout=5)
    # Complete Drill A through the capture hook (as the runtime would after the reading).
    r = await client.post(
        f"{HOOKS}/intake/sessions/{sid}/capture",
        json={
            "activity_id": start_a["activity_id"],
            "phase": "read",
            "text": "Gradient descent finds the minimum.",
        },
    )
    assert r.status_code == 200, r.text
    await ws.wait_type("activity.end", activity_id=start_a["activity_id"], timeout=20)
    start_b = await ws.wait_type("activity.start", kind="read_relay", timeout=20)
    drill_b = (await live_engine.load_state(sid)).intake.passages["drill_b"]
    paragraphs = list(kit.passage(drill_b).paragraphs)
    assert "content" not in start_b and [p["phase"] for p in start_b["phases"]] == ["read", "relay"]
    await ws.wait_for(
        lambda m: m.get("type") == "ai.utterance.end" and m["utterance_id"] not in delivered, timeout=20
    )
    await deliver_all(ws, delivered)
    read = await ws.wait_for(
        lambda m: (
            m.get("type") == "activity.phase"
            and m.get("phase") == "read"
            and m.get("activity_id") == start_b["activity_id"]
        ),
        timeout=10,
    )
    assert (
        read["status"] == "running"
        and read["content"]["paragraphs"] == paragraphs
        and read["remaining_ms"] == 30_000
    )
    # 30 s of interview time pass (test hook): the server hides the passage at once.
    mark = len(ws.messages)
    r = await client.post(f"{HOOKS}/sessions/{sid}/advance-clock", json={"seconds": 30.5})
    assert r.status_code == 200, r.text
    hidden = await ws.wait_for(
        lambda m: (
            m.get("type") == "activity.phase" and m.get("status") == "ended" and m.get("phase") == "read"
        ),
        after=mark,
        timeout=5,
    )
    assert hidden["hide_content"] is True and hidden["content"] is None
    prompt = await ws.wait_for(
        lambda m: m.get("type") == "ai.utterance.end" and "explain what you understood" in m.get("text", ""),
        after=mark,
        timeout=20,
    )
    await deliver_all(ws, delivered)
    relay = await ws.wait_for(
        lambda m: (
            m.get("type") == "activity.phase" and m.get("phase") == "relay" and m.get("status") == "running"
        ),
        after=mark,
        timeout=10,
    )
    assert relay["content"] is None and relay["hide_content"] is True and relay["capture"] is True
    assert prompt["text"].startswith("Now explain")
    # Secrecy: only the read-phase start message ever carried the passage.
    needle = paragraphs[0][:60]
    carriers = [m for m in ws.messages if needle in json.dumps(m)]
    assert carriers == [read]
    outbox = await redis.lrange(f"session:{sid}:outbox", 0, -1)
    assert outbox and not any(needle in item for item in outbox)
    assert not any(json.loads(item).get("type", "").startswith("activity.") for item in outbox)
    runtime = get_registry().get(sid)
    assert runtime is not None and runtime.activity is not None and runtime.activity.content is None
    state_msg = await runtime.build_state()
    assert needle not in state_msg.model_dump_json() and state_msg.intake.activity.phase == "relay"
    # The candidate finishes the relay: the activity ends, the reading follow-up comes next.
    mark = len(ws.messages)
    await ws.send_json({"type": "activity.finish", "activity_id": start_b["activity_id"], "phase": "relay"})
    end_b = await ws.wait_type("activity.end", activity_id=start_b["activity_id"], after=mark, timeout=20)
    assert end_b["reason"] == "finished_early"
    await ws.wait_for(
        lambda m: m.get("type") == "ai.utterance.end" and "least sure" in m.get("text", ""),
        after=mark,
        timeout=20,
    )
    rows = await activity_rows(sid)
    assert rows["read_relay"].status == "completed" and rows["read_relay"].reveal["shown_ms"] >= 29_000
    assert rows["read_relay"].transcript_status == "unavailable"  # no audio was sent: nothing to transcribe
    await ws.disconnect()
    await get_registry().shutdown_all()


async def test_reconnect_during_the_read_phase_reshows_only_the_remaining_time(
    app, client, intake_env, live_engine
) -> None:
    kit = load_intake_kit()
    world, sid, ws, delivered = await until_reading(app, client, intake_env, live_engine)
    start_a = await ws.wait_type("activity.start", kind="reading_aloud", timeout=20)
    await ws.wait_type("ai.utterance.end", timeout=20)
    await deliver_all(ws, delivered)
    await ws.wait_for(
        lambda m: m.get("type") == "activity.phase" and m.get("status") == "running", timeout=10
    )
    r = await client.post(
        f"{HOOKS}/intake/sessions/{sid}/capture",
        json={"activity_id": start_a["activity_id"], "phase": "read", "text": "Gradient descent."},
    )
    assert r.status_code == 200
    start_b = await ws.wait_type("activity.start", kind="read_relay", timeout=20)
    await ws.wait_for(
        lambda m: m.get("type") == "ai.utterance.end" and m["utterance_id"] not in delivered, timeout=20
    )
    await deliver_all(ws, delivered)
    await ws.wait_for(
        lambda m: m.get("type") == "activity.phase" and m.get("activity_id") == start_b["activity_id"],
        timeout=10,
    )
    r = await client.post(f"{HOOKS}/sessions/{sid}/advance-clock", json={"seconds": 20})
    assert r.status_code == 200
    await asyncio.sleep(0.3)  # the runtime re-syncs its clock from the bus
    last_seq = max(m["seq"] for m in ws.messages)
    await ws.disconnect()
    ws2 = await connect(app, world, sid, last_seq=last_seq)
    state = await ws2.wait_type("session.state", timeout=10)
    assert state["intake"]["activity"]["activity_id"] == start_b["activity_id"]
    drill_b = (await live_engine.load_state(sid)).intake.passages["drill_b"]
    needle = kit.passage(drill_b).paragraphs[0][:60]
    assert needle not in json.dumps(state)
    again = await ws2.wait_type("activity.start", activity_id=start_b["activity_id"], timeout=20)
    assert again["resumed"] is True and again["phases"][0]["phase"] == "read"
    await ws2.wait_for(
        lambda m: m.get("type") == "ai.utterance.end" and "Welcome back" in m.get("text", ""), timeout=20
    )
    seen: set[str] = set()
    await deliver_all(ws2, seen)
    reshown = await ws2.wait_for(
        lambda m: (
            m.get("type") == "activity.phase" and m.get("phase") == "read" and m.get("status") == "running"
        ),
        timeout=10,
    )
    assert reshown["content"]["paragraphs"] and 8_000 <= reshown["remaining_ms"] <= 10_100
    # Replayed messages (seq <= last_seq were already seen) never include activity messages.
    replayed = [m for m in ws2.messages if m.get("seq", 0) <= last_seq]
    assert not any(m.get("type", "").startswith("activity.") for m in replayed)
    await ws2.disconnect()
    await get_registry().shutdown_all()


async def test_hooks_without_a_live_runtime(client, intake_env) -> None:
    env = intake_env
    world = await build_intake_world()
    sid = await create_session(env, world)
    await env["engine"].prepare_session(sid)
    from .live_helpers import collect

    await collect(env["engine"].start(sid))
    r = await client.post(f"{HOOKS}/intake/sessions/{sid}/jump", json={"stage": "topic"})
    assert r.status_code == 200, r.text
    body = r.json()
    started = next(e for e in body["events"] if e["type"] == "activity_started")
    assert started["kind"] == "topic_card" and "content" not in started
    r = await client.post(
        f"{HOOKS}/intake/sessions/{sid}/activity-deadline", json={"activity_id": started["activity_id"]}
    )
    assert r.status_code == 409  # not running until the phase started
    await collect(
        env["engine"].handle_activity_event(
            sid,
            ActivityEvent(
                kind="phase_started", activity_id=started["activity_id"], phase="prepare", elapsed_s=1.0
            ),
        )
    )
    r = await client.post(
        f"{HOOKS}/intake/sessions/{sid}/activity-deadline", json={"activity_id": started["activity_id"]}
    )
    assert r.status_code == 200, r.text
    changed = next(e for e in r.json()["events"] if e["type"] == "activity_phase_changed")
    assert changed["phase"] == "speak" and changed["capture"] is True
    await collect(
        env["engine"].handle_activity_event(
            sid,
            ActivityEvent(
                kind="phase_started", activity_id=started["activity_id"], phase="speak", elapsed_s=70.0
            ),
        )
    )
    r = await client.post(
        f"{HOOKS}/intake/sessions/{sid}/capture",
        json={
            "activity_id": started["activity_id"],
            "phase": "speak",
            "text": "Teams need trust. Hybrid works.",
        },
    )
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    assert events[0]["type"] == "activity_ended" and events[0]["reason"] == "finished_early"
    assert any(e.get("item_key") == "topic.weakest" for e in events)
    rows = await activity_rows(sid)
    assert (
        rows["topic_card"].transcript_status == "live_only"
        and rows["topic_card"].transcript_source == "live_segments"
    )


async def test_reading_with_audio_runs_the_live_windowed_final_pass(
    app, client, intake_env, live_engine, monkeypatch
) -> None:
    import numpy as np

    from app.realtime import router as router_module
    from app.realtime.limits import ConnectionLimits, TokenBucket

    from ..realtime.conftest import load_clip, silence

    monkeypatch.setattr(
        router_module,
        "make_limits",
        lambda: ConnectionLimits(frames=TokenBucket(10_000, 10_000), audio_bytes=TokenBucket(10**8, 10**8)),
    )
    mock_ai = intake_env["mock_ai"]
    world, sid, ws, delivered = await until_reading(app, client, intake_env, live_engine)
    start_a = await ws.wait_type("activity.start", kind="reading_aloud", timeout=20)
    await ws.wait_type("ai.utterance.end", timeout=20)
    await deliver_all(ws, delivered)
    await ws.wait_for(
        lambda m: m.get("type") == "activity.phase" and m.get("status") == "running", timeout=10
    )
    mock_ai.stt.default_text = "gradient descent"
    clip = load_clip("cost_vague")
    await ws.send_audio(np.concatenate((silence(300), clip, silence(1200), clip, silence(400))))
    r = await client.post(f"{HOOKS}/sessions/{sid}/advance-clock", json={"seconds": 6})
    assert r.status_code == 200
    await asyncio.sleep(0.3)
    mark = len(ws.messages)
    await ws.send_json({"type": "activity.finish", "activity_id": start_a["activity_id"], "phase": "read"})
    ended = await ws.wait_type("activity.end", activity_id=start_a["activity_id"], after=mark, timeout=30)
    assert ended["reason"] == "finished_early"
    final = await ws.wait_type("transcript.final", after=mark, timeout=10)
    assert "gradient descent" in final["text"]
    row = None
    for _ in range(100):
        row = (await activity_rows(sid))["reading_aloud"]
        if row.transcript_status == "final" and row.answer_id is not None:
            break
        await asyncio.sleep(0.1)
    assert row is not None and row.transcript_status == "final" and row.transcript_source == "final_pass_live"
    assert row.capture["windows"] and all(
        w["end_ms"] - w["start_ms"] <= 25_000 for w in row.capture["windows"]
    )
    assert row.capture["pauses"] and any(p["duration_ms"] >= 900 for p in row.capture["pauses"])
    assert row.capture["segments"] and row.capture["end_reason"] == "finish_button"
    assert row.capture["audio_stored"] is True and row.audio_media_id is not None
    await ws.disconnect()
    await get_registry().shutdown_all()
