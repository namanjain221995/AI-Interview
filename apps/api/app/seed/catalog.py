"""Idempotent seeding of the global catalog and the demo jobs.

* :func:`seed_catalog` upserts competencies (by key), default interview
  templates (one per round type), the curated question bank (by
  ``external_key``) and curated coding questions (by ``slug``). Every record is
  validated by :mod:`app.seed.loaders` before anything is written.
* :func:`load_demo_jobs` creates the demo jobs of ``samples/jobs`` with their
  requirements and rounds for one organization.

Both functions only ``flush``: the caller owns the transaction (``get_db`` or
``session_scope`` commits). A PostgreSQL transaction-level advisory lock
serialises concurrent seeders, because the ``(organization_id, key)`` unique
constraints do not cover rows whose ``organization_id`` is NULL.

Scale: existing rows are loaded once per entity (by key, without decoding
their vectors), new rows are bulk-inserted in chunks and only changed rows
are updated; 10k questions + 1k coding problems seed in a few seconds.

Embeddings are not computed here. Rows that are new, whose embedded text
changed (their stale embedding is cleared) or that were never embedded are
reported in :class:`CatalogSeedResult`; after committing, the caller enqueues
the jobs from :meth:`CatalogSeedResult.embedding_jobs`::

    async with session_scope() as db:
        result = await seed_catalog(db)
    for job in result.embedding_jobs():
        await enqueue(job.job_type, job.payload, dedupe_key=job.dedupe_key)
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlalchemy.sql.elements import ColumnElement

from app.core.logging import get_logger
from app.db.models import (
    BankQuestion,
    CodingQuestion,
    Competency,
    InterviewTemplate,
    Job,
    JobRequirement,
    Round,
)
from app.domain.enums import BankStatus, RoundType
from app.seed.loaders import JobSeed, SeedCatalog, load_catalog, load_round_templates, resolve_job_rounds

log = get_logger(__name__)

#: pg_advisory_xact_lock key shared by every catalog/demo seeder.
SEED_LOCK_KEY = 0x5EED_CA7A
EMBED_JOB_TYPE = "question_bank.embed"
EMBED_BATCH_SIZE = 64
#: Rows per bulk INSERT statement.
INSERT_CHUNK = 1000

# Columns whose change makes a stored embedding stale.
_COMPETENCY_EMBED_FIELDS = ("name", "description", "rubric")
_QUESTION_EMBED_FIELDS = ("topic", "text", "expected_concepts")
_CODING_EMBED_FIELDS = ("title", "prompt_markdown", "expected_concepts")


class SeedDependencyError(RuntimeError):
    """Raised when demo data references catalog rows that do not exist."""


@dataclass(slots=True)
class EntityCounts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "retired": self.retired,
        }


@dataclass(frozen=True, slots=True)
class EmbeddingJob:
    job_type: str
    payload: dict[str, Any]
    dedupe_key: str


@dataclass(slots=True)
class CatalogSeedResult:
    organization_id: uuid.UUID | None
    competencies: EntityCounts = field(default_factory=EntityCounts)
    templates: EntityCounts = field(default_factory=EntityCounts)
    questions: EntityCounts = field(default_factory=EntityCounts)
    coding_questions: EntityCounts = field(default_factory=EntityCounts)
    question_ids_needing_embedding: list[uuid.UUID] = field(default_factory=list)
    coding_question_ids_needing_embedding: list[uuid.UUID] = field(default_factory=list)
    competency_ids_needing_embedding: list[uuid.UUID] = field(default_factory=list)
    duration_ms: float = 0.0

    def embedding_jobs(self, batch_size: int = EMBED_BATCH_SIZE) -> list[EmbeddingJob]:
        """``question_bank.embed`` jobs (CONTRACTS.md section 5) for the bank
        questions that need an embedding, in batches. Enqueue after commit."""
        ids = sorted(str(i) for i in self.question_ids_needing_embedding)
        jobs = []
        for start in range(0, len(ids), batch_size):
            batch = ids[start : start + batch_size]
            digest = hashlib.sha256(",".join(batch).encode()).hexdigest()[:16]
            jobs.append(
                EmbeddingJob(EMBED_JOB_TYPE, {"question_ids": batch}, f"{EMBED_JOB_TYPE}:seed:{digest}")
            )
        return jobs

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "competencies": self.competencies.as_dict(),
            "templates": self.templates.as_dict(),
            "questions": self.questions.as_dict(),
            "coding_questions": self.coding_questions.as_dict(),
            "questions_needing_embedding": len(self.question_ids_needing_embedding),
            "coding_questions_needing_embedding": len(self.coding_question_ids_needing_embedding),
            "competencies_needing_embedding": len(self.competency_ids_needing_embedding),
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass(slots=True)
class DemoJobsResult:
    organization_id: uuid.UUID
    created: dict[str, uuid.UUID] = field(default_factory=dict)  # job key -> id
    existing: dict[str, uuid.UUID] = field(default_factory=dict)
    rounds_created: int = 0
    requirements_created: int = 0
    missing_templates: list[str] = field(default_factory=list)

    @property
    def job_ids(self) -> dict[str, uuid.UUID]:
        return {**self.existing, **self.created}

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "created": {k: str(v) for k, v in self.created.items()},
            "existing": {k: str(v) for k, v in self.existing.items()},
            "rounds_created": self.rounds_created,
            "requirements_created": self.requirements_created,
            "missing_templates": self.missing_templates,
        }


# =============================================================================
# Helpers
# =============================================================================


async def _acquire_seed_lock(db: AsyncSession) -> None:
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SEED_LOCK_KEY})


def _org_filter(column: Any, organization_id: uuid.UUID | None) -> ColumnElement[bool]:
    return column.is_(None) if organization_id is None else column == organization_id


def _apply(obj: Any, row: dict[str, Any]) -> list[str]:
    """Set changed attributes on ``obj``; returns the names of changed columns."""
    changed = []
    for name, value in row.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed.append(name)
    return changed


def _needs_embedding(obj: Any, changed: Iterable[str], embed_fields: Sequence[str], missing: bool) -> bool:
    """Whether an existing row needs (re-)embedding; clears a stale vector."""
    stale = any(name in embed_fields for name in changed)
    if stale:
        obj.embedding = None
    return stale or missing


async def _existing_by[M: (Competency, InterviewTemplate, BankQuestion, CodingQuestion)](
    db: AsyncSession, model: type[M], key_column: Any, organization_id: uuid.UUID | None, *extra: Any
) -> dict[Any, tuple[M, bool]]:
    """Existing rows by key as ``(row, embedding_missing)``. The vector column
    is deferred: decoding 1024 floats per row would dominate re-seeds of a
    large, embedded catalog."""
    vector = getattr(model, "embedding", None)
    missing_flag = vector.is_(None) if vector is not None else literal(False)
    stmt = select(model, missing_flag.label("embedding_missing")).where(
        _org_filter(model.organization_id, organization_id), *extra
    )
    if vector is not None:
        stmt = stmt.options(defer(vector))
    found: dict[Any, tuple[M, bool]] = {}
    for row, missing in (await db.execute(stmt.order_by(model.created_at))).all():
        found.setdefault(
            getattr(row, key_column.key), (row, bool(missing))
        )  # oldest wins if duplicates exist
    return found


async def _bulk_insert(db: AsyncSession, model: type[Any], rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), INSERT_CHUNK):
        await db.execute(insert(model), rows[start : start + INSERT_CHUNK])


# =============================================================================
# Catalog
# =============================================================================


async def seed_catalog(
    db: AsyncSession,
    organization_id: uuid.UUID | None = None,
    *,
    catalog: SeedCatalog | None = None,
    retire_missing: bool = False,
) -> CatalogSeedResult:
    """Upsert competencies, default templates, curated questions and coding
    questions for ``organization_id`` (``None`` = the global catalog).

    Re-running is safe: unchanged rows are not touched, changed rows are
    updated in place (ids are stable, so sessions that reference them keep
    working). With ``retire_missing`` curated questions and coding questions
    that are no longer in the YAML are marked ``retired`` and competencies are
    deactivated; nothing is ever deleted.
    """
    started = time.perf_counter()
    catalog = catalog or load_catalog()
    await _acquire_seed_lock(db)
    result = CatalogSeedResult(organization_id=organization_id)

    await _seed_competencies(db, catalog, organization_id, result, retire_missing)
    await _seed_templates(db, catalog, organization_id, result)
    await _seed_questions(db, catalog, organization_id, result, retire_missing)
    await _seed_coding_questions(db, catalog, organization_id, result, retire_missing)
    await db.flush()

    result.duration_ms = (time.perf_counter() - started) * 1000
    log.info("seed.catalog.completed", **result.as_dict())
    return result


async def _seed_competencies(
    db: AsyncSession,
    catalog: SeedCatalog,
    organization_id: uuid.UUID | None,
    result: CatalogSeedResult,
    retire_missing: bool,
) -> None:
    existing = await _existing_by(db, Competency, Competency.key, organization_id)
    inserts: list[dict[str, Any]] = []
    for seed in catalog.competencies:
        row = seed.to_row()
        hit = existing.get(seed.key)
        if hit is None:
            row_id = uuid.uuid4()
            inserts.append({"id": row_id, "organization_id": organization_id, **row})
            result.competencies.created += 1
            result.competency_ids_needing_embedding.append(row_id)
            continue
        obj, missing = hit
        changed = _apply(obj, row)
        if changed:
            result.competencies.updated += 1
        else:
            result.competencies.unchanged += 1
        if _needs_embedding(obj, changed, _COMPETENCY_EMBED_FIELDS, missing):
            result.competency_ids_needing_embedding.append(obj.id)
    await _bulk_insert(db, Competency, inserts)
    if retire_missing:
        keep = {c.key for c in catalog.competencies}
        for key, (obj, _) in existing.items():
            if key not in keep and obj.is_active:
                obj.is_active = False
                result.competencies.retired += 1


async def _seed_templates(
    db: AsyncSession, catalog: SeedCatalog, organization_id: uuid.UUID | None, result: CatalogSeedResult
) -> None:
    existing = await _existing_by(
        db,
        InterviewTemplate,
        InterviewTemplate.round_type,
        organization_id,
        InterviewTemplate.is_default.is_(True),
    )
    for seed in catalog.round_templates:
        row = seed.to_row()
        hit = existing.get(seed.round_type.value)
        if hit is None:
            db.add(InterviewTemplate(id=uuid.uuid4(), organization_id=organization_id, **row))
            result.templates.created += 1
        elif _apply(hit[0], row):
            result.templates.updated += 1
        else:
            result.templates.unchanged += 1


async def _seed_questions(
    db: AsyncSession,
    catalog: SeedCatalog,
    organization_id: uuid.UUID | None,
    result: CatalogSeedResult,
    retire_missing: bool,
) -> None:
    existing = await _existing_by(
        db, BankQuestion, BankQuestion.external_key, organization_id, BankQuestion.external_key.is_not(None)
    )
    inserts: list[dict[str, Any]] = []
    for seed in catalog.questions:
        row = seed.to_row()
        hit = existing.get(seed.external_key)
        if hit is None:
            row_id = uuid.uuid4()
            inserts.append({"id": row_id, "organization_id": organization_id, **row})
            result.questions.created += 1
            if row["status"] == BankStatus.ACTIVE.value:
                result.question_ids_needing_embedding.append(row_id)
            continue
        obj, missing = hit
        changed = _apply(obj, row)
        if changed:
            result.questions.updated += 1
        else:
            result.questions.unchanged += 1
        if (
            _needs_embedding(obj, changed, _QUESTION_EMBED_FIELDS, missing)
            and obj.status == BankStatus.ACTIVE.value
        ):
            result.question_ids_needing_embedding.append(obj.id)
    await _bulk_insert(db, BankQuestion, inserts)
    if retire_missing:
        keep = {q.external_key for q in catalog.questions}
        for key, (obj, _) in existing.items():
            if key not in keep and obj.source == "curated" and obj.status != BankStatus.RETIRED.value:
                obj.status = BankStatus.RETIRED.value
                result.questions.retired += 1


async def _seed_coding_questions(
    db: AsyncSession,
    catalog: SeedCatalog,
    organization_id: uuid.UUID | None,
    result: CatalogSeedResult,
    retire_missing: bool,
) -> None:
    existing = await _existing_by(db, CodingQuestion, CodingQuestion.slug, organization_id)
    inserts: list[dict[str, Any]] = []
    for seed in catalog.coding_questions:
        row = seed.to_row()
        hit = existing.get(seed.slug)
        if hit is None:
            row_id = uuid.uuid4()
            inserts.append({"id": row_id, "organization_id": organization_id, **row})
            result.coding_questions.created += 1
            result.coding_question_ids_needing_embedding.append(row_id)
            continue
        obj, missing = hit
        changed = _apply(obj, row)
        if changed:
            result.coding_questions.updated += 1
        else:
            result.coding_questions.unchanged += 1
        if _needs_embedding(obj, changed, _CODING_EMBED_FIELDS, missing):
            result.coding_question_ids_needing_embedding.append(obj.id)
    await _bulk_insert(db, CodingQuestion, inserts)
    if retire_missing:
        keep = {c.slug for c in catalog.coding_questions}
        for slug, (obj, _) in existing.items():
            if slug not in keep and obj.source == "curated" and obj.status != BankStatus.RETIRED.value:
                obj.status = BankStatus.RETIRED.value
                result.coding_questions.retired += 1


# =============================================================================
# Demo jobs
# =============================================================================


async def _default_template_ids(db: AsyncSession, organization_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Round type -> default template id, preferring the organization's own defaults."""
    stmt = (
        select(InterviewTemplate.round_type, InterviewTemplate.id, InterviewTemplate.organization_id)
        .where(
            InterviewTemplate.is_default.is_(True),
            (InterviewTemplate.organization_id == organization_id)
            | InterviewTemplate.organization_id.is_(None),
        )
        .order_by(InterviewTemplate.created_at)
    )
    global_ids: dict[str, uuid.UUID] = {}
    org_ids: dict[str, uuid.UUID] = {}
    for round_type, template_id, org_id in (await db.execute(stmt)).all():
        (global_ids if org_id is None else org_ids).setdefault(round_type, template_id)  # oldest wins
    return {**global_ids, **org_ids}


async def _coding_ids_by_slug(
    db: AsyncSession, organization_id: uuid.UUID, slugs: set[str]
) -> dict[str, uuid.UUID]:
    if not slugs:
        return {}
    stmt = select(CodingQuestion.slug, CodingQuestion.id, CodingQuestion.organization_id).where(
        CodingQuestion.slug.in_(sorted(slugs)),
        CodingQuestion.status == BankStatus.ACTIVE.value,
        (CodingQuestion.organization_id == organization_id) | CodingQuestion.organization_id.is_(None),
    )
    found: dict[str, tuple[uuid.UUID, bool]] = {}
    for slug, coding_id, org_id in (await db.execute(stmt)).all():
        is_org = org_id is not None
        if slug not in found or (is_org and not found[slug][1]):
            found[slug] = (coding_id, is_org)
    return {slug: value[0] for slug, value in found.items()}


async def load_demo_jobs(
    db: AsyncSession,
    organization_id: uuid.UUID,
    created_by_id: uuid.UUID | None,
    *,
    catalog: SeedCatalog | None = None,
    keys: Sequence[str] | None = None,
) -> DemoJobsResult:
    """Create the demo jobs (``samples/jobs``) with requirements and rounds.

    Idempotent by job title within the organization: a job that already exists
    is left untouched (its rounds may already be referenced by interviews).
    Rounds link to the default template of their round type when one exists;
    pinned coding problems are resolved by slug, so :func:`seed_catalog` must
    have run first (:class:`SeedDependencyError` otherwise).
    """
    catalog = catalog or load_catalog()
    await _acquire_seed_lock(db)
    result = DemoJobsResult(organization_id=organization_id)
    templates = catalog.templates_by_type()
    wanted_keys = set(keys) if keys is not None else None
    jobs: list[JobSeed] = [j for j in catalog.jobs if wanted_keys is None or j.key in wanted_keys]

    titles = [j.title for j in jobs]
    existing_rows = (
        await db.execute(
            select(Job.title, Job.id).where(Job.organization_id == organization_id, Job.title.in_(titles))
        )
    ).all()
    existing = {title: job_id for title, job_id in existing_rows}

    template_ids = await _default_template_ids(db, organization_id)
    wanted_slugs = {slug for j in jobs for r in j.rounds for slug in r.pinned_coding_slugs}
    coding_ids = await _coding_ids_by_slug(db, organization_id, wanted_slugs)
    missing_slugs = sorted(wanted_slugs - set(coding_ids))
    if missing_slugs:
        raise SeedDependencyError(f"coding questions {missing_slugs} not found; run seed_catalog first")

    for seed in jobs:
        if seed.title in existing:
            result.existing[seed.key] = existing[seed.title]
            continue
        job = Job(
            id=uuid.uuid4(), organization_id=organization_id, created_by_id=created_by_id, **seed.to_row()
        )
        db.add(job)
        for order, requirement in enumerate(seed.requirements):
            db.add(JobRequirement(id=uuid.uuid4(), job_id=job.id, **requirement.to_row(order)))
            result.requirements_created += 1
        for resolved in resolve_job_rounds(seed, templates):
            template_id = template_ids.get(resolved.round_type.value)
            if template_id is None and resolved.round_type.value not in result.missing_templates:
                result.missing_templates.append(resolved.round_type.value)
            pinned = [coding_ids[slug] for slug in resolved.pinned_coding_slugs]
            db.add(
                Round(
                    id=uuid.uuid4(),
                    job_id=job.id,
                    template_id=template_id,
                    **resolved.to_row(pinned_coding_ids=[str(p) for p in pinned]),
                )
            )
            result.rounds_created += 1
        result.created[seed.key] = job.id
    await db.flush()
    if result.missing_templates:
        log.warning("seed.demo_jobs.templates_missing", round_types=result.missing_templates)
    log.info("seed.demo_jobs.completed", **result.as_dict())
    return result


def default_round_type_templates(catalog: SeedCatalog | None = None) -> dict[RoundType, dict[str, Any]]:
    """Default ``RoundConfig`` documents per round type, straight from the YAML
    (useful for ``GET /round-types`` before the database is seeded). Only
    ``catalog/round_templates.yaml`` is read when no catalog is given (the
    question bank and coding trees are large)."""
    templates = catalog.round_templates if catalog is not None else load_round_templates()
    return {t.round_type: t.config.model_dump(mode="json") for t in templates}


# =============================================================================
# ``python -m app.seed`` integration (app.seed.core.seed_step registry)
# =============================================================================


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def catalog_seed_step(db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
    """Seed step ``catalog``: the global catalog. The ids needing embeddings are
    passed on in the seed context for whichever step enqueues
    ``question_bank.embed`` (see :meth:`CatalogSeedResult.embedding_jobs`)."""
    result = await seed_catalog(db)
    counts = [result.competencies, result.templates, result.questions, result.coding_questions]
    return {
        "catalog_created": sum(c.created for c in counts),
        "catalog_updated": sum(c.updated for c in counts),
        "catalog_unchanged": sum(c.unchanged for c in counts),
        "question_ids_needing_embedding": [str(i) for i in result.question_ids_needing_embedding],
        "coding_question_ids_needing_embedding": [
            str(i) for i in result.coding_question_ids_needing_embedding
        ],
        "competency_ids_needing_embedding": [str(i) for i in result.competency_ids_needing_embedding],
    }


async def demo_jobs_seed_step(db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
    """Seed step ``demo_jobs`` (``--demo`` only): the demo jobs for the core
    organization, created by the seeded recruiter."""
    organization_id = context.get("organization_id")
    if organization_id is None:
        raise SeedDependencyError("the core seed step must provide organization_id")
    creator = context.get("recruiter_id") or context.get("admin_id")
    result = await load_demo_jobs(db, _as_uuid(organization_id), _as_uuid(creator) if creator else None)
    return {
        "demo_jobs_created": len(result.created),
        "demo_job_ids": {key: str(job_id) for key, job_id in result.job_ids.items()},
    }


try:
    from app.seed.core import seed_step
except ImportError:  # pragma: no cover - seed runner not installed
    pass
else:
    seed_step("catalog", order=20)(catalog_seed_step)
    seed_step("demo_jobs", order=30, demo_only=True)(demo_jobs_seed_step)


__all__ = [
    "EMBED_JOB_TYPE",
    "SEED_LOCK_KEY",
    "CatalogSeedResult",
    "DemoJobsResult",
    "EmbeddingJob",
    "EntityCounts",
    "SeedDependencyError",
    "catalog_seed_step",
    "default_round_type_templates",
    "demo_jobs_seed_step",
    "load_demo_jobs",
    "seed_catalog",
]
