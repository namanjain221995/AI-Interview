"""IntegrityService with Postgres + Redis: persistence, aggregation, derived
signals, warnings, rate limits, policy actions, bus and metrics."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.metrics import INTEGRITY_EVENTS
from app.core.queue import subscribe_session_events
from app.db.models import IntegrityEvent
from app.db.session import session_scope
from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity
from app.schemas.realtime import ClientIntegrityEvent
from app.services.integrity import IntegrityService, count_unreviewed_flags, integrity_summary
from app.services.integrity import service as service_module

pytestmark = pytest.mark.integration

T = IntegrityEventType
A = IntegrityAction


def ev(
    event_type: T, *, seconds_ago: float = 0, duration_ms: int | None = None, **details
) -> ClientIntegrityEvent:
    at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return ClientIntegrityEvent(
        event_type=event_type, occurred_at=at.isoformat(), duration_ms=duration_ms, details=details
    )


async def next_event(sub, wait: float = 2.0) -> dict | None:
    """``get`` returns None for subscribe confirmations: poll until the deadline."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while loop.time() < deadline:
        event = await sub.get(timeout=0.1)
        if event is not None:
            return event
    return None


async def rows(session_id: uuid.UUID) -> list[IntegrityEvent]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(
                    select(IntegrityEvent)
                    .where(IntegrityEvent.session_id == session_id)
                    .order_by(IntegrityEvent.occurred_at)
                )
            ).all()
        )


async def test_service_satisfies_protocol(service) -> None:
    assert isinstance(service, IntegrityService)


async def test_focus_lost_warns_then_aggregates(service, make_session) -> None:
    refs = await make_session()
    first = await service.record_client_events(refs.sid, [ev(T.FOCUS_LOST, duration_ms=5000)])
    assert len(first) == 1
    assert first[0].action is A.WARN_CANDIDATE
    assert first[0].candidate_message == "Please keep the interview window in focus."
    again = await service.record_client_events(refs.sid, [ev(T.FOCUS_LOST, duration_ms=4000)])
    assert again[0].event_id == first[0].event_id  # aggregated, not a new row
    assert again[0].candidate_message is None  # reminder cooldown
    [row] = await rows(refs.session_id)
    assert row.count == 2 and row.duration_ms == 9000
    assert row.type == "focus_lost" and row.source == "browser" and row.action == "warn_candidate"
    assert row.elapsed_seconds is not None and row.elapsed_seconds > 200
    assert row.details["label"] == "Interview window lost focus" and row.details["candidate_warnings"] == 1


async def test_short_blur_is_logged_not_warned(service, make_session) -> None:
    refs = await make_session()
    [outcome] = await service.record_client_events(refs.sid, [ev(T.FOCUS_LOST, duration_ms=800)])
    assert (
        outcome.action is A.LOG_ONLY
        and outcome.severity is IntegritySeverity.INFO
        and outcome.candidate_message is None
    )


async def test_escalation_creates_a_flag_row(service, make_session) -> None:
    refs = await make_session()
    outcomes = []
    for i in range(4):
        outcomes += await service.record_client_events(
            refs.sid, [ev(T.FOCUS_LOST, seconds_ago=40 - i * 10, duration_ms=4000)]
        )
    assert [o.action for o in outcomes] == [A.WARN_CANDIDATE] * 3 + [A.FLAG_FOR_HUMAN_REVIEW]
    data = await rows(refs.session_id)
    assert sorted((r.action, r.count) for r in data) == [("flag_for_human_review", 1), ("warn_candidate", 3)]
    async with session_scope() as db:
        assert await count_unreviewed_flags(db, refs.session_id) == 1
        summary = await integrity_summary(db, refs.session_id)
    assert summary["by_type"]["focus_lost"]["occurrences"] == 4
    assert summary["unreviewed_flags"] == 1 and "not determinations" in summary["notice"]


async def test_rapid_focus_switching_is_derived(service, make_session) -> None:
    refs = await make_session()
    batch = [
        ev(T.FOCUS_LOST, seconds_ago=30 - i * 5, duration_ms=3500) for i in range(6)
    ]  # qualifying switches
    outcomes = await service.record_client_events(refs.sid, batch)
    rapid = [o for o in outcomes if o.event_type is T.RAPID_FOCUS_SWITCHING]
    assert (
        rapid and rapid[0].action is A.FLAG_FOR_HUMAN_REVIEW and rapid[0].severity is IntegritySeverity.MEDIUM
    )
    data = {r.type: r for r in await rows(refs.session_id)}
    assert data["rapid_focus_switching"].details["derived_from"] == "focus_changes"
    assert data["rapid_focus_switching"].count == 2  # 5th and 6th switch aggregate into one flag
    assert sum(r.count for r in await rows(refs.session_id) if r.type == "focus_lost") == 6


async def test_sub_threshold_focus_blips_never_add_up_to_rapid_switching(service, make_session) -> None:
    """S22: 300 ms blips (notifications, OS focus glitches) are below the focus rules' minimum duration."""
    refs = await make_session()
    blips = [ev(T.FOCUS_LOST, seconds_ago=30 - i * 5, duration_ms=300) for i in range(6)]
    blips += [ev(T.TAB_HIDDEN, seconds_ago=29 - i * 5, duration_ms=300) for i in range(6)]
    outcomes = await service.record_client_events(refs.sid, blips)
    assert not [o for o in outcomes if o.event_type is T.RAPID_FOCUS_SWITCHING]
    data = {r.type: r for r in await rows(refs.session_id)}
    assert "rapid_focus_switching" not in data and data["focus_lost"].action == "log_only"


async def test_repeated_short_glances_still_add_up_to_rapid_switching(service, make_session) -> None:
    """Review of S22: 1.5 s looks are below the focus rule's own minimum (no warning each), yet six in half a
    minute are the pattern rapid switching exists for."""
    refs = await make_session()
    glances = [ev(T.FOCUS_LOST, seconds_ago=30 - i * 5, duration_ms=1500) for i in range(6)]
    outcomes = await service.record_client_events(refs.sid, glances)
    assert [o for o in outcomes if o.event_type is T.RAPID_FOCUS_SWITCHING]


async def test_large_paste_derived_from_paste_details_without_storing_content(service, make_session) -> None:
    refs = await make_session()
    outcomes = await service.record_client_events(
        refs.sid, [ev(T.PASTE_EVENT, length=1200, lines=40, context="coding", text="def solve(): ...")]
    )
    assert {o.event_type for o in outcomes} == {T.PASTE_EVENT, T.LARGE_PASTE_IN_CODING}
    data = {r.type: r for r in await rows(refs.session_id)}
    assert data["large_paste_in_coding"].action == "flag_for_human_review"
    assert data["large_paste_in_coding"].details["chars"] == 1200
    assert "text" not in data["paste_event"].details


async def test_explicit_large_paste_is_not_double_counted(service, make_session) -> None:
    refs = await make_session()
    await service.record_client_events(
        refs.sid,
        [ev(T.PASTE_EVENT, length=900, context="coding"), ev(T.LARGE_PASTE_IN_CODING, length=900)],
    )
    data = [r for r in await rows(refs.session_id) if r.type == "large_paste_in_coding"]
    assert len(data) == 1 and data[0].count == 1


async def test_client_cannot_claim_vision_or_server_types(service, make_session) -> None:
    refs = await make_session()
    outcomes = await service.record_client_events(
        refs.sid,
        [ev(T.MULTIPLE_PERSONS_VISIBLE), ev(T.SCREEN_CONTEXT_CHANGE), ev(T.DISCONNECT), ev(T.COPY_EVENT)],
    )
    assert [o.event_type for o in outcomes] == [T.COPY_EVENT]


async def test_disabled_monitoring_records_nothing(service, make_session) -> None:
    refs = await make_session(integrity={"monitor_clipboard": False, "monitor_focus": False})
    outcomes = await service.record_client_events(
        refs.sid, [ev(T.COPY_EVENT), ev(T.FOCUS_LOST, duration_ms=9000)]
    )
    assert outcomes == [] and await rows(refs.session_id) == []


async def test_warn_candidate_false_never_messages(service, make_session) -> None:
    refs = await make_session(integrity={"warn_candidate": False})
    outcomes = await service.record_client_events(refs.sid, [ev(T.FULLSCREEN_EXITED), ev(T.CAMERA_DISABLED)])
    assert all(o.candidate_message is None for o in outcomes)
    assert {o.event_type: o.action for o in outcomes} == {
        T.FULLSCREEN_EXITED: A.LOG_ONLY,
        T.CAMERA_DISABLED: A.FLAG_FOR_HUMAN_REVIEW,
    }


@pytest.mark.parametrize(
    ("terminate", "expected"), [(True, A.TERMINATE_INTERVIEW), (False, A.PAUSE_INTERVIEW)]
)
async def test_required_screen_share_stop_applies_policy_action(
    service, make_session, terminate, expected
) -> None:
    refs = await make_session(
        integrity={"screen_share_required": True, "terminate_on_required_screen_share_stop": terminate}
    )
    [outcome] = await service.record_client_events(refs.sid, [ev(T.SCREEN_SHARE_STOPPED)])
    assert outcome.action is expected and outcome.severity is IntegritySeverity.HIGH
    assert outcome.candidate_message


async def test_timestamps_are_clamped_to_server_time(service, make_session) -> None:
    refs = await make_session()
    future = datetime.now(UTC) + timedelta(hours=5)
    event = ClientIntegrityEvent(event_type=T.COPY_EVENT, occurred_at=future.isoformat(), details={})
    await service.record_client_events(refs.sid, [event])
    [row] = await rows(refs.session_id)
    assert row.occurred_at <= datetime.now(UTC)
    assert row.details["clock_adjusted"] is True


async def test_per_session_rate_limit(service, make_session, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "SIGNALS_PER_MINUTE", 5)
    refs = await make_session()
    outcomes = await service.record_client_events(
        refs.sid, [ev(T.COPY_EVENT, seconds_ago=i) for i in range(12)]
    )
    assert len(outcomes) == 5
    [row] = await rows(refs.session_id)
    assert row.count == 5


async def test_events_after_session_end_are_dropped(service, make_session) -> None:
    refs = await make_session(status="completed", ended_at=datetime.now(UTC) - timedelta(hours=1))
    assert await service.record_client_events(refs.sid, [ev(T.COPY_EVENT)]) == []
    recent = await make_session(status="completed", ended_at=datetime.now(UTC) - timedelta(minutes=1))
    assert len(await service.record_client_events(recent.sid, [ev(T.COPY_EVENT)])) == 1  # late flush grace


async def test_unknown_session(service) -> None:
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        await service.record_client_events(str(uuid.uuid4()), [ev(T.COPY_EVENT)])
    with pytest.raises(NotFound):
        await service.record_client_events("not-a-uuid", [ev(T.COPY_EVENT)])


async def test_server_events_and_disconnect_pattern(service, make_session) -> None:
    refs = await make_session()
    first = await service.record_server_event(refs.sid, T.DISCONNECT)
    assert first is not None and first.action is A.LOG_ONLY
    await service.record_server_event(refs.sid, T.DISCONNECT)
    third = await service.record_server_event(refs.sid, T.DISCONNECT, details={"code": 1006})
    assert third is not None and third.event_type is T.UNUSUAL_DISCONNECT_PATTERN
    assert third.action is A.FLAG_FOR_HUMAN_REVIEW
    data = {r.type: r for r in await rows(refs.session_id)}
    assert data["disconnect"].count == 3 and data["disconnect"].source == "server"
    assert data["unusual_disconnect_pattern"].details["reason"] == "frequent_disconnects"


async def test_reconnects_beyond_policy_limit(service, make_session) -> None:
    refs = await make_session(integrity={"max_reconnects": 2})
    results = [await service.record_server_event(refs.sid, T.RECONNECT) for _ in range(3)]
    assert [r.event_type for r in results] == [T.RECONNECT, T.RECONNECT, T.UNUSUAL_DISCONNECT_PATTERN]
    data = {r.type: r for r in await rows(refs.session_id)}
    assert data["unusual_disconnect_pattern"].details["reason"] == "max_reconnects_exceeded"


async def test_server_event_rejects_vision_types(service, make_session) -> None:
    refs = await make_session()
    with pytest.raises(ValueError):
        await service.record_server_event(refs.sid, T.POSSIBLE_PHONE_VISIBLE)


async def test_page_reload_flags_when_resume_disallowed(service, make_session) -> None:
    refs = await make_session(integrity={"allow_page_reload_resume": False})
    outcome = await service.record_server_event(refs.sid, T.PAGE_RELOAD)
    assert outcome is not None and outcome.action is A.FLAG_FOR_HUMAN_REVIEW


async def test_bus_events_and_metric(service, make_session) -> None:
    refs = await make_session()
    before = INTEGRITY_EVENTS.labels(type="fullscreen_exited", source="browser")._value.get()
    async with subscribe_session_events(refs.session_id) as sub:
        await asyncio.sleep(0.05)
        await service.record_client_events(refs.sid, [ev(T.FULLSCREEN_EXITED)])
        event = await next_event(sub)
        assert event is not None and event["type"] == "integrity.event"
        assert event["event_type"] == "fullscreen_exited" and event["action"] == "warn_candidate"
        assert event["delivered_to_caller"] is True and event["aggregated"] is False
        assert await next_event(sub, 0.3) is None  # warning returned to the caller, not published
        # REST fallback: ask for the warning to be published too
        await service.record_client_events(refs.sid, [ev(T.MICROPHONE_DISABLED)], publish_warnings=True)
        received = [await next_event(sub), await next_event(sub)]
    types = [m["type"] for m in received if m]
    assert types == ["integrity.event", "integrity.warning"]
    assert received[1]["message"].startswith("Your microphone seems to be off")
    after = INTEGRITY_EVENTS.labels(type="fullscreen_exited", source="browser")._value.get()
    assert after == before + 1


async def test_reviewed_events_are_not_aggregated_into(service, make_session) -> None:
    refs = await make_session()
    [first] = await service.record_client_events(refs.sid, [ev(T.CAMERA_DISABLED)])
    async with session_scope() as db:
        row = await db.get(IntegrityEvent, uuid.UUID(first.event_id))
        row.review_status = "dismissed"
    [second] = await service.record_client_events(refs.sid, [ev(T.CAMERA_DISABLED)])
    assert second.event_id != first.event_id


async def test_concurrent_batches_aggregate_consistently(service, make_session) -> None:
    refs = await make_session()
    await asyncio.gather(*[service.record_client_events(refs.sid, [ev(T.COPY_EVENT)]) for _ in range(8)])
    data = await rows(refs.session_id)
    assert len(data) == 1 and data[0].count == 8  # advisory lock serialises the session
