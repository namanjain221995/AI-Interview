"""Completed intake interview fixture for demos, the staff UI and end-to-end tests (no models are called).

``python -m app.services.intake.demo --org-slug demo [--variant complete|incomplete] [--decide] [--count N]``

Creates (in the given organisation): the intake demo job + round when missing (title "AI/ML Training Program —
Intake", round config from the catalog's intake template), a fictional candidate, a completed invitation with its
intake row (onboarding answers; MQ1 enabled with answers for the human-review block), a finished session with every
question, answer, transcript, activity capture and stage result of the seven stages
(:mod:`app.services.intake.demo_content`), one flagged integrity observation, then runs the real evaluation
pipeline with deterministic evaluator outputs (:class:`~app.services.intake.evaluators.FixtureBackend`) and the
report job with the deterministic training plan. ``--decide`` also records a human decision (mirrored into
review decisions) by the organisation's first admin or recruiter.

The ``incomplete`` variant ends after the reading stage: two competencies lack evidence, so the evaluation shows
``insufficient_evidence`` with a labelled partial total.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rbac import Principal
from app.core.security import generate_invite_token
from app.db.models import (
    Answer,
    Candidate,
    IntegrityEvent,
    Interview,
    InterviewSession,
    Job,
    Organization,
    Question,
    Round,
    Transcript,
    User,
)
from app.db.models.intake import IntakeActivityResult, IntakeInterview, IntakeStageResult
from app.db.session import session_scope
from app.domain.enums import Role, RoundType, SubjectType
from app.schemas.intake import IntakeInviteSettings, WorkAuthorizationPolicy
from app.schemas.rounds import RoundConfig
from app.services.intake import demo_content as content
from app.services.intake.evaluators import FixtureBackend
from app.services.intake.evidence import assign_aliases, load_evaluation_inputs
from app.services.intake.kit import load_intake_kit
from app.services.intake.pipeline import evaluate_session, generate_intake_report

log = get_logger(__name__)

DEMO_JOB_TITLE = "AI/ML Training Program — Intake"
DEMO_ROUND_NAME = "Intake Interview"


@dataclass(slots=True)
class DemoIntake:
    organization_id: uuid.UUID
    intake_id: uuid.UUID
    interview_id: uuid.UUID
    session_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    evaluation_id: uuid.UUID | None = None
    report_id: uuid.UUID | None = None
    band: str | None = None
    decision_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in asdict(self).items()}


def intake_round_config() -> dict[str, Any]:
    """The catalog's default intake template (falls back to the RoundConfig defaults)."""
    try:
        from app.seed.loaders import load_round_templates

        templates = {t.round_type: t for t in load_round_templates()}
        template = templates.get(RoundType.INTAKE)
        if template is not None:
            config = template.config.model_dump(mode="json")
            config["name"] = DEMO_ROUND_NAME
            return RoundConfig.model_validate(config).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - samples not mounted: minimal valid intake round
        log.warning("intake.demo_template_unavailable", error=type(exc).__name__)
    return RoundConfig(round_type=RoundType.INTAKE, name=DEMO_ROUND_NAME).model_dump(mode="json")


async def ensure_demo_job(db: AsyncSession, organization_id: uuid.UUID) -> tuple[Job, Round]:
    """The organisation's intake demo job with an active intake round (created when missing)."""
    row = (
        await db.execute(
            select(Job, Round)
            .join(Round, Round.job_id == Job.id)
            .where(
                Job.organization_id == organization_id,
                Round.round_type == RoundType.INTAKE.value,
                Round.is_active.is_(True),
            )
            .order_by(Job.created_at)
            .limit(1)
        )
    ).first()
    if row is not None:
        return row[0], row[1]
    config = intake_round_config()
    job = Job(
        organization_id=organization_id,
        title=DEMO_JOB_TITLE,
        department="Training",
        employment_type="training_program",
        seniority="entry",
        status="open",
        description=(
            "Intensive, time-boxed AI/ML training program. Every applicant takes a 45-minute intake interview; a "
            "person on the training team reviews it and makes every decision."
        ),
        candidate_report_mode="never",
        allow_candidate_round_selection=False,
    )
    db.add(job)
    await db.flush()
    round_ = Round(
        job_id=job.id,
        name=DEMO_ROUND_NAME,
        round_type=RoundType.INTAKE.value,
        sequence=1,
        duration_minutes=int(config.get("duration_minutes", 45)),
        max_duration_minutes=int(config.get("max_duration_minutes", 50)),
        expected_difficulty=str(config.get("expected_difficulty", "medium")),
        candidate_selectable=False,
        is_active=True,
        instructions=config.get("instructions"),
        config=config,
    )
    db.add(round_)
    await db.flush()
    return job, round_


def _copy(namespace: Any, model: Any, **overrides: Any) -> Any:
    columns = {c.key for c in model.__table__.columns}
    values = {k: v for k, v in vars(namespace).items() if k in columns}
    values.update(overrides)
    return model(**values)


async def create_demo_rows(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    variant: str = "complete",
    with_eligibility: bool = True,
    started_at: datetime | None = None,
    candidate_name: str = content.CANDIDATE_NAME,
) -> DemoIntake:
    """Insert the demo job (when missing), candidate, invitation, intake row and the finished session."""
    kit = load_intake_kit()
    job, round_ = await ensure_demo_job(db, organization_id)
    started_at = started_at or (datetime.now(UTC) - timedelta(hours=3)).replace(microsecond=0)
    candidate = Candidate(
        organization_id=organization_id,
        full_name=candidate_name,
        email=f"intake-demo-{secrets.token_hex(4)}@example.test",
        source="intake_demo",
    )
    db.add(candidate)
    await db.flush()
    _token, token_hash = generate_invite_token()
    interview = Interview(
        organization_id=organization_id,
        candidate_id=candidate.id,
        job_id=job.id,
        round_id=round_.id,
        status="completed",
        invite_token_hash=token_hash,
        invite_expires_at=started_at + timedelta(days=7),
        verified_at=started_at - timedelta(minutes=20),
        completed_at=started_at + timedelta(minutes=46),
        policy_snapshot={"candidate_report_mode": "never"},
    )
    db.add(interview)
    await db.flush()
    settings = IntakeInviteSettings(
        work_authorization=WorkAuthorizationPolicy(
            enabled=with_eligibility, country=content.ELIGIBILITY_COUNTRY if with_eligibility else None
        ),
        program_name=content.PROGRAM_NAME,
        kit_version=kit.version,
        kit_sha256=kit.source.sha256,
    ).model_dump(mode="json")
    session_id = uuid.uuid4()
    intake = IntakeInterview(
        organization_id=organization_id,
        candidate_id=candidate.id,
        job_id=job.id,
        round_id=round_.id,
        interview_id=interview.id,
        status="active",
        title="Intake Interview",
        settings=settings,
        sent_at=started_at - timedelta(days=1),
    )
    db.add(intake)
    await db.flush()
    rows = content.build_demo_rows(
        session_id=session_id,
        organization_id=organization_id,
        intake_id=intake.id,
        interview_id=interview.id,
        candidate_id=candidate.id,
        started_at=started_at,
        variant=variant,
        with_eligibility=with_eligibility,
    )
    intake.onboarding = dict(rows.onboarding, full_name=candidate_name)
    intake.onboarding_submitted_at = started_at - timedelta(hours=2)
    intake.eligibility = rows.eligibility
    session = InterviewSession(
        id=session_id,
        interview_id=interview.id,
        organization_id=organization_id,
        candidate_id=candidate.id,
        job_id=job.id,
        round_id=round_.id,
        status="completing",
        round_config=dict(round_.config),
        state={},
        started_at=rows.session.started_at,
        ended_at=rows.session.ended_at,
        end_reason=rows.session.end_reason,
        elapsed_seconds=rows.session.elapsed_seconds,
    )
    db.add(session)
    await db.flush()
    db.add_all(_copy(q, Question) for q in rows.questions)
    await db.flush()
    db.add_all(_copy(a, Answer) for a in rows.answers)
    await db.flush()
    db.add_all(_copy(t, Transcript) for t in rows.transcripts)
    db.add_all(_copy(a, IntakeActivityResult) for a in rows.activities)
    db.add_all(_copy(s, IntakeStageResult) for s in rows.stages)
    db.add(
        IntegrityEvent(
            session_id=session_id,
            organization_id=organization_id,
            type="tab_hidden",
            source="browser",
            severity="medium",
            occurred_at=started_at + timedelta(minutes=19),
            elapsed_seconds=19 * 60.0,
            duration_ms=6200,
            count=1,
            details={"note": "demo observation"},
            action="flag_for_human_review",
            review_status="unreviewed",
        )
    )
    await db.flush()
    return DemoIntake(
        organization_id=organization_id,
        intake_id=intake.id,
        interview_id=interview.id,
        session_id=session_id,
        candidate_id=candidate.id,
        job_id=job.id,
    )


async def demo_backend(session_id: uuid.UUID) -> FixtureBackend:
    """Canned evaluator outputs for the demo session (aliases resolved exactly as the pipeline assigns them)."""
    async with session_scope() as db:
        inputs = await load_evaluation_inputs(db, session_id)
    assign_aliases(inputs)
    return FixtureBackend(content.demo_evaluator_outputs(inputs))


async def record_demo_decision(demo: DemoIntake, reviewer: User) -> uuid.UUID:
    from app.services.intake.decisions import create_decision

    principal = Principal(
        subject_type=SubjectType.USER,
        user_id=reviewer.id,
        organization_id=reviewer.organization_id,
        role=Role(reviewer.role),
        email=reviewer.email,
        display_name=reviewer.full_name,
    )
    async with session_scope() as db:
        intake = await db.get(IntakeInterview, demo.intake_id)
        session = await db.get(InterviewSession, demo.session_id)
        assert intake is not None
        decision = await create_decision(
            db,
            principal,
            intake=intake,
            session=session,
            outcome="accept_with_notes",
            rationale="Solid pipeline experience and a clear relay; plan extra statistics practice in week one.",
        )
        return decision.id


async def create_demo_intake(
    organization_id: uuid.UUID,
    *,
    variant: str = "complete",
    with_eligibility: bool = True,
    decide: bool = False,
    reviewer: User | None = None,
    candidate_name: str = content.CANDIDATE_NAME,
) -> DemoIntake:
    """Create one completed demo intake, evaluate it and render its report (optionally decide it)."""
    async with session_scope() as db:
        demo = await create_demo_rows(
            db,
            organization_id,
            variant=variant,
            with_eligibility=with_eligibility,
            candidate_name=candidate_name,
        )
    backend = await demo_backend(demo.session_id)
    evaluation = await evaluate_session(
        demo.session_id, backend=backend, enqueue_followups=False, wait_for_transcripts=False
    )
    demo.evaluation_id, demo.band = evaluation.evaluation_id, evaluation.band
    report = await generate_intake_report(demo.session_id, evaluation.evaluation_id, use_model=False)
    demo.report_id = report.report_id
    if decide and reviewer is not None:
        demo.decision_id = await record_demo_decision(demo, reviewer)
    return demo


async def _main(args: argparse.Namespace) -> int:
    async with session_scope() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == args.org_slug))
        if org is None:
            print(f"organisation {args.org_slug!r} not found", file=sys.stderr)
            return 2
        reviewer = await db.scalar(
            select(User)
            .where(User.organization_id == org.id, User.role.in_([Role.ADMIN.value, Role.RECRUITER.value]))
            .order_by(User.created_at)
            .limit(1)
        )
        org_id = org.id
    for n in range(max(1, args.count)):
        name = content.CANDIDATE_NAME if args.count == 1 else f"{content.CANDIDATE_NAME} {n + 1}"
        demo = await create_demo_intake(
            org_id,
            variant=args.variant,
            with_eligibility=not args.no_eligibility,
            decide=args.decide,
            reviewer=reviewer,
            candidate_name=name,
        )
        print(
            f"intake {demo.intake_id} session {demo.session_id} evaluation {demo.evaluation_id} "
            f"band {demo.band} report {demo.report_id}"
            + (f" decision {demo.decision_id}" if demo.decision_id else "")
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--org-slug", default="demo")
    parser.add_argument("--variant", choices=("complete", "incomplete"), default="complete")
    parser.add_argument("--decide", action="store_true", help="also record a human decision")
    parser.add_argument(
        "--no-eligibility", action="store_true", help="MQ1 not enabled for the demo invitation"
    )
    parser.add_argument("--count", type=int, default=1)
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
