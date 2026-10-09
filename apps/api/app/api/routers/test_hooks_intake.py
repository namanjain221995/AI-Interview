"""Intake interview test hooks (INTAKE_INTERVIEW.md 13). Mounted only with ``ENABLE_TEST_HOOKS=true``
outside production (the app factory skips ``test_hooks_*`` modules otherwise); every handler re-checks.

* ``POST /api/v1/test/intake/sessions/{id}/jump {stage}`` - move the stage machine to the start of ``stage``
  (earlier open items become ``skipped``); a live runtime in this process then speaks the stage's first item.
* ``POST .../capture {activity_id, phase, text, segments?, pauses?, end_reason?, typed?}`` - inject an activity
  capture as the runtime would deliver it (``CandidateTurn.activity``). Injected spoken captures have no audio,
  so their transcript is settled as ``live_only``.
* ``POST .../activity-deadline {activity_id}`` - the current phase reaches its server deadline now (a live
  runtime hides the Drill B passage, ends the phase and asks the engine for the next prompt).

Responses summarise the produced events; they never carry passage or card text.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.routers.test_hooks import require_test_hooks_token
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.services.interview.contracts import (
    ActivityCapture,
    ActivityEnded,
    ActivityEvent,
    ActivityPhaseChanged,
    ActivityStarted,
    CandidateTurn,
    SessionClosing,
    UtteranceMeta,
)
from app.services.interview.engine import SessionNotFound, get_engine

TEST_HOOKS = True

router = APIRouter(
    prefix="/test/intake/sessions", tags=["test-hooks"], dependencies=[Depends(require_test_hooks_token)]
)

StageKey = Literal["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"]


def _guard() -> None:
    settings = get_settings()
    if not settings.enable_test_hooks or settings.is_production:
        raise NotFound()


def _runtime(session_id: uuid.UUID) -> Any | None:
    try:
        from app.realtime.registry import get_registry

        return get_registry().get(session_id)
    except Exception:  # noqa: BLE001 - no realtime layer in this process
        return None


def summarise(events: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, UtteranceMeta):
            intake = (event.decision or {}).get("intake") or {}
            out.append(
                {
                    "type": "utterance",
                    "utterance_id": event.utterance_id,
                    "question_id": event.question_id,
                    "text": event.full_text,
                    "item_key": intake.get("item_key"),
                    "item_kind": intake.get("item_kind"),
                }
            )
        elif isinstance(event, ActivityStarted):
            data = {k: v for k, v in event.activity.items() if k not in ("content", "phase_rules")}
            out.append(
                {
                    "type": "activity_started",
                    **data,
                    "start_after_utterance_id": event.start_after_utterance_id,
                }
            )
        elif isinstance(event, ActivityPhaseChanged):
            out.append(
                {
                    "type": "activity_phase_changed",
                    "activity_id": event.activity_id,
                    "phase": event.phase,
                    "duration_ms": event.duration_ms,
                    "capture": event.capture,
                    "start_after_utterance_id": event.start_after_utterance_id,
                }
            )
        elif isinstance(event, ActivityEnded):
            out.append({"type": "activity_ended", "activity_id": event.activity_id, "reason": event.reason})
        elif isinstance(event, SessionClosing):
            out.append({"type": "session_closing", "reason": event.reason.value})
    return out


class JumpRequest(BaseModel):
    stage: StageKey


class CaptureRequest(BaseModel):
    activity_id: str = Field(..., max_length=64)
    phase: Literal["read", "relay", "speak"]
    text: str = Field("", max_length=20_000)
    segments: list[dict[str, Any]] | None = None
    pauses: list[dict[str, Any]] | None = None
    end_reason: Literal["finish_button", "deadline", "silence", "disconnect", "max_audio"] = "finish_button"
    typed: bool = False


class DeadlineRequest(BaseModel):
    activity_id: str = Field(..., max_length=64)


@router.post("/{session_id}/jump", summary="Jump an intake session to the start of a stage (test only)")
async def jump(session_id: uuid.UUID, body: JumpRequest) -> dict[str, Any]:
    _guard()
    engine = get_engine()
    try:
        state = await engine.intake_jump(str(session_id), body.stage)
    except SessionNotFound as exc:
        raise NotFound("Intake session not found.") from exc
    runtime = _runtime(session_id)
    if runtime is not None and runtime.transport is not None:
        runtime._enqueue("resume", lambda: runtime._run_resume(reconnect=False), 2, needs_idle=False)
        return {"session_id": str(session_id), "stage": body.stage, "runtime": True, "events": []}
    events = [event async for event in engine.resume(str(session_id), reconnect=False)]
    del state
    return {"session_id": str(session_id), "stage": body.stage, "runtime": False, "events": summarise(events)}


@router.post("/{session_id}/capture", summary="Inject an intake activity capture (test only)")
async def capture(session_id: uuid.UUID, body: CaptureRequest) -> dict[str, Any]:
    _guard()
    runtime = _runtime(session_id)
    if runtime is not None:
        injected = await runtime.inject_activity_capture(
            body.activity_id,
            phase=body.phase,
            text=body.text,
            segments=body.segments,
            pauses=body.pauses,
            end_reason=body.end_reason,
            typed=body.typed,
        )
        if not injected:
            raise Conflict("That activity is not running in the live session.", code="activity_not_running")
        return {"session_id": str(session_id), "runtime": True, "events": []}
    engine = get_engine()
    try:
        state = await engine.load_state(str(session_id))
    except SessionNotFound as exc:
        raise NotFound("Session not found.") from exc
    elapsed_ms = int(state.elapsed_seconds() * 1000)
    segments = body.segments
    if segments is None:
        segments = (
            [{"start_ms": max(0, elapsed_ms - 4000), "end_ms": elapsed_ms, "speech_ms": 3600, "vad_prob": 0.95,
              "no_speech_prob": 0.0, "text": body.text}]
            if body.text and not body.typed
            else []
        )  # fmt: skip
    started = int(segments[0]["start_ms"]) if segments else elapsed_ms
    activity = state.intake.activity if state.intake is not None else None
    if activity is None or activity.activity_id != body.activity_id:
        raise Conflict("That activity is not active.", code="activity_not_running")
    turn = CandidateTurn(
        text=body.text,
        started_ms=started,
        ended_ms=elapsed_ms,
        typed=body.typed,
        activity=ActivityCapture(
            activity_id=body.activity_id,
            kind=activity.kind,  # type: ignore[arg-type]
            phase=body.phase,
            end_reason=body.end_reason,
            started_ms=started,
            ended_ms=elapsed_ms,
            voiced_ms=sum(int(s.get("speech_ms") or 0) for s in segments),
            segments=[dict(s) for s in segments],
            pauses=[dict(p) for p in body.pauses or []],
            typed=body.typed,
        ),
    )
    attempt = activity.attempt
    events = [event async for event in engine.handle_candidate_turn(str(session_id), turn)]
    if segments and not body.typed and body.end_reason != "disconnect":
        from app.services.intake import capture_store

        await capture_store.mark_transcript_status(
            str(session_id), body.activity_id, attempt=attempt, status="live_only", source="live_segments"
        )
    return {"session_id": str(session_id), "runtime": False, "events": summarise(events)}


@router.post("/{session_id}/activity-deadline", summary="End the current activity phase now (test only)")
async def activity_deadline(session_id: uuid.UUID, body: DeadlineRequest) -> dict[str, Any]:
    _guard()
    runtime = _runtime(session_id)
    if runtime is not None:
        ended = await runtime.force_activity_deadline(body.activity_id)
        if not ended:
            raise Conflict("That activity has no running phase.", code="activity_not_running")
        return {"session_id": str(session_id), "runtime": True, "events": []}
    engine = get_engine()
    try:
        state = await engine.load_state(str(session_id))
    except SessionNotFound as exc:
        raise NotFound("Session not found.") from exc
    activity = state.intake.activity if state.intake is not None else None
    if activity is None or activity.activity_id != body.activity_id or activity.status != "running":
        raise Conflict("That activity has no running phase.", code="activity_not_running")
    event = ActivityEvent(
        kind="phase_deadline",
        activity_id=body.activity_id,
        phase=activity.phase,
        elapsed_s=round(state.elapsed_seconds(), 3),
        detail={"test_hook": True, "at": time.time()},
    )
    events = [item async for item in engine.handle_activity_event(str(session_id), event)]
    return {"session_id": str(session_id), "runtime": False, "events": summarise(events)}
