"""Import every model so Alembic autogenerate and relationship resolution see
the complete metadata."""

from app.db.models.candidates import Candidate, CandidateProfile, CandidateResume, ResumeChunk
from app.db.models.coding import CodeRun, CodeSnapshot, CodingHint, CodingSession
from app.db.models.identity import AuditLog, AuthSession, Organization, SystemNote, User
from app.db.models.intake import (
    IntakeActivityResult,
    IntakeCompetencyScore,
    IntakeDecision,
    IntakeEvaluation,
    IntakeEvaluatorRun,
    IntakeInterview,
    IntakeOrgSettings,
    IntakeStageResult,
)
from app.db.models.integrity import IntegrityEvent, MediaAsset
from app.db.models.interviews import (
    Answer,
    AnswerEvaluation,
    CompetencyEvidence,
    Consent,
    Interview,
    InterviewEvent,
    InterviewSession,
    Question,
    Report,
    ReviewDecision,
    Score,
    ScoreSnapshot,
    Transcript,
)
from app.db.models.jobs import Competency, InterviewTemplate, Job, JobRequirement, Round
from app.db.models.open_links import InterviewLink, LinkRegistration, LinkRegistrationRound
from app.db.models.question_bank import BankQuestion, CodingQuestion
from app.db.models.system import BackgroundJob, ModelCall

__all__ = [
    "Answer",
    "AnswerEvaluation",
    "AuditLog",
    "AuthSession",
    "BackgroundJob",
    "BankQuestion",
    "Candidate",
    "CandidateProfile",
    "CandidateResume",
    "CodeRun",
    "CodeSnapshot",
    "CodingHint",
    "CodingQuestion",
    "CodingSession",
    "Competency",
    "CompetencyEvidence",
    "Consent",
    "IntakeActivityResult",
    "IntakeCompetencyScore",
    "IntakeDecision",
    "IntakeEvaluation",
    "IntakeEvaluatorRun",
    "IntakeInterview",
    "IntakeOrgSettings",
    "IntakeStageResult",
    "IntegrityEvent",
    "Interview",
    "InterviewEvent",
    "InterviewLink",
    "InterviewSession",
    "InterviewTemplate",
    "Job",
    "JobRequirement",
    "LinkRegistration",
    "LinkRegistrationRound",
    "MediaAsset",
    "ModelCall",
    "Organization",
    "Question",
    "Report",
    "ResumeChunk",
    "ReviewDecision",
    "Round",
    "Score",
    "ScoreSnapshot",
    "SystemNote",
    "Transcript",
    "User",
]
