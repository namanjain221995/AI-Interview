"""Recompute stored scores after the "no evidence = no score" fix (migration 0006).

    python -m app.services.scoring.recompute_cli            # dry run: what would change, writes nothing
    python -m app.services.scoring.recompute_cli --apply    # rewrite score rows

* Intake sessions with a ready evaluation: the generic ``scores`` rows are rebuilt from the newest ready
  intake evaluation (unscored competencies and an unbanded total become NULL; nothing is re-evaluated, no
  model call).
* Other finished sessions (completed / aborted / failed): ``recompute_session_scores(final=True)`` from
  the stored evidence (unassessed competencies, empty rubric categories and no-evidence overall scores
  become NULL; weak answers are no longer lifted by the neutral prior). No model call.

Stored report documents are not regenerated (that would call the model); the session views, lists and
analytics read the score rows. Requires migration 0006 (``scores.score`` nullable) and the new API code:
older code cannot read NULL scores. Prints ids and counts only.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.db.models import InterviewSession, Score
from app.db.models.intake import IntakeCompetencyScore, IntakeEvaluation
from app.db.session import session_scope
from app.domain.enums import RoundType, ScoreCategory
from app.services.scoring.persistence import (
    advisory_lock_key,
    compute_session_scores,
    latest_score_rows,
    recompute_session_scores,
)

FINISHED = ("completed", "aborted", "failed")


def _overall(rows: list[Score]) -> Score | None:
    return next((r for r in rows if r.category == ScoreCategory.OVERALL.value), None)


def _fmt(value: float | None) -> str:
    return "NULL" if value is None else f"{value:.1f}"


async def _intake(session: InterviewSession, apply: bool) -> str:
    from sqlalchemy import func

    from app.services.intake.pipeline import intake_generic_score_rows

    async with session_scope() as db:
        evaluation = await db.scalar(
            select(IntakeEvaluation)
            .where(IntakeEvaluation.session_id == session.id, IntakeEvaluation.status == "ready")
            .order_by(IntakeEvaluation.version.desc())
            .limit(1)
        )
        before = await latest_score_rows(db, session.id)
        if evaluation is None:
            return f"intake {session.id}: no ready evaluation (rows {len(before)}), skipped"
        comps = {
            r.competency_key: r
            for r in (
                await db.scalars(
                    select(IntakeCompetencyScore).where(IntakeCompetencyScore.evaluation_id == evaluation.id)
                )
            ).all()
        }
        rows = intake_generic_score_rows(session.id, evaluation, comps, datetime.now(UTC))
        old, new = _overall(before), _overall(rows)
        nulls = sum(1 for r in rows if r.score is None)
        line = (
            f"intake {session.id}: overall {_fmt(old.score if old else None)} -> {_fmt(new.score if new else None)}"
            f" ({evaluation.band}), {len(before)} -> {len(rows)} rows, {nulls} NULL"
        )
        if apply:
            await db.execute(
                select(func.pg_advisory_xact_lock(advisory_lock_key("intake.evaluate", session.id)))
            )
            await db.execute(delete(Score).where(Score.session_id == session.id))
            db.add_all(rows)
        return line


async def _generic(session: InterviewSession, apply: bool) -> str:
    async with session_scope() as db:
        before = await latest_score_rows(db, session.id)
        old = _overall(before)
        if apply:
            result = await recompute_session_scores(
                db, session.id, final=True, trigger="backfill_null_scores"
            )
            new_score, rec = result.rubric.overall_score, result.recommendation.recommendation.value
        else:
            computation = await compute_session_scores(db, session.id)
            new_score = computation.rubric.overall_score
            rec = computation.recommendation.recommendation.value
        return (
            f"session {session.id}: overall {_fmt(old.score if old else None)} -> {_fmt(new_score)} ({rec})"
        )


async def main(apply: bool) -> None:
    async with session_scope() as db:
        sessions = list(
            (
                await db.execute(
                    select(InterviewSession.id, InterviewSession.round_config["round_type"].astext).where(
                        InterviewSession.status.in_(FINISHED)
                    )
                )
            ).all()
        )
    done = failed = 0
    for sid, round_type in sessions:
        stub = InterviewSession(id=sid)
        try:
            if round_type == RoundType.INTAKE.value:
                print(await _intake(stub, apply))
            else:
                print(await _generic(stub, apply))
            done += 1
        except Exception as exc:  # noqa: BLE001 - one bad session must not stop the backfill
            failed += 1
            print(f"session {sid}: FAILED {type(exc).__name__}")
    print(f"{'applied' if apply else 'dry run'}: {done} sessions, {failed} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the recomputed rows (default: dry run)")
    asyncio.run(main(parser.parse_args().apply))
