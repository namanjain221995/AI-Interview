"""Performance timeline bucketing (pure).

Score snapshots are bucketed into fixed windows (default 10 minutes); each
window keeps the *last* value of every competency observed inside it, which
gives charts and summaries such as "Minute 0-10: AWS 7.2, Kubernetes 6.5".
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_WINDOW_MINUTES = 10
MAX_WINDOWS = 500


@dataclass(slots=True, frozen=True)
class TimelinePoint:
    elapsed_seconds: float
    competency_key: str
    score: float
    confidence: float
    evidence_count: int = 0
    trigger: str | None = None


def _fmt_minutes(value: float) -> str:
    return f"{value:g}"


def window_label(start_s: float, end_s: float) -> str:
    return f"Minute {_fmt_minutes(start_s / 60)}-{_fmt_minutes(end_s / 60)}"


def bucket_timeline(
    points: Iterable[TimelinePoint],
    *,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    duration_seconds: float | None = None,
    names: Mapping[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Windows ``[start, end)`` covering the session with the last value per
    competency in each window (windows without any point are kept, empty,
    so a chart's x axis stays continuous)."""
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")
    names = names or {}
    width = float(window_minutes) * 60.0
    ordered = sorted(points, key=lambda p: (p.elapsed_seconds, p.competency_key))
    horizon = max([duration_seconds or 0.0] + [p.elapsed_seconds for p in ordered])
    count = max(1, math.ceil(horizon / width)) if horizon > 0 else 1
    if ordered and ordered[-1].elapsed_seconds >= count * width:
        count += 1  # a point exactly on the last boundary opens the next window
    count = min(count, MAX_WINDOWS)
    windows: list[dict[str, Any]] = []
    for index in range(count):
        start, end = index * width, (index + 1) * width
        windows.append(
            {
                "index": index,
                "start_seconds": start,
                "end_seconds": end,
                "label": window_label(start, end),
                "scores": {},
                "details": {},
                "summary": "",
            }
        )
    for point in ordered:
        index = min(int(point.elapsed_seconds // width), count - 1)
        window = windows[index]
        window["scores"][point.competency_key] = round(point.score, 2)
        window["details"][point.competency_key] = {
            "score": round(point.score, 2),
            "confidence": round(point.confidence, 3),
            "evidence_count": point.evidence_count,
            "elapsed_seconds": round(point.elapsed_seconds, 1),
        }
    for window in windows:
        if window["scores"]:
            parts = [
                f"{names.get(key) or key} {score:.1f}"
                for key, score in sorted(window["scores"].items(), key=lambda kv: kv[0])
            ]
            window["summary"] = f"{window['label']}: " + ", ".join(parts)
    return windows


def competency_series(points: Iterable[TimelinePoint]) -> dict[str, list[dict[str, Any]]]:
    """Raw points grouped per competency, in time order."""
    series: dict[str, list[dict[str, Any]]] = {}
    for point in sorted(points, key=lambda p: (p.elapsed_seconds, p.competency_key)):
        series.setdefault(point.competency_key, []).append(
            {
                "elapsed_seconds": round(point.elapsed_seconds, 1),
                "score": round(point.score, 2),
                "confidence": round(point.confidence, 3),
                "evidence_count": point.evidence_count,
            }
        )
    return series
