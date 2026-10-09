"""Reviewer-facing "AI review summary" of one session (intake and normal rounds).

Deterministic facts come first and are always shown: how long the interview ran, how long the candidate
actually spoke, how many questions were answered vs skipped, the silence share and how much evidence that
produced. Strengths and weaknesses are taken from stored evidence (with the candidate's own words where a
quote exists). Model-written text is shown only when there is enough evidence to judge; it is never
generated here: it is the executive summary the report pipeline already wrote with the main model
(``reports.content.narrative``, only when ``narrative_source == "model"``) or, for intake rounds, the
strengths/improvements the intake evaluators wrote. When the evidence is too thin, the summary says so in
plain words and withholds any model text.

Timing columns are unreliable on some sessions (``start_ms == end_ms``); speaking time then falls back to
a words-per-second estimate and the facts carry ``speaking_time_estimated = true``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Answer,
    AnswerEvaluation,
    CompetencyEvidence,
    InterviewSession,
    Question,
    Report,
    Transcript,
)
from app.db.models.intake import IntakeCompetencyScore, IntakeEvaluation
from app.domain.enums import ReportStatus, ReportType, RoundType, ScoreCategory
from app.services.reports.queries import TERMINAL_STATUSES
from app.services.reports.wording import neutral
from app.services.scoring.persistence import humanize_key, latest_score_rows, parse_round_config

# Spoken English runs ~2.3-2.7 words per second; used only when the timing columns are missing.
CANDIDATE_WORDS_PER_SECOND = 2.5
INTERVIEWER_WORDS_PER_SECOND = 2.6
MIN_ANSWER_WORDS = 4  # fewer words than this is not an answer ("um", "okay", "yes")
THIN_MIN_ANSWERED = 3  # fewer answered questions than this is too little to judge
THIN_MIN_ANSWER_SECONDS = 120.0  # less than two minutes of candidate speech is too little to judge
MAX_POINTS = 3
SKIPPED_QUALITIES = frozenset({"no_answer"})
INSUFFICIENT_HEADLINE = "Insufficient evidence — interview too short / no answers"


def _is_intake(session: InterviewSession) -> bool:
    return str((session.round_config or {}).get("round_type") or "") == RoundType.INTAKE.value


def _words(text: str | None) -> int:
    return len((text or "").split())


def _span_ms(start: int | None, end: int | None) -> int | None:
    if start is None or end is None or end <= start:
        return None
    return int(end - start)


def _union_seconds(spans: list[tuple[int, int]]) -> float:
    total = 0
    cur_start: int | None = None
    cur_end = 0
    for start, end in sorted(spans):
        if cur_start is None or start > cur_end:
            if cur_start is not None:
                total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_start is not None:
        total += cur_end - cur_start
    return total / 1000.0


def _fmt_minutes(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.1f} min"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def _facts(db: AsyncSession, session: InterviewSession) -> dict[str, Any]:
    sid = session.id
    questions = list(
        (
            await db.scalars(select(Question).where(Question.session_id == sid).order_by(Question.sequence))
        ).all()
    )
    answers = list((await db.scalars(select(Answer).where(Answer.session_id == sid))).all())
    qualities = {
        row.answer_id: str(row.overall_quality or "")
        for row in (
            await db.execute(
                select(AnswerEvaluation.answer_id, AnswerEvaluation.overall_quality).where(
                    AnswerEvaluation.session_id == sid
                )
            )
        ).all()
    }
    transcripts = (
        await db.execute(
            select(Transcript.speaker, Transcript.text, Transcript.start_ms, Transcript.end_ms).where(
                Transcript.session_id == sid, Transcript.kind == "final"
            )
        )
    ).all()

    def substantive(answer: Answer) -> bool:
        words = answer.word_count or _words(answer.transcript_text)
        return words >= MIN_ANSWER_WORDS and qualities.get(answer.id) not in SKIPPED_QUALITIES

    by_question: dict[uuid.UUID, list[Answer]] = {}
    for answer in answers:
        if answer.question_id is not None:
            by_question.setdefault(answer.question_id, []).append(answer)
    asked = [q for q in questions if q.kind != "coding"]
    answered = [q for q in asked if any(substantive(a) for a in by_question.get(q.id, []))]
    unprompted = [a for a in answers if a.question_id is None and substantive(a)]

    candidate_words = sum(a.word_count or _words(a.transcript_text) for a in answers)
    candidate_spans: list[tuple[int, int]] = []
    interviewer_spans: list[tuple[int, int]] = []
    interviewer_words = 0
    transcript_candidate_words = 0
    for speaker, text, start, end in transcripts:
        span = (int(start), int(end)) if start is not None and end is not None and end > start else None
        if speaker == "candidate":
            transcript_candidate_words += _words(text)
            if span is not None:
                candidate_spans.append(span)
        elif speaker == "interviewer":
            interviewer_words += _words(text)
            if span is not None:
                interviewer_spans.append(span)
    candidate_words = max(candidate_words, transcript_candidate_words)

    answer_ms = sum(a.duration_ms or _span_ms(a.started_ms, a.ended_ms) or 0 for a in answers)
    candidate_seconds = max(answer_ms / 1000.0, _union_seconds(candidate_spans))
    estimated = False
    if candidate_seconds <= 0 and candidate_words > 0:
        candidate_seconds = candidate_words / CANDIDATE_WORDS_PER_SECOND
        estimated = True
    interviewer_seconds = _union_seconds(interviewer_spans)
    if interviewer_seconds <= 0 and interviewer_words > 0:
        interviewer_seconds = interviewer_words / INTERVIEWER_WORDS_PER_SECOND
        estimated = True

    duration = float(session.elapsed_seconds or 0.0)
    silence_share: float | None = None
    talk_share: float | None = None
    if duration > 0:
        spoken = (
            _union_seconds(candidate_spans + interviewer_spans)
            if not estimated
            else candidate_seconds + interviewer_seconds
        )
        silence_share = round(max(0.0, min(1.0, 1.0 - spoken / duration)), 3)
        talk_share = round(max(0.0, min(1.0, candidate_seconds / duration)), 3)
    config = parse_round_config(session.round_config)
    return {
        "duration_seconds": round(duration, 1),
        "planned_seconds": float(config.duration_minutes * 60),
        "end_reason": session.end_reason,
        "ended_early": bool(duration and duration < 0.5 * config.duration_minutes * 60),
        "questions_asked": len(asked),
        "questions_answered": len(answered),
        "questions_skipped": max(0, len(asked) - len(answered)),
        "unprompted_answers": len(unprompted),
        "candidate_words": int(candidate_words),
        "candidate_speaking_seconds": round(candidate_seconds, 1),
        "minutes_answered": round(candidate_seconds / 60.0, 2),
        "candidate_talk_share": talk_share,
        "silence_share": silence_share,
        "speaking_time_estimated": estimated,
    }


def _quote(ref: dict[str, Any] | None) -> str | None:
    quote = str((ref or {}).get("quote") or "").strip()
    return quote[:280] or None


async def _generic_points(
    db: AsyncSession, session: InterviewSession, names: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = list(
        (
            await db.scalars(
                select(CompetencyEvidence).where(
                    CompetencyEvidence.session_id == session.id,
                    CompetencyEvidence.signal.in_(["positive", "negative"]),
                    CompetencyEvidence.source != "hint_usage",
                )
            )
        ).all()
    )

    def points(signal: str) -> list[dict[str, Any]]:
        ranked = sorted(
            (r for r in rows if r.signal == signal),
            key=lambda r: (-(float(r.strength) * float(r.confidence)), r.elapsed_seconds or 0.0, str(r.id)),
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in ranked:
            if row.competency_key in seen:  # one point per competency keeps the list varied
                continue
            seen.add(row.competency_key)
            out.append(
                {
                    "competency_key": row.competency_key,
                    "competency": names.get(row.competency_key) or humanize_key(row.competency_key),
                    "text": neutral(row.evidence or ""),
                    "quote": _quote(row.transcript_ref),
                    "evidence_id": str(row.id),
                    "question_id": str(row.question_id) if row.question_id else None,
                    "answer_id": str(row.answer_id) if row.answer_id else None,
                    "start_ms": (row.transcript_ref or {}).get("start_ms"),
                }
            )
            if len(out) >= MAX_POINTS:
                break
        return out

    return points("positive"), points("negative")


async def _latest_intake_evaluation(db: AsyncSession, session_id: uuid.UUID) -> IntakeEvaluation | None:
    return await db.scalar(
        select(IntakeEvaluation)
        .where(IntakeEvaluation.session_id == session_id, IntakeEvaluation.status == "ready")
        .order_by(IntakeEvaluation.version.desc())
        .limit(1)
    )


async def _intake_points(
    db: AsyncSession, evaluation: IntakeEvaluation | None, names: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if evaluation is None:
        return [], []
    rows = list(
        (
            await db.scalars(
                select(IntakeCompetencyScore).where(
                    IntakeCompetencyScore.evaluation_id == evaluation.id,
                    IntakeCompetencyScore.status == "scored",
                )
            )
        ).all()
    )
    ranked = sorted(rows, key=lambda r: (-(r.score or 0), r.competency_key))

    def point(row: IntakeCompetencyScore, text: str, supports: str) -> dict[str, Any]:
        ref = next((e for e in row.evidence or [] if e.get("supports") == supports and e.get("quote")), None)
        ref = ref or next((e for e in row.evidence or [] if e.get("quote")), None)
        return {
            "competency_key": row.competency_key,
            "competency": names.get(row.competency_key) or humanize_key(row.competency_key),
            "text": neutral(text),
            "quote": _quote(ref),
            "evidence_id": None,
            "question_id": str(ref["question_id"]) if ref and ref.get("question_id") else None,
            "answer_id": str(ref["answer_id"]) if ref and ref.get("answer_id") else None,
            "start_ms": ref.get("start_ms") if ref else None,
        }

    strengths = [point(r, r.strengths[0], "positive") for r in ranked if r.strengths][:MAX_POINTS]
    weaknesses = [point(r, r.improvements[0], "negative") for r in reversed(ranked) if r.improvements][
        :MAX_POINTS
    ]
    return strengths, weaknesses


def _evidence_level(
    facts: dict[str, Any], *, assessed: int, overall_score: float | None, recommendation: str | None
) -> str:
    """none | thin | partial | sufficient (``thin`` = too little to judge)."""
    if assessed == 0 or overall_score is None:
        return "none"
    if (
        facts["questions_answered"] < THIN_MIN_ANSWERED
        or facts["candidate_speaking_seconds"] < THIN_MIN_ANSWER_SECONDS
    ):
        return "thin"
    if recommendation == "insufficient_evidence":
        return "partial"
    return "sufficient"


def _fact_statements(facts: dict[str, Any]) -> list[str]:
    out = [
        f"The interview ran {_fmt_minutes(facts['duration_seconds'])} of "
        f"{_fmt_minutes(facts['planned_seconds'])} planned"
        + (" and was ended by the candidate." if facts["end_reason"] == "candidate_ended" else ".")
    ]
    out.append(
        f"The candidate answered {facts['questions_answered']} of "
        f"{_plural(facts['questions_asked'], 'question')} asked"
        + (f" ({facts['questions_skipped']} skipped or unanswered)" if facts["questions_skipped"] else "")
        + f" and spoke for about {_fmt_minutes(facts['candidate_speaking_seconds'])}"
        + f" ({facts['candidate_words']} words)."
    )
    if facts["silence_share"] is not None:
        out.append(f"About {facts['silence_share']:.0%} of the interview time was silence.")
    return out


def _thin_statement(level: str, facts: dict[str, Any]) -> str | None:
    if level == "none":
        return (
            "There is no assessable evidence: no answer produced anything to score"
            + (" and the interview ended early." if facts["ended_early"] else ".")
            + " No score is given; the AI cannot judge this candidate from this session."
        )
    if level == "thin":
        return (
            "The evidence is too thin to judge: "
            f"{_plural(facts['questions_answered'], 'answered question')} and about "
            f"{_fmt_minutes(facts['candidate_speaking_seconds'])} of candidate speech. Treat any score as "
            "provisional and consider a follow-up interview."
        )
    if level == "partial":
        return (
            "Some competencies still lack enough evidence; the scores that exist are shown, the rest are not."
        )
    return None


async def _recruiter_narrative(db: AsyncSession, session_id: uuid.UUID) -> tuple[str | None, str | None]:
    row = (
        await db.execute(
            select(Report.content, Report.generated_at)
            .where(
                Report.session_id == session_id,
                Report.type == ReportType.RECRUITER.value,
                Report.status == ReportStatus.READY.value,
            )
            .order_by(Report.version.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    content = row.content or {}
    if content.get("kind") == "intake" or content.get("narrative_source") != "model":
        return None, None
    text = str((content.get("narrative") or {}).get("executive_summary") or "").strip()
    return (neutral(text) or None), (row.generated_at.isoformat() if row.generated_at else None)


def _intake_assessment(evaluation: IntakeEvaluation | None) -> str | None:
    if evaluation is None:
        return None
    summary = evaluation.summary or {}
    strengths = [neutral(s) for s in summary.get("strengths", []) if s][:3]
    improvements = [neutral(s) for s in summary.get("improvements", []) if s][:3]
    if not strengths and not improvements:
        return None
    parts = []
    if strengths:
        parts.append("Strengths the evaluators noted: " + "; ".join(s.rstrip(".") for s in strengths) + ".")
    if improvements:
        parts.append("Areas to develop: " + "; ".join(s.rstrip(".") for s in improvements) + ".")
    return " ".join(parts)


async def build_review_summary(db: AsyncSession, session: InterviewSession) -> dict[str, Any]:
    """The review summary of one session (JSON-ready; see module docstring)."""
    intake = _is_intake(session)
    completed = session.status in TERMINAL_STATUSES
    facts = await _facts(db, session)
    rows = await latest_score_rows(db, session.id)
    overall = next((r for r in rows if r.category == ScoreCategory.OVERALL.value), None)
    competency_rows = [r for r in rows if r.category == ScoreCategory.COMPETENCY.value]
    names = {r.key: r.label or humanize_key(r.key) for r in competency_rows}
    for comp in parse_round_config(session.round_config).competencies:
        names.setdefault(comp.key, comp.name or humanize_key(comp.key))
    assessed = sum(1 for r in competency_rows if r.score is not None)
    recommendation = str((overall.details or {}).get("recommendation") or "") if overall else None
    overall_score = overall.score if overall is not None else None

    evaluation = await _latest_intake_evaluation(db, session.id) if intake else None
    if intake:
        strengths, weaknesses = await _intake_points(db, evaluation, names)
    else:
        strengths, weaknesses = await _generic_points(db, session, names)

    level = _evidence_level(
        facts, assessed=assessed, overall_score=overall_score, recommendation=recommendation
    )
    if (
        intake
        and evaluation is not None
        and level == "sufficient"
        and evaluation.evidence_sufficiency == "partial"
    ):
        level = "partial"
    statements = _fact_statements(facts)
    thin = _thin_statement(level, facts)

    ai_text: str | None = None
    ai_source: str | None = None
    generated_at: str | None = None
    withheld: str | None = None
    if not completed:
        withheld = "The summary is written after the interview ends."
    elif level in ("none", "thin"):
        withheld = "Not enough evidence for an AI assessment."
    elif intake:
        ai_text = _intake_assessment(evaluation)
        ai_source = "intake_evaluation" if ai_text else None
        generated_at = evaluation.finished_at.isoformat() if evaluation and evaluation.finished_at else None
        if ai_text is None:
            withheld = "The intake evaluation has not written an assessment yet."
    else:
        ai_text, generated_at = await _recruiter_narrative(db, session.id)
        ai_source = "recruiter_report" if ai_text else None
        if ai_text is None:
            withheld = "The AI assessment appears once the recruiter report is ready."

    if level in ("none", "thin"):
        headline = INSUFFICIENT_HEADLINE
    elif level == "partial":
        headline = "Partial evidence — some competencies could not be judged"
    else:
        headline = "Enough evidence to review"
    if level in ("none", "thin"):
        # Strengths drawn from a sliver of evidence would read as a verdict: show what exists as weaknesses
        # only when it is evidence-backed, never invent strengths.
        strengths = strengths[:1]
    return {
        "session_id": session.id,
        "kind": "intake" if intake else "interview",
        "completed": completed,
        "evidence_level": level,
        "headline": headline,
        "insufficient": level in ("none", "thin"),
        "facts": facts,
        "statements": statements,
        "evidence_statement": thin,
        "assessed_competencies": assessed,
        "total_competencies": len(names),
        "overall_score": overall_score,
        "recommendation": recommendation or None,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "ai_assessment": {"text": ai_text, "source": ai_source, "generated_at": generated_at}
        if ai_text
        else None,
        "ai_withheld_reason": withheld,
        "note": "Facts are computed from the recording data; AI text is assistive. A human reviewer owns the decision.",
    }
