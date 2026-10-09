"""pgvector cosine search over any model with an ``embedding`` column."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import ColumnElement, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.logging import get_logger

log = get_logger(__name__)

Filters = Mapping[str, Any] | Sequence[ColumnElement[bool]] | None

# None = unknown, probed once per process with a savepoint.
_iterative_scan_supported: bool | None = None


@dataclass(slots=True)
class VectorHit[M]:
    item: M
    distance: float  # pgvector cosine distance (0 = identical direction, 2 = opposite)

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom > 0 else 0.0


def build_filters(model: type[Any], filters: Filters) -> list[ColumnElement[bool]]:
    """``{"column": value}`` (``None`` -> IS NULL, list/tuple/set -> IN) or
    ready-made SQLAlchemy boolean expressions."""
    if filters is None:
        return []
    if isinstance(filters, Mapping):
        clauses: list[ColumnElement[bool]] = []
        for name, value in filters.items():
            column = getattr(model, name, None)
            if column is None:
                raise ValueError(f"{model.__name__} has no column {name!r}")
            if value is None:
                clauses.append(column.is_(None))
            elif isinstance(value, list | tuple | set | frozenset):
                clauses.append(column.in_(list(value)))
            else:
                clauses.append(column == value)
        return clauses
    return list(filters)


async def _enable_iterative_scan(db: AsyncSession) -> None:
    """Filtered HNSW scans otherwise stop after ``ef_search`` candidates and
    can return fewer rows than requested (pgvector >= 0.8 keeps scanning)."""
    global _iterative_scan_supported
    if _iterative_scan_supported is False:
        return
    statement = text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")
    if _iterative_scan_supported:
        await db.execute(statement)
        return
    try:
        async with db.begin_nested():
            await db.execute(statement)
        _iterative_scan_supported = True
    except Exception as exc:  # noqa: BLE001 - older pgvector: fall back to plain scans
        _iterative_scan_supported = False
        log.info("retrieval.iterative_scan_unavailable", error=f"{type(exc).__name__}"[:100])


async def vector_search[M](
    db: AsyncSession,
    model: type[M],
    query_vector: Sequence[float],
    *,
    filters: Filters = None,
    limit: int = 10,
    max_distance: float | None = None,
    column: str = "embedding",
    ef_search: int | None = None,
) -> list[VectorHit[M]]:
    """Nearest rows by cosine distance (rows without an embedding are
    skipped), closest first."""
    if limit <= 0:
        return []
    vector_column = getattr(model, column, None)
    if vector_column is None:
        raise ValueError(f"{model.__name__} has no vector column {column!r}")
    vector = [float(x) for x in query_vector]
    distance = vector_column.cosine_distance(vector)
    clauses = build_filters(model, filters)
    stmt = select(model, distance.label("distance")).where(vector_column.is_not(None), *clauses)
    if max_distance is not None:
        stmt = stmt.where(distance <= max_distance)
    stmt = stmt.order_by(distance).limit(limit)
    if clauses:
        await _enable_iterative_scan(db)
    if ef_search:
        await db.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"), {"value": str(int(ef_search))}
        )
    with metrics.DB_QUERY_SECONDS.labels(operation="vector_search").time():
        rows = (await db.execute(stmt)).all()
    hits = [VectorHit(item=row[0], distance=float(row[1])) for row in rows]
    hits.sort(key=lambda hit: hit.distance)  # relaxed_order may return near-sorted rows
    return hits
