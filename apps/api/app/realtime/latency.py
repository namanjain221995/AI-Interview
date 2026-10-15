"""What a spoken turn costs the candidate, stage by stage (plan package P0.4 / C7).

The runtime keeps one record per candidate turn (``SessionRuntime._latency_note``). When the first audio of
the interviewer's reply is sent, :func:`observe_turn_latency` adds the candidate-facing totals and feeds the
histograms (once per turn). The clock the candidate lives on starts at their last voiced audio, which is
``endpoint_silence_ms`` before the end-of-turn decision; every other timing in the record counts from that
decision (``ack_ms``, ``filler_ms``, ``first_audio_ms``, ...):

* ``voice_to_voice_ms``: last voiced audio -> first audio of the interviewer's reply sent
  (``interview_voice_to_voice_seconds`` reply="question"). The honest per-turn speed number.
* ``first_sound_ms``: last voiced audio -> the first thing the candidate hears, whichever comes first: the
  "Okay." acknowledgement (``ack_ms``), the "One moment." filler (``filler_ms``) or the reply itself
  (reply="first_sound"); ``first_sound_by`` says which one ("ack", "filler" or "reply").
* stages (``interview_turn_stage_seconds``): endpoint_silence, stt_final, commit_wait, decision_to_text (engine
  and model until the first speech chunk is ready), tts_first_chunk, send (the remainder until it is sent).

Typed turns and intake activity captures (no endpoint silence) are persisted but feed no histogram. Server side
only: the browser adds its playback lead (about 150 ms) and the network.
"""

from __future__ import annotations

from typing import Any

from app.core import metrics

# Sounds that can reach the candidate before the reply, by record key (all counted from the end-of-turn decision).
_EARLY_SOUNDS = (("ack", "ack_ms"), ("filler", "filler_ms"))


def _ms(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, int | float) else None


def _stage(name: str, value_ms: float | None) -> None:
    if value_ms is not None and value_ms >= 0:
        metrics.TURN_STAGE_SECONDS.labels(stage=name).observe(value_ms / 1000.0)


def first_sound(record: dict[str, Any]) -> tuple[str, float] | None:
    """The first audio the candidate hears after the end-of-turn decision: ("ack" | "filler" | "reply", ms)."""
    first_audio = _ms(record, "first_audio_ms")
    if first_audio is None:
        return None
    best = ("reply", first_audio)
    for source, key in _EARLY_SOUNDS:
        at = _ms(record, key)
        if at is not None and at >= 0 and at < best[1]:
            best = (source, at)
    return best


def observe_turn_latency(record: dict[str, Any]) -> None:
    """Add ``voice_to_voice_ms`` / ``first_sound_ms`` / ``first_sound_by`` to ``record`` and observe the metrics.
    Call once per turn, when ``first_audio_ms`` is known. Only spoken answer turns count (they have an
    ``endpoint_silence_ms``): typed answers (no transcription wait) and intake activity captures would skew the
    stage distributions."""
    first_audio = _ms(record, "first_audio_ms")
    heard = first_sound(record)
    silence = None if record.get("typed") else _ms(record, "endpoint_silence_ms")
    if first_audio is None or heard is None or silence is None:
        return
    question = silence + first_audio
    source, at = heard
    record["voice_to_voice_ms"] = round(question)
    record["first_sound_ms"] = round(silence + at)
    record["first_sound_by"] = source
    metrics.VOICE_TO_VOICE.labels(reply="question").observe(question / 1000.0)
    metrics.VOICE_TO_VOICE.labels(reply="first_sound").observe((silence + at) / 1000.0)
    _stage("endpoint_silence", silence)
    stt = _ms(record, "stt_wait_ms")
    commit = _ms(record, "commit_wait_ms") or 0.0
    _stage("stt_final", stt)
    if commit:
        _stage("commit_wait", commit)
    ready = _ms(record, "first_chunk_ready_ms")
    if ready is not None and stt is not None:
        _stage("decision_to_text", ready - stt - commit)
    tts = _ms(record, "tts_first_ms")
    _stage("tts_first_chunk", tts)
    if ready is not None and tts is not None:
        _stage("send", first_audio - ready - tts)
