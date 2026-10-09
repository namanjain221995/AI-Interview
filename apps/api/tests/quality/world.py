"""World for the interview-quality harness (throwaway test database).

* the global catalog from ``samples/`` (competencies, round templates, the
  curated question bank) and the demo *Senior DevOps Engineer* job with its
  requirements, exactly as ``python -m app.seed`` creates them;
* a 15-minute screening round on that job (derived from the job's Combined AI
  Round, without the coding exercise, which text personas cannot take);
* per persona: candidate, resume + ``ready`` profile (with claims), consent
  and an interview;
* real embeddings for the bank questions of the round's competencies, so
  retrieval behaves as in production.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from app.db.models import (
    BankQuestion,
    Candidate,
    CandidateProfile,
    CandidateResume,
    Consent,
    Interview,
    Job,
    Organization,
    Round,
)
from app.db.session import session_scope
from app.schemas.policies import OrganizationSettings, PolicySnapshot
from app.schemas.rounds import RoundConfig

from .personas import Persona

JOB_KEY = "senior_devops_engineer"

QUALITY_ROUND: dict[str, Any] = {
    # Small on purpose (the models are shared with production): ~6-9 candidate turns per interview.
    "round_type": "technical_screening",
    "name": "Technical Screening",
    "duration_minutes": 10,
    "max_duration_minutes": 12,
    "expected_difficulty": "medium",
    "competencies": [
        {"key": "kubernetes", "weight": 1.5, "focus_areas": ["availability", "scaling", "troubleshooting"]},
        {"key": "aws", "weight": 1.2, "focus_areas": ["networking", "iam", "high_availability"]},
        {"key": "ci_cd", "weight": 0.8, "focus_areas": ["deployment_strategy", "rollback"]},
        {
            "key": "ownership",
            "weight": 0.6,
            "required": False,
            "min_evidence": 1,
            "focus_areas": ["accountability"],
        },
        {"key": "communication", "weight": 0.4, "required": False, "min_evidence": 1},
    ],
    "rubric": [
        {
            "key": "technical_knowledge",
            "label": "Technical Knowledge",
            "weight": 50,
            "competency_keys": ["kubernetes", "aws", "ci_cd"],
            "dimensions": ["correctness", "depth"],
        },
        {
            "key": "practical_experience",
            "label": "Practical Experience",
            "weight": 15,
            "dimensions": ["practical_experience", "specificity"],
        },
        {"key": "problem_solving", "label": "Problem Solving", "weight": 10, "dimensions": ["reasoning"]},
        {
            "key": "ownership_leadership",
            "label": "Ownership & Leadership",
            "weight": 10,
            "competency_keys": ["ownership"],
        },
        {
            "key": "communication_content",
            "label": "Communication Content",
            "weight": 10,
            "competency_keys": ["communication"],
            "dimensions": ["communication_clarity"],
        },
        {
            "key": "resume_consistency",
            "label": "Resume Consistency",
            "weight": 5,
            "dimensions": ["consistency"],
        },
    ],
    "min_evidence_per_competency": 2,
    "confidence_threshold": 0.65,
    "question_strategy": {"max_followup_depth": 3, "allow_candidate_questions_at_end": True},
    "stopping": {
        "stop_on_sufficient_evidence": True,
        "min_duration_minutes": 5,
        "min_questions": 4,
        "max_questions": 8,
    },
    "coding": {"enabled": False},
    "resume_validation": {"enabled": True, "max_claims": 2, "min_priority": 4},
    "interviewer_notes": (
        "Senior DevOps candidate. Prioritise verifying the largest platform claims on the resume (scale of the "
        "Kubernetes platform and the EKS migration). Suggested flow: intro (1 min), resume and technical scenarios "
        "(about 7 min), candidate questions (about 1 min)."
    ),
}


@dataclass(slots=True)
class QualityWorld:
    organization_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    job_title: str
    round_config: RoundConfig
    embedded_questions: int


@dataclass(slots=True)
class PersonaWorld:
    candidate_id: uuid.UUID
    interview_id: uuid.UUID


async def _add_inflight_columns(db: Any) -> None:
    """Columns present on the ORM models whose migration is still being written
    by another module (throwaway test database only; a no-op once migrated)."""
    from app.db.base import Base

    rows = (
        await db.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
    ).all()
    existing = {(r[0], r[1]) for r in rows}
    tables = {r[0] for r in rows}
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        for column in table.columns:
            if (
                (table.name, column.name) in existing
                or not column.nullable
                or column.server_default is not None
            ):
                continue
            ddl = column.type.compile(dialect=db.bind.dialect)
            await db.execute(
                text(f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{column.name}" {ddl}')
            )


async def build_quality_world(ai: Any, *, round_overrides: dict[str, Any] | None = None) -> QualityWorld:
    """Seed catalog + demo job + quality round (idempotent per database)."""
    from app.seed.catalog import load_demo_jobs, seed_catalog
    from app.services.question_bank.embeddings import embed_payload

    config = RoundConfig.model_validate({**QUALITY_ROUND, **(round_overrides or {})})
    suffix = secrets.token_hex(3)
    async with session_scope() as db:
        await _add_inflight_columns(db)
    async with session_scope() as db:
        await seed_catalog(db, None)
        org = Organization(
            name=f"Quality Corp {suffix}",
            slug=f"quality-{suffix}",
            is_active=True,
            settings=OrganizationSettings().model_dump(mode="json"),
        )
        db.add(org)
        await db.flush()
        jobs = await load_demo_jobs(db, org.id, None, keys=[JOB_KEY])
        job_id = jobs.created.get(JOB_KEY) or jobs.existing[JOB_KEY]
        job = await db.get(Job, job_id)
        assert job is not None
        rnd = Round(
            job_id=job.id,
            name=config.name,
            round_type=config.round_type.value,
            duration_minutes=config.duration_minutes,
            max_duration_minutes=config.max_duration_minutes,
            config=config.model_dump(mode="json"),
        )
        db.add(rnd)
        await db.flush()
        question_ids = [
            str(r[0])
            for r in (
                await db.execute(
                    select(BankQuestion.id).where(
                        BankQuestion.competency_key.in_(config.competency_keys()),
                        BankQuestion.embedding.is_(None),
                    )
                )
            ).all()
        ]
        world = QualityWorld(
            organization_id=org.id,
            job_id=job.id,
            round_id=rnd.id,
            job_title=job.title,
            round_config=config,
            embedded_questions=len(question_ids),
        )
    if question_ids:
        await embed_payload({"question_ids": question_ids}, ai=ai)
    return world


async def add_persona(world: QualityWorld, persona: Persona) -> PersonaWorld:
    """Candidate + resume + ready profile + consent + interview for one persona."""
    suffix = secrets.token_hex(4)
    profile = persona.profile_doc()
    async with session_scope() as db:
        cand = Candidate(
            organization_id=world.organization_id,
            full_name=persona.candidate_name,
            email=f"{persona.key}-{suffix}@example.test",
        )
        db.add(cand)
        await db.flush()
        snapshot = PolicySnapshot()
        interview = Interview(
            organization_id=world.organization_id,
            candidate_id=cand.id,
            job_id=world.job_id,
            round_id=world.round_id,
            status="invited",
            invite_token_hash=secrets.token_hex(32),
            invite_expires_at=datetime.now(UTC) + timedelta(days=3),
            policy_snapshot=snapshot.model_dump(mode="json"),
        )
        db.add(interview)
        await db.flush()
        resume = CandidateResume(
            candidate_id=cand.id,
            organization_id=world.organization_id,
            interview_id=interview.id,
            storage_key=f"orgs/{world.organization_id}/resume-{suffix}.txt",
            original_filename="resume.txt",
            content_type="text/plain",
            size_bytes=10,
            sha256="0" * 64,
            status="ready",
        )
        db.add(resume)
        await db.flush()
        db.add(
            CandidateProfile(
                candidate_id=cand.id,
                resume_id=resume.id,
                job_id=world.job_id,
                profile={**profile, "candidate_id": str(cand.id)},
                status="ready",
            )
        )
        db.add(
            Consent(
                interview_id=interview.id,
                candidate_id=cand.id,
                policy_version="2026-10-01",
                policy_hash="h" * 64,
                items={"microphone": True, "audio_recording": False, "transcript": True},
            )
        )
        return PersonaWorld(candidate_id=cand.id, interview_id=interview.id)


__all__ = ["QUALITY_ROUND", "PersonaWorld", "QualityWorld", "add_persona", "build_quality_world"]
