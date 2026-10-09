"""Read helpers other modules use (scoring, reports, analytics).

Counts are objective: events, occurrences (aggregated repeats), review state.
Nothing here is a judgement about the candidate.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IntegrityEvent
from app.domain.enums import IntegrityEventType, IntegrityReviewStatus, IntegritySeverity
from app.services.integrity.rules import FACE_CHECK_TYPES, REVIEWABLE_ACTIONS, SEVERITY_RANK, label_for

NOTICE = "Integrity signals are automated observations for human review, not determinations."
_REVIEWABLE = tuple(action.value for action in REVIEWABLE_ACTIONS)


async def count_unreviewed_flags(db: AsyncSession, session_id: uuid.UUID) -> int:
    """Events a human still has to look at (flagged / pause / terminate, unreviewed)."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(IntegrityEvent)
            .where(
                IntegrityEvent.session_id == session_id,
                IntegrityEvent.action.in_(_REVIEWABLE),
                IntegrityEvent.review_status == IntegrityReviewStatus.UNREVIEWED.value,
            )
        )
        or 0
    )


_FACE_COUNTERS = ("monitored_ms", "one_face_ms", "no_face_ms", "multi_face_ms", "static_faces", "frames")
_FACE_TYPE_VALUES = tuple(t.value for t in FACE_CHECK_TYPES)


def _interval_stats(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    episodes: dict[str, int] = {}
    confirmed = 0
    for row in rows:
        details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
        bucket = details.get("episodes") if isinstance(details.get("episodes"), Mapping) else {}
        if bucket:
            for key, value in bucket.items():
                if isinstance(value, int | float):
                    episodes[str(key)] = max(episodes.get(str(key), 0), int(value))
        else:
            episodes[str(row.get("id") or len(episodes))] = int(row.get("duration_ms") or 0)
        checks = details.get("server_checks") if isinstance(details.get("server_checks"), Mapping) else {}
        confirmed += sum(1 for status in checks.values() if status == "confirmed")
    return {
        "intervals": len(episodes),
        "total_ms": sum(episodes.values()),
        "longest_ms": max(episodes.values(), default=0),
        "confirmed_by_server": confirmed,
    }


def summarize_face_presence(events: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Face-presence summary from face-check event rows (``type``, ``details``,
    ``count``, ``duration_ms``): share of monitored time with exactly one face,
    intervals with no face / more than one face, motionless faces set aside.
    ``None`` when the face check produced nothing for the session."""
    rows = [e for e in events if str(e.get("type")) in _FACE_TYPE_VALUES]
    if not rows:
        return None
    runs: dict[str, dict[str, int]] = {}
    static_events = 0
    for row in rows:
        details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
        if row.get("type") == IntegrityEventType.FACE_CHECK_SUMMARY.value:
            for run, counters in (details.get("runs") or {}).items():
                if not isinstance(counters, Mapping):
                    continue
                merged = runs.setdefault(str(run), {})
                for name in _FACE_COUNTERS:
                    value = counters.get(name)
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        merged[name] = max(merged.get(name, 0), int(value))
        elif row.get("type") == IntegrityEventType.STATIC_FACE_IGNORED.value:
            static_events += int(row.get("count") or 1)
    totals = {name: sum(run.get(name, 0) for run in runs.values()) for name in _FACE_COUNTERS}
    monitored = totals["monitored_ms"]
    return {
        "monitored_ms": monitored,
        "one_face_ms": totals["one_face_ms"],
        "no_face_ms": totals["no_face_ms"],
        "multi_face_ms": totals["multi_face_ms"],
        "one_face_share": round(totals["one_face_ms"] / monitored, 4) if monitored > 0 else None,
        "no_face": _interval_stats(
            [r for r in rows if r.get("type") == IntegrityEventType.FACE_NOT_VISIBLE.value]
        ),
        "multiple_faces": _interval_stats(
            [r for r in rows if r.get("type") == IntegrityEventType.MULTIPLE_FACES_VISIBLE.value]
        ),
        "static_faces_ignored": max(static_events, totals["static_faces"]),
        "page_runs": len(runs),
    }


async def face_presence_summary(db: AsyncSession, session_id: uuid.UUID) -> dict[str, Any] | None:
    rows = (
        await db.execute(
            select(
                IntegrityEvent.id,
                IntegrityEvent.type,
                IntegrityEvent.details,
                IntegrityEvent.count,
                IntegrityEvent.duration_ms,
            ).where(IntegrityEvent.session_id == session_id, IntegrityEvent.type.in_(_FACE_TYPE_VALUES))
        )
    ).all()
    return summarize_face_presence(
        {
            "id": str(r.id),
            "type": r.type,
            "details": r.details,
            "count": r.count,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    )


async def integrity_summary(db: AsyncSession, session_id: uuid.UUID) -> dict[str, Any]:
    """Objective counts for the recruiter report (never shown to candidates)."""
    rows = (
        await db.execute(
            select(
                IntegrityEvent.type,
                IntegrityEvent.severity,
                IntegrityEvent.action,
                IntegrityEvent.review_status,
                func.count(),
                func.coalesce(func.sum(IntegrityEvent.count), 0),
            )
            .where(IntegrityEvent.session_id == session_id)
            .group_by(
                IntegrityEvent.type,
                IntegrityEvent.severity,
                IntegrityEvent.action,
                IntegrityEvent.review_status,
            )
        )
    ).all()
    by_type: dict[str, dict[str, Any]] = {}
    by_review: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    flagged = unreviewed = total = 0
    for event_type, severity, action, review_status, events, occurrences in rows:
        entry = by_type.setdefault(
            event_type,
            {
                "label": label_for(event_type),
                "events": 0,
                "occurrences": 0,
                "max_severity": IntegritySeverity.INFO.value,
            },
        )
        entry["events"] += int(events)
        entry["occurrences"] += int(occurrences)
        if (
            SEVERITY_RANK[IntegritySeverity(severity)]
            > SEVERITY_RANK[IntegritySeverity(entry["max_severity"])]
        ):
            entry["max_severity"] = severity
        by_review[review_status] = by_review.get(review_status, 0) + int(events)
        by_severity[severity] = by_severity.get(severity, 0) + int(events)
        total += int(events)
        if action in _REVIEWABLE:
            flagged += int(events)
            if review_status == IntegrityReviewStatus.UNREVIEWED.value:
                unreviewed += int(events)
    return {
        "notice": NOTICE,
        "total_events": total,
        "flagged_for_review": flagged,
        "unreviewed_flags": unreviewed,
        "by_type": dict(sorted(by_type.items())),
        "by_severity": by_severity,
        "by_review_status": by_review,
        "face_presence": await face_presence_summary(db, session_id),
    }
