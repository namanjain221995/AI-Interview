"""Platform test fixtures (pytest plugin loaded by ``tests/conftest.py``).

Isolation (safe for several test runs in parallel on the shared dev servers):

* **Postgres** - every pytest process creates its own database
  ``interview_test_<pid>_<hex>`` on the dev server the first time a test needs
  it, runs ``alembic upgrade head`` into it and drops it at the end. Tables are
  truncated after every test that used the database. Databases left behind by
  crashed runs (owner pid gone, nobody connected) are reaped on start.
* **Redis** - every process leases one logical database (1-14) through
  ``SET NX`` keys in DB 15; it is flushed before and after each test using it.
* **Storage** - ``STORAGE_LOCAL_ROOT`` points at a temporary directory, and the
  ``storage`` fixture installs a per-test ``LocalStorage`` under ``tmp_path``.

The environment variables are overridden when this module is imported (before
any test imports the app), so code calling ``get_settings()`` can never reach
the development database.

Fixtures: ``settings``, ``test_database``, ``db_session``, ``redis``,
``storage``, ``app``, ``client``, ``org_factory``, ``user_factory``,
``auth_client``, ``candidate_auth_client``, ``worker_factory``.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import re
import secrets
import shutil
import socket
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

# --------------------------------------------------------------------------
# Environment isolation (import time)
# --------------------------------------------------------------------------

_API_ROOT = Path(__file__).resolve().parents[2]
_HOST = socket.gethostname()
_LEASE_DB = 15
_LEASE_CANDIDATES = range(1, 15)
_LEASE_TTL_SECONDS = 12 * 3600
_TEST_DB_PATTERN = re.compile(r"^interview_test_(\d+)_[0-9a-f]+$")
_CAS_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]) return 1 end return 0"
_CAD_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _redis_url_for_db(url: str, db: int) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


class _RedisLease:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.db: int = 14
        self.key: str | None = None
        self.value = f"{_HOST}:{os.getpid()}:{secrets.token_hex(4)}"

    def acquire(self) -> int:
        import redis as sync_redis

        try:
            registry = sync_redis.Redis.from_url(
                _redis_url_for_db(self.base_url, _LEASE_DB), socket_timeout=3, decode_responses=True
            )
            for db in _LEASE_CANDIDATES:
                key = f"interview:pytest:lease:{db}"
                if registry.set(key, self.value, nx=True, ex=_LEASE_TTL_SECONDS):
                    self.db, self.key = db, key
                    break
                owner = registry.get(key) or ""
                host, _, rest = owner.partition(":")
                pid = rest.split(":", 1)[0]
                if host == _HOST and pid.isdigit() and not _pid_alive(int(pid)):
                    if registry.eval(_CAS_LUA, 1, key, owner, self.value, _LEASE_TTL_SECONDS):
                        self.db, self.key = db, key
                        break
            registry.close()
        except Exception:  # Redis down: integration tests will fail loudly anyway
            self.db = 14
        return self.db

    def release(self) -> None:
        if self.key is None:
            return
        import redis as sync_redis

        with contextlib.suppress(Exception):
            registry = sync_redis.Redis.from_url(
                _redis_url_for_db(self.base_url, _LEASE_DB), socket_timeout=3
            )
            registry.eval(_CAD_LUA, 1, self.key, self.value)
            registry.close()
        self.key = None


def _configure_test_environment() -> tuple[str, str, str, _RedisLease, str]:
    from app.core.config import Settings, get_settings

    base = Settings()  # type: ignore[call-arg]  # .env + environment
    db_name = f"interview_test_{os.getpid()}_{secrets.token_hex(3)}"
    db_url = make_url(base.database_url).set(database=db_name).render_as_string(hide_password=False)
    admin_url = make_url(base.database_url).set(database="postgres").render_as_string(hide_password=False)
    lease = _RedisLease(base.redis_url)
    redis_url = _redis_url_for_db(base.redis_url, lease.acquire())
    storage_root = tempfile.mkdtemp(prefix="interview-test-storage-")
    os.environ["DATABASE_URL"] = db_url
    os.environ["REDIS_URL"] = redis_url
    os.environ["STORAGE_LOCAL_ROOT"] = storage_root
    os.environ.setdefault("CSRF_ENABLED", "true")
    os.environ.setdefault("OTEL_ENABLED", "false")
    get_settings.cache_clear()
    return db_name, db_url, admin_url, lease, storage_root


TEST_DB_NAME, TEST_DATABASE_URL, _ADMIN_DATABASE_URL, _LEASE, TEST_STORAGE_ROOT = (
    _configure_test_environment()
)


def _cleanup_process_resources() -> None:
    _LEASE.release()
    shutil.rmtree(TEST_STORAGE_ROOT, ignore_errors=True)


atexit.register(_cleanup_process_resources)


def pytest_unconfigure(config: pytest.Config) -> None:
    _cleanup_process_resources()


# Imports below read the (now isolated) settings.
import httpx  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.redis import close_redis, create_redis_client  # noqa: E402
from app.core.security import generate_invite_token, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import Candidate, Interview, Job, Organization, User  # noqa: E402
from app.db.session import dispose_engine, get_engine, get_sessionmaker, session_scope  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.schemas.policies import OrganizationSettings  # noqa: E402
from app.services.storage import LocalStorage, set_storage  # noqa: E402

# --------------------------------------------------------------------------
# Database lifecycle
# --------------------------------------------------------------------------


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _reap_stale_databases(conn: Any) -> None:
    for row in await conn.fetch("SELECT datname FROM pg_database WHERE datname LIKE 'interview_test_%'"):
        name = row["datname"]
        match = _TEST_DB_PATTERN.match(name)
        if match is None or name == TEST_DB_NAME or _pid_alive(int(match.group(1))):
            continue
        with contextlib.suppress(Exception):  # still in use (another host/namespace): leave it
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _alembic_upgrade(sync_connection: Any, revision: str = "head") -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.attributes["connection"] = sync_connection
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, revision)


async def run_migrations(url: str, revision: str = "head") -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_alembic_upgrade, revision)
            await conn.commit()
    finally:
        await engine.dispose()


_TRUNCATE_SQL: str | None = None


async def truncate_all_tables() -> None:
    """Empty every application table (``alembic_version`` is kept)."""
    global _TRUNCATE_SQL
    if _TRUNCATE_SQL is None:
        names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        _TRUNCATE_SQL = f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"
    async with get_engine().begin() as conn:
        await conn.execute(text(_TRUNCATE_SQL))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_database() -> AsyncIterator[str]:
    """Create, migrate and finally drop this process's database; yields its URL."""
    import asyncpg

    admin = await asyncpg.connect(_asyncpg_dsn(_ADMIN_DATABASE_URL))
    try:
        await _reap_stale_databases(admin)
        await admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await admin.close()
    try:
        await run_migrations(TEST_DATABASE_URL)
        yield TEST_DATABASE_URL
    finally:
        await dispose_engine()
        admin = await asyncpg.connect(_asyncpg_dsn(_ADMIN_DATABASE_URL))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest_asyncio.fixture
async def _db_cleanup(test_database: str) -> AsyncIterator[None]:
    yield
    await truncate_all_tables()


@pytest_asyncio.fixture
async def db_session(_db_cleanup: None) -> AsyncIterator[AsyncSession]:
    """An ``AsyncSession`` on the test database. Commit to make rows visible to the app."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


# --------------------------------------------------------------------------
# Settings, Redis, storage
# --------------------------------------------------------------------------


@pytest.fixture
def settings() -> Iterator[Settings]:
    """The active (test) settings; attribute changes are undone after the test."""
    current = get_settings()
    snapshot = dict(current.__dict__)
    yield current
    current.__dict__.clear()
    current.__dict__.update(snapshot)


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Redis]:
    """``str`` client on this process's leased logical database (flushed around the test)."""
    client = create_redis_client(get_settings().redis_url, client_name="pytest")
    await client.flushdb()
    try:
        yield client
    finally:
        with contextlib.suppress(Exception):
            await client.flushdb()
        await client.aclose()
        await close_redis()


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    backend = LocalStorage(tmp_path / "storage")
    set_storage(backend)
    try:
        yield backend
    finally:
        set_storage(None)


# --------------------------------------------------------------------------
# Application and HTTP clients
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app(_db_cleanup: None, redis: Redis, storage: LocalStorage) -> AsyncIterator[FastAPI]:
    """A fresh application (lifespan started) bound to the isolated resources."""
    from app.main import create_app

    application = create_app(get_settings())
    async with LifespanManager(application, startup_timeout=30, shutdown_timeout=30):
        yield application


def _transport(app: FastAPI, *, raise_app_exceptions: bool = True) -> httpx.ASGITransport:
    return httpx.ASGITransport(
        app=app, raise_app_exceptions=raise_app_exceptions, client=("127.0.0.1", 50000)
    )


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=_transport(app), base_url="http://testserver") as http:
        yield http


@dataclass(slots=True)
class TestUser:
    __test__ = False  # not a test class

    id: uuid.UUID
    email: str
    password: str
    role: Role
    organization_id: uuid.UUID | None
    full_name: str


class AuthedClient(httpx.AsyncClient):
    """httpx client signed in through ``POST /api/v1/auth/login`` with ``X-CSRF-Token`` set."""

    user: TestUser
    csrf_token: str

    async def refresh_session(self) -> httpx.Response:
        response = await self.post("/api/v1/auth/refresh")
        if response.status_code == 200:
            self.csrf_token = response.json()["csrf_token"]
            self.headers["X-CSRF-Token"] = self.csrf_token
        return response


@dataclass(slots=True)
class CandidateContext:
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    interview_id: uuid.UUID
    invite_token: str
    auth_session_id: uuid.UUID
    csrf_token: str


class CandidateClient(httpx.AsyncClient):
    candidate: CandidateContext


@pytest_asyncio.fixture
async def org_factory(_db_cleanup: None) -> Callable[..., Awaitable[uuid.UUID]]:
    async def make(
        *,
        name: str | None = None,
        slug: str | None = None,
        is_active: bool = True,
        features: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        suffix = secrets.token_hex(4)
        async with session_scope() as session:
            org = Organization(
                name=name or f"Test Org {suffix}",
                slug=slug or f"test-{suffix}",
                is_active=is_active,
                settings=OrganizationSettings().model_dump(mode="json"),
                features=features or {},
            )
            session.add(org)
            await session.flush()
            return org.id

    return make


@pytest_asyncio.fixture
async def user_factory(
    org_factory: Callable[..., Awaitable[uuid.UUID]],
) -> Callable[..., Awaitable[TestUser]]:
    """``await user_factory(Role.ADMIN, organization_id=None, email=None, password=None, is_active=True)``.

    Users without an explicit organization share one organization per test.
    """
    shared: dict[str, uuid.UUID] = {}

    async def make(
        role: Role | str = Role.RECRUITER,
        *,
        organization_id: uuid.UUID | None = None,
        email: str | None = None,
        password: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
    ) -> TestUser:
        role = Role(role)
        if organization_id is None:
            if "org" not in shared:
                shared["org"] = await org_factory()
            organization_id = shared["org"]
        plain = password or f"Tst-{secrets.token_urlsafe(12)}"
        address = (email or f"{role.value.lower()}-{secrets.token_hex(4)}@example.test").lower()
        name = full_name or f"Test {role.value.title()}"
        async with session_scope() as session:
            user = User(
                organization_id=organization_id,
                email=address,
                full_name=name,
                role=role.value,
                password_hash=hash_password(plain),
                is_active=is_active,
                password_changed_at=datetime.now(UTC),
            )
            session.add(user)
            await session.flush()
            user_id = user.id
        return TestUser(
            id=user_id,
            email=address,
            password=plain,
            role=role,
            organization_id=organization_id,
            full_name=name,
        )

    return make


@pytest_asyncio.fixture
async def auth_client(
    app: FastAPI, user_factory: Callable[..., Awaitable[TestUser]]
) -> AsyncIterator[Callable[..., Awaitable[AuthedClient]]]:
    """``client = await auth_client(Role.ADMIN)`` (or ``auth_client(user=existing)``)."""
    opened: list[AuthedClient] = []

    async def make(
        role: Role | str = Role.ADMIN, *, user: TestUser | None = None, **user_kwargs: Any
    ) -> AuthedClient:
        account = user or await user_factory(role, **user_kwargs)
        http = AuthedClient(transport=_transport(app), base_url="http://testserver")
        opened.append(http)
        response = await http.post(
            "/api/v1/auth/login", json={"email": account.email, "password": account.password}
        )
        assert response.status_code == 200, response.text
        http.user = account
        http.csrf_token = response.json()["csrf_token"]
        http.headers["X-CSRF-Token"] = http.csrf_token
        return http

    yield make
    for http in opened:
        await http.aclose()


@pytest_asyncio.fixture
async def candidate_auth_client(
    app: FastAPI, org_factory: Callable[..., Awaitable[uuid.UUID]]
) -> AsyncIterator[Callable[..., Awaitable[CandidateClient]]]:
    """Signed-in candidate for a minimal job + interview (rows created directly)."""
    from app.services.identity.sessions import start_candidate_session

    opened: list[CandidateClient] = []

    async def make(
        *, organization_id: uuid.UUID | None = None, interview_status: str = "invited"
    ) -> CandidateClient:
        org_id = organization_id or await org_factory()
        token, token_hash = generate_invite_token()
        async with session_scope() as session:
            job = Job(organization_id=org_id, title="Test Engineer", description="Test job", status="open")
            candidate = Candidate(
                organization_id=org_id,
                full_name="Test Candidate",
                email=f"cand-{secrets.token_hex(4)}@example.test",
            )
            session.add_all([job, candidate])
            await session.flush()
            interview = Interview(
                organization_id=org_id,
                candidate_id=candidate.id,
                job_id=job.id,
                status=interview_status,
                invite_token_hash=token_hash,
                invite_expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            session.add(interview)
            await session.flush()
            tokens = await start_candidate_session(
                session, candidate_id=candidate.id, interview_id=interview.id, organization_id=org_id
            )
            context = CandidateContext(
                organization_id=org_id,
                candidate_id=candidate.id,
                job_id=job.id,
                interview_id=interview.id,
                invite_token=token,
                auth_session_id=tokens.auth_session_id,
                csrf_token=tokens.csrf_token,
            )
        cookies = {"access_token": tokens.access_token, "csrf_token": tokens.csrf_token}
        if tokens.refresh_token:
            cookies["refresh_token"] = tokens.refresh_token
        http = CandidateClient(transport=_transport(app), base_url="http://testserver", cookies=cookies)
        http.headers["X-CSRF-Token"] = tokens.csrf_token
        http.candidate = context
        opened.append(http)
        return http

    yield make
    for http in opened:
        await http.aclose()


# --------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _WorkerTracker:
    workers: list[Any] = field(default_factory=list)


@pytest_asyncio.fixture
async def worker_factory(_db_cleanup: None, redis: Redis) -> AsyncIterator[Callable[..., Any]]:
    """``worker = worker_factory(job_types=["system.ping"])`` -> ``await worker.drain()``.

    Fast defaults for tests: 10 ms retry backoff, retry schedules / retry-after hints scaled by 1/1000,
    2 s visibility timeout.
    """
    from app.core.queue import Worker

    tracker = _WorkerTracker()

    def make(**kwargs: Any) -> Worker:
        kwargs.setdefault("consumer_name", f"pytest-{secrets.token_hex(3)}")
        kwargs.setdefault("backoff_base", 0.01)
        kwargs.setdefault("backoff_cap", 0.05)
        kwargs.setdefault("retry_schedule_scale", 0.001)  # 15 s -> 15 ms
        kwargs.setdefault("visibility_timeout", 2.0)
        kwargs.setdefault("block_ms", 100)
        worker = Worker(redis=redis, **kwargs)
        tracker.workers.append(worker)
        return worker

    yield make
    for worker in tracker.workers:
        worker.stop()
