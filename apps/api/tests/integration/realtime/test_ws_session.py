"""Full WebSocket sessions through the ASGI app with a fake engine, real
candidate audio (tests/e2e/fixtures/audio) and mocked STT/TTS."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from app.core import metrics
from app.realtime.registry import get_registry
from app.realtime.turns import ChunkRecord, spoken_portion
from app.schemas.realtime import unpack_frame

from .conftest import CLIPS, load_clip, silence

pytestmark = pytest.mark.integration


def types_after(ws, mark: int) -> list[str]:
    return [m.get("type") for m in ws.messages[mark:]]


def is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needles)


async def deliver(ws, utterance_id: str) -> None:
    await ws.send_json({"type": "playback.started", "utterance_id": utterance_id, "played_ms": 0})
    await ws.send_json({"type": "playback.finished", "utterance_id": utterance_id, "played_ms": 99999})


async def wait_call(engine, name: str, timeout: float = 5.0):  # noqa: ASYNC109
    for _ in range(int(timeout / 0.02)):
        for call in engine.calls:
            if call[0] == name:
                return call
        await asyncio.sleep(0.02)
    raise AssertionError(f"engine.{name} not called; calls={engine.calls}")


async def live(env, ws_connect, **hello):
    ws = await ws_connect(env.session_id, await env.ticket())
    assert ws.accepted
    await ws.hello(**hello)
    state = await ws.wait_type("session.state")
    await ws.send_json({"type": "control.start"})
    intro = await ws.wait_type("ai.utterance.start", kind="intro")
    await ws.wait_type("ai.utterance.end", utterance_id=intro["utterance_id"])
    await deliver(ws, intro["utterance_id"])
    await ws.wait_type("turn.state", state="listening")
    await wait_call(env.engine, "delivered")
    return ws, state, intro


async def test_voice_turn_message_order_latency_and_answer_audio(realtime_env, ws_connect, mock_ai) -> None:
    env = await realtime_env()
    mock_ai.stt.default_text = ""
    mock_ai.stt.script_partial("We reduced cloud costs")
    mock_ai.stt.script("We reduced cloud costs by forty percent,", "mostly by rightsizing.")
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello(user_agent="pytest")
    state = await ws.wait_type("session.state")
    assert state["status"] == "ready" and state["turn_state"] == "idle"
    assert state["show_subtitles"] is True and state["allow_typed_answers"] is True
    assert state["integrity"]["record_answer_audio"] is True
    assert state["round_name"] == "Technical Screening"

    await ws.send_json({"type": "control.start"})
    intro = await ws.wait_type("ai.utterance.start", kind="intro")
    assert intro["behavior"]["state"] == "SPEAKING"
    end = await ws.wait_type("ai.utterance.end", utterance_id=intro["utterance_id"])
    assert end["text"] == env.engine.INTRO and end["interrupted"] is False
    # Every tts.chunk is immediately followed by its binary 0x02 frame.
    chunk_msgs = [m for m in ws.of_type("tts.chunk") if m["utterance_id"] == intro["utterance_id"]]
    assert chunk_msgs and " ".join(c["text"] for c in chunk_msgs) == env.engine.INTRO
    timeline = [(kind, payload) for _, kind, payload in ws.timeline]
    for i, (kind, payload) in enumerate(timeline):
        if kind == "tts.chunk":
            nxt_kind, frame = timeline[i + 1]
            assert nxt_kind == "binary"
            ftype, _flags, chunk_id, pcm = unpack_frame(frame)
            assert ftype == 0x02 and chunk_id == payload["chunk_id"]
            assert len(pcm) == payload["sample_rate"] * payload["duration_ms"] // 1000 * 2
    deltas = "".join(
        m["text"] for m in ws.of_type("ai.text.delta") if m["utterance_id"] == intro["utterance_id"]
    )
    assert deltas == env.engine.INTRO
    seqs = [m["seq"] for m in ws.messages]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    await deliver(ws, intro["utterance_id"])
    await ws.wait_type("turn.state", state="listening")
    await wait_call(env.engine, "delivered")
    assert env.engine.delivered[0] == (intro["utterance_id"], env.engine.INTRO)

    # Candidate answers (real speech), first 1.6 s, then a pause for the partial.
    mark = len(ws.messages)
    answer = np.concatenate((silence(300), load_clip("cost_vague"), silence(1900)))
    await ws.send_audio(answer[: 16000 * 2])
    await asyncio.sleep(0.15)
    await ws.send_audio(answer[16000 * 2 :])
    final = await ws.wait_type("transcript.final", after=mark)
    assert final["text"] == "We reduced cloud costs by forty percent, mostly by rightsizing."
    reply = await ws.wait_type("ai.utterance.start", kind="question", after=mark)
    await ws.wait_type("ai.utterance.end", utterance_id=reply["utterance_id"])

    seen = types_after(ws, mark)
    assert is_subsequence(
        [
            "turn.state",  # candidate_speaking
            "transcript.partial",
            "turn.state",  # processing
            "ai.utterance.start",  # acknowledgement
            "tts.chunk",
            "ai.utterance.end",
            "transcript.final",
            "ai.utterance.start",  # question
            "ai.text.delta",
            "tts.chunk",
            "ai.utterance.end",
        ],
        seen,
    ), seen
    turn_states = [m["state"] for m in ws.messages[mark:] if m["type"] == "turn.state"]
    assert turn_states[:3] == ["candidate_speaking", "processing", "ai_speaking"]
    ack = next(m for m in ws.messages[mark:] if m["type"] == "ai.utterance.start")
    assert ack["kind"] == "acknowledgment" and ack["behavior"]["state"] == "ACKNOWLEDGING"
    partial = next(m for m in ws.messages[mark:] if m["type"] == "transcript.partial")
    assert partial["text"].startswith("We reduced")

    turn = env.engine.turns[0]
    assert turn.text == final["text"] and not turn.typed and not turn.interrupted_ai
    assert turn.audio_wav is not None and turn.audio_wav[:4] == b"RIFF"
    assert 0 <= turn.started_ms < turn.ended_ms
    assert turn.stt_confidence == 1.0
    assert env.engine.partials and env.engine.partials[0].startswith(
        "We reduced"
    )  # on_partial_transcript during silence

    runtime = get_registry().get(env.session_id)
    record = runtime.latency[-1]
    assert record["end_reason"] in ("short_complete", "base", "long_trailing", "long_short_text")
    for key in ("stt_wait_ms", "ack_ms", "engine_first_event_ms", "first_audio_ms"):
        assert isinstance(record[key], int) and record[key] >= 0, record
    assert record["ack_ms"] <= record["first_audio_ms"]
    assert record["voiced_ms"] > 2500 and record["segments"] >= 2
    # breakdown fields (latency harness / realtime.turn_latency log line)
    for key in ("tail_audio_ms", "tail_stt_ms", "tail_lead_ms", "first_delta_ms", "first_chunk_ready_ms"):
        assert isinstance(record[key], int) and record[key] >= 0, (key, record)
    assert record["first_chunk_chars"] > 0 and isinstance(record["tts_first_ms"], int)
    assert record["engine_first_event_ms"] <= record["first_delta_ms"] <= record["first_audio_ms"]


async def test_barge_in_backchannel_resumes_interrupted_question(realtime_env, ws_connect, mock_ai) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    env.engine.replies = [
        "Got it. Walk me through how you sized the node groups, how Karpenter chose instance types, "
        "and what you monitored to know the autoscaling was healthy over time."
    ]
    await ws.send_json({"type": "answer.text", "text": "We ran EKS with three node groups."})
    reply = await ws.wait_type("ai.utterance.start", kind="question")
    await ws.wait_type("turn.state", state="ai_speaking")
    first_chunk = await ws.wait_type("tts.chunk", utterance_id=reply["utterance_id"])
    await ws.send_json({"type": "playback.started", "utterance_id": reply["utterance_id"], "played_ms": 0})
    barge_count = next(s.value for s in metrics.BARGE_INS.collect()[0].samples if s.name.endswith("_total"))

    mark = len(ws.messages)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("Mm-hmm.")
    await ws.send_audio(load_clip("backchannel"))
    barge = await ws.wait_type("barge_in", after=mark)
    assert barge["utterance_id"] == reply["utterance_id"]
    await ws.wait_type("ai.utterance.end", after=mark, utterance_id=reply["utterance_id"], interrupted=True)
    await ws.send_json(
        {"type": "playback.interrupted", "utterance_id": reply["utterance_id"], "played_ms": 1200}
    )
    await ws.send_audio(silence(2000))
    resumed = await ws.wait_for(
        lambda m: m.get("type") == "ai.utterance.start" and m["utterance_id"] != reply["utterance_id"],
        after=mark,
    )
    assert resumed["kind"] == "question"
    await wait_call(env.engine, "backchannel_resume")

    interrupted = env.engine.interruptions[-1]
    assert interrupted.utterance_id == reply["utterance_id"] and interrupted.played_ms == 1200
    runtime = get_registry().get(env.session_id)
    chunks = runtime.utterances[reply["utterance_id"]].speech.chunks
    assert interrupted.spoken_text == spoken_portion(chunks, 1200)[0]
    assert interrupted.spoken_text and interrupted.full_text.startswith(interrupted.spoken_text.split(" ")[0])
    assert first_chunk["index"] == 0
    states = [(m["state"], m.get("reason")) for m in ws.messages[mark:] if m["type"] == "turn.state"]
    assert ("candidate_speaking", "barge_in") in states
    avatar = [m["state"] for m in ws.messages[mark:] if m["type"] == "avatar.behavior"]
    assert "INTERRUPTED" in avatar
    assert (
        next(s.value for s in metrics.BARGE_INS.collect()[0].samples if s.name.endswith("_total"))
        == barge_count + 1
    )
    # Not a new answer: handle_candidate_turn was only called for the typed answer.
    assert [t.text for t in env.engine.turns] == ["We ran EKS with three node groups."]
    assert isinstance(ChunkRecord, type)


async def test_barge_in_with_substantive_answer_starts_new_turn(realtime_env, ws_connect, mock_ai) -> None:
    env = await realtime_env()
    ws, _, intro = await live(env, ws_connect)
    env.engine.replies = [
        "Interesting. Tell me more about the failure modes you planned for in each zone and region."
    ]
    await ws.send_json({"type": "answer.text", "text": "Three zones."})
    reply = await ws.wait_type("ai.utterance.start", kind="question")
    await ws.wait_type("tts.chunk", utterance_id=reply["utterance_id"])
    mark = len(ws.messages)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("Actually we used four zones, not three.", "")
    await ws.send_audio(np.concatenate((load_clip("dont_know")[: 16000 * 3], silence(2000))))
    await ws.wait_type("barge_in", after=mark)
    final = await ws.wait_type("transcript.final", after=mark)
    assert final["text"].startswith("Actually we used four zones")
    await wait_call(env.engine, "interruption")
    for _ in range(100):
        if len(env.engine.turns) == 2:
            break
        await asyncio.sleep(0.02)
    second = env.engine.turns[1]
    assert second.interrupted_ai is True and second.text.startswith("Actually")
    # No playback report arrived: the spoken portion was estimated from chunk timing.
    assert env.engine.interruptions[-1].played_ms >= 0
    assert intro["utterance_id"] != reply["utterance_id"]


async def test_barge_in_during_acknowledgement_cancels_the_pending_reply(
    realtime_env, ws_connect, mock_ai
) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    env.engine.first_event_delay = (
        3.0  # the model is slow; the candidate keeps talking over the acknowledgement
    )
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("We reduced costs by forty percent,", "mostly by rightsizing.")
    mark = len(ws.messages)
    await ws.send_audio(np.concatenate((load_clip("cost_vague"), silence(1200))))
    ack = await ws.wait_type("ai.utterance.start", kind="acknowledgment", after=mark)
    await wait_call(env.engine, "turn")
    mock_ai.stt.script("And we also bought savings plans for the baseline.", "")
    mark2 = len(ws.messages)
    await ws.send_audio(np.concatenate((load_clip("generic_medium")[: 16000 * 2], silence(2000))))
    barge = await ws.wait_type("barge_in", after=mark2)
    assert barge["utterance_id"] == ack["utterance_id"]  # the client stops the acknowledgement audio
    await ws.wait_type("transcript.final", after=mark2)
    for _ in range(150):
        if len(env.engine.turns) == 2:
            break
        await asyncio.sleep(0.02)
    first, second = env.engine.turns
    assert first.text.startswith("We reduced") and second.text.startswith("And we also")
    assert second.interrupted_ai is True
    assert env.engine.interruptions == []  # nothing the engine said was cut: no handle_interruption
    # The slow reply to the first answer was cancelled before any of it was spoken.
    assert not [
        m
        for m in ws.messages[mark:mark2]
        if m.get("type") == "ai.utterance.start" and m.get("kind") == "question"
    ]


async def test_reconnect_replays_outbox_and_resumes(realtime_env, ws_connect, mock_ai) -> None:
    env = await realtime_env()
    ws, _, intro = await live(env, ws_connect)
    last_seen = ws.messages[5]["seq"]
    missed = [
        m for m in ws.messages if m["seq"] > last_seen and m["type"] not in ("tts.chunk", "heartbeat.ack")
    ]
    await ws.disconnect()
    await wait_call(env.engine, "pause")
    for _ in range(100):
        if any(e[0].value == "disconnect" for e in env.integrity.server_events):
            break
        await asyncio.sleep(0.02)
    assert ("pause", "disconnect") in env.engine.calls

    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=last_seen)
    state = await ws2.wait_type("session.state")
    replayed = [m for m in ws2.messages if m["seq"] <= missed[-1]["seq"]]
    assert [m["seq"] for m in replayed] == [m["seq"] for m in missed]
    assert all(m["seq"] > last_seen for m in ws2.messages)
    assert state["status"] in ("live", "paused")
    resume = await ws2.wait_type("ai.utterance.start", kind="resume")
    assert ("resume", True) in env.engine.calls
    await ws2.wait_type("ai.utterance.end", utterance_id=resume["utterance_id"])
    kinds = [e[0].value for e in env.integrity.server_events]
    assert "disconnect" in kinds and "reconnect" in kinds
    runtime = get_registry().get(env.session_id)
    assert runtime.connections == 2 and runtime.ctx.reconnect_count == 1
    assert intro["utterance_id"]


async def test_session_stays_resumable_after_grace_period(realtime_env, ws_connect, settings) -> None:
    env = await realtime_env()
    settings.ws_disconnect_grace_seconds = 0.5
    ws, _, _ = await live(env, ws_connect)
    first_runtime = get_registry().get(env.session_id)
    seq = ws.messages[-1]["seq"]
    await ws.disconnect()
    for _ in range(100):
        if get_registry().get(env.session_id) is None:
            break
        await asyncio.sleep(0.05)
    assert get_registry().get(env.session_id) is None  # runtime torn down, lock released
    assert first_runtime.closed and not first_runtime.lock.held
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=seq)
    await ws2.wait_type("session.state")
    resume = await ws2.wait_type("ai.utterance.start", kind="resume")
    assert resume and ("resume", True) in env.engine.calls
    second_runtime = get_registry().get(env.session_id)
    assert (
        second_runtime is not first_runtime and second_runtime.outbox.seq > seq
    )  # sequence continued from Redis


async def test_answer_in_flight_during_disconnect_is_not_lost(realtime_env, ws_connect, mock_ai) -> None:
    from app.ai.mock import MockStt

    class SlowStt(MockStt):
        async def transcribe(self, audio: bytes, **kwargs):
            if not kwargs.get("partial"):
                await asyncio.sleep(0.8)
            return await super().transcribe(audio, **kwargs)

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    slow = SlowStt(default_text="")
    slow.script("We reduced costs by forty percent,", "mostly by rightsizing.")
    mock_ai.stt = slow
    await ws.send_audio(np.concatenate((load_clip("cost_vague"), silence(1200))))
    await ws.wait_type("turn.state", state="processing")
    seq = ws.messages[-1]["seq"]
    await ws.disconnect()  # STT finals still running
    for _ in range(200):
        if env.engine.turns and env.engine.interruptions:
            break
        await asyncio.sleep(0.02)
    turn = env.engine.turns[0]
    assert (
        turn.text == "We reduced costs by forty percent, mostly by rightsizing."
    )  # submitted although offline
    offline = env.engine.interruptions[0]
    assert offline.spoken_text == "" and offline.played_ms == 0  # the reply was never heard
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=seq)
    replayed_final = await ws2.wait_type("transcript.final")
    assert replayed_final["text"] == turn.text  # the client sees what was captured
    await ws2.wait_type("ai.utterance.start", kind="resume")  # the engine re-asks


async def test_page_reload_refused_when_policy_forbids(realtime_env, ws_connect) -> None:
    from .conftest import DEFAULT_POLICY

    policy = {
        **DEFAULT_POLICY,
        "integrity": {**DEFAULT_POLICY["integrity"], "allow_page_reload_resume": False},
    }
    env = await realtime_env(policy=policy)
    ws, _, _ = await live(env, ws_connect)
    await ws.disconnect()
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=0)
    error = await ws2.wait_type("error")
    assert error["code"] == "page_reload_not_allowed" and error["recoverable"] is False
    assert await ws2.wait_closed() == 4011
    assert any(e[0].value == "page_reload" for e in env.integrity.server_events)


async def test_too_many_reconnects_is_refused(realtime_env, ws_connect) -> None:
    from .conftest import DEFAULT_POLICY

    policy = {**DEFAULT_POLICY, "integrity": {**DEFAULT_POLICY["integrity"], "max_reconnects": 0}}
    env = await realtime_env(policy=policy)
    ws, _, _ = await live(env, ws_connect)
    seq = ws.messages[-1]["seq"]
    await ws.disconnect()
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=seq)
    error = await ws2.wait_type("error")
    assert error["code"] == "too_many_reconnects"
    assert await ws2.wait_closed() == 4013


async def test_typed_answers_and_policy(realtime_env, ws_connect) -> None:
    from .conftest import DEFAULT_POLICY

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    await ws.send_json({"type": "answer.text", "text": "  I would use a hash map.  "})
    await ws.wait_type("ai.utterance.start", kind="question")
    turn = env.engine.turns[0]
    assert turn.typed and turn.text == "I would use a hash map." and turn.audio_wav is None

    policy = {**DEFAULT_POLICY, "recording": {**DEFAULT_POLICY["recording"], "allow_typed_answers": False}}
    env2 = await realtime_env(policy=policy)
    ws2, state, _ = await live(env2, ws_connect)
    assert state["allow_typed_answers"] is False
    await ws2.send_json({"type": "answer.text", "text": "typed"})
    error = await ws2.wait_type("error", code="typed_answers_disabled")
    assert error["recoverable"] is True
    assert env2.engine.turns == []


async def test_tts_failure_falls_back_with_notice(realtime_env, ws_connect, mock_ai) -> None:
    from app.ai.base import TtsUnavailable

    env = await realtime_env()
    mock_ai.tts.fail_with = TtsUnavailable("down", endpoint="tts")
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    await ws.send_json({"type": "control.start"})
    intro = await ws.wait_type("ai.utterance.start", kind="intro")
    fallback = await ws.wait_type("tts.fallback", utterance_id=intro["utterance_id"])
    assert fallback["text"] and fallback["reason"]
    notice = await ws.wait_type("notice", code="tts_degraded")
    assert notice["level"] == "warning"
    await ws.wait_type("ai.utterance.end", utterance_id=intro["utterance_id"])
    assert not [m for m in ws.of_type("tts.chunk") if m["utterance_id"] == intro["utterance_id"]]
    texts = " ".join(
        m["text"] for m in ws.of_type("tts.fallback") if m["utterance_id"] == intro["utterance_id"]
    )
    assert texts == env.engine.INTRO


async def test_stt_failure_keeps_turn_and_typed_answer_recovers(
    realtime_env, ws_connect, mock_ai, monkeypatch
) -> None:
    from app.ai.base import AIUnavailable
    from app.realtime import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "UNRESOLVED_RETRY_DELAYS", (30.0,))
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    mock_ai.stt.default_text = ""
    failures = [AIUnavailable("whisper down")] * 12
    mock_ai.stt.script(*failures)
    await ws.send_audio(np.concatenate((load_clip("cost_vague"), silence(2000))))
    await ws.wait_type("notice", code="stt_degraded")
    unavailable = await ws.wait_type("notice", code="stt_unavailable")
    assert unavailable["level"] == "error"
    await ws.wait_type("turn.state", state="listening", after=ws.messages.index(unavailable) - 1)
    assert env.engine.turns == []  # never sent an empty/garbled answer
    await ws.send_json({"type": "answer.text", "text": "We cut costs by forty percent by rightsizing."})
    await ws.wait_type("ai.utterance.start", kind="question")
    turn = env.engine.turns[0]
    assert (
        turn.typed and turn.audio_wav is not None and turn.audio_wav[:4] == b"RIFF"
    )  # audio kept from the failed capture


async def test_manual_end_turn_and_repeat(realtime_env, ws_connect, mock_ai) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("I think the answer is", "")
    await ws.send_audio(load_clip("generic_medium")[: 16000 * 2])  # still speaking
    await ws.wait_type("turn.state", state="candidate_speaking")
    await ws.send_json({"type": "control.end_turn"})
    await ws.wait_type("transcript.final")
    for _ in range(100):
        if env.engine.turns:
            break
        await asyncio.sleep(0.02)
    assert env.engine.turns[0].text == "I think the answer is"
    runtime = get_registry().get(env.session_id)
    assert runtime.latency[-1]["end_reason"] == "manual"
    reply = await ws.wait_type("ai.utterance.start", kind="question")
    await ws.wait_type("ai.utterance.end", utterance_id=reply["utterance_id"])
    await deliver(ws, reply["utterance_id"])
    await ws.wait_type("turn.state", state="listening", after=ws.messages.index(reply))
    mark = len(ws.messages)
    await ws.send_json({"type": "control.repeat"})
    repeat = await ws.wait_type("ai.utterance.start", after=mark)
    assert ("repeat", None) in env.engine.calls and repeat["kind"] == "question"


async def test_silence_prompt_after_question(realtime_env, ws_connect, monkeypatch) -> None:
    from app.realtime import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "SILENCE_PROMPT_S", 0.4)
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    prompt = await ws.wait_type("ai.utterance.start", kind="comment", timeout=5)
    assert prompt
    assert env.engine.timers[0].kind == "silence_timeout" and env.engine.timers[0].detail == {"count": 1}


async def test_end_interview_closing_and_report_ready(realtime_env, ws_connect) -> None:
    from app.core.queue import publish_session_event

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    await ws.send_json({"type": "control.end_interview", "reason": "done"})
    closing = await ws.wait_type("ai.utterance.start", kind="closing")
    await ws.wait_type("ai.utterance.end", utterance_id=closing["utterance_id"])
    await deliver(ws, closing["utterance_id"])
    ended = await ws.wait_type("session.ended")
    assert ended["reason"] == "candidate_ended" and ended["report_status"] == "pending"
    assert env.engine.ended.value == "candidate_ended"
    await ws.wait_type("turn.state", state="ended")
    await publish_session_event(env.session_id, {"type": "report.ready", "candidate_report_available": True})
    ready = await ws.wait_for(
        lambda m: m.get("type") == "session.ended" and m.get("report_status") == "ready"
    )
    assert ready["candidate_report_available"] is True
    assert await ws.wait_closed() == 1000
    for _ in range(50):
        if get_registry().get(env.session_id) is None:
            break
        await asyncio.sleep(0.02)
    assert get_registry().get(env.session_id) is None


async def test_engine_initiated_close_after_turn(realtime_env, ws_connect) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    env.engine.replies = ["__close__"]
    await ws.send_json({"type": "answer.text", "text": "No more questions from me."})
    closing = await ws.wait_type("ai.utterance.start", kind="closing")
    await ws.wait_type("ai.utterance.end", utterance_id=closing["utterance_id"])
    await deliver(ws, closing["utterance_id"])
    ended = await ws.wait_type("session.ended")
    assert ended["reason"] == "sufficient_evidence"


async def test_clips_manifest_matches_fixture_audio() -> None:
    for name, meta in CLIPS.items():
        assert abs(load_clip(name).size / 16000 - meta["seconds"]) < 0.25, name


async def test_another_instance_takes_over_a_disconnected_runtime(realtime_env, ws_connect, settings) -> None:
    import uuid as uuid_module

    from app.realtime.context import load_session_context
    from app.realtime.registry import RuntimeRegistry, SessionActiveElsewhere

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    local = get_registry().get(env.session_id)
    other_settings = settings.model_copy(update={"instance_id": "other-instance"})
    other = RuntimeRegistry()
    ctx = await load_session_context(uuid_module.UUID(str(env.session_id)))
    # Connected here: the handover is refused.
    with pytest.raises(SessionActiveElsewhere):
        await other.get_or_create(ctx, settings=other_settings)
    assert not local.closed
    # Disconnected (grace period): the other instance takes over.
    await ws.disconnect()
    await asyncio.sleep(0.1)
    runtime = await other.get_or_create(ctx, settings=other_settings)
    assert runtime.lock.value.startswith("other-instance:")
    assert local.closed and get_registry().get(env.session_id) is None
    await other.shutdown_all()


async def test_reload_before_start_is_not_a_reconnect(realtime_env, ws_connect) -> None:
    from .conftest import DEFAULT_POLICY

    policy = {
        **DEFAULT_POLICY,
        "integrity": {**DEFAULT_POLICY["integrity"], "allow_page_reload_resume": False, "max_reconnects": 0},
    }
    env = await realtime_env(policy=policy)
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    await ws.disconnect()
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=0)  # fresh page, interview not started yet
    state = await ws2.wait_type("session.state")
    assert state["status"] == "ready" and ws2.close_code is None
    assert env.integrity.server_events == []
    await ws2.send_json({"type": "control.start"})
    await ws2.wait_type("ai.utterance.start", kind="intro")


async def test_candidate_speaking_over_the_closing_never_hangs_the_session(
    realtime_env, ws_connect, mock_ai
) -> None:
    """Regression (live E2E 2026-10-07): the candidate started talking as the goodbye
    began; barge-in cancelled the engine stream before SessionClosing arrived and the
    session never ended. Closing speech is now not interruptible."""
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    env.engine.replies = ["__close__"]
    await ws.send_json({"type": "answer.text", "text": "That's all from me."})
    closing = await ws.wait_type("ai.utterance.start", kind="closing")
    await ws.wait_type("tts.chunk", utterance_id=closing["utterance_id"])
    await ws.send_json({"type": "playback.started", "utterance_id": closing["utterance_id"], "played_ms": 0})
    mark = len(ws.messages)
    mock_ai.stt.script("The time complexity is linear.")
    await ws.send_audio(load_clip("complexity"))
    await deliver(ws, closing["utterance_id"])
    ended = await ws.wait_type("session.ended", after=mark)
    assert ended["reason"] == "sufficient_evidence"
    assert not any(m["type"] == "barge_in" for m in ws.messages[mark:])


async def test_session_closing_bus_event_finalizes_when_stream_was_lost(realtime_env, ws_connect) -> None:
    """Safety net: the engine committed the end (session.closing on the bus) but the
    runtime never saw SessionClosing — it must still end the session."""
    from app.core.queue import publish_session_event

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    await publish_session_event(env.session_id, {"type": "session.closing", "reason": "time_limit"})
    ended = await ws.wait_type("session.ended", timeout=15)
    assert ended["reason"] in ("time_limit", "sufficient_evidence")


async def test_unfinished_transcription_keeps_the_turn_instead_of_dropping_audio(
    realtime_env, ws_connect, mock_ai, monkeypatch
) -> None:
    """Audit regression: when the wait for segment finals timed out while a segment
    was still being transcribed, the turn went on without that audio. Now the turn
    stays unresolved (candidate notice, background retries) until every segment is
    transcribed, then the complete answer reaches the engine."""
    from app.ai.mock import MockStt
    from app.realtime import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "STT_FINAL_TIMEOUT_S", 0.5)
    monkeypatch.setattr(runtime_module, "UNRESOLVED_RETRY_DELAYS", (0.2, 0.3, 0.5, 1.0, 2.0))
    release = asyncio.Event()

    class SlowSecondFinal(MockStt):
        finals = 0

        async def transcribe(self, audio: bytes, **kwargs):
            if not kwargs.get("partial"):
                self.finals += 1
                if self.finals == 2:
                    await release.wait()
            return await super().transcribe(audio, **kwargs)

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    stt = SlowSecondFinal(default_text="")
    stt.script("We reduced cloud costs by forty percent,", "mostly by rightsizing.")
    mock_ai.stt = stt
    mark = len(ws.messages)
    await ws.send_audio(np.concatenate((silence(300), load_clip("cost_vague"), silence(1900))))
    notice = await ws.wait_type("notice", code="stt_unavailable", after=mark, timeout=10)
    assert notice["level"] == "error"
    await asyncio.sleep(0.3)
    assert not env.engine.turns  # nothing answered with half the audio
    assert not [m for m in ws.messages[mark:] if m["type"] == "transcript.final"]
    release.set()
    final = await ws.wait_type("transcript.final", after=mark, timeout=10)
    assert final["text"] == "We reduced cloud costs by forty percent, mostly by rightsizing."
    await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=10)
    assert env.engine.turns[0].text == final["text"]


async def test_disconnect_while_begin_is_still_preparing_is_not_a_reconnect(realtime_env, ws_connect) -> None:
    """Realtime leftover: "Begin" pressed, the engine still deciding (preparing); the
    socket drops and the candidate reloads. Nothing was said yet, so no disconnect,
    reconnect or page-reload is recorded, and Begin works again."""
    from .conftest import DEFAULT_POLICY

    policy = {
        **DEFAULT_POLICY,
        "integrity": {**DEFAULT_POLICY["integrity"], "allow_page_reload_resume": False, "max_reconnects": 0},
    }
    env = await realtime_env(policy=policy)
    env.engine.start_delay = 1.0
    ws = await ws_connect(env.session_id, await env.ticket())
    await ws.hello()
    await ws.wait_type("session.state")
    await ws.send_json({"type": "control.start"})
    await wait_call(env.engine, "start")
    await ws.disconnect()
    runtime = get_registry().get(env.session_id)
    for _ in range(50):
        if not runtime.started:
            break
        await asyncio.sleep(0.02)
    assert not runtime.started
    ws2 = await ws_connect(env.session_id, await env.ticket())
    await ws2.hello(last_seq=0)  # a reloaded page
    state = await ws2.wait_type("session.state")
    assert ws2.close_code is None and state["status"] == "ready"
    assert env.integrity.server_events == []  # no disconnect / reconnect / page_reload
    assert runtime.ctx.reconnect_count == 0
    env.engine.start_delay = 0.0
    await ws2.send_json({"type": "control.start"})
    await ws2.wait_type("ai.utterance.start", kind="intro", timeout=10)


async def _speak_with_pause(ws, first, pause_ms: int, second) -> None:
    """Part one instantly, the pause in real time (the runtime decides on the audio
    clock while STT runs in wall time), then part two and a final silence."""
    await ws.send_audio(np.concatenate((silence(300), first)))
    await ws.send_audio(silence(pause_ms), realtime_factor=1.0)
    await ws.send_audio(np.concatenate((second, silence(2200))))


async def test_mid_thought_pause_does_not_end_the_answer(realtime_env, ws_connect, mock_ai) -> None:
    """Speech understanding (user report): a 1.2 s pause after "... spot instances
    and" ended the turn because the end-of-turn decision ran before the last
    segment was transcribed. Now the turn only commits when the silence also
    suits the final text; the continuation merges back into the same answer."""
    from app.ai.mock import MockStt

    class SlowFinals(MockStt):
        async def transcribe(self, audio: bytes, **kwargs):
            if not kwargs.get("partial"):
                # The transcript of the pause's segment arrives after the provisional end
                # (850 ms of silence on stale text) but before the candidate resumes.
                await asyncio.sleep(0.6)
            return await super().transcribe(audio, **kwargs)

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    stt = SlowFinals(default_text="")
    stt.script("We moved the batch jobs to spot instances and", "then rightsized the databases.")
    mock_ai.stt = stt
    clip = load_clip("cost_vague")
    mark = len(ws.messages)
    await _speak_with_pause(ws, clip[: 16000 * 2], 1500, clip[16000 * 2 :])
    final = await ws.wait_for(
        lambda m: m.get("type") == "transcript.final" and "rightsized" in m.get("text", ""),
        after=mark,
        timeout=15,
    )
    await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=15)
    assert [t.text for t in env.engine.turns] == [
        "We moved the batch jobs to spot instances and then rightsized the databases."
    ]
    assert final["text"] == env.engine.turns[0].text
    states = [m.get("reason") for m in ws.messages[mark:] if m.get("type") == "turn.state"]
    assert "speech_resumed" in states  # merged back instead of answering half an answer


async def test_done_answering_is_the_fast_path(realtime_env, ws_connect, mock_ai) -> None:
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("I would start with the ingress logs and")  # unfinished on purpose
    mark = len(ws.messages)
    await ws.send_audio(np.concatenate((silence(300), load_clip("cost_vague")[: 16000 * 2], silence(200))))
    await ws.wait_type("turn.state", state="candidate_speaking", after=mark)
    await ws.send_json({"type": "control.end_turn"})
    await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=10)
    assert env.engine.turns[0].text == "I would start with the ingress logs and"  # no commit wait
    runtime = get_registry().get(env.session_id)
    assert "commit_wait_ms" not in runtime.latency[-1]


async def test_misrecognized_technical_terms_are_corrected(realtime_env, ws_connect, mock_ai) -> None:
    """The deployed Whisper ignores the vocabulary prompt; the glossary pass fixes
    "Carpenter"/"Ometheus" against the session vocabulary before the engine sees it."""
    from app.db.models import JobRequirement
    from app.db.session import session_scope

    env = await realtime_env()
    async with session_scope() as db:
        db.add_all(
            [
                JobRequirement(job_id=env.job_id, kind="required_skill", name="Karpenter"),
                JobRequirement(job_id=env.job_id, kind="required_skill", name="Prometheus"),
            ]
        )
    ws, _, _ = await live(env, ws_connect)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("We used Carpenter for node autoscaling,", "and Ometheus with Grafana for monitoring.")
    mark = len(ws.messages)
    await ws.send_audio(np.concatenate((silence(300), load_clip("cost_vague"), silence(2200))))
    final = await ws.wait_type("transcript.final", after=mark, timeout=15)
    await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=15)
    expected = "We used Karpenter for node autoscaling, and Prometheus with Grafana for monitoring."
    assert final["text"] == expected and env.engine.turns[0].text == expected
    assert get_registry().get(env.session_id).latency[-1]["glossary"] == 2


async def test_known_misrecognitions_reach_the_engine_corrected(realtime_env, ws_connect, mock_ai) -> None:
    """Live intake 2026-10-10: "we use the CACD pipeline" was echoed back by the interviewer. The glossary's exact
    aliases fix CI/CD, LLM and TechSara before the engine (and the transcript) see the answer."""
    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("At Tech Sara we deploy the L L M service,", "and we use the CACD pipeline for it.")
    mark = len(ws.messages)
    await ws.send_audio(np.concatenate((silence(300), load_clip("cost_vague"), silence(2200))))
    final = await ws.wait_type("transcript.final", after=mark, timeout=15)
    await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=15)
    expected = "At TechSara we deploy the LLM service, and we use the CI/CD pipeline for it."
    assert final["text"] == expected and env.engine.turns[0].text == expected
    assert get_registry().get(env.session_id).latency[-1]["glossary"] == 3


async def test_unclear_answer_is_asked_again_not_answered(realtime_env, ws_connect, mock_ai) -> None:
    """Hesitation sounds only (or nothing usable after real speech) never reach the
    engine: the interviewer asks the candidate to repeat; a clear answer then goes on."""
    from app.realtime.acknowledgements import REPEAT_PROMPT

    env = await realtime_env()
    ws, _, _ = await live(env, ws_connect)
    mock_ai.stt.default_text = ""
    mock_ai.stt.script("Um.", "Uh,")
    mark = len(ws.messages)
    await ws.send_audio(np.concatenate((silence(300), load_clip("cost_vague"), silence(2500))))
    notice = await ws.wait_type("notice", code="not_understood", after=mark, timeout=15)
    assert notice["message"] == REPEAT_PROMPT
    prompt = await ws.wait_type("ai.utterance.start", kind="comment", after=mark)
    await ws.wait_type("ai.utterance.end", utterance_id=prompt["utterance_id"], text=REPEAT_PROMPT)
    await ws.wait_type("turn.state", state="listening", after=mark)
    assert env.engine.turns == []  # nothing was sent to the engine
    mark = len(ws.messages)
    mock_ai.stt.script("We reduced costs by forty percent,", "mostly by rightsizing.")
    await ws.send_audio(np.concatenate((load_clip("cost_vague"), silence(2500))))
    await ws.wait_type("ai.utterance.start", kind="question", after=mark, timeout=15)
    assert env.engine.turns[0].text == "We reduced costs by forty percent, mostly by rightsizing."


async def test_second_pass_revises_the_stored_answer_only_before_evaluation(realtime_env) -> None:
    from app.db.models import Answer, Transcript
    from app.db.session import session_scope
    from app.realtime.revision import revise_answer_transcript

    env = await realtime_env()
    live_text = "I coordinated the response. rotated the certificate"
    async with session_scope() as db:
        answer = Answer(
            session_id=env.session_id, sequence=1, transcript_text=live_text, started_ms=1000, ended_ms=9000
        )
        db.add(answer)
        await db.flush()
        db.add(
            Transcript(
                session_id=env.session_id, seq=1, speaker="candidate", text=live_text, answer_id=answer.id
            )
        )
        evaluated = Answer(
            session_id=env.session_id,
            sequence=2,
            transcript_text="old",
            started_ms=20000,
            ended_ms=25000,
            status="evaluated",
        )
        db.add(evaluated)
        answer_id = answer.id
    revised = "I coordinated the response, rotated the certificate and added alerts."
    sid = str(env.session_id)
    assert await revise_answer_transcript(
        sid, started_ms=1000, ended_ms=9000, live_text=live_text, revised_text=revised
    )
    async with session_scope() as db:
        assert (await db.get(Answer, answer_id)).transcript_text == revised
        rows = (await db.execute(__import__("sqlalchemy").select(Transcript.text))).scalars().all()
        assert rows == [revised]
    # changed since (or already evaluated): never overwritten
    assert not await revise_answer_transcript(
        sid, started_ms=1000, ended_ms=9000, live_text=live_text, revised_text="something else entirely"
    )
    assert not await revise_answer_transcript(
        sid, started_ms=20000, ended_ms=25000, live_text="old", revised_text="new words here"
    )
