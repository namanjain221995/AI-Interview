"""Adaptive interview engine (ENGINE_DESIGN.md).

Public entry points (imported lazily to keep ``state`` / ``contracts``
importable from other modules' contracts without cycles)::

    from app.services.interview import get_engine, get_session_service

    engine = get_engine()                      # InterviewEngine (TurnEngine)
    async for event in engine.start(session_id): ...

    service = get_session_service()            # SessionServiceImpl (SessionService)
    session = await service.create_or_resume(db, interview_id=..., round_id=None)
"""

from __future__ import annotations

from typing import Any

_LAZY = {
    "InterviewEngine": "app.services.interview.engine",
    "EventStream": "app.services.interview.engine",
    "SessionNotFound": "app.services.interview.engine",
    "get_engine": "app.services.interview.engine",
    "set_engine": "app.services.interview.engine",
    "SessionServiceImpl": "app.services.interview.sessions",
    "get_session_service": "app.services.interview.sessions",
    "set_session_service": "app.services.interview.sessions",
    "mark_session_completed": "app.services.interview.sessions",
    "EvaluationService": "app.services.interview.evaluation",
    "StateStore": "app.services.interview.state_store",
    "compute_directive": "app.services.interview.policy",
    "PolicyDirective": "app.services.interview.policy",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module 'app.services.interview' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


__all__ = sorted(_LAZY)
