"""WebSocket admission, protocol validation, limits, coding and integrity bridges."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.queue import publish_session_event
from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity
from app.realtime.limits import MAX_TEXT_BYTES
from app.realtime.lock import lock_key
from app.services.integrity.contracts import IntegrityOutcome

from .conftest import coding_payload
from .test_ws_session import deliver, live, wait_call

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------ admission


async def test_foreign_origin_is_rejected_before_accept(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket(), origin="https://evil.example")
    assert not ws.accepted and ws.close_code in (4003, 403)


async def test_lan_and_missing_origin(realtime_env, ws_connect, settings) -> None:
    env = await realtime_env()
    ws = await ws_connect(
        env.session_id, await env.ticket(), origin="http://localhost:3200"
    )  # PUBLIC_BASE_URL
    assert ws.accepted
    await ws.disconnect()
    ws = await ws_connect(env.session_id, await env.ticket(), origin=None)  # non-browser client in test env
    assert ws.accepted


async def test_missing_and_foreign_tickets_close_4001(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    other = await realtime_env()
    for ticket in (None, "not-a-real-ticket-value-xxxxxxxxxxxxxxxx", await other.ticket()):
        ws = await ws_connect(env.session_id, ticket)
        assert ws.accepted
        error = await ws.wait_type("error")
        assert error["code"] == "unauthorized"
        assert await ws.wait_closed() == 4001


async def test_ticket_is_single_use(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ticket = await env.ticket()
    ws = await ws_connect(env.session_id, ticket)
    await ws.hello()
    await ws.wait_type("session.state")
    ws2 = await ws_connect(env.session_id, ticket)
    assert (await ws2.wait_type("error"))["code"] == "unauthorized"


async def test_hello_is_required(realtime_env, ws_connect, monkeypatch) -> None:
    from app.realtime import router as router_module

    monkeypatch.setattr(router_module, "HELLO_TIMEOUT_SECONDS", 0.3)
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket())
    assert (await ws.wait_type("error"))["code"] == "hello_required"
    assert await ws.wait_closed() == 4400
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.send_json({"type": "control.start"})
    assert (await ws.wait_type("error"))["code"] == "hello_required"


async def test_wrong_protocol_or_sample_rate_is_refused(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello(sample_rate=48000)
    assert (await ws.wait_type("error"))["code"] == "unsupported_sample_rate"
    assert await ws.wait_closed() == 4400
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.send_json({"type": "hello", "protocol": 2, "last_seq": 0})
    assert (await ws.wait_type("error"))["code"] == "unsupported_protocol"


async def test_ended_session_is_closed_4011(realtime_env, ws_connect) -> None:
    env = await realtime_env(session_status="completed")
    ws = await ws_connect(env.session_id, await env.ticket())
    ended = await ws.wait_type("session.ended")
    assert ended["reason"] == "completed"
    assert await ws.wait_closed() == 4011


async def test_session_owned_by_another_instance(realtime_env, ws_connect, redis) -> None:
    env = await realtime_env()
    await redis.set(lock_key(str(env.session_id)), "other-instance:abc", px=30_000)
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    assert (await ws.wait_type("error"))["code"] == "session_active_elsewhere"
    assert await ws.wait_closed() == 4009


async def test_newer_connection_replaces_older_one(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws1 = await ws_connect(env.session_id, await env.ticket())
    await ws1.hello()
    await ws1.wait_type("session.state")
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=ws1.messages[-1]["seq"])
    await ws2.wait_type("session.state")
    assert await ws1.wait_closed() == 4010


async def test_engine_unavailable_closes_1011(realtime_env, ws_connect, monkeypatch) -> None:
    from app.core.errors import ServiceUnavailable
    from app.realtime import deps

    env = await realtime_env()

    def unavailable():
        raise ServiceUnavailable("engine missing", code="turn_engine_unavailable")

    monkeypatch.setattr(deps, "get_turn_engine", unavailable)
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    error = await ws.wait_type("error")
    assert error["code"] == "turn_engine_unavailable" and error["recoverable"] is True
    assert await ws.wait_closed() == 1011


# ------------------------------------------------------------------ protocol


async def test_invalid_messages_are_reported_without_closing(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    await ws.send_text("{not json")
    assert (await ws.wait_type("error", code="invalid_json"))["recoverable"] is True
    await ws.send_json({"type": "no.such.type"})
    await ws.wait_type("error", code="invalid_message")
    await ws.send_json({"type": "answer.text", "text": ""})  # min_length=1
    await ws.wait_type("error", code="invalid_message")
    await ws.send_json({"type": "control.end_turn"})  # before start
    await ws.wait_type("error", code="not_started")
    await ws.send_json({"type": "heartbeat", "t": 123.5})
    ack = await ws.wait_type("heartbeat.ack")
    assert ack["t"] == 123.5 and ack["server_time"] > 0
    assert ws.close_code is None


async def test_oversized_frames_close_1009(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    await ws.send_text(
        json.dumps({"type": "client.error", "component": "other", "message": "x" * (MAX_TEXT_BYTES + 10)})
    )
    assert await ws.wait_closed() == 1009
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    await ws.send_bytes(b"\x01\x00\x00\x00" + (1).to_bytes(4, "little") + b"\x00" * 64_000)
    assert await ws.wait_closed() == 1009


async def test_message_flood_is_rate_limited_then_closed(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    for _ in range(60):
        await ws.send_json({"type": "playback.progress", "utterance_id": "nope", "played_ms": 1})
    await ws.wait_type("error", code="rate_limited")
    for _ in range(400):
        await ws.send_json({"type": "playback.progress", "utterance_id": "nope", "played_ms": 1})
    assert await ws.wait_closed() == 4029


async def test_unknown_binary_frame_type_is_reported(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    await ws.send_bytes(b"\x02\x00\x00\x00" + (1).to_bytes(4, "little") + b"\x00" * 10)
    await ws.wait_type("error", code="unsupported_frame")
    await ws.send_bytes(b"\x01\x00")
    await ws.wait_type("error", code="bad_frame")


# ------------------------------------------------------------------- coding


async def test_coding_bridge_runs_updates_and_hints(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    env.engine.coding_payload = coding_payload("cs1")
    env.engine.replies = ["__coding__"]
    ws, _, _ = await live(env, ws_connect)
    await ws.send_json({"type": "answer.text", "text": "Ready for coding."})
    start = await ws.wait_type("coding.start")
    assert start["coding"]["coding_session_id"] == "cs1" and start["coding"]["question"]["title"] == "Two Sum"
    synced = await ws.wait_for(lambda m: m.get("type") == "session.state" and m.get("coding") is not None)
    assert (
        synced["coding"]["coding_session_id"] == "cs1"
    )  # StateChanged -> session.state with the coding payload
    transition = await ws.wait_type("ai.utterance.start", kind="transition")
    await ws.wait_type("ai.utterance.end", utterance_id=transition["utterance_id"])
    await deliver(ws, transition["utterance_id"])
    await ws.wait_type("turn.state", state="coding")

    code = "def two_sum(nums, target):\n    return [0, 1]\n"
    await ws.send_json(
        {"type": "code.update", "coding_session_id": "cs1", "language": "python", "code": code, "version": 3}
    )
    await ws.send_json(
        {
            "type": "code.update",
            "coding_session_id": "wrong",
            "language": "python",
            "code": code,
            "version": 4,
        }
    )
    await ws.wait_type("error", code="coding_not_active")
    await ws.send_json(
        {"type": "code.run", "coding_session_id": "cs1", "kind": "test", "language": "python", "code": code}
    )
    result = await ws.wait_type("coding.run_result")
    assert result["status"] == "passed" and result["passed"] == 2
    hidden = [t for t in result["tests"] if t.get("hidden")]
    assert hidden == [
        {"id": "h1", "passed": True, "hidden": True}
    ]  # hidden test details never reach the candidate
    assert next(t for t in result["tests"] if t["id"] == "t1")["actual"] == [0, 1]  # visible results as-is
    await wait_call(env.engine, "coding")
    for _ in range(100):
        kinds = [a.kind for a in env.engine.coding_activities]
        if "run_completed" in kinds:
            break
        await asyncio.sleep(0.02)
    assert "edit" in kinds and "run_completed" in kinds
    assert env.coding.updates == [("cs1", 3)] and env.coding.runs == [("cs1", "test")]

    mark = len(ws.messages)
    await ws.send_json({"type": "code.language", "coding_session_id": "cs1", "language": "javascript"})
    refreshed = await ws.wait_for(
        lambda m: (
            m.get("type") == "session.state" and (m.get("coding") or {}).get("language") == "javascript"
        ),
        after=mark,
    )
    assert refreshed["coding"]["code"] == "// starter\n" and env.coding.language_changes == ["javascript"]

    await ws.send_json({"type": "code.hint_request", "coding_session_id": "cs1"})
    hint = await ws.wait_type("coding.hint")
    assert hint["level"] == 1 and hint["hints_remaining"] == 2
    spoken = await ws.wait_type("ai.utterance.start", kind="hint")
    await ws.wait_type("ai.utterance.end", utterance_id=spoken["utterance_id"])
    await deliver(ws, spoken["utterance_id"])
    await ws.wait_type("turn.state", state="coding", after=ws.messages.index(spoken))

    await ws.send_json(
        {"type": "code.submit", "coding_session_id": "cs1", "language": "python", "code": code}
    )
    end = await ws.wait_type("coding.end")
    assert end["reason"] == "submitted"
    followup = await ws.wait_for(
        lambda m: (
            m.get("type") == "ai.utterance.start"
            and m.get("utterance_id") != spoken["utterance_id"]
            and m.get("kind") == "question"
        ),
        after=ws.messages.index(end),
    )
    await ws.wait_type("ai.utterance.end", utterance_id=followup["utterance_id"])
    await deliver(ws, followup["utterance_id"])
    await ws.wait_type(
        "turn.state", state="listening", after=ws.messages.index(followup)
    )  # coding over -> listening
    assert ("cs1", "submit") in env.coding.runs


# ---------------------------------------------------------------- integrity


async def test_integrity_events_warn_and_objective_terminate(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    env.integrity.next_outcomes = [
        IntegrityOutcome(
            event_id="e1",
            event_type=IntegrityEventType.TAB_HIDDEN,
            severity=IntegritySeverity.LOW,
            action=IntegrityAction.WARN_CANDIDATE,
            candidate_message="Please keep the interview tab visible.",
        )
    ]
    event = {"event_type": "tab_hidden", "occurred_at": "2026-10-07T10:00:00Z", "duration_ms": 1200}
    await ws.send_json({"type": "integrity.events", "events": [event]})
    warning = await ws.wait_type("integrity.warning")
    assert warning == {
        **warning,
        "event_type": "tab_hidden",
        "message": "Please keep the interview tab visible.",
    }
    assert env.integrity.client_events[0].event_type is IntegrityEventType.TAB_HIDDEN

    # A vision-derived observation can never terminate the interview.
    env.integrity.next_outcomes = [
        IntegrityOutcome(
            event_id="e2",
            event_type=IntegrityEventType.POSSIBLE_PHONE_VISIBLE,
            severity=IntegritySeverity.HIGH,
            action=IntegrityAction.TERMINATE_INTERVIEW,
        )
    ]
    await ws.send_json({"type": "integrity.events", "events": [{**event, "event_type": "focus_lost"}]})
    await asyncio.sleep(0.3)
    assert not any(c[0] == "end_session" for c in env.engine.calls)

    # An objective, recruiter-configured condition can.
    env.integrity.next_outcomes = [
        IntegrityOutcome(
            event_id="e3",
            event_type=IntegrityEventType.SCREEN_SHARE_STOPPED,
            severity=IntegritySeverity.HIGH,
            action=IntegrityAction.TERMINATE_INTERVIEW,
            candidate_message="Screen sharing is required for this interview.",
        )
    ]
    await ws.send_json(
        {"type": "integrity.events", "events": [{**event, "event_type": "screen_share_stopped"}]}
    )
    closing = await ws.wait_type("ai.utterance.start", kind="closing")
    await ws.wait_type("ai.utterance.end", utterance_id=closing["utterance_id"])
    await deliver(ws, closing["utterance_id"])
    ended = await ws.wait_type("session.ended")
    assert ended["reason"] == "policy_termination"


async def test_policy_pause_and_resume(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    env.integrity.next_outcomes = [
        IntegrityOutcome(
            event_id="e1",
            event_type=IntegrityEventType.SCREEN_SHARE_STOPPED,
            severity=IntegritySeverity.HIGH,
            action=IntegrityAction.PAUSE_INTERVIEW,
            candidate_message="Please share your entire screen to continue.",
        )
    ]
    await ws.send_json(
        {
            "type": "integrity.events",
            "events": [{"event_type": "screen_share_stopped", "occurred_at": "2026-10-07T10:00:00Z"}],
        }
    )
    notice = await ws.wait_type("notice", code="interview_paused")
    assert notice["message"] == "Please share your entire screen to continue."
    await ws.wait_type("turn.state", state="paused")
    assert any(c[0] == "pause" for c in env.engine.calls)
    await ws.send_json({"type": "answer.text", "text": "typing while paused"})
    await ws.wait_type("error", code="interview_paused")
    assert env.engine.turns == []
    await ws.send_json({"type": "control.start"})
    resume = await ws.wait_type("ai.utterance.start", kind="resume")
    assert ("resume", False) in env.engine.calls and resume


async def test_session_bus_integrity_messages(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    await asyncio.sleep(0.2)  # let the bus subscription settle
    warning = {
        "type": "integrity.warning",
        "event_id": "ev-1",
        "event_type": "multiple_persons_visible",
        "message": "Please make sure you are alone.",
    }
    await publish_session_event(env.session_id, warning)
    await publish_session_event(env.session_id, warning)  # same event id: delivered once
    forwarded = await ws.wait_type("integrity.warning")
    assert (
        forwarded["event_type"] == "multiple_persons_visible" and forwarded["message"] == warning["message"]
    )

    # Vision-derived terminate from the worker: never applied.
    await publish_session_event(
        env.session_id,
        {
            "type": "integrity.event",
            "event_type": "possible_phone_visible",
            "action": "terminate_interview",
            "source": "vision",
            "delivered_to_caller": False,
        },
    )
    # Objective action already delivered to its caller: skipped.
    await publish_session_event(
        env.session_id,
        {
            "type": "integrity.event",
            "event_type": "screen_share_stopped",
            "action": "terminate_interview",
            "source": "browser",
            "delivered_to_caller": True,
        },
    )
    await asyncio.sleep(0.4)
    assert not any(c[0] in ("end_session", "pause") for c in env.engine.calls)
    assert len(ws.of_type("integrity.warning")) == 1

    # Objective pause not yet delivered (e.g. REST fallback -> integrity.action): applied.
    await publish_session_event(
        env.session_id,
        {
            "type": "integrity.action",
            "event_type": "screen_share_stopped",
            "action": "pause_interview",
            "source": "browser",
            "candidate_message": "Share your screen again.",
        },
    )
    notice = await ws.wait_type("notice", code="interview_paused")
    assert notice["message"] == "Share your screen again."
    assert any(c[0] == "pause" for c in env.engine.calls)


async def test_clock_advanced_event_refreshes_state(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    await asyncio.sleep(0.2)
    env.engine.state.elapsed_before_pause_s += 600
    mark = len(ws.messages)
    await publish_session_event(
        env.session_id, {"type": "clock.advanced", "session_id": str(env.session_id), "seconds": 600}
    )
    state = await ws.wait_type("session.state", after=mark)
    assert state["elapsed_s"] >= 600 and state["remaining_s"] <= 1200


async def test_audio_byte_budget_drops_excess_frames(realtime_env, ws_connect, monkeypatch) -> None:
    from app.core import metrics
    from app.realtime import router as router_module
    from app.realtime.limits import ConnectionLimits
    from app.realtime.registry import get_registry

    from .conftest import silence

    env = await realtime_env()
    monkeypatch.setattr(router_module, "make_limits", ConnectionLimits)  # production limits
    ws, _, _ = await live(env, ws_connect)

    def dropped() -> float:
        return next(
            (
                s.value
                for s in metrics.RATE_LIMITED.collect()[0].samples
                if s.labels.get("scope") == "ws_audio" and s.name.endswith("_total")
            ),
            0.0,
        )

    before = dropped()
    await ws.send_audio(silence(12_000))  # 120 frames (12 s of audio) in a burst: > 192 KB budget
    await asyncio.sleep(0.3)
    runtime = get_registry().get(env.session_id)
    assert dropped() > before
    assert runtime.audio_frames < 120
    assert ws.close_code is None  # a burst is tolerated; only sustained abuse closes the socket


async def test_coding_idle_detection(realtime_env, ws_connect, monkeypatch) -> None:
    from app.realtime import bridges

    monkeypatch.setattr(bridges, "CODING_IDLE_SECONDS", 0.6)
    env = await realtime_env()
    env.engine.coding_payload = coding_payload("cs2")
    env.engine.replies = ["__coding__"]
    ws, _, _ = await live(env, ws_connect)
    await ws.send_json({"type": "answer.text", "text": "Ready."})
    transition = await ws.wait_type("ai.utterance.start", kind="transition")
    await ws.wait_type("ai.utterance.end", utterance_id=transition["utterance_id"])
    await deliver(ws, transition["utterance_id"])
    await ws.wait_type("turn.state", state="coding")
    for _ in range(150):
        if any(a.kind == "idle" for a in env.engine.coding_activities):
            break
        await asyncio.sleep(0.02)
    idle = next(a for a in env.engine.coding_activities if a.kind == "idle")
    assert idle.coding_session_id == "cs2" and idle.idle_seconds >= 0.6


async def test_heartbeat_watchdog_closes_silent_connections(realtime_env, ws_connect, settings) -> None:
    settings.ws_heartbeat_interval_seconds = 0.3
    env = await realtime_env()
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    assert await ws.wait_closed(timeout=5) == 4008


async def test_subtitles_disabled_by_policy(realtime_env, ws_connect) -> None:
    from .conftest import DEFAULT_POLICY

    policy = {**DEFAULT_POLICY, "recording": {**DEFAULT_POLICY["recording"], "show_live_subtitles": False}}
    env = await realtime_env(policy=policy)
    ws, state, intro = await live(env, ws_connect)
    assert state["show_subtitles"] is False
    assert not ws.of_type("ai.text.delta")
    assert [
        m for m in ws.of_type("tts.chunk") if m["utterance_id"] == intro["utterance_id"]
    ]  # audio still flows


async def test_system_check_probe_never_touches_the_session(realtime_env, ws_connect) -> None:
    """Realtime leftover: the system-check page's connectivity probe
    (hello.client.system_check) gets heartbeat acks but is not a connection of the
    interview: no runtime, no reconnect / page reload, never replaces the live tab."""
    from app.realtime.registry import get_registry

    env = await realtime_env()
    probe = await ws_connect(env.session_id, await env.ticket())
    await probe.hello(user_agent="probe", system_check=True, avatar_mode="none", tts_mode="text")
    await probe.send_json({"type": "heartbeat", "t": 123.5})
    ack = await probe.wait_type("heartbeat.ack")
    assert ack["t"] == 123.5 and ack["server_time"] > 0
    await probe.disconnect()
    assert get_registry().get(env.session_id) is None  # before the interview: no runtime at all

    ws, _, _ = await live(env, ws_connect)  # the interview is live now
    runtime = get_registry().get(env.session_id)
    again = await ws_connect(env.session_id, await env.ticket())
    await again.hello(system_check=True)
    await again.send_json({"type": "heartbeat", "t": 1.0})
    await again.wait_type("heartbeat.ack")
    await again.disconnect()
    assert ws.close_code is None  # the live socket was not replaced
    assert runtime.connections == 1 and runtime.ctx.reconnect_count == 0
    assert not [
        e for e in env.integrity.server_events if e[0].value in ("reconnect", "page_reload", "disconnect")
    ]
    await ws.send_json({"type": "answer.text", "text": "Still here."})
    await ws.wait_type("ai.utterance.start", kind="question")


async def test_coding_platform_fault_on_submit_does_not_end_the_exercise(realtime_env, ws_connect) -> None:
    """Audit: a submit the sandbox could not run (runner busy) must not end the
    exercise; the candidate retries and the real submission ends it."""
    from app.domain.enums import RunKind, RunStatus
    from app.schemas.realtime import CodingRunResultMsg
    from app.services.coding.sandbox_client import SANDBOX_BUSY_MESSAGE

    env = await realtime_env()
    env.engine.coding_payload = coding_payload("cs1")
    env.engine.replies = ["__coding__"]
    ws, _, _ = await live(env, ws_connect)
    await ws.send_json({"type": "answer.text", "text": "Ready for coding."})
    transition = await ws.wait_type("ai.utterance.start", kind="transition")
    await ws.wait_type("ai.utterance.end", utterance_id=transition["utterance_id"])
    await deliver(ws, transition["utterance_id"])
    await ws.wait_type("turn.state", state="coding")
    env.coding.next_results = [
        CodingRunResultMsg(
            coding_session_id="cs1",
            run_id="r-fault",
            kind=RunKind.SUBMIT,
            status=RunStatus.ERROR,
            stderr=SANDBOX_BUSY_MESSAGE,
            total=2,
        )
    ]
    code = "def two_sum(nums, target):\n    return [0, 1]\n"
    submit = {"type": "code.submit", "coding_session_id": "cs1", "language": "python", "code": code}
    mark = len(ws.messages)
    await ws.send_json(submit)
    busy = await ws.wait_type("coding.run_result", after=mark)
    assert busy["status"] == "error" and busy["stderr"] == SANDBOX_BUSY_MESSAGE
    await asyncio.sleep(0.3)
    assert not [m for m in ws.messages[mark:] if m["type"] == "coding.end"]
    assert "submitted" not in [a.kind for a in env.engine.coding_activities]
    await ws.send_json(submit)  # the candidate simply tries again
    end = await ws.wait_type("coding.end", after=mark)
    assert end["reason"] == "submitted" and env.coding.runs == [("cs1", "submit"), ("cs1", "submit")]
