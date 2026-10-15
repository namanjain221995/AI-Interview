"""Helpers for the open-link integration tests.

Jobs and rounds are inserted directly (no catalog seeding needed); interviews
are completed through ``POST /api/v1/test/interviews/{id}/complete`` (no live
model calls).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.db.models import Job, Round
from app.db.session import session_scope

API = "/api/v1"


@dataclass(slots=True)
class LinkEnv:
    recruiter: Any  # AuthedClient
    organization_id: uuid.UUID
    job_id: uuid.UUID
    round_ids: list[uuid.UUID] = field(default_factory=list)


async def make_job(
    organization_id: uuid.UUID, *, rounds: int = 2, status: str = "open"
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with session_scope() as db:
        job = Job(
            organization_id=organization_id,
            title="Backend Engineer",
            description="Build APIs in Python.",
            location="Pune",
            status=status,
        )
        db.add(job)
        await db.flush()
        ids: list[uuid.UUID] = []
        for index in range(rounds):
            row = Round(
                job_id=job.id,
                name=f"Round {index + 1}",
                round_type="technical_screening" if index == 0 else "deep_technical",
                sequence=index + 1,
                duration_minutes=20 + 10 * index,
                config={},
            )
            db.add(row)
            await db.flush()
            ids.append(row.id)
        return job.id, ids


def link_body(env: LinkEnv, **overrides: Any) -> dict[str, Any]:
    return {
        "job_id": str(env.job_id),
        "name": "Campus drive Pune",
        "round_ids": [str(r) for r in env.round_ids],
        **overrides,
    }


async def create_link(env: LinkEnv, **overrides: Any) -> dict[str, Any]:
    response = await env.recruiter.post(f"{API}/open-links", json=link_body(env, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def token_of(link: dict[str, Any]) -> str:
    return link["url"].rsplit("/j/", 1)[1]


def answers(n: int | str = 1, **extra: Any) -> dict[str, Any]:
    return {
        "full_name": f"Candidate {n}",
        "email": f"candidate{n}@example.com",
        "phone": f"+91 98765 {int(str(n), 36) % 100000:05d}",
        **extra,
    }


async def solved_pow(http: httpx.AsyncClient, token: str) -> str | None:
    """Fetch this link's proof-of-work challenge and solve it, like the browser does (S15)."""
    from app.core.pow import Challenge, solve

    response = await http.get(f"{API}/link-access/{token}/challenge")
    if response.status_code != 200:
        return None  # unknown link: let the registration answer for itself
    return solve(Challenge(**response.json()))


async def register(
    http: httpx.AsyncClient, token: str, body_answers: dict[str, Any] | None = None, **extra: Any
) -> httpx.Response:
    payload = {
        "answers": body_answers if body_answers is not None else answers(),
        "privacy_ack": True,
        **extra,
    }
    if "pow" not in payload:
        payload["pow"] = await solved_pow(http, token)
    return await http.post(f"{API}/link-access/{token}/register", json=payload)


async def complete(
    http: httpx.AsyncClient, interview_id: str, end_reason: str | None = None
) -> dict[str, Any]:
    body = {"end_reason": end_reason} if end_reason else None
    response = await http.post(f"{API}/test/interviews/{interview_id}/complete", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def portal_headers(registration_response: httpx.Response) -> dict[str, str]:
    return {"X-CSRF-Token": registration_response.json()["csrf_token"]}
