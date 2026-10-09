"""Coding interview module: coding sessions, snapshots and diffs, sandbox
client, test orchestration, the three-hint system, the code observer and the
coding evaluation.

Public entry points::

    from app.services.coding import get_coding_service, decide, ObservationDecision

    service = get_coding_service()            # implements contracts.CodingService
    qid = await service.select_question(session_id, competency_keys=[...], difficulty=..., languages=[...])
    state = await service.start(session_id, qid, question_id=None, language=CodeLanguage.PYTHON)
    decision = await service.observe(state.coding_session_id, "idle")   # or observer.decide(context, trigger, state)
"""

from app.services.coding.contracts import CodingContext, CodingService, ExecuteRequest, ExecuteResponse
from app.services.coding.observer import ObservationDecision, decide
from app.services.coding.sandbox_client import (
    SANDBOX_BUSY_MESSAGE,
    SandboxClient,
    SandboxFault,
    get_sandbox_client,
    set_sandbox_client,
)
from app.services.coding.service import CodingServiceImpl, HintResult, get_coding_service, set_coding_service

__all__ = [
    "SANDBOX_BUSY_MESSAGE",
    "CodingContext",
    "CodingService",
    "CodingServiceImpl",
    "ExecuteRequest",
    "ExecuteResponse",
    "HintResult",
    "ObservationDecision",
    "SandboxClient",
    "SandboxFault",
    "decide",
    "get_coding_service",
    "get_sandbox_client",
    "set_coding_service",
    "set_sandbox_client",
]
