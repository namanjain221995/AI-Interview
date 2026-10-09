"""A candidate with every kind of record (and real files under the test storage root),
for the permanent-delete tests. No model calls: reports use ``use_model=False`` and
the intake evaluation runs on the demo :class:`FixtureBackend`."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update

from app.db.models import (
    Answer,
    AnswerEvaluation,
    AuthSession,
    Candidate,
    CandidateProfile,
    CandidateResume,
    CodeRun,
    CodeSnapshot,
    CodingHint,
    CodingSession,
    CompetencyEvidence,
    Consent,
    IntegrityEvent,
    Interview,
    InterviewEvent,
    InterviewLink,
    InterviewSession,
    LinkRegistration,
    LinkRegistrationRound,
    MediaAsset,
    ModelCall,
    Question,
    Report,
    ResumeChunk,
    ReviewDecision,
    Score,
    ScoreSnapshot,
    Transcript,
    User,
)
from app.db.models.intake import (
    IntakeActivityResult,
    IntakeCompetencyScore,
    IntakeDecision,
    IntakeEvaluation,
    IntakeEvaluatorRun,
    IntakeInterview,
    IntakeStageResult,
)
from app.db.session import session_scope
from app.services.identity.sessions import start_candidate_session
from app.services.intake.demo import create_demo_intake
from app.services.reports import generate_reports
from app.services.storage.media import store_media
from tests.integration.reports.factories import SessionFixture, create_completed_session

API = "/api/v1"


@dataclass(slots=True)
class World:
    organization_id: uuid.UUID
    candidate_id: uuid.UUID
    email: str
    intake_id: uuid.UUID
    intake_session_id: uuid.UUID
    intake_interview_id: uuid.UUID
    session: SessionFixture
    link_id: uuid.UUID
    session_ids: list[uuid.UUID] = field(default_factory=list)
    interview_ids: list[uuid.UUID] = field(default_factory=list)
    file_keys: list[str] = field(default_factory=list)
    model_call_ids: list[uuid.UUID] = field(default_factory=list)


async def count(model: Any, *where: Any) -> int:
    async with session_scope() as db:
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def stored_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


async def _store(db: Any, org: uuid.UUID, kind: str, key: str, data: bytes, **kwargs: Any) -> MediaAsset:
    return await store_media(
        db,
        organization_id=org,
        kind=kind,
        key=key,
        data=data,
        content_type=kwargs.pop("content_type"),
        **kwargs,
    )


async def add_session_records(
    org: uuid.UUID, fx: SessionFixture, *, reviewer_id: uuid.UUID, storage: Any
) -> list[str]:
    """Real files and the extra rows the completed-session factory does not create; returns the file keys."""
    audio_key = f"orgs/{org}/sessions/{fx.session_id}/audio/1.wav"
    await storage.put(audio_key, b"RIFF0000WAVEfmt ", "audio/wav")
    await generate_reports(fx.session_id, use_model=False)
    keys = [audio_key]
    async with session_scope() as db:
        frame = await _store(
            db,
            org,
            "camera_frame",
            f"orgs/{org}/sessions/{fx.session_id}/frames/{secrets.token_hex(4)}.jpg",
            b"\xff\xd8\xff frame",
            content_type="image/jpeg",
            session_id=fx.session_id,
            candidate_id=fx.candidate_id,
        )
        recording = await _store(
            db,
            org,
            "video_segment",
            f"orgs/{org}/sessions/{fx.session_id}/recordings/camera-p1-0001.webm",
            b"\x1aE\xdf\xa3 webm",
            content_type="video/webm",
            session_id=fx.session_id,
            candidate_id=fx.candidate_id,
        )
        keys += [frame.storage_key, recording.storage_key]
        db.add(
            IntegrityEvent(
                session_id=fx.session_id,
                organization_id=org,
                type="face_missing",
                source="vision",
                severity="medium",
                evidence_media_id=frame.id,
                action="flag_for_human_review",
            )
        )
        if fx.coding_session_id:
            db.add(
                CodeSnapshot(
                    coding_session_id=fx.coding_session_id,
                    seq=1,
                    language="python",
                    code="def noisiest(logs):\n    return None\n",
                    char_count=40,
                    trigger="autosave",
                )
            )
        db.add(Score(session_id=fx.session_id, category="overall", key="overall", score=71.0, confidence=0.7))
        db.add(
            ScoreSnapshot(
                session_id=fx.session_id,
                elapsed_seconds=120.0,
                competency_key="python",
                score=7.0,
                confidence=0.6,
            )
        )
        db.add(
            ReviewDecision(session_id=fx.session_id, reviewer_id=reviewer_id, decision="advance", notes="ok")
        )
        db.add(
            Consent(
                interview_id=fx.interview_id,
                candidate_id=fx.candidate_id,
                policy_version="v1",
                policy_hash="0" * 64,
                ip_address="10.9.8.7",
            )
        )
        report_keys = [
            k
            for k in (
                await db.scalars(
                    select(Report.artifact_storage_key).where(Report.session_id == fx.session_id)
                )
            ).all()
            if k
        ]
        keys += report_keys
    return keys


async def build_world(
    *, org: uuid.UUID, admin_user_id: uuid.UUID, storage: Any, recruiter: Any, name: str = "Erin Erasure"
) -> World:
    async with session_scope() as db:
        reviewer = await db.get(User, admin_user_id)
        assert reviewer is not None
        db.expunge(reviewer)
    # Intake interview: candidate, invitation, session, stage/activity rows, evaluation (+ runs, scores),
    # report with its HTML artifact, decision (mirrored review decision).
    demo = await create_demo_intake(org, decide=True, reviewer=reviewer, candidate_name=name)
    cid = demo.candidate_id
    # A regular completed session (questions, answers, transcripts, evaluations, evidence, coding,
    # integrity events, interview events, model calls), moved to the same candidate.
    fx = await create_completed_session(org, candidate_name=name)
    async with session_scope() as db:
        old = fx.candidate_id
        await db.execute(update(Interview).where(Interview.candidate_id == old).values(candidate_id=cid))
        await db.execute(
            update(InterviewSession).where(InterviewSession.candidate_id == old).values(candidate_id=cid)
        )
        await db.execute(update(MediaAsset).where(MediaAsset.candidate_id == old).values(candidate_id=cid))
        await db.execute(delete(Candidate).where(Candidate.id == old))
        email = (await db.get(Candidate, cid)).email  # type: ignore[union-attr]
    fx.candidate_id = cid
    keys = await add_session_records(org, fx, reviewer_id=admin_user_id, storage=storage)

    async with session_scope() as db:
        # Intake report artifact + a captured activity recording.
        keys += [
            k
            for k in (
                await db.scalars(
                    select(Report.artifact_storage_key).where(Report.session_id == demo.session_id)
                )
            ).all()
            if k
        ]
        capture = await _store(
            db,
            org,
            "answer_audio",
            f"orgs/{org}/sessions/{demo.session_id}/intake/reading-1.wav",
            b"RIFF capture",
            content_type="audio/wav",
            session_id=demo.session_id,
            candidate_id=cid,
        )
        keys.append(capture.storage_key)
        await db.execute(
            update(IntakeActivityResult)
            .where(IntakeActivityResult.session_id == demo.session_id)
            .values(audio_media_id=capture.id)
        )
        # Ledger row referenced only by an intake evaluator run (no session id on the call).
        evaluator_call = ModelCall(
            organization_id=org,
            task="answer_evaluation",
            endpoint="main",
            model="test-intake-evaluator",
            status="ok",
            latency_ms=1200.0,
            request_payload={"prompt": f"{name} said ..."},
            response_payload={"text": "..."},
        )
        db.add(evaluator_call)
        await db.flush()
        await db.execute(
            update(IntakeEvaluatorRun)
            .where(IntakeEvaluatorRun.session_id == demo.session_id)
            .values(model_call_id=evaluator_call.id)
        )
        session_call = ModelCall(
            organization_id=org,
            session_id=fx.session_id,
            task="answer_evaluation",
            endpoint="main",
            model="test-session-model",
            status="ok",
            latency_ms=800.0,
            request_payload={"prompt": f"{name} ..."},
        )
        db.add(session_call)
        # Résumé (+ chunk) and profile with real bytes.
        resume_id = uuid.uuid4()
        resume_bytes = f"{name}\nKubernetes engineer\n".encode()
        resume_media = await _store(
            db,
            org,
            "resume",
            f"orgs/{org}/resumes/{cid}/{resume_id}/cv.txt",
            resume_bytes,
            content_type="text/plain",
            candidate_id=cid,
        )
        keys.append(resume_media.storage_key)
        db.add(
            CandidateResume(
                id=resume_id,
                candidate_id=cid,
                organization_id=org,
                interview_id=fx.interview_id,
                storage_key=resume_media.storage_key,
                original_filename="erin_cv.txt",
                content_type="text/plain",
                size_bytes=len(resume_bytes),
                sha256=hashlib.sha256(resume_bytes).hexdigest(),
                status="ready",
            )
        )
        await db.flush()
        db.add(ResumeChunk(resume_id=resume_id, candidate_id=cid, chunk_index=0, text="Kubernetes engineer"))
        db.add(CandidateProfile(candidate_id=cid, resume_id=resume_id, profile={"headline": "SRE"}))
        await db.flush()
        model_call_ids = [evaluator_call.id, session_call.id]

    # Open-link registration (with a round pointing at the regular interview) + a blocked attempt.
    link = (
        await recruiter.post(
            f"{API}/open-links",
            json={"job_id": str(fx.job_id), "name": "Drive", "round_ids": [str(fx.round_id)]},
        )
    ).json()["link"]
    link_id = uuid.UUID(link["id"])
    async with session_scope() as db:
        registration = LinkRegistration(
            organization_id=org,
            link_id=link_id,
            job_id=fx.job_id,
            candidate_id=cid,
            status="completed",
            full_name=name,
            email=email,
            email_key=email.lower(),
            answers={"full_name": name, "email": email},
        )
        db.add(registration)
        db.add(
            LinkRegistration(
                organization_id=org,
                link_id=link_id,
                job_id=fx.job_id,
                candidate_id=None,
                status="blocked",
                full_name=name,
                email=email,
                email_key=email.lower(),
                answers={"full_name": name},
            )
        )
        await db.flush()
        db.add(
            LinkRegistrationRound(
                registration_id=registration.id,
                round_id=fx.round_id,
                round_name="Round 1",
                sequence=1,
                interview_id=fx.interview_id,
                unlocked_by="auto",
            )
        )
        tokens = await start_candidate_session(
            db, candidate_id=cid, interview_id=fx.interview_id, organization_id=org
        )
        assert tokens.access_token
        await db.execute(
            update(InterviewSession)
            .where(InterviewSession.id.in_([fx.session_id, demo.session_id]))
            .values(status="completed", updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
    return World(
        organization_id=org,
        candidate_id=cid,
        email=email,
        intake_id=demo.intake_id,
        intake_session_id=demo.session_id,
        intake_interview_id=demo.interview_id,
        session=fx,
        link_id=link_id,
        session_ids=[fx.session_id, demo.session_id],
        interview_ids=[fx.interview_id, demo.interview_id],
        file_keys=keys,
        model_call_ids=model_call_ids,
    )


def session_tables(
    session_ids: list[uuid.UUID], coding_session_id: uuid.UUID | None
) -> list[tuple[Any, Any]]:
    """Every table that holds rows of a session (model, condition)."""
    rows: list[tuple[Any, Any]] = [
        (InterviewSession, InterviewSession.id.in_(session_ids)),
        (InterviewEvent, InterviewEvent.session_id.in_(session_ids)),
        (Question, Question.session_id.in_(session_ids)),
        (Answer, Answer.session_id.in_(session_ids)),
        (Transcript, Transcript.session_id.in_(session_ids)),
        (AnswerEvaluation, AnswerEvaluation.session_id.in_(session_ids)),
        (CompetencyEvidence, CompetencyEvidence.session_id.in_(session_ids)),
        (Score, Score.session_id.in_(session_ids)),
        (ScoreSnapshot, ScoreSnapshot.session_id.in_(session_ids)),
        (Report, Report.session_id.in_(session_ids)),
        (ReviewDecision, ReviewDecision.session_id.in_(session_ids)),
        (CodingSession, CodingSession.session_id.in_(session_ids)),
        (IntegrityEvent, IntegrityEvent.session_id.in_(session_ids)),
        (IntakeStageResult, IntakeStageResult.session_id.in_(session_ids)),
        (IntakeActivityResult, IntakeActivityResult.session_id.in_(session_ids)),
        (IntakeEvaluation, IntakeEvaluation.session_id.in_(session_ids)),
        (IntakeEvaluatorRun, IntakeEvaluatorRun.session_id.in_(session_ids)),
        (IntakeCompetencyScore, IntakeCompetencyScore.session_id.in_(session_ids)),
        (IntakeDecision, IntakeDecision.session_id.in_(session_ids)),
        (MediaAsset, MediaAsset.session_id.in_(session_ids)),
    ]
    if coding_session_id is not None:
        rows += [
            (CodeRun, CodeRun.coding_session_id == coding_session_id),
            (CodeSnapshot, CodeSnapshot.coding_session_id == coding_session_id),
            (CodingHint, CodingHint.coding_session_id == coding_session_id),
        ]
    return rows


def candidate_tables(world: World) -> list[tuple[Any, Any]]:
    cid = world.candidate_id
    return [
        (Candidate, Candidate.id == cid),
        (CandidateResume, CandidateResume.candidate_id == cid),
        (ResumeChunk, ResumeChunk.candidate_id == cid),
        (CandidateProfile, CandidateProfile.candidate_id == cid),
        (Interview, Interview.candidate_id == cid),
        (Consent, Consent.candidate_id == cid),
        (AuthSession, AuthSession.candidate_id == cid),
        (IntakeInterview, IntakeInterview.candidate_id == cid),
        (LinkRegistration, LinkRegistration.candidate_id == cid),
        (LinkRegistration, LinkRegistration.email == world.email),
        (LinkRegistrationRound, LinkRegistrationRound.interview_id.in_(world.interview_ids)),
        (InterviewSession, InterviewSession.candidate_id == cid),
        *session_tables(world.session_ids, world.session.coding_session_id),
    ]


__all__ = [
    "API",
    "InterviewLink",
    "World",
    "build_world",
    "candidate_tables",
    "count",
    "session_tables",
    "stored_files",
]
