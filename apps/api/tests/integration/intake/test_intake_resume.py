"""Intake resume rules (INTAKE_INTERVIEW.md 6.9, 7.8): disconnect mid item / mid activity, restarts with new
attempts (max 2), read_relay reveal accounting (remaining time only, never past 30 s), topic prepare re-show,
answers that arrive while paused, state reload in a fresh engine (Redis and Postgres)."""

from __future__ import annotations

import pytest

from app.services.intake import prompts as P
from app.services.interview.contracts import ActivityEnded, ActivityStarted

from . import live_helpers
from .live_helpers import (
    CAPTURE_TEXTS,
    Driver,
    activity_rows,
    build_intake_world,
    collect,
    create_session,
    first,
    item_keys,
    metas,
    stage_rows,
    texts,
)

intake_env = live_helpers.intake_env  # the shared fixture

pytestmark = pytest.mark.integration


async def setup(env, stage: str | None = None, **world_kwargs):
    world = await build_intake_world(**world_kwargs)
    sid = await create_session(env, world)
    engine = env["engine"]
    await engine.prepare_session(sid)
    events = await collect(engine.start(sid))
    if stage is not None:
        await engine.intake_jump(sid, stage)
        events = await collect(engine.resume(sid, reconnect=False))
    return sid, Driver(env, sid), events


async def disconnect(env, sid: str) -> None:
    await env["engine"].pause(sid, reason="disconnect")


async def reconnect(env, sid: str) -> list:
    return await collect(env["engine"].resume(sid, reconnect=True))


async def test_disconnect_mid_item_re_asks_the_pending_question(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env)
    events = await driver.say("I'm Priya, a data analyst at a retail company.", seconds=60)
    question = metas(events)[-1]
    await disconnect(env, sid)
    events = await reconnect(env, sid)
    assert texts(events) == [f"{P.RESUME_LEAD} {P.ITEM_TEMPLATES['greeting.project']}"]
    assert metas(events)[0].question_id == question.question_id  # the same question, re-asked
    state = await env["engine"].load_state(sid)
    assert state.reconnects == 1 and state.intake.current_item == "greeting.project"


async def test_answer_that_arrives_while_paused_is_answered_on_resume(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env)
    await disconnect(env, sid)
    events = await driver.say("I'm Priya, a data analyst at a retail company.", seconds=40)
    assert events == []  # kept, no reply while paused
    state = await env["engine"].load_state(sid)
    assert state.intake.pending_candidate_turn
    events = await reconnect(env, sid)
    assert item_keys(events)[-1] == "greeting.project"


async def test_reading_restarts_after_disconnect_then_ends_interrupted(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    started = first(events, ActivityStarted)
    activity_id = started.activity["activity_id"]
    for attempt in (1, 2, 3):
        await driver.phase_event("phase_started", activity_id, "read", duration_ms=180_000)
        env["clock"].advance(20)
        await disconnect(env, sid)
        events = await driver.capture(
            activity_id, "reading_aloud", "read", "Gradient descent is", end_reason="disconnect"
        )
        assert events == []  # the interrupted attempt is kept, nothing is said while paused
        events = await reconnect(env, sid)
        if attempt < 3:
            restarted = first(events, ActivityStarted)
            assert restarted is not None and restarted.activity["resumed"] is True
            assert restarted.activity["attempt"] == attempt + 1
            assert restarted.activity["phases"][0] == {
                "phase": "read",
                "duration_ms": 180_000,
                "capture": True,
            }
            assert texts(events)[-1] == P.RESTART_PROMPTS[("reading_aloud", "read")]
            assert restarted.start_after_utterance_id == metas(events)[-1].utterance_id
    # Third disconnect: restarts exhausted -> interrupted, the plan moves on to Drill B.
    ended = first(events, ActivityEnded)
    assert ended is not None and ended.reason == "interrupted"
    assert texts(events)[-1].startswith(P.MOVE_ON)
    nxt = first(events, ActivityStarted)
    assert nxt is not None and nxt.activity["kind"] == "read_relay"
    acts = await activity_rows(sid)
    row = acts["reading_aloud"]
    assert (
        row.status == "interrupted" and len(row.attempts_log) == 3 and row.transcript_status == "unavailable"
    )
    assert {e["end_reason"] for e in row.attempts_log} == {"disconnect"}
    state = await env["engine"].load_state(sid)
    item = state.intake.find_item("reading.drill_a")[1]
    assert (item.status, item.skip_reason) == ("interrupted", "activity_interrupted")


async def test_successful_restart_scores_only_the_new_attempt(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    activity_id = first(events, ActivityStarted).activity["activity_id"]
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=180_000)
    env["clock"].advance(15)
    await disconnect(env, sid)
    await driver.capture(activity_id, "reading_aloud", "read", "Gradient descent", end_reason="disconnect")
    events = await reconnect(env, sid)
    restarted = first(events, ActivityStarted)
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=180_000)
    events = await driver.capture(activity_id, "reading_aloud", "read", CAPTURE_TEXTS["reading_aloud"])
    assert first(events, ActivityEnded).reason == "finished_early"
    row = (await activity_rows(sid))["reading_aloud"]
    assert restarted.activity["attempt"] == 2 and row.attempt == 2 and row.status == "completed"
    assert row.capture["attempt"] == 2 and len(row.attempts_log) == 1
    assert row.transcript_live == CAPTURE_TEXTS["reading_aloud"] and row.transcript_status == "final"


async def test_read_relay_reveal_resumes_with_the_remaining_time_only(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    # Finish Drill A quickly.
    reading = first(events, ActivityStarted)
    events = await driver.run_activity(reading)
    relay = first(events, ActivityStarted)
    assert relay is not None and relay.activity["kind"] == "read_relay"
    activity_id = relay.activity["activity_id"]
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=30_000)
    env["clock"].advance(20)
    await disconnect(env, sid)
    events = await reconnect(env, sid)
    again = first(events, ActivityStarted)
    assert again.activity["start_phase"] == "read" and again.activity["resumed"] is True
    remaining = again.activity["start_duration_ms"]
    assert 9_000 <= remaining <= 10_000  # 30 s - 20 s shown - safety margin
    assert texts(events)[-1] == P.RESHOW_PROMPTS[("read_relay", "read")]
    # Shown again for 8 s, disconnect: only ~2 s were left -> straight to the relay, passage not re-shown.
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=remaining)
    env["clock"].advance(8)
    await disconnect(env, sid)
    events = await reconnect(env, sid)
    third = first(events, ActivityStarted)
    assert third.activity["start_phase"] == "relay"
    assert [p["phase"] for p in third.activity["phases"]] == ["relay"]
    assert texts(events)[-1] == P.SKIP_TO_CAPTURE_PROMPTS[("read_relay", "relay")]
    row = (await activity_rows(sid))["read_relay"]
    assert row.reveal["shown_ms"] == 28_000 and row.reveal["interrupted"] is True
    assert len(row.reveal["windows"]) == 2 and all(
        w["to_elapsed_s"] is not None for w in row.reveal["windows"]
    )


async def test_relay_disconnect_restarts_without_the_passage(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    events = await driver.run_activity(first(events, ActivityStarted))
    relay = first(events, ActivityStarted)
    activity_id = relay.activity["activity_id"]
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=30_000)
    env["clock"].advance(30)
    events = await driver.phase_event("phase_deadline", activity_id, "read")
    assert texts(events)[-1] == P.PHASE_PROMPTS[("read_relay", "relay")]
    await driver.phase_event("phase_started", activity_id, "relay", duration_ms=150_000)
    env["clock"].advance(30)
    await disconnect(env, sid)
    await driver.capture(
        activity_id, "read_relay", "relay", "It was about overfitting", end_reason="disconnect"
    )
    events = await reconnect(env, sid)
    restarted = first(events, ActivityStarted)
    assert restarted.activity["start_phase"] == "relay" and restarted.activity["attempt"] == 2
    assert [p["phase"] for p in restarted.activity["phases"]] == ["relay"]
    assert restarted.activity["phases"][0]["duration_ms"] == 150_000
    assert texts(events)[-1] == P.RESTART_PROMPTS[("read_relay", "relay")]


async def test_topic_prepare_is_reshown_with_the_remaining_time(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "topic")
    card = first(events, ActivityStarted)
    assert card.activity["kind"] == "topic_card" and card.activity["content"]["bullets"]
    activity_id = card.activity["activity_id"]
    await driver.phase_event("phase_started", activity_id, "prepare", duration_ms=60_000)
    env["clock"].advance(25)
    await disconnect(env, sid)
    events = await reconnect(env, sid)
    again = first(events, ActivityStarted)
    assert (
        again.activity["start_phase"] == "prepare" and 34_000 <= again.activity["start_duration_ms"] <= 35_000
    )
    assert texts(events)[-1] == P.RESHOW_PROMPTS[("topic_card", "prepare")]
    # Early finish moves to the speak phase with the card still visible.
    await driver.phase_event(
        "phase_started", activity_id, "prepare", duration_ms=again.activity["start_duration_ms"]
    )
    events = await driver.phase_event("finish_requested", activity_id, "prepare")
    assert texts(events)[-1] == P.PHASE_PROMPTS_EARLY[("topic_card", "speak")]
    from app.services.interview.contracts import ActivityPhaseChanged

    changed = first(events, ActivityPhaseChanged)
    assert (
        changed.phase == "speak"
        and changed.capture
        and changed.content["bullets"]
        and not changed.hide_content
    )


async def test_armed_activity_is_re_explained_after_a_question(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    first_started = first(events, ActivityStarted)
    events = await driver.say("Sorry, how long do I have?", seconds=3)
    again = first(events, ActivityStarted)
    assert again is not None and again.activity["resumed"] is True
    assert again.activity["activity_id"] == first_started.activity["activity_id"]
    assert texts(events)[-1].startswith(P.REEXPLAIN_LEAD)
    assert again.start_after_utterance_id == metas(events)[-1].utterance_id


async def test_state_reloads_in_a_fresh_engine_from_redis_and_postgres(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env)
    await driver.say("I'm Priya, a data analyst at a retail company.", seconds=60)
    # A new process (same Redis): resume continues where we were.
    fresh = env["make_engine"]()
    state = await fresh.load_state(sid)
    assert state.intake.current_item == "greeting.project"
    events = await collect(fresh.resume(sid, reconnect=True))
    assert texts(events)[-1].endswith(P.ITEM_TEMPLATES["greeting.project"])
    # Redis lost the hot copy: the durable snapshot in Postgres is enough.
    await fresh.store.redis.delete(
        fresh.store.key(sid) if hasattr(fresh.store, "key") else f"session:{sid}:state"
    )
    rebuilt = env["make_engine"]()
    state = await rebuilt.load_state(sid)
    assert state.intake is not None and state.intake.current_item == "greeting.project"
    rows = await stage_rows(sid)
    assert rows["greeting"].status == "in_progress"


async def test_capture_committed_while_paused_ends_the_activity_on_resume(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    activity_id = first(events, ActivityStarted).activity["activity_id"]
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=180_000)
    await disconnect(env, sid)
    # The candidate pressed "Finish reading" just before the connection dropped: the turn finishes offline.
    events = await driver.capture(activity_id, "reading_aloud", "read", CAPTURE_TEXTS["reading_aloud"])
    assert events == []
    row = (await activity_rows(sid))["reading_aloud"]
    assert row.status == "completed"
    events = await reconnect(env, sid)
    ended = first(events, ActivityEnded)
    assert ended is not None and ended.activity_id == activity_id and ended.reason == "finished_early"
    nxt = first(events, ActivityStarted)
    assert nxt is not None and nxt.activity["kind"] == "read_relay"
    assert events.index(ended) < events.index(nxt)


async def test_a_turn_during_a_lost_running_phase_recovers_without_reshowing_the_passage(intake_env) -> None:
    env = intake_env
    sid, driver, events = await setup(env, "reading")
    events = await driver.run_activity(first(events, ActivityStarted))
    relay = first(events, ActivityStarted)
    activity_id = relay.activity["activity_id"]
    await driver.phase_event("phase_started", activity_id, "read", duration_ms=30_000)
    env["clock"].advance(12)
    events = await driver.say(
        "Hello? Is it working?", seconds=2
    )  # only possible when the runtime lost the phase
    again = first(events, ActivityStarted)
    assert again is not None and again.activity["start_phase"] == "relay"
    assert [p["phase"] for p in again.activity["phases"]] == ["relay"]
    assert texts(events)[-1].startswith(P.REEXPLAIN_LEAD)
    row = (await activity_rows(sid))["read_relay"]
    assert 12_000 <= row.reveal["shown_ms"] <= 14_500
