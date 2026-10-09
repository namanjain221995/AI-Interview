"""``app.core.deps``: pagination and role-gated dependencies."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.deps import Pagination, require_role
from app.core.errors import Forbidden
from app.core.rbac import Permission, Principal
from app.db.models import Candidate
from app.domain.enums import Role, SubjectType


def _principal(role: Role) -> Principal:
    return Principal(
        subject_type=SubjectType.USER, user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role=role
    )


def test_pagination_apply_keeps_the_select_and_adds_limit_offset() -> None:
    page = Pagination(limit=25, offset=50)
    entity = page.apply(select(Candidate).where(Candidate.full_name == "x"))
    columns = page.apply(select(Candidate.id, Candidate.email))  # any arity: Select is variadic
    for stmt in (entity, columns):
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "LIMIT 25 OFFSET 50" in sql
    assert entity.column_descriptions[0]["entity"] is Candidate
    assert [c["name"] for c in columns.column_descriptions] == ["id", "email"]
    assert repr(page) == "Pagination(limit=25, offset=50)"


async def test_require_role_allows_listed_roles_only() -> None:
    dependency = require_role(Role.ADMIN, Role.SUPER_ADMIN, perms=(Permission.CANDIDATES_WRITE,))
    for role in (Role.ADMIN, Role.SUPER_ADMIN):
        principal = _principal(role)
        assert await dependency(principal) is principal
    for role in (Role.RECRUITER, Role.INTERVIEWER, Role.CANDIDATE):
        with pytest.raises(Forbidden) as caught:
            await dependency(_principal(role))
        assert caught.value.details == {"required_roles": ["ADMIN", "SUPER_ADMIN"]}


async def test_require_role_also_checks_permissions() -> None:
    dependency = require_role(Role.INTERVIEWER, perms=(Permission.CANDIDATES_WRITE,))
    with pytest.raises(Forbidden) as caught:
        await dependency(_principal(Role.INTERVIEWER))  # role matches, permission missing
    assert caught.value.details == {"missing_permissions": ["candidates:write"]}


def test_require_role_validates_names_at_definition() -> None:
    with pytest.raises(ValueError):
        require_role()
    with pytest.raises(ValueError):
        require_role("OWNER")
    with pytest.raises(ValueError):
        require_role(Role.ADMIN, perms=("candidates:destroy",))
