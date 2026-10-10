"""Fixtures for the open-link integration tests (helpers live in ``helpers.py``)."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.domain.enums import Role
from tests.integration.open_links.helpers import LinkEnv, make_job


@pytest_asyncio.fixture
async def link_env(auth_client: Callable[..., Awaitable[Any]]) -> LinkEnv:
    recruiter = await auth_client(Role.RECRUITER)
    org_id = recruiter.user.organization_id
    job_id, round_ids = await make_job(org_id)
    return LinkEnv(recruiter=recruiter, organization_id=org_id, job_id=job_id, round_ids=round_ids)


@pytest_asyncio.fixture
async def public_client(app: FastAPI) -> AsyncIterator[Callable[..., httpx.AsyncClient]]:
    """``public_client(ip="10.0.0.5", user_agent=...)``: a fresh anonymous browser."""
    opened: list[httpx.AsyncClient] = []

    def make(*, ip: str = "127.0.0.1", user_agent: str | None = None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(ip, 50000))
        headers = {"user-agent": user_agent or f"pytest-browser/{secrets.token_hex(3)}"}
        http = httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=headers)
        opened.append(http)
        return http

    yield make
    for http in opened:
        await http.aclose()


@pytest.fixture(autouse=True)
def _fast_proof_of_work(settings: Any) -> None:
    """Every registration solves a real challenge (the helper does it); a small one keeps tests fast."""
    settings.open_link_pow_max_number = 500
