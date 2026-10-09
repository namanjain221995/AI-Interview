"""Demo seed: demo candidates with open invitations whose links work."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import Candidate, Interview
from app.domain.enums import InterviewStatus
from app.seed.catalog import load_demo_jobs, seed_catalog
from app.seed.demo import DEMO_CANDIDATES, seed_demo
from app.seed.loaders import SeedCatalog, load_catalog

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def catalog() -> SeedCatalog:
    return load_catalog()


async def test_seed_demo_creates_candidates_and_rotating_invites(
    seed_sessions, catalog: SeedCatalog, make_org
) -> None:
    org_id, user_id = await make_org()
    async with seed_sessions() as session:
        await seed_catalog(session, org_id, catalog=catalog)
        await load_demo_jobs(session, org_id, user_id, catalog=catalog)
        first = await seed_demo(session, {"organization_id": org_id, "recruiter_id": user_id})
        await session.commit()
    assert first["demo_candidates"] == len(DEMO_CANDIDATES)
    links = {k: v for k, v in first.items() if k.startswith("invite[")}
    assert len(links) == len(DEMO_CANDIDATES) and all("/i/" in url for url in links.values())

    async with seed_sessions() as session:
        second = await seed_demo(session, {"organization_id": org_id, "recruiter_id": user_id})
        await session.commit()
        candidates = await session.scalar(
            select(func.count()).select_from(Candidate).where(Candidate.organization_id == org_id)
        )
        open_invites = await session.scalar(
            select(func.count())
            .select_from(Interview)
            .where(Interview.organization_id == org_id, Interview.status == InterviewStatus.INVITED.value)
        )
    # Idempotent rows, rotated links (tokens are stored hashed and cannot be re-shown).
    assert candidates == len(DEMO_CANDIDATES)
    assert open_invites == len(DEMO_CANDIDATES)
    assert {k for k in second if k.startswith("invite[")} == set(links)
    assert all(second[k] != links[k] for k in links)


async def test_seed_demo_without_organization_is_a_noop(seed_sessions) -> None:
    async with seed_sessions() as session:
        assert (await seed_demo(session, {}))["demo_candidates"] == 0
