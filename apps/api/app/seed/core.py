"""Core seed data: the demo organization and one account per staff role.

``seed_core(db)`` is idempotent: it creates what is missing and leaves existing
rows untouched (passwords are only reset when asked). Other seeders build on
the returned ids and register extra steps with :func:`seed_step`::

    from app.seed.core import seed_step

    @seed_step("catalog", order=20)
    async def seed_catalog(db: AsyncSession, core: dict[str, Any]) -> dict[str, Any]: ...

By convention ``app.seed.catalog.seed_catalog`` and ``app.seed.demo.seed_demo``
are picked up even without the decorator (see ``python -m app.seed --help``).
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import hash_password_async, password_problems
from app.db.models import Organization, User
from app.domain.enums import Role
from app.schemas.policies import OrganizationSettings

log = get_logger(__name__)

DEMO_ORG_NAME = "TechSara Solutions"
DEMO_ORG_SLUG = "demo"

SeedFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class SeedStep:
    name: str
    fn: SeedFn
    order: int
    demo_only: bool


_STEPS: dict[str, SeedStep] = {}


def seed_step(name: str, *, order: int = 50, demo_only: bool = False) -> Callable[[SeedFn], SeedFn]:
    """Register an extra seed step run by ``python -m app.seed`` after the core step.

    ``demo_only`` steps run only with ``--demo``. Steps receive ``(db, context)``
    where ``context`` is the merged dict of the core ids and earlier step results
    (a step taking only ``db`` is also accepted).
    """

    def decorator(fn: SeedFn) -> SeedFn:
        _STEPS[name] = SeedStep(name=name, fn=fn, order=order, demo_only=demo_only)
        return fn

    return decorator


def registered_steps() -> list[SeedStep]:
    return sorted(_STEPS.values(), key=lambda s: (s.order, s.name))


async def call_seed_fn(fn: SeedFn, db: AsyncSession, context: dict[str, Any]) -> Any:
    params = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
    ]
    result = fn(db, context) if len(params) >= 2 else fn(db)
    return await result if inspect.isawaitable(result) else result


def seed_accounts() -> list[tuple[Role, str, str]]:
    """``(role, email, full_name)`` for every seeded staff account."""
    settings = get_settings()
    return [
        (Role.SUPER_ADMIN, settings.seed_superadmin_email, "Super Admin"),
        (Role.ADMIN, settings.seed_admin_email, "Demo Admin"),
        (Role.RECRUITER, settings.seed_recruiter_email, "Demo Recruiter"),
        (Role.INTERVIEWER, settings.seed_interviewer_email, "Demo Interviewer"),
    ]


def seed_password() -> str:
    secret = get_settings().seed_password
    value = secret.get_secret_value() if secret is not None else ""
    if not value:
        raise RuntimeError("SEED_PASSWORD is not set; run scripts/setup.sh to generate it")
    problems = password_problems(value)
    if problems:
        raise RuntimeError("SEED_PASSWORD does not satisfy the password policy: " + " ".join(problems))
    return value


async def seed_core(db: AsyncSession, *, reset_passwords: bool = False) -> dict[str, Any]:
    """Create the demo organization and staff accounts; returns their ids.

    Returned keys: ``organization_id``, ``organization_slug``, ``users``
    (role value -> user id), ``user_emails`` (role value -> email),
    ``superadmin_id``, ``admin_id``, ``recruiter_id``, ``interviewer_id`` and
    ``created`` (what this run inserted).
    """
    created: dict[str, Any] = {"organization": False, "users": []}
    org = await db.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    if org is None:
        org = Organization(
            name=DEMO_ORG_NAME,
            slug=DEMO_ORG_SLUG,
            is_active=True,
            settings=OrganizationSettings().model_dump(mode="json"),
            features={"intake": True},  # the demo organisation runs the intake kit (G8)
        )
        db.add(org)
        await db.flush()
        created["organization"] = True
        log.info("seed.organization_created", organization_id=str(org.id))
    elif not (org.features or {}).get("intake"):
        org.features = {**(org.features or {}), "intake": True}  # demo data includes the intake kit (G8)
        await db.flush()

    password: str | None = None
    users: dict[str, uuid.UUID] = {}
    emails: dict[str, str] = {}
    for role, email, full_name in seed_accounts():
        normalized = email.strip().lower()
        user = await db.scalar(select(User).where(User.email == normalized))
        if user is None:
            password = password or seed_password()
            user = User(
                organization_id=org.id,
                email=normalized,
                full_name=full_name,
                role=role.value,
                password_hash=await hash_password_async(password),
                is_active=True,
                password_changed_at=datetime.now(UTC),
            )
            db.add(user)
            await db.flush()
            created["users"].append(normalized)
            log.info("seed.user_created", role=role.value, user_id=str(user.id))
        elif reset_passwords:
            password = password or seed_password()
            user.password_hash = await hash_password_async(password)
            user.password_changed_at = datetime.now(UTC)
            user.failed_login_count = 0
            user.locked_until = None
            await db.flush()
        users[role.value] = user.id
        emails[role.value] = normalized

    return {
        "organization_id": org.id,
        "organization_slug": org.slug,
        "organization_name": org.name,
        "users": users,
        "user_emails": emails,
        "superadmin_id": users[Role.SUPER_ADMIN.value],
        "admin_id": users[Role.ADMIN.value],
        "recruiter_id": users[Role.RECRUITER.value],
        "interviewer_id": users[Role.INTERVIEWER.value],
        "created": created,
    }
