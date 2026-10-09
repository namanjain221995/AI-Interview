"""Role-based access control (CONTRACTS.md section 3).

``Permission`` values are plain strings, so ``require_permission("jobs:write")``
and ``require_permission(Permission.JOBS_WRITE)`` are equivalent. Unknown
permission names fail at import time instead of silently denying access.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import Role, SubjectType


class Permission(StrEnum):
    ORGANIZATIONS_MANAGE = "organizations:manage"
    USERS_MANAGE = "users:manage"
    SETTINGS_MANAGE = "settings:manage"
    JOBS_READ = "jobs:read"
    JOBS_WRITE = "jobs:write"
    TEMPLATES_WRITE = "templates:write"
    QUESTION_BANK_WRITE = "question_bank:write"
    CANDIDATES_READ = "candidates:read"
    CANDIDATES_WRITE = "candidates:write"
    INTERVIEWS_INVITE = "interviews:invite"
    SESSIONS_READ = "sessions:read"
    REPORTS_READ = "reports:read"
    TRANSCRIPTS_READ = "transcripts:read"
    REPORTS_RELEASE = "reports:release"
    REVIEWS_WRITE = "reviews:write"
    INTEGRITY_REVIEW = "integrity:review"
    ANALYTICS_READ = "analytics:read"
    AUDIT_READ = "audit:read"
    PORTAL_SELF = "portal:self"


P = Permission

_INTERVIEWER: frozenset[Permission] = frozenset(
    {
        P.JOBS_READ,
        P.CANDIDATES_READ,
        P.SESSIONS_READ,
        P.REPORTS_READ,
        P.TRANSCRIPTS_READ,
        P.REPORTS_RELEASE,
        P.REVIEWS_WRITE,
        P.ANALYTICS_READ,
    }
)
_RECRUITER: frozenset[Permission] = _INTERVIEWER | {
    P.JOBS_WRITE,
    P.TEMPLATES_WRITE,
    P.QUESTION_BANK_WRITE,
    P.CANDIDATES_WRITE,
    P.INTERVIEWS_INVITE,
    P.INTEGRITY_REVIEW,
}
_ADMIN: frozenset[Permission] = _RECRUITER | {P.USERS_MANAGE, P.SETTINGS_MANAGE, P.AUDIT_READ}
_SUPER_ADMIN: frozenset[Permission] = _ADMIN | {P.ORGANIZATIONS_MANAGE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.SUPER_ADMIN: _SUPER_ADMIN,
    Role.ADMIN: _ADMIN,
    Role.RECRUITER: _RECRUITER,
    Role.INTERVIEWER: _INTERVIEWER,
    Role.CANDIDATE: frozenset({P.PORTAL_SELF}),
}

STAFF_ROLES: frozenset[Role] = frozenset({Role.SUPER_ADMIN, Role.ADMIN, Role.RECRUITER, Role.INTERVIEWER})

# Privilege ordering used to stop privilege escalation: an actor may only
# grant or manage roles strictly below its own rank (SUPER_ADMIN may manage
# every role, including other super admins).
ROLE_RANK: dict[Role, int] = {
    Role.CANDIDATE: 0,
    Role.INTERVIEWER: 1,
    Role.RECRUITER: 2,
    Role.ADMIN: 3,
    Role.SUPER_ADMIN: 4,
}


def permission(value: str | Permission) -> Permission:
    """Parse a permission name, raising ``ValueError`` for unknown names."""
    try:
        return Permission(value)
    except ValueError as exc:
        raise ValueError(f"Unknown permission {value!r}") from exc


def permissions_for(role: Role | str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(Role(role), frozenset())


def can_assign_role(actor_role: Role, target_role: Role) -> bool:
    """Whether ``actor_role`` may create a user with / change a user to ``target_role``."""
    if target_role not in STAFF_ROLES:
        return False
    if actor_role is Role.SUPER_ADMIN:
        return True
    if actor_role is Role.ADMIN:
        return ROLE_RANK[target_role] <= ROLE_RANK[Role.ADMIN]
    return False


def can_manage_user(actor_role: Role, target_role: Role) -> bool:
    """Whether ``actor_role`` may modify an existing user holding ``target_role``."""
    if actor_role is Role.SUPER_ADMIN:
        return True
    if actor_role is Role.ADMIN:
        return target_role is not Role.SUPER_ADMIN
    return False


@dataclass(frozen=True, slots=True, kw_only=True)
class Principal:
    """The authenticated caller of a request (staff user or candidate).

    Keyword-only construction: ``Principal(subject_type=..., role=..., ...)``.
    """

    subject_type: SubjectType
    user_id: uuid.UUID | None = None
    candidate_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    role: Role
    auth_session_id: uuid.UUID | None = None
    interview_id: uuid.UUID | None = None
    email: str | None = None
    display_name: str | None = None

    @property
    def id(self) -> uuid.UUID | None:
        return self.user_id if self.subject_type is SubjectType.USER else self.candidate_id

    @property
    def is_staff(self) -> bool:
        return self.subject_type is SubjectType.USER and self.role in STAFF_ROLES

    @property
    def is_candidate(self) -> bool:
        return self.subject_type is SubjectType.CANDIDATE

    @property
    def is_super_admin(self) -> bool:
        return self.role is Role.SUPER_ADMIN and self.subject_type is SubjectType.USER

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS.get(self.role, frozenset())

    def has(self, perm: str | Permission) -> bool:
        return permission(perm) in self.permissions

    def has_all(self, *perms: str | Permission) -> bool:
        return all(self.has(p) for p in perms)

    def has_any(self, *perms: str | Permission) -> bool:
        return any(self.has(p) for p in perms)
