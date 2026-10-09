"""Organisation intake settings (``intake_org_settings``; INTAKE_INTERVIEW.md decision 17).

Kept out of ``OrganizationSettings`` on purpose: ``PUT /settings`` replaces that whole document, which would wipe
an intake section saved elsewhere. Until the first ``PUT /intake/settings`` the defaults apply (nothing is
persisted by reads).

* ``program_name`` is spoken in the opening/closing; ``approved_info`` holds the only facts stage 7 may state;
  both pass the catalog fairness lint (422 ``protected_topic``), so the AI interviewer never discusses
  protected topics (including work authorisation, which is form-only).
* ``effective_work_authorization`` decides the MQ1 policy frozen into an invitation: the invitation's own
  choice, else the round configuration, else the organisation default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.db.models import IntakeOrgSettings, User
from app.schemas.api.intake_admin import IntakeOrgSettingsIn, IntakeOrgSettingsOut, UserRef
from app.schemas.intake import (
    INTAKE_DEFAULT_PROGRAM_NAME,
    ApprovedInfoItem,
    IntakeOrgDefaults,
    IntakeRoundConfig,
    WorkAuthorizationPolicy,
)
from app.seed.fairness import fairness_hits

log = get_logger(__name__)


@dataclass(slots=True)
class OrgIntakeSettings:
    program_name: str = INTAKE_DEFAULT_PROGRAM_NAME
    work_authorization: WorkAuthorizationPolicy = field(default_factory=WorkAuthorizationPolicy)
    approved_info: list[ApprovedInfoItem] = field(default_factory=list)
    defaults: IntakeOrgDefaults = field(default_factory=IntakeOrgDefaults)
    updated_at: datetime | None = None
    updated_by_id: uuid.UUID | None = None
    persisted: bool = False

    def approved_info_dicts(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.approved_info]


def _parse(model: Any, value: Any, default: Any, *, part: str, organization_id: uuid.UUID) -> Any:
    try:
        return model.model_validate(value) if value is not None else default
    except ValidationError:
        log.warning("intake.org_settings_unreadable", organization_id=str(organization_id), part=part)
        return default


async def get_org_settings(db: AsyncSession, organization_id: uuid.UUID) -> OrgIntakeSettings:
    """The organisation's intake settings, or the defaults when none were saved (tolerant per section)."""
    row = await db.get(IntakeOrgSettings, organization_id)
    if row is None:
        return OrgIntakeSettings()
    approved: list[ApprovedInfoItem] = []
    for item in row.approved_info if isinstance(row.approved_info, list) else []:
        parsed = _parse(ApprovedInfoItem, item, None, part="approved_info", organization_id=organization_id)
        if parsed is not None:
            approved.append(parsed)
    return OrgIntakeSettings(
        program_name=row.program_name or INTAKE_DEFAULT_PROGRAM_NAME,
        work_authorization=_parse(
            WorkAuthorizationPolicy,
            row.work_authorization,
            WorkAuthorizationPolicy(),
            part="work_authorization",
            organization_id=organization_id,
        ),
        approved_info=approved,
        defaults=_parse(
            IntakeOrgDefaults,
            row.defaults or {},
            IntakeOrgDefaults(),
            part="defaults",
            organization_id=organization_id,
        ),
        updated_at=row.updated_at,
        updated_by_id=row.updated_by_id,
        persisted=True,
    )


def check_fairness(body: IntakeOrgSettingsIn) -> None:
    """422 ``protected_topic`` when the program name or an approved answer touches a protected topic."""
    hits = fairness_hits(body.program_name, field="program_name")
    if hits:
        raise ValidationFailed(
            "The program name mentions a topic the interviewer must never discuss.",
            code="protected_topic",
            details={"field": "program_name", "topic": hits[0].topic},
        )
    for index, item in enumerate(body.approved_info):
        hits = fairness_hits(f"{item.topic}\n{item.answer}", field=f"approved_info[{index}]")
        if hits:
            raise ValidationFailed(
                "This approved answer mentions a topic the interviewer must never discuss "
                f"({hits[0].topic}). Remove it or rephrase it.",
                code="protected_topic",
                details={"field": f"approved_info[{index}]", "index": index, "topic": hits[0].topic},
            )


async def save_org_settings(
    db: AsyncSession, organization_id: uuid.UUID, body: IntakeOrgSettingsIn, *, user_id: uuid.UUID | None
) -> tuple[OrgIntakeSettings, list[str]]:
    """Replace the organisation's intake settings; returns them and the changed top-level keys."""
    check_fairness(body)
    before = await get_org_settings(db, organization_id)
    row = await db.get(IntakeOrgSettings, organization_id)
    if row is None:
        row = IntakeOrgSettings(organization_id=organization_id)
        db.add(row)
    new_values: dict[str, Any] = {
        "program_name": body.program_name,
        "work_authorization": body.work_authorization.model_dump(mode="json"),
        "approved_info": [item.model_dump(mode="json") for item in body.approved_info],
        "defaults": body.defaults.model_dump(mode="json"),
    }
    old_values: dict[str, Any] = {
        "program_name": before.program_name,
        "work_authorization": before.work_authorization.model_dump(mode="json"),
        "approved_info": before.approved_info_dicts(),
        "defaults": before.defaults.model_dump(mode="json"),
    }
    changed = [key for key, value in new_values.items() if value != old_values[key] or not before.persisted]
    for key, value in new_values.items():
        setattr(row, key, value)
    row.updated_by_id = user_id
    await db.flush()
    await db.refresh(row)
    return await get_org_settings(db, organization_id), changed


async def settings_out(db: AsyncSession, settings: OrgIntakeSettings) -> IntakeOrgSettingsOut:
    updated_by = None
    if settings.updated_by_id is not None:
        user = await db.get(User, settings.updated_by_id)
        if user is not None:
            updated_by = UserRef(id=user.id, full_name=user.full_name)
    return IntakeOrgSettingsOut(
        program_name=settings.program_name,
        work_authorization=settings.work_authorization,
        approved_info=settings.approved_info,
        defaults=settings.defaults,
        updated_at=settings.updated_at,
        updated_by=updated_by,
    )


def effective_work_authorization(
    round_intake: IntakeRoundConfig | None,
    org: OrgIntakeSettings,
    override: WorkAuthorizationPolicy | None = None,
) -> WorkAuthorizationPolicy:
    """MQ1 policy frozen at send time: the invitation's choice, else the round configuration, else the
    organisation default (INTAKE_INTERVIEW.md 4.1)."""
    if override is not None:
        return override
    if round_intake is not None and round_intake.work_authorization is not None:
        return round_intake.work_authorization
    return org.work_authorization


__all__ = [
    "OrgIntakeSettings",
    "check_fairness",
    "effective_work_authorization",
    "get_org_settings",
    "save_org_settings",
    "settings_out",
]
