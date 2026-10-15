"""P0.4: the candidate-facing turn latency (last voiced audio -> first reply audio) and its stages."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.metrics import REGISTRY
from app.realtime.latency import first_sound, observe_turn_latency
from app.realtime.runtime import SessionRuntime
from app.realtime.turns import is_repeat_complaint

STAGES = ("endpoint_silence", "stt_final", "commit_wait", "decision_to_text", "tts_first_chunk", "send")


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _snapshot() -> dict[str, float]:
    values = {
        f"v2v_{reply}": _sample("interview_voice_to_voice_seconds_count", {"reply": reply})
        for reply in ("question", "first_sound")
    }
    values |= {
        f"v2v_sum_{reply}": _sample("interview_voice_to_voice_seconds_sum", {"reply": reply})
        for reply in ("question", "first_sound")
    }
    for stage in STAGES:
        values[stage] = _sample("interview_turn_stage_seconds_count", {"stage": stage})
        values[f"sum_{stage}"] = _sample("interview_turn_stage_seconds_sum", {"stage": stage})
    return values


def test_a_spoken_turn_counts_from_the_last_word_and_feeds_every_stage() -> None:
    before = _snapshot()
    record = {
        "endpoint_silence_ms": 850,  # silence already heard when the end of turn was decided
        "stt_wait_ms": 1200,
        "commit_wait_ms": 0,
        "first_chunk_ready_ms": 2500,
        "tts_first_ms": 600,
        "first_audio_ms": 3200,
        "ack_ms": 400,
    }
    observe_turn_latency(record)
    after = _snapshot()
    assert record["voice_to_voice_ms"] == 850 + 3200
    assert record["first_sound_ms"] == 850 + 400  # the "Okay." is the first thing heard
    assert record["first_sound_by"] == "ack"
    assert after["v2v_question"] - before["v2v_question"] == 1
    assert after["v2v_sum_question"] - before["v2v_sum_question"] == pytest.approx(4.05)
    assert after["v2v_sum_first_sound"] - before["v2v_sum_first_sound"] == pytest.approx(1.25)
    deltas = {stage: after[f"sum_{stage}"] - before[f"sum_{stage}"] for stage in STAGES}
    assert deltas["endpoint_silence"] == pytest.approx(0.85)
    assert deltas["stt_final"] == pytest.approx(1.2)
    assert deltas["decision_to_text"] == pytest.approx(1.3)  # 2500 - 1200 - 0
    assert deltas["tts_first_chunk"] == pytest.approx(0.6)
    assert deltas["send"] == pytest.approx(0.1)  # 3200 - 2500 - 600
    assert after["commit_wait"] == before["commit_wait"]  # no commit wait, nothing observed


def test_ack_first_turn_counts_the_okay_as_the_first_sound() -> None:
    """Tier 1: "Okay." about a second after a finished answer, then the reply; the filler never played."""
    before = _snapshot()
    record = {"endpoint_silence_ms": 700, "stt_wait_ms": 900, "ack_ms": 350, "ack": "Okay.", "first_audio_ms": 2600}
    observe_turn_latency(record)
    after = _snapshot()
    assert record["first_sound_by"] == "ack"
    assert record["first_sound_ms"] == 700 + 350
    assert record["voice_to_voice_ms"] == 700 + 2600  # the question still counts to the reply's first audio
    assert after["v2v_sum_first_sound"] - before["v2v_sum_first_sound"] == pytest.approx(1.05)
    assert after["v2v_sum_question"] - before["v2v_sum_question"] == pytest.approx(3.3)


def test_filler_first_turn_counts_one_moment_as_the_first_sound() -> None:
    """A short answer (no "Okay.") and a slow reply: "One moment." at 3.5 s is the first thing heard."""
    before = _snapshot()
    record = {
        "endpoint_silence_ms": 900,
        "stt_wait_ms": 1100,
        "filler_ms": 3500,
        "filler": "One moment.",
        "first_audio_ms": 6200,
    }
    observe_turn_latency(record)
    after = _snapshot()
    assert record["first_sound_by"] == "filler"
    assert record["first_sound_ms"] == 900 + 3500
    assert record["voice_to_voice_ms"] == 900 + 6200
    assert after["v2v_sum_first_sound"] - before["v2v_sum_first_sound"] == pytest.approx(4.4)
    assert after["v2v_sum_question"] - before["v2v_sum_question"] == pytest.approx(7.1)


def test_the_earliest_sound_wins_and_a_quick_reply_beats_both() -> None:
    assert first_sound({"ack_ms": 1200, "filler_ms": 3500, "first_audio_ms": 5000}) == ("ack", 1200)
    assert first_sound({"filler_ms": 3500, "first_audio_ms": 5000}) == ("filler", 3500)
    assert first_sound({"ack_ms": 1200, "first_audio_ms": 800}) == ("reply", 800)  # the reply came first
    assert first_sound({"ack_ms": 400}) is None  # not complete until the reply's first audio


def test_a_reply_without_ack_or_filler_is_its_own_first_sound() -> None:
    record = {"endpoint_silence_ms": 600, "first_audio_ms": 1900}
    observe_turn_latency(record)
    assert record["first_sound_by"] == "reply"
    assert record["first_sound_ms"] == record["voice_to_voice_ms"] == 2500


def _fake_runtime() -> SimpleNamespace:
    persisted: list[dict[str, Any]] = []
    rt = SimpleNamespace(latency=[], session_id="s-1", persisted=persisted)
    rt._persist_latency = persisted.append
    rt.spawn = lambda result, name: None
    rt._latency_note = lambda capture, **fields: SessionRuntime._latency_note(rt, capture, **fields)  # type: ignore[arg-type]
    return rt


def test_the_runtime_completes_a_turn_once_when_its_first_reply_audio_arrives() -> None:
    """The ack and filler notes come first; the reply's first audio completes, observes and persists the record."""
    rt = _fake_runtime()
    capture = SimpleNamespace(capture_id="cap-1")
    before = _snapshot()
    rt._latency_note(capture, ack_ms=300, ack="Okay.")
    rt._latency_note(capture, turn_id="cap-1", endpoint_silence_ms=800, stt_wait_ms=1000, typed=False)
    rt._latency_note(capture, filler_ms=3500, filler="One moment.")
    assert rt.persisted == [] and _snapshot()["v2v_question"] == before["v2v_question"]
    rt._latency_note(capture, first_audio_ms=4100)
    rt._latency_note(capture, first_audio_ms=9999)  # a second report for the same turn is not a second turn
    after = _snapshot()
    assert after["v2v_question"] - before["v2v_question"] == 1
    (persisted,) = rt.persisted
    assert persisted["first_sound_by"] == "ack" and persisted["first_sound_ms"] == 1100
    assert persisted["voice_to_voice_ms"] == 4900
    assert persisted["filler"] == "One moment." and persisted["ack"] == "Okay."


def test_a_reopened_turn_drops_timings_from_the_decision_that_no_longer_stands() -> None:
    """Speech resumed after "Okay." was played: the next decision starts a fresh record (the stale ack would
    otherwise count as the first sound of the final answer)."""
    rt = _fake_runtime()
    capture = SimpleNamespace(capture_id="cap-2")
    rt._latency_note(capture, ack_ms=250, ack="Okay.", stt_wait_ms=700)
    SessionRuntime._latency_reopen(rt, capture)  # type: ignore[arg-type]
    assert rt.latency == [{"turn_id": "cap-2", "reopened": 1}]
    rt._latency_note(capture, turn_id="cap-2", endpoint_silence_ms=900, stt_wait_ms=600, typed=False)
    rt._latency_note(capture, first_audio_ms=2000)
    (persisted,) = rt.persisted
    assert "ack_ms" not in persisted and persisted["reopened"] == 1
    assert persisted["first_sound_by"] == "reply" and persisted["first_sound_ms"] == 2900


def test_a_typed_turn_has_no_voice_to_voice_figure() -> None:
    before = _snapshot()
    record = {
        "typed": True,
        "stt_wait_ms": 3,
        "first_chunk_ready_ms": 1500,
        "tts_first_ms": 400,
        "first_audio_ms": 2100,
        "endpoint_silence_ms": None,
    }
    observe_turn_latency(record)
    after = _snapshot()
    assert "voice_to_voice_ms" not in record and "first_sound_ms" not in record
    assert after == before  # nor any stage: a 3 ms "transcription" would drag the spoken turns' stt_final down


def test_an_activity_capture_feeds_no_histogram() -> None:
    before = _snapshot()
    record = {"activity": "read_relay", "voiced_ms": 9000, "first_audio_ms": 1800, "tts_first_ms": 300}
    observe_turn_latency(record)
    assert _snapshot() == before
    assert "voice_to_voice_ms" not in record


@pytest.mark.parametrize(
    "text",
    [
        "I already told you.",
        "I've already answered that, it stops when the pointers cross.",
        "As I said, we used Argo Rollouts.",
        "I just mentioned it, the cache is invalidated on write.",
        "I already answered this one.",
    ],
)
def test_repeat_complaints_are_recognised(text: str) -> None:
    assert is_repeat_complaint(text)


@pytest.mark.parametrize(
    "text",
    [
        "I told the team we should roll back first.",
        "We had already migrated the cluster when the outage started.",
        "Can you repeat the question?",
        "",
    ],
)
def test_ordinary_answers_are_not_repeat_complaints(text: str) -> None:
    assert not is_repeat_complaint(text)
