"""Organization settings DTOs (``/api/v1/settings``).

The wire format is exactly :class:`app.schemas.policies.OrganizationSettings`
(``extra="forbid"``, fully validated); this module only re-exports it under
API names so routers and the frontend share one definition.
"""

from __future__ import annotations

from app.schemas.policies import (
    AvatarSettings,
    IntegrityPolicy,
    IntegrityRule,
    OrganizationSettings,
    RecordingPolicy,
    RetentionPolicy,
)

OrganizationSettingsIn = OrganizationSettings
OrganizationSettingsOut = OrganizationSettings

__all__ = [
    "AvatarSettings",
    "IntegrityPolicy",
    "IntegrityRule",
    "OrganizationSettings",
    "OrganizationSettingsIn",
    "OrganizationSettingsOut",
    "RecordingPolicy",
    "RetentionPolicy",
]
