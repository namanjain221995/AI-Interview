"""View builders for the recruiter-facing session APIs (JSON-ready dicts
validated by ``app.schemas.api.sessions``)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import sanitize_details
from app.db.models import (
    Answer,
    AnswerEvaluation,
    Candidate,
    CompetencyEvidence,
    IntegrityEvent,
    InterviewEvent,
    InterviewSession,
    Job,
    Question,
    Report,
    ReviewDecision,
    Round,
    Score,
    ScoreSnapshot,
)
from app.domain.enums import IntegrityReviewStatus, Recommendation, ReportType, ScoreCategory
from app.services.reports.queries import (
    LIVE_STATUSES,
    TERMINAL_STATUSES,
    answer_audio_media,
    elapsed_from_state,
    load_live_state,
    resolve_candidate_report_mode,
    review_count,
    state_digest,
)
from app.services.scoring.compute import HUMAN_DECISION_NOTE
from app.services.scoring.persistence import (
    FLAG_ACTIONS,
    build_score_rows,
    compute_session_scores,
    humanize_key,
    latest_score_rows,
    parse_round_config,
)
from app.services.scoring.timeline import TimelinePoint, bucket_timeline, competency_series

MAX_TIMELINE_EVENTS = 2000


def _recommendation(value: str | None) -> str | None:
    """A stored recommendation string when it is a valid ``Recommendation``."""
    try:
        return Recommendation(value).value if value else None
    except ValueError:
        return None


# Columns ``session_summaries`` reads: list queries load only these (the
# ``state``/``plan``/``round_config`` documents are tens of KB per session).
SUMMARY_COLUMNS = (
    InterviewSession.id,
    InterviewSession.interview_id,
    InterviewSession.organization_id,
    InterviewSession.candidate_id,
    InterviewSession.job_id,
    InterviewSession.round_id,
    InterviewSession.status,
    InterviewSession.started_at,
    InterviewSession.ended_at,
    InterviewSession.end_reason,
    InterviewSession.elapsed_seconds,
    InterviewSession.reconnect_count,
    InterviewSession.created_at,
    InterviewSession.updated_at,
)


async def session_summaries(db: AsyncSession, sessions: Sequence[InterviewSession]) -> list[dict[str, Any]]:
    """Summary dicts for a page of sessions: eight batched, column-only
    queries per page whatever its size (no N+1, no large JSON documents).
    Reads only :data:`SUMMARY_COLUMNS` of ``sessions``."""
    if not sessions:
        return []
    ids = [s.id for s in sessions]
    candidates = {
        row.id: row
        for row in (
            await db.execute(
                select(Candidate.id, Candidate.full_name, Candidate.email).where(
                    Candidate.id.in_({s.candidate_id for s in sessions})
                )
            )
        ).all()
    }
    jobs = {
        row.id: row
        for row in (
            await db.execute(select(Job.id, Job.title).where(Job.id.in_({s.job_id for s in sessions})))
        ).all()
    }
    rounds = {
        row.id: row
        for row in (
            await db.execute(
                select(Round.id, Round.name, Round.round_type).where(
                    Round.id.in_({s.round_id for s in sessions})
                )
            )
        ).all()
    }
    overall = {
        row.session_id: row
        for row in (
            await db.execute(
                select(
                    Score.session_id,
                    Score.score,
                    Score.confidence,
                    Score.details["recommendation"].astext.label("recommendation"),
                )
                .where(Score.session_id.in_(ids), Score.category == ScoreCategory.OVERALL.value)
                .order_by(Score.session_id, Score.created_at.desc())
                .distinct(Score.session_id)
            )
        ).all()
    }
    reports = {
        row.session_id: row
        for row in (
            await db.execute(
                select(Report.session_id, Report.status, Report.recommendation)
                .where(Report.session_id.in_(ids), Report.type == ReportType.RECRUITER.value)
                .order_by(Report.session_id, Report.version.desc())
                .distinct(Report.session_id)
            )
        ).all()
    }
    reviews = {
        row.session_id: row
        for row in (
            await db.execute(
                select(ReviewDecision.session_id, ReviewDecision.decision)
                .where(ReviewDecision.session_id.in_(ids))
                .order_by(ReviewDecision.session_id, ReviewDecision.created_at.desc())
                .distinct(ReviewDecision.session_id)
            )
        ).all()
    }
    flags = dict(
        (
            await db.execute(
                select(IntegrityEvent.session_id, func.count())
                .where(
                    IntegrityEvent.session_id.in_(ids),
                    IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
                    IntegrityEvent.action.in_(FLAG_ACTIONS),
                )
                .group_by(IntegrityEvent.session_id)
            )
        ).all()
    )
    question_counts = dict(
        (
            await db.execute(
                select(Question.session_id, func.count())
                .where(Question.session_id.in_(ids))
                .group_by(Question.session_id)
            )
        ).all()
    )
    out = []
    for s in sessions:
        cand = candidates.get(s.candidate_id)
        job = jobs.get(s.job_id)
        rnd = rounds.get(s.round_id)
        score = overall.get(s.id)
        report = reports.get(s.id)
        review = reviews.get(s.id)
        out.append(
            {
                "id": s.id,
                "interview_id": s.interview_id,
                "organization_id": s.organization_id,
                "candidate_id": s.candidate_id,
                "job_id": s.job_id,
                "round_id": s.round_id,
                "status": s.status,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "end_reason": s.end_reason,
                "elapsed_seconds": float(s.elapsed_seconds or 0.0),
                "reconnect_count": s.reconnect_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "candidate": {"id": cand.id, "full_name": cand.full_name, "email": cand.email}
                if cand
                else None,
                "job": {"id": job.id, "title": job.title} if job else None,
                "round": {"id": rnd.id, "name": rnd.name, "round_type": rnd.round_type} if rnd else None,
                "overall_score": round(score.score, 2) if score else None,
                "confidence": round(score.confidence, 3) if score else None,
                "recommendation": (report.recommendation if report and report.recommendation else None)
                or _recommendation(score.recommendation if score else None),
                "report_status": report.status if report else None,
                "review_decision": review.decision if review else None,
                "integrity_flags": int(flags.get(s.id, 0)),
                "question_count": int(question_counts.get(s.id, 0)),
                "live": s.status in LIVE_STATUSES,
            }
        )
    return out


async def session_detail(db: AsyncSession, session: InterviewSession) -> dict[str, Any]:
    [summary] = await session_summaries(db, [session])
    config = parse_round_config(session.round_config)
    state = await load_live_state(session)
    digest = state_digest(state, session)
    elapsed = (
        elapsed_from_state(state, session)
        if state and session.status not in TERMINAL_STATUSES
        else float(session.elapsed_seconds or 0.0)
    )
    now = datetime.now(UTC)
    wall = None
    if session.started_at is not None:
        wall = max(0.0, ((session.ended_at or now) - session.started_at).total_seconds())
    scheduled = float(config.duration_minutes * 60)
    rows = await latest_score_rows(db, session.id)
    overall = next((r for r in rows if r.category == ScoreCategory.OVERALL.value), None)
    reports = {}
    for row in (
        await db.execute(
            select(
                Report.type,
                Report.id,
                Report.version,
                Report.status,
                Report.generated_at,
                Report.released_to_candidate,
            )
            .where(
                Report.session_id == session.id,
                Report.type.in_([ReportType.CANDIDATE.value, ReportType.RECRUITER.value]),
            )
            .order_by(Report.type, Report.version.desc())
            .distinct(Report.type)
        )
    ).all():
        reports[row.type] = {
            "id": row.id,
            "version": row.version,
            "status": row.status,
            "generated_at": row.generated_at,
            "released_to_candidate": row.released_to_candidate,
        }
    summary.update(
        {
            "round_config": session.round_config or {},
            "plan": session.plan,
            "state_digest": digest,
            "client_info": session.client_info or {},
            "error": session.error,
            "durations": {
                "elapsed_seconds": round(elapsed, 1),
                "wall_seconds": round(wall, 1) if wall is not None else None,
                "paused_seconds": round(max(0.0, wall - elapsed), 1) if wall is not None else None,
                "scheduled_seconds": scheduled,
                "max_seconds": float(config.max_duration_minutes * 60),
                "remaining_seconds": round(max(0.0, scheduled - elapsed), 1)
                if session.status not in TERMINAL_STATUSES
                else None,
            },
            "recommendation_rationale": (overall.details or {}).get("rationale") if overall else None,
            "human_decision_note": ((overall.details or {}).get("human_decision_note") if overall else None)
            or HUMAN_DECISION_NOTE,
            "scores_final": bool(overall.is_final) if overall else False,
            "scores_computed_at": overall.created_at if overall else None,
            "rubric": [
                {
                    "key": r.key,
                    "label": r.label,
                    "weight": r.weight,
                    "score": r.score,
                    "confidence": r.confidence,
                }
                for r in rows
                if r.category == ScoreCategory.RUBRIC_CATEGORY.value
            ],
            "reports": reports,
            "candidate_report_mode": (await resolve_candidate_report_mode(db, session)).value,
            "claims": (digest or {}).get("claims", []),
            "contradictions": (digest or {}).get("contradictions", []),
            "review_count": await review_count(db, session.id),
        }
    )
    if overall is not None:
        summary["overall_score"] = round(overall.score, 2)
        summary["confidence"] = round(overall.confidence, 3)
    return summary


# ------------------------------------------------------------------ evidence


def evidence_dict(row: CompetencyEvidence) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "competency_key": row.competency_key,
        "question_id": row.question_id,
        "answer_id": row.answer_id,
        "evaluation_id": row.evaluation_id,
        "coding_session_id": row.coding_session_id,
        "source": row.source,
        "signal": row.signal,
        "dimension": row.dimension,
        "evidence": row.evidence,
        "strength": row.strength,
        "confidence": row.confidence,
        "difficulty": row.difficulty,
        "elapsed_seconds": row.elapsed_seconds,
        "transcript_ref": row.transcript_ref or {},
        "created_at": row.created_at,
    }


# ---------------------------------------------------------------- questions


async def questions_view(db: AsyncSession, session: InterviewSession) -> list[dict[str, Any]]:
    sid = session.id
    questions = list(
        (
            await db.scalars(select(Question).where(Question.session_id == sid).order_by(Question.sequence))
        ).all()
    )
    answers = list(
        (await db.scalars(select(Answer).where(Answer.session_id == sid).order_by(Answer.sequence))).all()
    )
    evaluations = {
        e.answer_id: e
        for e in (await db.scalars(select(AnswerEvaluation).where(AnswerEvaluation.session_id == sid))).all()
    }
    evidence = list(
        (
            await db.scalars(
                select(CompetencyEvidence)
                .where(CompetencyEvidence.session_id == sid)
                .order_by(CompetencyEvidence.created_at, CompetencyEvidence.id)
            )
        ).all()
    )
    audio = await answer_audio_media(db, sid)
    evidence_by_answer: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in evidence:
        if row.answer_id is not None:
            evidence_by_answer.setdefault(row.answer_id, []).append(evidence_dict(row))
    answers_by_question: dict[uuid.UUID | None, list[Answer]] = {}
    for answer in answers:
        answers_by_question.setdefault(answer.question_id, []).append(answer)
    by_id = {q.id: q for q in questions}
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for q in questions:
        if q.parent_question_id in by_id:
            children.setdefault(q.parent_question_id, []).append(q.id)

    def chain(q: Question) -> tuple[uuid.UUID, int]:
        depth, current, seen = 0, q, {q.id}
        while current.parent_question_id in by_id and current.parent_question_id not in seen:
            current = by_id[current.parent_question_id]
            seen.add(current.id)
            depth += 1
        return current.id, depth

    out = []
    for q in questions:
        root, depth = chain(q)
        q_answers = []
        for a in answers_by_question.get(q.id, []):
            ev = evaluations.get(a.id)
            q_answers.append(
                {
                    "id": a.id,
                    "question_id": a.question_id,
                    "sequence": a.sequence,
                    "transcript_text": a.transcript_text,
                    "typed": a.typed,
                    "started_ms": a.started_ms,
                    "ended_ms": a.ended_ms,
                    "duration_ms": a.duration_ms,
                    "word_count": a.word_count,
                    "stt_confidence": a.stt_confidence,
                    "interrupted_ai": a.interrupted_ai,
                    "status": a.status,
                    "audio_media_id": audio.get(a.id),
                    "evaluation": ev,
                    "evidence": evidence_by_answer.get(a.id, []),
                }
            )
        out.append(
            {
                **{
                    c: getattr(q, c)
                    for c in (
                        "id",
                        "session_id",
                        "sequence",
                        "parent_question_id",
                        "bank_question_id",
                        "coding_question_id",
                        "kind",
                        "action",
                        "source",
                        "segment",
                        "topic",
                        "difficulty",
                        "text",
                        "spoken_text",
                        "question_focus",
                        "reason",
                        "claim_id",
                        "interrupted",
                        "asked_at",
                    )
                },
                "competency_keys": list(q.competency_keys or []),
                "expected_evidence": list(q.expected_evidence or []),
                "elapsed_seconds": float(q.elapsed_seconds or 0.0),
                "followup_ids": children.get(q.id, []),
                "followup_depth": depth,
                "chain_root_id": root if depth else None,
                "answers": q_answers,
            }
        )
    # Answers with no question (e.g. the candidate spoke before the first question).
    orphans = answers_by_question.get(None, [])
    if orphans:
        for a in orphans:
            out.append(
                {
                    "id": a.id,
                    "session_id": sid,
                    "sequence": 0,
                    "parent_question_id": None,
                    "bank_question_id": None,
                    "coding_question_id": None,
                    "kind": "intro",
                    "action": "acknowledge_and_wait",
                    "source": "generated",
                    "segment": None,
                    "topic": None,
                    "competency_keys": [],
                    "difficulty": "medium",
                    "text": "(answer without a recorded question)",
                    "spoken_text": None,
                    "question_focus": None,
                    "reason": None,
                    "expected_evidence": [],
                    "claim_id": None,
                    "interrupted": False,
                    "asked_at": a.created_at,
                    "elapsed_seconds": (a.started_ms or 0) / 1000.0,
                    "followup_ids": [],
                    "followup_depth": 0,
                    "chain_root_id": None,
                    "answers": [
                        {
                            "id": a.id,
                            "question_id": None,
                            "sequence": a.sequence,
                            "transcript_text": a.transcript_text,
                            "typed": a.typed,
                            "started_ms": a.started_ms,
                            "ended_ms": a.ended_ms,
                            "duration_ms": a.duration_ms,
                            "word_count": a.word_count,
                            "stt_confidence": a.stt_confidence,
                            "interrupted_ai": a.interrupted_ai,
                            "status": a.status,
                            "audio_media_id": audio.get(a.id),
                            "evaluation": evaluations.get(a.id),
                            "evidence": evidence_by_answer.get(a.id, []),
                        }
                    ],
                }
            )
    return out


# ------------------------------------------------------------------- scores


def _score_dict(row: Score) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "category": row.category,
        "key": row.key,
        "label": row.label,
        "score": row.score,
        "confidence": row.confidence,
        "weight": row.weight,
        "evidence_count": row.evidence_count,
        "positive_count": row.positive_count,
        "negative_count": row.negative_count,
        "sufficient_evidence": row.sufficient_evidence,
        "unanswered_areas": list(row.unanswered_areas or []),
        "supporting_question_ids": list(row.supporting_question_ids or []),
        "positive_evidence_ids": list(row.positive_evidence_ids or []),
        "negative_evidence_ids": list(row.negative_evidence_ids or []),
        "transcript_refs": list(row.transcript_refs or []),
        "details": dict(row.details or {}),
        "is_final": row.is_final,
        "created_at": row.created_at,
    }


async def scores_view(db: AsyncSession, session: InterviewSession) -> dict[str, Any]:
    """Persisted scores with the "why" of each competency; when nothing was
    persisted yet (live session), computed on the fly without writing."""
    rows = await latest_score_rows(db, session.id)
    source = "persisted"
    if not rows:
        computation = await compute_session_scores(db, session.id)
        rows = build_score_rows(computation, final=False, created_at=datetime.now(UTC), trigger="live_view")
        source = "live"
    evidence = {
        str(e.id): e
        for e in (
            await db.scalars(select(CompetencyEvidence).where(CompetencyEvidence.session_id == session.id))
        ).all()
    }
    questions = {
        str(q.id): q
        for q in (await db.scalars(select(Question).where(Question.session_id == session.id))).all()
    }
    overall = next((r for r in rows if r.category == ScoreCategory.OVERALL.value), None)
    competencies = []
    for row in rows:
        if row.category != ScoreCategory.COMPETENCY.value:
            continue
        data = _score_dict(row)
        details = data["details"]
        data["why"] = {
            "positive_evidence": [
                evidence_dict(evidence[i]) for i in data["positive_evidence_ids"] if i in evidence
            ],
            "negative_evidence": [
                evidence_dict(evidence[i]) for i in data["negative_evidence_ids"] if i in evidence
            ],
            "unanswered_areas": data["unanswered_areas"],
            "supporting_questions": [
                {
                    "id": questions[i].id,
                    "sequence": questions[i].sequence,
                    "text": questions[i].spoken_text or questions[i].text,
                    "focus": questions[i].question_focus,
                    "difficulty": questions[i].difficulty,
                }
                for i in data["supporting_question_ids"]
                if i in questions
            ],
            "transcript_refs": data["transcript_refs"],
            "calculation": details.get("calculation"),
            "observations": details.get("observations") or [],
        }
        competencies.append(data)
    competencies.sort(
        key=lambda c: (not (c["details"] or {}).get("required", True), -(c["weight"] or 0), c["key"])
    )
    od = (overall.details or {}) if overall is not None else {}
    return {
        "session_id": session.id,
        "source": source,
        "is_final": bool(overall.is_final) if overall is not None else False,
        "computed_at": overall.created_at if overall is not None else None,
        "overall": _score_dict(overall) if overall is not None else None,
        "rubric": [_score_dict(r) for r in rows if r.category == ScoreCategory.RUBRIC_CATEGORY.value],
        "competencies": competencies,
        "recommendation": od.get("recommendation"),
        "rationale": od.get("rationale"),
        "factors": od.get("factors") or {},
        "human_decision_note": od.get("human_decision_note") or HUMAN_DECISION_NOTE,
    }


# ----------------------------------------------------------------- timeline


async def timeline_view(
    db: AsyncSession, session: InterviewSession, *, window_minutes: float
) -> dict[str, Any]:
    snapshots = list(
        (
            await db.scalars(
                select(ScoreSnapshot)
                .where(ScoreSnapshot.session_id == session.id)
                .order_by(ScoreSnapshot.elapsed_seconds, ScoreSnapshot.created_at)
            )
        ).all()
    )
    names: dict[str, str] = {}
    computation = None
    if snapshots:
        points = [
            TimelinePoint(
                s.elapsed_seconds, s.competency_key, s.score, s.confidence, s.evidence_count, s.trigger
            )
            for s in snapshots
        ]
        source = "snapshots"
    else:
        computation = await compute_session_scores(db, session.id)
        points = [
            TimelinePoint(p.elapsed_s, key, p.score, p.confidence, p.evidence_count, "replay")
            for key, replay_points in computation.replays.items()
            for p in replay_points
        ]
        source = "replay"
    config = parse_round_config(session.round_config)
    for comp in config.competencies:
        names[comp.key] = comp.name or humanize_key(comp.key)
    if computation is not None:
        names.update({k: s.name or names.get(k, k) for k, s in computation.states.items()})
    else:
        rows = await latest_score_rows(db, session.id)
        names.update({r.key: r.label or r.key for r in rows if r.category == ScoreCategory.COMPETENCY.value})
    duration = float(session.elapsed_seconds or 0.0)
    if session.status not in TERMINAL_STATUSES:
        duration = max(duration, elapsed_from_state(await load_live_state(session), session))
    events = list(
        (
            await db.scalars(
                select(InterviewEvent)
                .where(InterviewEvent.session_id == session.id)
                .order_by(InterviewEvent.seq)
                .limit(MAX_TIMELINE_EVENTS)
            )
        ).all()
    )
    keys = list(dict.fromkeys([c.key for c in config.competencies] + [p.competency_key for p in points]))
    return {
        "session_id": session.id,
        "window_minutes": window_minutes,
        "duration_seconds": duration,
        "source": source,
        "competencies": [{"key": k, "name": names.get(k, k)} for k in keys],
        "windows": bucket_timeline(
            points, window_minutes=window_minutes, duration_seconds=duration, names=names
        ),
        "snapshots": [
            {
                "elapsed_seconds": p.elapsed_seconds,
                "competency_key": p.competency_key,
                "score": p.score,
                "confidence": p.confidence,
                "evidence_count": p.evidence_count,
                "trigger": p.trigger,
            }
            for p in sorted(points, key=lambda p: (p.elapsed_seconds, p.competency_key))
        ],
        "series": competency_series(points),
        "events": [
            {
                "seq": e.seq,
                "type": e.type,
                "elapsed_seconds": e.elapsed_seconds,
                "payload": sanitize_details(e.payload),
                "created_at": e.created_at,
            }
            for e in events
        ],
    }
