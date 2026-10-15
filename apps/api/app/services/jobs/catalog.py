"""Catalog services: competencies, interview templates, round-type defaults and
round configuration (resolution, validation, column sync).

Ownership model: rows with ``organization_id IS NULL`` are the global catalog
seeded from ``samples/`` (read-only through the API - duplicate them into the
organization to customise); everything else belongs to one organization and
is invisible to the others (cross-organization ids answer 404).
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Forbidden, NotFound, UnsupportedRoundType, ValidationFailed
from app.core.logging import get_logger
from app.db.models import CodingQuestion, Competency, InterviewTemplate, Round
from app.domain.enums import RoundType
from app.schemas.rounds import RoundConfig, load_round_config

log = get_logger(__name__)

ROUND_TYPE_INFO: dict[RoundType, tuple[str, str]] = {
    RoundType.RESUME_SCREENING: (
        "Resume screening",
        "Short conversation that verifies the key claims of the resume.",
    ),
    RoundType.RECRUITER_SCREENING: (
        "Recruiter screening",
        "Motivation, logistics and role fit, without deep technical probing.",
    ),
    RoundType.TECHNICAL_SCREENING: ("Technical screening", "Broad technical fundamentals for the role."),
    RoundType.DEEP_TECHNICAL: (
        "Deep technical",
        "In-depth exploration of the candidate's core technical areas.",
    ),
    RoundType.CODING: ("Coding", "Live coding in the browser editor with sandboxed execution."),
    RoundType.SYSTEM_DESIGN: ("System design", "Architecture, scalability and trade-off discussion."),
    RoundType.PROBLEM_SOLVING: ("Problem solving", "Structured reasoning through realistic scenarios."),
    RoundType.BEHAVIORAL: ("Behavioral", "Past behaviour and collaboration, evidence-based."),
    RoundType.MANAGERIAL: ("Managerial", "People management, delivery and stakeholder handling."),
    RoundType.LEADERSHIP: ("Leadership", "Technical leadership, decisions and influence."),
    RoundType.CUSTOM: ("Custom", "Recruiter-defined round."),
    RoundType.COMBINED: (
        "Combined AI round",
        "Resume validation, technical depth and optional coding in one session.",
    ),
    RoundType.INTAKE: (
        "Intake interview",
        "Seven-stage intake screening before a training program, with a reading and a speaking exercise.",
    ),
}


def parse_round_type(value: RoundType | str) -> RoundType:
    """Stored ``round_type`` -> enum. Unknown values (written by a newer release) -> 409, never a 500."""
    if isinstance(value, RoundType):
        return value
    try:
        return RoundType(value)
    except ValueError as exc:
        raise UnsupportedRoundType(details={"round_type": str(value)[:64]}) from exc


def round_type_label(round_type: RoundType | str) -> str:
    """Human label; unknown stored values fall back to a title-cased version of the key."""
    fallback = (str(round_type).replace("_", " ").title(), "")
    try:
        return ROUND_TYPE_INFO.get(RoundType(round_type), fallback)[0]
    except ValueError:
        return fallback[0]


def org_visible(column: Any, organization_id: uuid.UUID) -> Any:
    """Global rows plus the organization's own."""
    return or_(column.is_(None), column == organization_id)


def ensure_org_owned(row: Any, organization_id: uuid.UUID, *, what: str) -> None:
    """Raise unless ``row`` belongs to ``organization_id`` (global rows are read-only)."""
    if row.organization_id is None:
        raise Forbidden(
            f"Global {what}s are read-only. Create an organization {what} instead.", code="global_read_only"
        )
    if row.organization_id != organization_id:
        raise NotFound(f"{what.capitalize()} not found.")


def validation_details(exc: ValidationError, prefix: str = "config") -> dict[str, Any]:
    return {
        "errors": [
            {
                "loc": [prefix, *[str(p) for p in err.get("loc", ())]],
                "msg": err.get("msg"),
                "type": err.get("type"),
            }
            for err in exc.errors()[:30]
        ]
    }


# =============================================================================
# Competencies
# =============================================================================


async def get_competency(
    db: AsyncSession, competency_id: uuid.UUID, organization_id: uuid.UUID
) -> Competency:
    row = await db.get(Competency, competency_id)
    if row is None or (row.organization_id is not None and row.organization_id != organization_id):
        raise NotFound("Competency not found.")
    return row


async def competency_by_key(db: AsyncSession, key: str, organization_id: uuid.UUID) -> Competency | None:
    """Organization competency first, then the global one."""
    rows = (
        await db.scalars(
            select(Competency).where(
                Competency.key == key, org_visible(Competency.organization_id, organization_id)
            )
        )
    ).all()
    own = [r for r in rows if r.organization_id is not None]
    candidates = own or list(rows)
    return candidates[0] if candidates else None


async def ensure_competency_key_available(db: AsyncSession, key: str, organization_id: uuid.UUID) -> None:
    existing = await db.scalar(
        select(Competency.id).where(
            Competency.key == key, org_visible(Competency.organization_id, organization_id)
        )
    )
    if existing is not None:
        raise Conflict(
            "A competency with this key already exists.", code="competency_exists", details={"field": "key"}
        )


async def unknown_competency_keys(db: AsyncSession, keys: list[str], organization_id: uuid.UUID) -> list[str]:
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return []
    known = set(
        (
            await db.scalars(
                select(Competency.key).where(
                    Competency.key.in_(wanted), org_visible(Competency.organization_id, organization_id)
                )
            )
        ).all()
    )
    return [k for k in wanted if k not in known]


# =============================================================================
# Round configuration
# =============================================================================


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; lists and scalars in ``patch`` replace ``base``."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_round_config(data: dict[str, Any]) -> RoundConfig:
    try:
        return load_round_config(data)  # unknown round_type -> 409 unsupported_round_type
    except ValidationError as exc:
        raise ValidationFailed(
            "The round configuration is invalid.", details=validation_details(exc)
        ) from exc


@lru_cache(maxsize=1)
def _sample_defaults() -> dict[str, dict[str, Any]]:
    try:
        from app.seed.catalog import default_round_type_templates

        return {rt.value: cfg for rt, cfg in default_round_type_templates().items()}
    except Exception as exc:  # noqa: BLE001 - samples are optional at runtime (e.g. slim images)
        log.warning("catalog.sample_round_defaults_unavailable", error=type(exc).__name__)
        return {}


def _default_from(
    round_type: RoundType, row: InterviewTemplate | None
) -> tuple[dict[str, Any], uuid.UUID | None, str]:
    if row is not None and row.config:
        return dict(row.config), row.id, "database"
    sample = _sample_defaults().get(round_type.value)
    if sample:
        return dict(sample), None, "samples"
    config = RoundConfig(round_type=round_type, name=round_type_label(round_type))
    return config.model_dump(mode="json"), None, "builtin"


async def round_type_default(
    db: AsyncSession, round_type: RoundType
) -> tuple[dict[str, Any], uuid.UUID | None, str]:
    """``(config, template_id, source)`` for a round type: the seeded global
    default template, else the YAML sample, else a built-in minimal config."""
    row = await db.scalar(
        select(InterviewTemplate)
        .where(
            InterviewTemplate.organization_id.is_(None),
            InterviewTemplate.is_default.is_(True),
            InterviewTemplate.round_type == round_type.value,
        )
        .limit(1)
    )
    return _default_from(round_type, row)


async def list_round_type_defaults(db: AsyncSession) -> list[dict[str, Any]]:
    """Defaults of every round type (one query for all global default templates)."""
    templates: dict[str, InterviewTemplate] = {}
    for row in (
        await db.scalars(
            select(InterviewTemplate)
            .where(InterviewTemplate.organization_id.is_(None), InterviewTemplate.is_default.is_(True))
            .order_by(InterviewTemplate.created_at)
        )
    ).all():
        templates.setdefault(row.round_type, row)
    out = []
    for round_type in RoundType:
        config, template_id, source = _default_from(round_type, templates.get(round_type.value))
        label, description = ROUND_TYPE_INFO.get(round_type, (round_type.value, ""))
        out.append(
            {
                "round_type": round_type,
                "label": label,
                "description": description,
                "template_id": template_id,
                "source": source,
                "config": parse_round_config(config).model_dump(mode="json"),
            }
        )
    return out


async def validate_config_references(
    db: AsyncSession, config: RoundConfig, organization_id: uuid.UUID
) -> None:
    """Competency keys and pinned coding questions must exist for the organization."""
    keys = [c.key for c in config.competencies] + [k for cat in config.rubric for k in cat.competency_keys]
    unknown = await unknown_competency_keys(db, keys, organization_id)
    problems: dict[str, Any] = {}
    if unknown:
        problems["unknown_competency_keys"] = unknown
    pinned: list[uuid.UUID] = []
    bad_ids: list[str] = []
    for value in config.coding.question_ids:
        try:
            pinned.append(uuid.UUID(str(value)))
        except ValueError:
            bad_ids.append(str(value))
    if pinned:
        found = set(
            (
                await db.scalars(
                    select(CodingQuestion.id).where(
                        CodingQuestion.id.in_(pinned),
                        org_visible(CodingQuestion.organization_id, organization_id),
                    )
                )
            ).all()
        )
        bad_ids.extend(str(i) for i in pinned if i not in found)
    if bad_ids:
        problems["unknown_coding_question_ids"] = bad_ids
    if problems:
        raise ValidationFailed("The round configuration references unknown items.", details=problems)


def sync_round_columns(row: Round, config: RoundConfig) -> None:
    """Columns mirror the validated config (the engine reads ``config``)."""
    row.name = config.name[:200]
    row.round_type = config.round_type.value
    row.duration_minutes = config.duration_minutes
    row.max_duration_minutes = config.max_duration_minutes
    row.expected_difficulty = config.expected_difficulty.value
    row.candidate_selectable = config.candidate_selectable
    row.instructions = config.instructions
    row.config = config.model_dump(mode="json")


COLUMN_TO_CONFIG = (
    "name",
    "duration_minutes",
    "max_duration_minutes",
    "expected_difficulty",
    "candidate_selectable",
    "instructions",
)


def overlay_columns(config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Explicit column values (request fields) win over the config document."""
    out = dict(config)
    for name in COLUMN_TO_CONFIG:
        if name in values and values[name] is not None:
            value = values[name]
            out[name] = value.value if hasattr(value, "value") else value
    if values.get("round_type") is not None:
        out["round_type"] = (
            values["round_type"].value if hasattr(values["round_type"], "value") else values["round_type"]
        )
    if (
        values.get("duration_minutes") is not None
        and values.get("max_duration_minutes") is None
        and int(out.get("max_duration_minutes") or 0) < int(values["duration_minutes"])
    ):
        out["max_duration_minutes"] = values["duration_minutes"]
    return out


# =============================================================================
# Templates
# =============================================================================


async def get_template(
    db: AsyncSession, template_id: uuid.UUID, organization_id: uuid.UUID
) -> InterviewTemplate:
    row = await db.get(InterviewTemplate, template_id)
    if row is None or (row.organization_id is not None and row.organization_id != organization_id):
        raise NotFound("Template not found.")
    return row


async def build_template_config(
    db: AsyncSession,
    *,
    round_type: RoundType,
    name: str,
    base: dict[str, Any] | None,
    patch: dict[str, Any] | None,
    organization_id: uuid.UUID,
) -> RoundConfig:
    if base is None:
        base, _, _ = await round_type_default(db, round_type)
    data = deep_merge(base, patch or {})
    data["round_type"] = round_type.value
    if not (patch or {}).get("name"):
        data["name"] = name
    config = parse_round_config(data)
    await validate_config_references(db, config, organization_id)
    return config


async def clear_other_defaults(
    db: AsyncSession, organization_id: uuid.UUID, round_type: str, keep_id: uuid.UUID | None
) -> None:
    stmt = update(InterviewTemplate).where(
        InterviewTemplate.organization_id == organization_id,
        InterviewTemplate.round_type == round_type,
        InterviewTemplate.is_default.is_(True),
    )
    if keep_id is not None:
        stmt = stmt.where(InterviewTemplate.id != keep_id)
    await db.execute(stmt.values(is_default=False).execution_options(synchronize_session=False))
