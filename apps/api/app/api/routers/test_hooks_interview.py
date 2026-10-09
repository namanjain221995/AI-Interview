"""Interview engine test hooks (CONTRACTS.md section 4).

Only mounted when ``ENABLE_TEST_HOOKS=true`` and never in production (the
app factory skips ``test_hooks_*`` modules otherwise and the settings
validator refuses the flag in production). Each handler re-checks the
setting as defence in depth.

* ``POST /api/v1/test/sessions/{id}/advance-clock {seconds}`` - add to the
  session's elapsed interview time (publishes ``clock.advanced`` on the
  session bus so a live runtime re-checks its timers).
* ``POST /api/v1/test/sessions/{id}/force-coding`` - the next candidate turn
  starts the coding exercise (when the round has coding enabled).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.routers.test_hooks import require_test_hooks_token
from app.core.config import get_settings
from app.core.errors import NotFound
from app.services.interview.engine import SessionNotFound, get_engine

TEST_HOOKS = True

router = APIRouter(prefix="/test/sessions", tags=["test-hooks"], dependencies=[Depends(require_test_hooks_token)])


class AdvanceClockRequest(BaseModel):
    seconds: float = Field(..., gt=0, le=24 * 3600)


def _guard() -> None:
    settings = get_settings()
    if not settings.enable_test_hooks or settings.is_production:
        raise NotFound()


@router.post("/{session_id}/advance-clock", summary="Advance a session's interview clock (test only)")
async def advance_clock(session_id: uuid.UUID, body: AdvanceClockRequest) -> dict[str, Any]:
    _guard()
    try:
        state = await get_engine().advance_clock(str(session_id), body.seconds)
    except SessionNotFound as exc:
        raise NotFound("Session not found.") from exc
    return {
        "session_id": str(session_id),
        "elapsed_s": round(state.elapsed_seconds(), 1),
        "remaining_s": round(state.remaining_seconds(), 1),
        "status": state.status.value,
    }


@router.post("/{session_id}/force-coding", summary="Start coding on the next candidate turn (test only)")
async def force_coding(session_id: uuid.UUID) -> dict[str, Any]:
    _guard()
    try:
        state = await get_engine().force_coding(str(session_id))
    except SessionNotFound as exc:
        raise NotFound("Session not found.") from exc
    return {
        "session_id": str(session_id),
        "scheduled": True,
        "coding_enabled": state.round_config.coding.enabled,
        "coding_active": state.coding.active,
    }
