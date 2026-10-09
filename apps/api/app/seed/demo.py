"""Demo candidates and live invite links (``python -m app.seed --demo``).

Runs after the catalog and demo jobs. Creates two demo candidates in the demo
organization and gives each an open interview invitation, printing the invite
links so the whole candidate journey can be tried immediately:

* Jane Doe — Senior DevOps Engineer, Combined AI Round (voice + coding).
* Marcus Okafor — Backend Engineer (Python), candidate chooses the round.

Invite tokens are only stored hashed, so an existing open invitation cannot be
shown again: re-running the seed rotates its link and prints the new one.
Resumes are uploaded by the candidate during the journey (samples/resumes/).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Candidate, Interview, Job, Round
from app.domain.enums import InterviewStatus, RoundType
from app.schemas.api.interviews import InterviewCreate
from app.services.jobs.invitations import create_invitation, regenerate_link

log = get_logger(__name__)

_OPEN = (InterviewStatus.INVITED.value, InterviewStatus.IN_PROGRESS.value)
_INVITE_HOURS = 24 * 14


@dataclass(frozen=True)
class DemoCandidate:
    full_name: str
    email: str
    job_title: str
    round_type: RoundType | None  # None = candidate picks among selectable rounds
    resume_hint: str


DEMO_CANDIDATES = (
    DemoCandidate(
        "Jane Doe",
        "jane.doe@demo.local",
        "Senior DevOps Engineer",
        RoundType.COMBINED,
        "samples/resumes/jane_doe_devops.pdf",
    ),
    DemoCandidate(
        "Marcus Okafor",
        "marcus.okafor@demo.local",
        "Backend Engineer (Python)",
        None,
        "samples/resumes/marcus_okafor_backend.pdf",
    ),
)


async def _candidate(db: AsyncSession, organization_id: uuid.UUID, spec: DemoCandidate, created_by: Any) -> Candidate:
    row = await db.scalar(
        select(Candidate).where(Candidate.organization_id == organization_id, Candidate.email == spec.email)
    )
    if row is None:
        row = Candidate(
            organization_id=organization_id,
            full_name=spec.full_name,
            email=spec.email,
            source="demo_seed",
            created_by_id=created_by,
            notes=f"Demo candidate. Upload {spec.resume_hint} during the interview journey.",
        )
        db.add(row)
        await db.flush()
    return row


async def seed_demo(db: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
    organization_id = context.get("organization_id")
    if organization_id is None:
        return {"demo_candidates": 0, "note": "no organization in seed context"}
    organization_id = uuid.UUID(str(organization_id))
    created_by = context.get("recruiter_id")
    created_by = uuid.UUID(str(created_by)) if created_by else None

    invites: dict[str, str] = {}
    for spec in DEMO_CANDIDATES:
        job = await db.scalar(
            select(Job).where(Job.organization_id == organization_id, Job.title == spec.job_title)
        )
        if job is None:
            log.warning("seed.demo.job_missing", job_title=spec.job_title)
            continue
        candidate = await _candidate(db, organization_id, spec, created_by)
        round_id = None
        if spec.round_type is not None:
            round_row = await db.scalar(
                select(Round)
                .where(Round.job_id == job.id, Round.round_type == spec.round_type.value, Round.is_active.is_(True))
                .order_by(Round.sequence)
            )
            round_id = round_row.id if round_row else None
        existing = await db.scalar(
            select(Interview).where(
                Interview.candidate_id == candidate.id,
                Interview.job_id == job.id,
                Interview.status.in_(_OPEN),
            )
        )
        if existing is not None:
            issued = await regenerate_link(db, existing, expires_in_hours=_INVITE_HOURS, rotate_access_code=False)
        else:
            issued = await create_invitation(
                db,
                organization_id=organization_id,
                invited_by_id=created_by,
                body=InterviewCreate(
                    candidate_id=candidate.id,
                    job_id=job.id,
                    round_id=round_id,
                    allow_round_selection=round_id is None,
                    expires_in_hours=_INVITE_HOURS,
                    require_access_code=False,
                    notes="Demo invitation created by the seed.",
                ),
            )
        invites[spec.email] = issued.invite_url
        log.info("seed.demo.invite", candidate=spec.email, job=spec.job_title)
    return {
        "demo_candidates": len(invites),
        **{f"invite[{email}]": url for email, url in invites.items()},
    }
