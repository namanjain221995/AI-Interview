from __future__ import annotations

import uuid

import pytest

from app.core.deps import require_permission, resolve_org_id
from app.core.errors import Forbidden, ValidationFailed
from app.core.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    Principal,
    can_assign_role,
    can_manage_user,
    permission,
    permissions_for,
)
from app.domain.enums import Role, SubjectType

S, A, R, IV, C = Role.SUPER_ADMIN, Role.ADMIN, Role.RECRUITER, Role.INTERVIEWER, Role.CANDIDATE

# CONTRACTS.md section 3, row by row.
MATRIX: dict[str, set[Role]] = {
    "organizations:manage": {S},
    "users:manage": {S, A},
    "settings:manage": {S, A},
    "jobs:read": {S, A, R, IV},
    "jobs:write": {S, A, R},
    "templates:write": {S, A, R},
    "question_bank:write": {S, A, R},
    "candidates:read": {S, A, R, IV},
    "candidates:write": {S, A, R},
    "interviews:invite": {S, A, R},
    "sessions:read": {S, A, R, IV},
    "reports:read": {S, A, R, IV},
    "transcripts:read": {S, A, R, IV},
    "reports:release": {S, A, R, IV},
    "reviews:write": {S, A, R, IV},
    "integrity:review": {S, A, R},
    "analytics:read": {S, A, R, IV},
    "audit:read": {S, A},
    "portal:self": {C},
}


def test_matrix_covers_every_permission():
    assert set(MATRIX) == {p.value for p in Permission}


@pytest.mark.parametrize("perm", sorted(MATRIX))
def test_role_permission_matrix(perm):
    for role in Role:
        assert (Permission(perm) in ROLE_PERMISSIONS[role]) == (role in MATRIX[perm]), (perm, role)


def _principal(role: Role, org: uuid.UUID | None = None) -> Principal:
    if role is Role.CANDIDATE:
        return Principal(subject_type=SubjectType.CANDIDATE, candidate_id=uuid.uuid4(), role=role, organization_id=org,
                         interview_id=uuid.uuid4())
    return Principal(subject_type=SubjectType.USER, user_id=uuid.uuid4(), role=role, organization_id=org)


def test_principal_helpers():
    admin = _principal(A, uuid.uuid4())
    assert admin.has("users:manage") and admin.has(Permission.AUDIT_READ)
    assert not admin.has("organizations:manage")
    assert admin.is_staff and not admin.is_candidate and not admin.is_super_admin
    assert admin.has_all("jobs:read", "jobs:write") and admin.has_any("organizations:manage", "jobs:read")
    candidate = _principal(C)
    assert candidate.is_candidate and not candidate.is_staff and candidate.id == candidate.candidate_id
    assert candidate.permissions == frozenset({Permission.PORTAL_SELF})
    with pytest.raises(ValueError):
        admin.has("jobs:delete")
    with pytest.raises(TypeError):
        Principal(SubjectType.USER, None)  # type: ignore[misc]  # keyword-only
    with pytest.raises(AttributeError):
        admin.role = S  # type: ignore[misc]  # frozen


def test_permission_parsing():
    assert permission("jobs:read") is Permission.JOBS_READ
    assert permissions_for("RECRUITER") == ROLE_PERMISSIONS[R]
    with pytest.raises(ValueError):
        permission("nope")


def test_require_permission_validates_names_at_definition():
    with pytest.raises(ValueError):
        require_permission("jobs:delete")
    with pytest.raises(ValueError):
        require_permission()
    assert callable(require_permission("jobs:read", Permission.JOBS_WRITE))


def test_privilege_escalation_rules():
    assert can_assign_role(S, S) and can_assign_role(S, A)
    assert can_assign_role(A, A) and can_assign_role(A, R) and can_assign_role(A, IV)
    assert not can_assign_role(A, S)
    assert not can_assign_role(R, IV) and not can_assign_role(IV, IV)
    assert not can_assign_role(S, C) and not can_assign_role(A, C)
    assert can_manage_user(S, S) and can_manage_user(A, A) and can_manage_user(A, R)
    assert not can_manage_user(A, S) and not can_manage_user(R, IV)


def test_resolve_org_id_scoping():
    org, other = uuid.uuid4(), uuid.uuid4()
    admin = _principal(A, org)
    assert resolve_org_id(admin, None) == org
    assert resolve_org_id(admin, org) == org
    with pytest.raises(Forbidden):
        resolve_org_id(admin, other)
    root = _principal(S, org)
    assert resolve_org_id(root, other) == other
    assert resolve_org_id(root, None) == org
    with pytest.raises(ValidationFailed):
        resolve_org_id(_principal(S, None), None)
    with pytest.raises(Forbidden):
        resolve_org_id(_principal(R, None), None)
