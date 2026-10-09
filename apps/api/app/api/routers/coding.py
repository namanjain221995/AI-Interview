"""Staff view of a session's coding work: ``GET /sessions/{id}/coding``.

Returns every coding session of the interview session with the problem, all
snapshots (with unified diffs), all runs (staff-side test results, including
hidden tests), the hints issued and the coding evaluation. Staff only
(``sessions:read``), organization scoped, and audited like transcript views.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.core.audit import audit
from app.core.deps import DbSession, require_permission
from app.core.errors import NotFound
from app.core.rbac import Permission, Principal
from app.db.models import CodeRun, CodeSnapshot, CodingHint, CodingQuestion, CodingSession, InterviewSession
from app.schemas.api.coding import (
    CodeRunOut,
    CodeSnapshotOut,
    CodingHintOut,
    CodingQuestionBrief,
    CodingSessionDetailOut,
)
from app.schemas.api.common import ERROR_RESPONSES, Page

router = APIRouter(tags=["coding"], responses=ERROR_RESPONSES)

SessionReader = Annotated[Principal, Depends(require_permission(Permission.SESSIONS_READ))]


@router.get(
    "/sessions/{session_id}/coding",
    response_model=Page[CodingSessionDetailOut],
    summary="Coding sessions of an interview session (snapshots, runs, hints, evaluation)",
)
async def session_coding(
    session_id: uuid.UUID, request: Request, principal: SessionReader, db: DbSession
) -> Page[CodingSessionDetailOut]:
    session = await db.get(InterviewSession, session_id)
    if session is None or (
        not principal.is_super_admin and session.organization_id != principal.organization_id
    ):
        raise NotFound("Session not found.", code="session_not_found")
    coding_sessions = (
        await db.scalars(
            select(CodingSession)
            .where(CodingSession.session_id == session_id)
            .order_by(CodingSession.started_at, CodingSession.id)
        )
    ).all()
    ids = [cs.id for cs in coding_sessions]
    snapshots: dict[uuid.UUID, list[CodeSnapshot]] = defaultdict(list)
    runs: dict[uuid.UUID, list[CodeRun]] = defaultdict(list)
    hints: dict[uuid.UUID, list[CodingHint]] = defaultdict(list)
    questions: dict[uuid.UUID, CodingQuestion] = {}
    if ids:
        for snap in (
            await db.scalars(
                select(CodeSnapshot).where(CodeSnapshot.coding_session_id.in_(ids)).order_by(CodeSnapshot.seq)
            )
        ).all():
            snapshots[snap.coding_session_id].append(snap)
        for run in (
            await db.scalars(
                select(CodeRun)
                .where(CodeRun.coding_session_id.in_(ids))
                .order_by(CodeRun.created_at, CodeRun.id)
            )
        ).all():
            runs[run.coding_session_id].append(run)
        for hint in (
            await db.scalars(
                select(CodingHint).where(CodingHint.coding_session_id.in_(ids)).order_by(CodingHint.level)
            )
        ).all():
            hints[hint.coding_session_id].append(hint)
        question_ids = {cs.coding_question_id for cs in coding_sessions}
        for row in (
            await db.scalars(select(CodingQuestion).where(CodingQuestion.id.in_(question_ids)))
        ).all():
            questions[row.id] = row

    items: list[CodingSessionDetailOut] = []
    for cs in coding_sessions:
        question = questions.get(cs.coding_question_id)
        items.append(
            CodingSessionDetailOut(
                id=cs.id,
                session_id=cs.session_id,
                coding_question_id=cs.coding_question_id,
                question_id=cs.question_id,
                language=cs.language,
                status=cs.status,
                started_at=cs.started_at,
                ended_at=cs.ended_at,
                time_limit_seconds=cs.time_limit_seconds,
                hints_available=cs.hints_available,
                hints_used=cs.hints_used,
                paste_event_count=cs.paste_event_count,
                final_code=cs.final_code,
                final_run_id=cs.final_run_id,
                end_reason=(cs.observer_state or {}).get("end_reason"),
                evaluation=dict(cs.evaluation or {}),
                question=CodingQuestionBrief.model_validate(question) if question is not None else None,
                snapshots=[CodeSnapshotOut.model_validate(s) for s in snapshots[cs.id]],
                runs=[
                    CodeRunOut.model_validate(r).model_copy(
                        update={
                            "platform_fault": bool((r.sandbox_meta or {}).get("platform_fault")),
                            "system_run": bool((r.sandbox_meta or {}).get("system")),
                        }
                    )
                    for r in runs[cs.id]
                ],
                hints=[CodingHintOut.model_validate(h) for h in hints[cs.id]],
                created_at=cs.created_at,
                updated_at=cs.updated_at,
            )
        )
    await audit(
        db,
        principal,
        "session.coding.view",
        resource_type="interview_session",
        resource_id=str(session_id),
        request=request,
        organization_id=session.organization_id,
        details={"coding_sessions": len(items)},
    )
    return Page[CodingSessionDetailOut](items=items, total=len(items), limit=max(1, len(items)), offset=0)
