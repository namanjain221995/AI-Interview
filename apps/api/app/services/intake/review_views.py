"""Staff review payloads of the intake module (design contract sections 10.2 and 15.13).

* :func:`detail_view` — ``IntakeDetailOut`` = T3's ``IntakeSummaryOut`` + settings, onboarding, the MQ1 block (staff
  only; never in reports, exports, analytics or prompts), résumé review, stages, activities (with the kit material:
  staff may read the passages), the newest ready evaluation, review items with their latest resolution, decisions,
  the report version, integrity counts and recording parts.
* :func:`timeline_view` — stages -> items -> question/answer exchanges with timestamps and answer audio ids.
* :func:`evaluation_view` — ``IntakeEvaluationOut`` with the seven ``CompetencyScoreOut`` rows (kit anchors).
* :func:`request_reevaluation` — queues ``intake.evaluate`` (reason ``reevaluate``) for a new version.

Every query is scoped to the intake row's organisation; the caller resolves the row with
:func:`intake_for_principal` (another organisation's id answers 404 ``intake_not_found``).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound
from app.core.queue import enqueue
from app.core.rbac import Principal
from app.db.models import (
    Answer,
    CandidateResume,
    IntegrityEvent,
    Interview,
    InterviewSession,
    MediaAsset,
    Question,
    Report,
)
from app.db.models.intake import (
    IntakeActivityResult,
    IntakeCompetencyScore,
    IntakeEvaluation,
    IntakeInterview,
    IntakeStageResult,
)
from app.domain.enums import IntegrityReviewStatus, MediaKind, ReportType
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.listing import get_summary
from app.services.intake.pipeline import (
    ENDED_STATUSES,
    EVALUATE_JOB,
    decisions_out,
    latest_ready_evaluation,
    running_evaluation,
)
from app.services.intake.scoring import BAND_LABELS, INTAKE_COMPETENCIES, NORMALIZED_LABEL, DecisionBand
from app.services.integrity.rules import REVIEWABLE_ACTIONS
from app.services.reports.queries import answer_audio_media, media_ids_for_keys

INTAKE_SEGMENT_PREFIX = "intake:"


# ============================================================================================== lookups


async def intake_for_principal(
    db: AsyncSession, principal: Principal, intake_id: uuid.UUID
) -> IntakeInterview:
    """The intake row when the staff principal may see it (SUPER_ADMIN: any organisation; else 404)."""
    row = await db.get(IntakeInterview, intake_id)
    if row is None or (not principal.is_super_admin and row.organization_id != principal.organization_id):
        raise NotFound("Intake interview not found.", code="intake_not_found")
    return row


async def session_for_intake(db: AsyncSession, intake: IntakeInterview) -> InterviewSession | None:
    """The newest session of the intake's invitation (``None`` for drafts and unopened invitations)."""
    if intake.interview_id is None:
        return None
    return await db.scalar(
        select(InterviewSession)
        .where(InterviewSession.interview_id == intake.interview_id)
        .order_by(InterviewSession.created_at.desc())
        .limit(1)
    )


async def evaluation_for(
    db: AsyncSession, session: InterviewSession | None, *, version: int | None = None
) -> IntakeEvaluation:
    """The requested evaluation version, else the newest ready one (404 ``evaluation_not_ready``)."""
    if session is None:
        raise NotFound("No evaluation is ready yet.", code="evaluation_not_ready")
    if version is not None:
        row = await db.scalar(
            select(IntakeEvaluation).where(
                IntakeEvaluation.session_id == session.id, IntakeEvaluation.version == version
            )
        )
        if row is None:
            raise NotFound("Evaluation version not found.", code="evaluation_not_found")
        return row
    row = await latest_ready_evaluation(db, session.id)
    if row is None:
        raise NotFound("No evaluation is ready yet.", code="evaluation_not_ready")
    return row


# ============================================================================================== evaluation


def _dec(value: Any) -> str | None:
    return None if value is None else format(value, "f")


def competency_out(row: IntakeCompetencyScore, kit: IntakeKit) -> dict[str, Any]:
    competency = kit.competency(row.competency_key)
    return {
        "key": row.competency_key,
        "label": competency.label,
        "kit_label": competency.kit_label,
        "weight": row.weight,
        "status": row.status,
        "score": row.score,
        "weighted": row.weighted,
        "evaluator": row.evaluator,
        "reasoning": row.reasoning or "",
        "evidence": list(row.evidence or []),
        "missing_evidence": list(row.missing_evidence or []),
        "strengths": list(row.strengths or []),
        "improvements": list(row.improvements or []),
        "anchors": {str(level): text for level, text in competency.anchors.items()},
        "sub_scores": dict(row.sub_scores or {}),
    }


def _partial_label(rows: Sequence[IntakeCompetencyScore]) -> str | None:
    scored = [r for r in rows if r.status == "scored"]
    if len(scored) == len(rows):
        return None
    weight = sum((Decimal(str(r.weight)) for r in scored), Decimal(0))
    return (
        f"Partial total re-normalised over {len(scored)} of {len(rows)} competencies "
        f"({float(weight) * 100:.0f}% of the weight); not a decision band"
    )


async def evaluation_view(
    db: AsyncSession, evaluation: IntakeEvaluation, *, kit: IntakeKit | None = None
) -> dict:
    """``IntakeEvaluationOut`` (contract 15.13)."""
    kit = kit or load_intake_kit()
    rows = (
        await db.scalars(
            select(IntakeCompetencyScore).where(IntakeCompetencyScore.evaluation_id == evaluation.id)
        )
    ).all()
    order = {c.key: n for n, c in enumerate(INTAKE_COMPETENCIES)}
    rows = sorted(rows, key=lambda r: order.get(r.competency_key, 99))
    band = evaluation.band
    return {
        "id": evaluation.id,
        "version": evaluation.version,
        "status": evaluation.status,
        "reason": evaluation.reason,
        "created_at": evaluation.created_at,
        "finished_at": evaluation.finished_at,
        "kit_version": evaluation.kit_version,
        "scoring_version": evaluation.scoring_version,
        "weighted_total": evaluation.weighted_total,
        "partial_total": evaluation.partial_total,
        "partial_label": _partial_label(rows) if (rows and evaluation.partial_total is not None) else None,
        "normalized_100": evaluation.normalized_100,
        "normalized_label": NORMALIZED_LABEL,
        "band": band,
        "band_label": BAND_LABELS[DecisionBand(band)] if band else None,
        "evidence_sufficiency": evaluation.evidence_sufficiency,
        "competencies": [competency_out(r, kit) for r in rows],
        "summary": dict(evaluation.summary or {}),
        "validator_issues": [
            {"code": i.get("code"), "competency": i.get("competency"), "detail": i.get("detail") or ""}
            for i in (evaluation.validator or {}).get("issues", [])
        ],
        "review_items": list(evaluation.review_items or []),
        "training_plan": evaluation.training_plan,
        "error": evaluation.error,
    }


def review_items_with_resolution(
    items: Sequence[Mapping[str, Any]], decisions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Review items + the newest decision's resolution of each (``resolution`` null when unresolved)."""
    latest = decisions[0] if decisions else None
    resolutions = {}
    if latest is not None:
        for entry in latest.get("review_items_resolution") or []:
            resolutions[str(entry.get("item_id"))] = {
                "resolution": entry.get("resolution"),
                "note": entry.get("note"),
                "reviewer": {"id": latest["reviewer"]["id"], "full_name": latest["reviewer"]["full_name"]},
                "at": latest.get("created_at"),
            }
    return [{**dict(item), "resolution": resolutions.get(str(item.get("id")))} for item in items]


# ============================================================================================== detail


def _material(kit: IntakeKit, kind: str, key: str) -> dict[str, Any]:
    if kind == "topic_card":
        try:
            card = kit.topic_card(key)
        except KeyError:
            return {"key": key, "title": key}
        return {
            "key": key,
            "title": card.title,
            "type": card.type,
            "bullets": list(card.bullets),
            "instruction": card.instruction,
        }
    try:
        passage = kit.passage(key)
    except KeyError:
        return {"key": key, "title": key}
    return {
        "key": key,
        "title": passage.title,
        "level": passage.level,
        "paragraphs": list(passage.paragraphs),
    }


def _activity_out(row: IntakeActivityResult, kit: IntakeKit) -> dict[str, Any]:
    capture = row.capture or {}
    return {
        "id": row.id,
        "kind": row.kind,
        "stage_key": row.stage_key,
        "status": row.status,
        "attempt": row.attempt,
        "material": _material(kit, row.kind, row.material_key),
        "phases": list(row.phases or []),
        "reveal": dict(row.reveal or {}),
        "capture": {
            "started_ms": capture.get("started_ms"),
            "ended_ms": capture.get("ended_ms"),
            "voiced_ms": capture.get("voiced_ms"),
            "end_reason": capture.get("end_reason") or row.ended_reason,
            "typed": bool(capture.get("typed")),
            "pauses_count": len(capture.get("pauses") or []),
            "segments_count": len(capture.get("segments") or []),
        },
        "transcript": {
            "final": row.transcript_final,
            "live": row.transcript_live,
            "status": row.transcript_status,
            "source": row.transcript_source,
        },
        "question_id": row.question_id,
        "answer_id": row.answer_id,
        "audio_media_id": row.audio_media_id,
    }


def _eligibility_block(intake: IntakeInterview) -> dict[str, Any] | None:
    policy = (intake.settings or {}).get("work_authorization") or {}
    record = intake.eligibility or {}
    if not policy.get("enabled") and not record:
        return None
    return {
        "enabled": bool(policy.get("enabled")),
        "country": record.get("country") or policy.get("country"),
        "ask_sponsorship": bool(policy.get("ask_sponsorship", True)),
        "authorized": record.get("authorized"),
        "needs_sponsorship": record.get("needs_sponsorship"),
        "answered_at": record.get("answered_at"),
    }


async def _resume(db: AsyncSession, intake: IntakeInterview) -> dict[str, Any] | None:
    review = intake.resume_review or {}
    resume_id = (review.get("source") or {}).get("resume_id")
    resume = None
    if resume_id:
        try:
            resume = await db.get(CandidateResume, uuid.UUID(str(resume_id)))
        except ValueError:
            resume = None
    if resume is None:
        resume = await db.scalar(
            select(CandidateResume)
            .where(CandidateResume.candidate_id == intake.candidate_id)
            .order_by(CandidateResume.created_at.desc())
            .limit(1)
        )
    if resume is None or resume.candidate_id != intake.candidate_id:
        return None
    return {"id": resume.id, "original_filename": resume.original_filename, "status": resume.status}


async def _integrity(db: AsyncSession, session_id: uuid.UUID) -> dict[str, int]:
    reviewable = [a.value for a in REVIEWABLE_ACTIONS]
    row = (
        await db.execute(
            select(
                func.count().filter(IntegrityEvent.action.in_(reviewable)),
                func.count().filter(
                    IntegrityEvent.action.in_(reviewable),
                    IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
                ),
            ).where(IntegrityEvent.session_id == session_id)
        )
    ).one()
    return {"flags": int(row[0] or 0), "unreviewed": int(row[1] or 0)}


async def _recordings(db: AsyncSession, session_id: uuid.UUID) -> dict[str, Any]:
    parts = int(
        await db.scalar(
            select(func.count())
            .select_from(MediaAsset)
            .where(
                MediaAsset.session_id == session_id,
                MediaAsset.kind == MediaKind.SESSION_RECORDING.value,
                MediaAsset.deleted_at.is_(None),
            )
        )
        or 0
    )
    return {"parts": parts, "ready": parts > 0}


async def _report(db: AsyncSession, session_id: uuid.UUID) -> dict[str, Any] | None:
    report = await db.scalar(
        select(Report)
        .where(Report.session_id == session_id, Report.type == ReportType.RECRUITER.value)
        .order_by(Report.version.desc())
        .limit(1)
    )
    if report is None:
        return None
    media = await media_ids_for_keys(db, [report.artifact_storage_key])
    return {
        "version": report.version,
        "status": report.status,
        "artifact_media_id": media.get(report.artifact_storage_key or ""),
        "generated_at": report.generated_at,
    }


async def detail_view(
    db: AsyncSession, intake: IntakeInterview, *, kit: IntakeKit | None = None
) -> tuple[dict[str, Any], bool]:
    """``(IntakeDetailOut fields, eligibility_included)``."""
    kit = kit or load_intake_kit()
    summary = await get_summary(db, intake.organization_id, intake.id)
    payload: dict[str, Any] = summary.model_dump()
    # The summary's evaluation reference (newest of any status) stays available; ``evaluation`` becomes the full
    # newest ready evaluation in the detail (contract 15.13).
    payload["evaluation_ref"] = payload.get("evaluation")
    session = await session_for_intake(db, intake)
    eligibility = _eligibility_block(intake)
    payload.update(
        settings=dict(intake.settings or {}),
        onboarding=intake.onboarding,
        eligibility=eligibility,
        resume_review=intake.resume_review,
        resume=await _resume(db, intake),
        stages=[],
        activities=[],
        evaluation=None,
        review_items=[],
        decisions=await decisions_out(db, intake.id),
        report=None,
        integrity={"flags": 0, "unreviewed": 0},
        recordings={"parts": 0, "ready": False},
    )
    if session is not None:
        stages = (
            await db.scalars(
                select(IntakeStageResult)
                .where(IntakeStageResult.session_id == session.id)
                .order_by(IntakeStageResult.stage_number)
            )
        ).all()
        payload["stages"] = [
            {
                "key": s.stage_key,
                "number": s.stage_number,
                "title": _stage_title(kit, s.stage_key),
                "status": s.status,
                "started_s": s.started_elapsed_s,
                "ended_s": s.ended_elapsed_s,
                "budget_s": s.budget_s,
                "items": list(s.items or []),
                "extra": dict(s.extra or {}),
            }
            for s in stages
        ]
        activities = (
            await db.scalars(
                select(IntakeActivityResult).where(IntakeActivityResult.session_id == session.id)
            )
        ).all()
        order = {"reading_aloud": 0, "read_relay": 1, "topic_card": 2}
        payload["activities"] = [
            _activity_out(a, kit) for a in sorted(activities, key=lambda a: order.get(a.kind, 9))
        ]
        evaluation = await latest_ready_evaluation(db, session.id)
        if evaluation is not None:
            payload["evaluation"] = await evaluation_view(db, evaluation, kit=kit)
            payload["review_items"] = review_items_with_resolution(
                evaluation.review_items or [], payload["decisions"]
            )
        payload["report"] = await _report(db, session.id)
        payload["integrity"] = await _integrity(db, session.id)
        payload["recordings"] = await _recordings(db, session.id)
    return payload, eligibility is not None


def _stage_title(kit: IntakeKit, key: str) -> str:
    try:
        return kit.stage(key).title
    except KeyError:
        return key


# ============================================================================================== timeline


async def timeline_view(db: AsyncSession, intake: IntakeInterview, *, kit: IntakeKit | None = None) -> dict:
    """``IntakeTimelineOut``: stages -> items -> exchanges (question + answer with timing and audio id)."""
    kit = kit or load_intake_kit()
    session = await session_for_intake(db, intake)
    out: dict[str, Any] = {
        "intake_id": intake.id,
        "session_id": session.id if session else None,
        "stages": [],
    }
    if session is None:
        return out
    stage_rows = {
        row.stage_key: row
        for row in (
            await db.scalars(select(IntakeStageResult).where(IntakeStageResult.session_id == session.id))
        ).all()
    }
    questions = (
        await db.scalars(
            select(Question).where(Question.session_id == session.id).order_by(Question.sequence)
        )
    ).all()
    answers = (
        await db.scalars(select(Answer).where(Answer.session_id == session.id).order_by(Answer.sequence))
    ).all()
    activities = {
        row.answer_id: row
        for row in (
            await db.scalars(
                select(IntakeActivityResult).where(IntakeActivityResult.session_id == session.id)
            )
        ).all()
        if row.answer_id is not None
    }
    audio = await answer_audio_media(db, session.id)
    by_question: dict[uuid.UUID, list[Answer]] = {}
    for answer_row in answers:
        if answer_row.question_id is not None:
            by_question.setdefault(answer_row.question_id, []).append(answer_row)
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for question in questions:
        meta = (question.decision or {}).get("intake") or {}
        stage = meta.get("stage") or (
            question.segment[len(INTAKE_SEGMENT_PREFIX) :]
            if question.segment and question.segment.startswith(INTAKE_SEGMENT_PREFIX)
            else None
        )
        if not stage:
            continue
        item_key = meta.get("item_key") or f"{stage}.other"
        answer = next(iter(by_question.get(question.id) or []), None)
        activity = activities.get(answer.id) if answer is not None else None
        text = (
            (activity.transcript_final or activity.transcript_live or answer.transcript_text)
            if (activity is not None and answer is not None)
            else (answer.transcript_text if answer is not None else None)
        )
        grouped.setdefault(stage, {}).setdefault(item_key, []).append(
            {
                "question_id": question.id,
                "text": question.spoken_text or question.text,
                "asked_ms": int(round((question.elapsed_seconds or 0.0) * 1000)),
                "template": bool(meta.get("template", False)),
                "item_kind": meta.get("item_kind") or "prompt",
                "answer": {
                    "answer_id": answer.id,
                    "text": text or "",
                    "started_ms": answer.started_ms,
                    "ended_ms": answer.ended_ms,
                    "duration_ms": answer.duration_ms,
                    "typed": bool(answer.typed),
                    "audio_media_id": audio.get(answer.id),
                }
                if answer is not None
                else None,
            }
        )
    for stage in kit.stages:
        row = stage_rows.get(stage.key)
        items_meta = {str(i.get("item_key")): i for i in (row.items if row is not None else []) or []}
        exchanges = grouped.get(stage.key, {})
        keys = list(dict.fromkeys([*items_meta.keys(), *exchanges.keys()]))
        if row is None and not exchanges:
            continue
        items = []
        for key in keys:
            meta = items_meta.get(key, {})
            items.append(
                {
                    "item_key": key,
                    "title": meta.get("title") or key,
                    "mandatory": bool(meta.get("mandatory", False)),
                    "status": meta.get("status") or ("answered" if exchanges.get(key) else "asked"),
                    "skip_reason": meta.get("skip_reason"),
                    "exchanges": exchanges.get(key, []),
                }
            )
        out["stages"].append(
            {
                "key": stage.key,
                "number": stage.number,
                "title": stage.title,
                "status": row.status if row is not None else "pending",
                "items": items,
            }
        )
    return out


# ============================================================================================== re-evaluation


async def request_reevaluation(
    db: AsyncSession, principal: Principal, intake: IntakeInterview, *, request: Request | None = None
) -> dict[str, Any]:
    """Queue a new evaluation version (409 ``session_not_finished`` / ``evaluation_running``)."""
    session = await session_for_intake(db, intake)
    if session is None or session.status not in ENDED_STATUSES:
        raise Conflict(
            "The interview can be evaluated once it has ended.",
            code="session_not_finished",
            details={"status": session.status if session is not None else None},
        )
    if await running_evaluation(db, session.id) is not None:
        raise Conflict("An evaluation is already running.", code="evaluation_running")
    version = (
        int(
            await db.scalar(
                select(func.coalesce(func.max(IntakeEvaluation.version), 0)).where(
                    IntakeEvaluation.session_id == session.id
                )
            )
            or 0
        )
        + 1
    )
    job_id = await enqueue(
        EVALUATE_JOB,
        {"session_id": str(session.id), "reason": "reevaluate", "requested_by": str(principal.user_id)},
        dedupe_key=f"{EVALUATE_JOB}:{session.id}:v{version}",
        db=db,
    )
    await audit(
        db,
        principal,
        "intake.evaluate",
        resource_type="intake_interview",
        resource_id=str(intake.id),
        organization_id=intake.organization_id,
        request=request,
        details={"session_id": str(session.id), "evaluation_version": version, "job_id": job_id},
    )
    return {"evaluation_version": version, "job_id": uuid.UUID(job_id)}


async def interview_of(db: AsyncSession, intake: IntakeInterview) -> Interview | None:
    return await db.get(Interview, intake.interview_id) if intake.interview_id else None


__all__ = [
    "competency_out",
    "detail_view",
    "evaluation_for",
    "evaluation_view",
    "intake_for_principal",
    "interview_of",
    "request_reevaluation",
    "review_items_with_resolution",
    "session_for_intake",
    "timeline_view",
]
