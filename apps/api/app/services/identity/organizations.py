"""Organizations and their settings (``OrganizationSettings`` in app.schemas.policies)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import get_logger
from app.db.models import Organization
from app.schemas.policies import OrganizationSettings

log = get_logger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


def _model_type(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _strip_unknown(model: type[BaseModel], data: Any) -> Any:
    """Drop keys the current schema no longer knows (forward-compatible load)."""
    if not isinstance(data, Mapping):
        return data
    cleaned: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if name not in data:
            continue
        value = data[name]
        nested = _model_type(field.annotation)
        if nested is not None:
            value = _strip_unknown(nested, value)
        elif get_origin(field.annotation) is list:
            args = get_args(field.annotation)
            item_model = _model_type(args[0]) if args else None
            if item_model is not None and isinstance(value, list):
                value = [_strip_unknown(item_model, item) for item in value]
        cleaned[name] = value
    return cleaned


def load_organization_settings(raw: Mapping[str, Any] | None) -> OrganizationSettings:
    """Parse stored settings tolerantly: unknown keys are dropped and invalid
    sections fall back to defaults (logged), so schema evolution never breaks
    reading an organization."""
    if not raw:
        return OrganizationSettings()
    try:
        return OrganizationSettings.model_validate(raw)
    except ValidationError:
        pass
    cleaned = _strip_unknown(OrganizationSettings, raw)
    try:
        return OrganizationSettings.model_validate(cleaned)
    except ValidationError:
        log.warning("organization.settings_invalid_sections")
    sections: dict[str, Any] = {}
    for name in OrganizationSettings.model_fields:
        if name in cleaned:
            try:
                sections[name] = getattr(OrganizationSettings.model_validate({name: cleaned[name]}), name)
            except ValidationError:
                continue
    return OrganizationSettings(**sections)


def normalize_slug(slug: str) -> str:
    value = slug.strip().lower()
    if not SLUG_PATTERN.match(value):
        raise ValidationFailed(
            "Slug must be 1-100 lowercase letters, digits or hyphens and start/end with a letter or digit.",
            details={"field": "slug"},
        )
    return value


async def get_organization(db: AsyncSession, organization_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise NotFound("Organization not found.")
    return org


async def get_organization_settings(db: AsyncSession, organization_id: uuid.UUID) -> OrganizationSettings:
    """Effective organization defaults (used when freezing interview policies)."""
    org = await get_organization(db, organization_id)
    return load_organization_settings(org.settings)


async def ensure_slug_available(db: AsyncSession, slug: str, *, exclude_id: uuid.UUID | None = None) -> None:
    stmt = select(func.count()).select_from(Organization).where(Organization.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(Organization.id != exclude_id)
    if (await db.scalar(stmt) or 0) > 0:
        raise Conflict("An organization with this slug already exists.", details={"field": "slug"})


async def create_organization(
    db: AsyncSession, *, name: str, slug: str, settings: OrganizationSettings | None = None, is_active: bool = True
) -> Organization:
    clean_slug = normalize_slug(slug)
    await ensure_slug_available(db, clean_slug)
    org = Organization(
        name=name.strip(),
        slug=clean_slug,
        is_active=is_active,
        settings=(settings or OrganizationSettings()).model_dump(mode="json"),
    )
    db.add(org)
    await db.flush()
    return org


def changed_sections(before: OrganizationSettings, after: OrganizationSettings) -> list[str]:
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    return sorted(name for name in new if old.get(name) != new.get(name))


async def save_organization_settings(
    db: AsyncSession, organization: Organization, settings: OrganizationSettings
) -> list[str]:
    """Replace the organization's settings; returns the top-level sections that changed."""
    before = load_organization_settings(organization.settings)
    organization.settings = settings.model_dump(mode="json")
    await db.flush()
    return changed_sections(before, settings)
