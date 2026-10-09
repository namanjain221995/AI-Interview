"""Alembic environment (async, asyncpg).

URL resolution order: an open connection in ``config.attributes["connection"]``
(programmatic use, e.g. the test fixtures), ``-x dburl=...``, then
``DATABASE_URL`` via :func:`app.core.config.get_settings`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = x_args.get("dburl") or config.get_main_option("sqlalchemy.url") or get_settings().database_url
    return url


def _include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    # Only manage objects declared in the metadata; ignore anything else that
    # lives in the database (e.g. tables created by extensions or by hand).
    if type_ == "table" and reflected and compare_to is None:
        return False
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=False,
        include_object=_include_object,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_sync(connection)
        return
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
