"""Fixtures for interview-engine integration tests (Postgres + Redis + mock AI).

* ``world`` - builds an organization, job, round, candidate, resume profile
  with claims, consent, interview (policy snapshot) and question bank rows.
* ``engine`` - an ``InterviewEngine`` with a fake clock, the local scoring
  stub (``FallbackScoring``) and an in-memory fake coding service.
* ``SimulatedInterviewer`` - a scripted TURN_DECISION model that follows the
  policy directive found in the prompt (like a well-behaved LLM would) and
  grades the last answer from markers such as ``[strong]`` / ``[vague]``.
* ``MarkerEvaluator`` - scripted ANSWER_EVALUATION grading by markers.
"""

from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio

from app.ai.mock import MockAIServices, MockLLMCall
from app.db.models import (
    BankQuestion,
    Candidate,
    CandidateProfile,
    CandidateResume,
    CodingQuestion,
    Consent,
    Interview,
    Job,
    JobRequirement,
    Organization,
    Round,
)
from app.db.session import session_scope
from app.domain.enums import AnswerQuality, CodeLanguage, Difficulty, ModelTask, TurnAction
from app.schemas.ai import (
    AnswerAssessment,
    AnswerEvaluation,
    ContradictionFinding,
    DimensionScores,
    EvidenceItem,
    TurnDecision,
)
from app.schemas.policies import OrganizationSettings, PolicySnapshot
from app.schemas.realtime import CodingQuestionPublic, CodingStateMsg
from app.services.interview.adapters import CodingPort, ScoringPort
from app.services.interview.config import EngineConfig
from app.services.interview.contracts import (
    CodingStarted,
    SessionClosing,
    SpeechDelta,
    UtteranceBegin,
    UtteranceMeta,
)
from app.services.interview.engine import InterviewEngine
from app.services.interview.scoring_fallback import FallbackScoring
from app.services.interview.sessions import SessionServiceImpl

MARKERS = {
    "[strong]": AnswerQuality.STRONG,
    "[adequate]": AnswerQuality.ADEQUATE,
    "[partial]": AnswerQuality.PARTIAL,
    "[vague]": AnswerQuality.VAGUE,
    "[incorrect]": AnswerQuality.INCORRECT,
}


def marker_quality(text: str) -> AnswerQuality | None:
    for marker, quality in MARKERS.items():
        if marker in text:
            return quality
    return None


# ---------------------------------------------------------------- clock


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


# ---------------------------------------------------------------- simulated model


def _field(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


FOLLOWUP_ASPECTS = ("failure recovery", "capacity planning", "monitoring and alerting", "rollout safety", "cost control")


@dataclass
class SimulatedInterviewer:
    """TURN_DECISION responder that follows the prompt's directive."""

    follow_directive: bool = True
    override_action: dict[int, str] = field(default_factory=dict)  # call index -> action
    calls: int = 0
    decisions: list[TurnDecision] = field(default_factory=list)

    def __call__(self, call: MockLLMCall) -> TurnDecision:
        self.calls += 1
        prompt = "\n".join(str(m.get("content", "")) for m in call.messages)
        user = str(call.messages[-1].get("content", ""))
        coding = "CODING EXERCISE IN PROGRESS" in user
        rec = _field(r"Recommended action: (\w+)", user) or "ask_question"
        action = self.override_action.get(self.calls, rec)
        key = _field(r"Target competency: (\w+)", user)
        difficulty = (
            _field(r"Target competency: \w+ \([^)]*\); difficulty: (easy|medium|hard)", user) or "medium"
        )
        claim_id = _field(r"Claim to verify: (\w+):", user)
        bank_id = _field(r"- id=([0-9a-f-]{36})", user)
        last = user.split("LAST ANSWER", 1)[1] if "LAST ANSWER" in user else ""
        if coding:
            last = user.split("WHAT THE CANDIDATE JUST SAID", 1)[1]
        quality = marker_quality(last.split("DIRECTIVE", 1)[0]) if last else None
        if coding:
            action = "coding_comment" if "?" in last.split("DIRECTIVE")[0] else "acknowledge_and_wait"
        n = self.calls
        speeches = {
            "request_specificity": f"What was the measured impact, roughly, and over what time range? (turn {n})",
            # Follow-ups go one level deeper on different aspects (an identical re-ask is a repeat: policy.same_point).
            "ask_followup": f"How did you handle {FOLLOWUP_ASPECTS[n % len(FOLLOWUP_ASPECTS)]} in that setup? (turn {n})",
            "increase_difficulty": f"How would that design change at ten times the traffic? (turn {n})",
            "simplify": f"Let's narrow it down: what is the first thing you would check? (turn {n})",
            "clarify_question": f"Let me put it another way: what did you personally build there? (turn {n})",
            "verify_claim": f"Your resume mentions a large platform; what did you personally own there? (turn {n})",
            "verify_contradiction": f"Earlier you mentioned one thing; help me reconcile that with the other? (turn {n})",
            "change_topic": f"Let's shift gears to {key}: how have you used it recently? (turn {n})",
            "ask_question": f"Can you walk me through a recent project involving {key}? (turn {n})",
            "acknowledge_and_wait": "Of course, take your time.",
            "answer_candidate_question": "The hiring team can share more about that when they follow up. Anything else you'd like to know?",
            "redirect": f"Let's come back to the question about {key}? (turn {n})",
            "invite_candidate_questions": "Before we wrap up, what questions do you have for me about the role?",
            "coding_comment": "Good thinking. What is the time complexity of that approach?",
        }
        speech = speeches.get(action, f"Tell me more about {key}? (turn {n})")
        decision = TurnDecision(
            speech=speech,
            action=TurnAction(action),
            answer_quality=quality,
            competency_key=key,
            topic=f"{key}-topic-{n}"
            if action in ("ask_question", "change_topic", "verify_claim")
            else (f"{key}-topic" if key else None),
            difficulty=Difficulty(difficulty),
            kind="technical",
            expression="curious",
            gesture="none",
            gesture_intensity="low",
            question_focus=speech[:120],
            expected_evidence=["specifics", "trade-offs"],
            claim_id=claim_id if action == "verify_claim" else None,
            bank_question_id=bank_id if bank_id and action in ("ask_question", "change_topic") else None,
            missing_specifics=[],
            new_claims=[],
            possible_contradiction=None,
            reason=f"directive {rec}",
        )
        self.decisions.append(decision)
        del prompt
        return decision


@dataclass
class MarkerEvaluator:
    """ANSWER_EVALUATION responder grading by markers in the answer."""

    contradiction_on: str | None = None
    calls: int = 0

    def __call__(self, call: MockLLMCall) -> AnswerEvaluation:
        self.calls += 1
        user = str(call.messages[-1].get("content", ""))
        answer = user.split("ANSWER TO EVALUATE:", 1)[1]
        key = _field(r"competency (\w+)\)", user) or "kubernetes"
        quality = marker_quality(answer) or AnswerQuality.ADEQUATE
        score = {
            AnswerQuality.STRONG: 9.0,
            AnswerQuality.ADEQUATE: 7.0,
            AnswerQuality.PARTIAL: 5.0,
            AnswerQuality.VAGUE: 3.0,
            AnswerQuality.INCORRECT: 2.0,
        }[quality]
        positive = quality in (AnswerQuality.STRONG, AnswerQuality.ADEQUATE)
        evidence = [
            EvidenceItem(
                competency_key=key,
                dimension=dim,
                signal="positive" if positive else "negative",
                evidence=f"{quality.value} answer on {dim}",
                quote=None,
                strength=0.85 if quality is AnswerQuality.STRONG else 0.6,
                confidence=0.85,
            )
            for dim in ("depth", "practical_experience")
        ]
        contradictions = []
        if self.contradiction_on and self.contradiction_on in answer:
            contradictions.append(
                ContradictionFinding(
                    statement="Managed 3 nodes",
                    conflicts_with="Resume says 40-node platform",
                    severity="medium",
                )
            )
        return AnswerEvaluation(
            overall_quality=quality,
            scores=DimensionScores(
                correctness=score, depth=score, specificity=score, practical_experience=score, reasoning=score
            ),
            summary=f"{quality.value} answer",
            evidence=evidence,
            contradictions=contradictions,
            evaluation_confidence=0.9 if quality is AnswerQuality.STRONG else 0.75,
        )


def quick_by_marker(call: MockLLMCall) -> AnswerAssessment:
    text = str(call.messages[-1].get("content", ""))
    answer = text.split("Answer:", 1)[1] if "Answer:" in text else text
    quality = marker_quality(answer) or AnswerQuality.ADEQUATE
    return AnswerAssessment(
        quality=quality,
        confidence=0.9,
        missing_specifics=["time range"] if quality is AnswerQuality.VAGUE else [],
    )


# ---------------------------------------------------------------- fake coding


class FakeCodingService:
    """In-memory CodingService (contract) recording calls."""

    def __init__(self, coding_question_id: str, *, hints: int = 3) -> None:
        self.coding_question_id = coding_question_id
        self.hints_left = hints
        self.used = 0
        self.calls: list[tuple[str, Any]] = []
        self.finished: list[tuple[str, str]] = []
        self.session_id = str(uuid.uuid4())

    async def select_question(self, session_id, *, competency_keys, difficulty, languages):
        self.calls.append(("select", competency_keys))
        return self.coding_question_id

    async def start(self, session_id, coding_question_id, *, question_id, language):
        self.calls.append(("start", question_id))
        return CodingStateMsg(
            coding_session_id=self.session_id,
            question=CodingQuestionPublic(
                coding_question_id=coding_question_id,
                title="Most Frequent Error Service",
                prompt_markdown="Find it.",
                difficulty="easy",
                languages=[CodeLanguage.PYTHON],
                starter_code={"python": "def f(): pass"},
                visible_tests=[],
            ),
            language=language,
            time_limit_s=600,
            hints_available=3,
        )

    async def record_update(self, coding_session_id, *, code, language, version):
        return True

    async def run(
        self, coding_session_id, *, kind, code, language, stdin=None
    ):  # pragma: no cover - not used by the engine
        raise NotImplementedError

    async def request_hint(self, coding_session_id, *, requested_via):
        self.calls.append(("hint", requested_via))
        if self.hints_left <= 0:
            return None
        self.hints_left -= 1
        self.used += 1
        return self.used, f"Level {self.used} hint: think about counting with a hash map."

    async def finish(self, coding_session_id, *, reason):
        self.finished.append((coding_session_id, reason))

    async def context(self, coding_session_id):
        from app.services.coding.contracts import CodingContext

        return CodingContext(
            coding_session_id=coding_session_id,
            question_title="Most Frequent Error Service",
            prompt_markdown="Find it.",
            difficulty=Difficulty.EASY,
            language=CodeLanguage.PYTHON,
            current_code="for a in x:\n  for b in x: pass",
            previous_code=None,
            recent_diff=None,
            snapshots_count=1,
        )

    async def state_payload(self, coding_session_id):  # pragma: no cover
        raise NotImplementedError


@dataclass
class FakeObservation:
    should_speak: bool
    speech: str
    kind: str = "question"
    focus: str = "complexity"
    behavior: Any = None


def make_observer(speak_on: set[str]) -> Callable[..., Any]:
    async def decide(context, trigger, state):
        state["calls"] = state.get("calls", 0) + 1
        if trigger in speak_on:
            return FakeObservation(
                True, "You've chosen a nested loop here. What is the current time complexity?"
            )
        return None

    return decide


# ---------------------------------------------------------------- world


@dataclass
class World:
    organization_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    candidate_id: uuid.UUID
    interview_id: uuid.UUID
    coding_question_id: uuid.UUID | None = None
    bank_ids: list[uuid.UUID] = field(default_factory=list)


def round_config_dict(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "round_type": "technical_screening",
        "name": "Technical Screening",
        "duration_minutes": 30,
        "max_duration_minutes": 35,
        "expected_difficulty": "medium",
        "competencies": [
            {
                "key": "kubernetes",
                "name": "Kubernetes",
                "weight": 1.5,
                "focus_areas": ["autoscaling", "networking"],
            },
            {"key": "aws", "name": "AWS", "weight": 1.2, "focus_areas": ["iam", "vpc"]},
            {"key": "communication", "name": "Communication", "weight": 0.4, "required": False},
        ],
        "min_evidence_per_competency": 2,
        "confidence_threshold": 0.6,
        "stopping": {
            "stop_on_sufficient_evidence": True,
            "min_duration_minutes": 6,
            "min_questions": 4,
            "max_questions": 14,
        },
        "question_strategy": {"max_followup_depth": 3, "allow_candidate_questions_at_end": True},
        "coding": {"enabled": False},
        "resume_validation": {"enabled": True, "max_claims": 2, "min_priority": 3},
        "interviewer_notes": "Suggested flow: intro (1 min), resume and technical scenarios (about 24 min), candidate questions (about 3 min).",
    }
    config.update(overrides)
    return config


PROFILE = {
    "candidate_id": "",
    "candidate_name": "Jane Doe",
    "headline": "Senior DevOps Engineer",
    "experience_years": 8,
    "roles": [
        {
            "title": "Senior DevOps Engineer",
            "company": "Acme",
            "start": "2020",
            "end": "present",
            "technologies": ["EKS", "Terraform"],
        }
    ],
    "skills": {"programming_language": [{"name": "Go", "years": 3}, {"name": "Python", "years": 6}]},
    "claims_to_verify": [
        {
            "id": "c1",
            "claim": "Ran a 40-node EKS platform serving 40 microservices",
            "priority": 5,
            "competency_keys": ["kubernetes"],
            "evidence_topics": ["node count", "autoscaling", "ingress"],
        },
        {
            "id": "c2",
            "claim": "Cut AWS spend by 35 percent",
            "priority": 4,
            "competency_keys": ["aws"],
            "evidence_topics": ["baseline spend", "measurement"],
        },
        {
            "id": "c3",
            "claim": "Familiar with Kafka",
            "priority": 2,
            "competency_keys": [],
            "evidence_topics": [],
        },
    ],
}

BANK = [
    (
        "kubernetes",
        "medium",
        "How does the Horizontal Pod Autoscaler decide when to scale, and what metrics have you used?",
        "autoscaling",
    ),
    (
        "kubernetes",
        "hard",
        "Walk me through debugging intermittent 502s behind an ingress controller in a multi-AZ cluster.",
        "networking",
    ),
    ("kubernetes", "easy", "What is the difference between a Deployment and a StatefulSet?", "workloads"),
    (
        "aws",
        "medium",
        "How would you structure IAM roles and permission boundaries for many product teams?",
        "iam",
    ),
    (
        "aws",
        "hard",
        "Design a multi-account VPC network with shared services and strict egress control.",
        "vpc",
    ),
    ("aws", "easy", "What is the difference between a security group and a network ACL?", "vpc"),
]


async def build_world(
    mock_ai: MockAIServices,
    *,
    config: dict[str, Any] | None = None,
    policy: PolicySnapshot | None = None,
    with_profile: bool = True,
    with_coding_question: bool = False,
    consent_audio: bool = True,
) -> World:
    suffix = secrets.token_hex(4)
    async with session_scope() as db:
        org = Organization(
            name=f"Org {suffix}",
            slug=f"org-{suffix}",
            is_active=True,
            settings=OrganizationSettings().model_dump(mode="json"),
        )
        db.add(org)
        await db.flush()
        job = Job(
            organization_id=org.id,
            title="Senior DevOps Engineer",
            description="Run our EKS platform.",
            status="open",
            seniority="senior",
        )
        cand = Candidate(organization_id=org.id, full_name="Jane Doe", email=f"jane-{suffix}@example.test")
        db.add_all([job, cand])
        await db.flush()
        db.add_all(
            [
                JobRequirement(
                    job_id=job.id, kind="required_skill", name="Kubernetes", competency_key="kubernetes"
                ),
                JobRequirement(job_id=job.id, kind="required_skill", name="AWS", competency_key="aws"),
            ]
        )
        cfg = config or round_config_dict()
        rnd = Round(
            job_id=job.id,
            name=cfg["name"],
            round_type=cfg["round_type"],
            duration_minutes=cfg["duration_minutes"],
            max_duration_minutes=cfg["max_duration_minutes"],
            config=cfg,
        )
        db.add(rnd)
        await db.flush()
        snapshot = policy or PolicySnapshot()
        interview = Interview(
            organization_id=org.id,
            candidate_id=cand.id,
            job_id=job.id,
            round_id=rnd.id,
            status="invited",
            invite_token_hash=secrets.token_hex(32),
            invite_expires_at=datetime.now(UTC) + timedelta(days=3),
            policy_snapshot=snapshot.model_dump(mode="json"),
        )
        db.add(interview)
        await db.flush()
        if with_profile:
            resume = CandidateResume(
                candidate_id=cand.id,
                organization_id=org.id,
                interview_id=interview.id,
                storage_key=f"orgs/{org.id}/r.pdf",
                original_filename="r.pdf",
                content_type="application/pdf",
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
                    job_id=job.id,
                    profile={**PROFILE, "candidate_id": str(cand.id)},
                    status="ready",
                )
            )
        db.add(
            Consent(
                interview_id=interview.id,
                candidate_id=cand.id,
                policy_version="2026-10-01",
                policy_hash="h" * 64,
                items={"microphone": True, "audio_recording": consent_audio, "transcript": True},
            )
        )
        bank_ids: list[uuid.UUID] = []
        texts = [b[2] for b in BANK]
        vectors = await mock_ai.embeddings.embed_documents(texts)
        for i, ((key, difficulty, text, topic), vec) in enumerate(zip(BANK, vectors, strict=True)):
            row = BankQuestion(
                organization_id=org.id,
                external_key=f"t-{suffix}-{i}",
                competency_key=key,
                difficulty=difficulty,
                kind="technical",
                text=text,
                topic=topic,
                expected_concepts=[topic],
                source="curated",
                status="active",
                embedding=vec,
            )
            db.add(row)
            await db.flush()
            bank_ids.append(row.id)
        coding_id = None
        if with_coding_question:
            cq = CodingQuestion(
                slug=f"freq-{suffix}",
                title="Most Frequent Error Service",
                difficulty="easy",
                competency_keys=["data_structures"],
                prompt_markdown="Find the service with most errors.",
                languages=["python"],
                time_limit_minutes=10,
            )
            db.add(cq)
            await db.flush()
            coding_id = cq.id
        return World(org.id, job.id, rnd.id, cand.id, interview.id, coding_id, bank_ids)


@pytest_asyncio.fixture
async def interview_env(db_session, redis, storage, mock_ai) -> AsyncIterator[dict[str, Any]]:
    """Shared plumbing: fake clock, scripted models, engine factory."""
    clock = FakeClock()
    engines: list[InterviewEngine] = []
    interviewer = SimulatedInterviewer()
    evaluator = MarkerEvaluator()
    mock_ai.llm.set_default(ModelTask.TURN_DECISION, interviewer)
    mock_ai.llm.set_default(ModelTask.ANSWER_EVALUATION, evaluator)
    mock_ai.llm.set_default(ModelTask.ANSWER_ASSESSMENT, quick_by_marker)

    def make_engine(
        *, coding: CodingPort | None = None, scoring: ScoringPort | None = None, **cfg: Any
    ) -> InterviewEngine:
        base = {
            "closing_reserve_s": 75.0,
            "default_interviewer_name": "Maya",
            "start_wait_for_plan_s": 0.3,
            "turn_first_speech_timeout_s": 3.0,
            "turn_total_timeout_s": 8.0,
            "turn_retrieval_timeout_s": 5.0,
            "postgres_flush_interval_s": 0.05,
            # Off by default in production (it rarely beat the end-of-turn under shared load);
            # these tests cover the policy path that uses it.
            "quick_assessment_enabled": True,
        }
        base.update(cfg)
        engine = InterviewEngine(
            config=EngineConfig(**base),
            ai=mock_ai,
            scoring=scoring or ScoringPort(api=FallbackScoring(), recompute=None),
            coding=coding or CodingPort(service=None, observer=None, source="test-none"),
            clock=clock,
        )
        engine.store.set_rebuild_hook(
            _rebuild_hook_with(scoring or ScoringPort(api=FallbackScoring(), recompute=None))
        )
        engines.append(engine)
        return engine

    env = {
        "clock": clock,
        "interviewer": interviewer,
        "evaluator": evaluator,
        "make_engine": make_engine,
        "mock_ai": mock_ai,
        "sessions": SessionServiceImpl(
            config=EngineConfig(closing_reserve_s=75.0, default_interviewer_name="Maya")
        ),
    }
    try:
        yield env
    finally:
        for engine in engines:
            await engine.drain_background(5.0)
            await engine.aclose()


def _rebuild_hook_with(scoring: ScoringPort):
    from app.services.interview.evaluation import recompute_competencies
    from app.services.interview.transitions import DERIVED_FIELDS

    async def hook(db, state):
        states = await recompute_competencies(
            db, state.session_id, state.round_config, state.competencies, scoring=scoring
        )
        for key, incoming in states.items():
            current = state.competencies.get(key)
            if current is None:
                state.competencies[key] = incoming
                continue
            for name in DERIVED_FIELDS:
                setattr(current, name, getattr(incoming, name))

    return hook


# ---------------------------------------------------------------- helpers


async def create_session(env: dict[str, Any], world: World, round_id: Any | None = None):
    async with session_scope() as db:
        row = await env["sessions"].create_or_resume(db, interview_id=world.interview_id, round_id=round_id)
        return str(row.id)


async def collect(stream) -> list[Any]:
    return [event async for event in stream]


def spoken(events: list[Any]) -> list[str]:
    """Full texts of the utterances in an event list."""
    out: list[str] = []
    for e in events:
        if isinstance(e, UtteranceMeta):
            out.append(e.full_text)
    return out


def metas(events: list[Any]) -> list[UtteranceMeta]:
    return [e for e in events if isinstance(e, UtteranceMeta)]


def kinds(events: list[Any]) -> list[str]:
    return [type(e).__name__ for e in events]


__all__ = [
    "CodingStarted",
    "FakeClock",
    "FakeCodingService",
    "MarkerEvaluator",
    "SessionClosing",
    "SimulatedInterviewer",
    "SpeechDelta",
    "UtteranceBegin",
    "UtteranceMeta",
    "World",
    "build_world",
    "collect",
    "create_session",
    "kinds",
    "make_observer",
    "metas",
    "round_config_dict",
    "spoken",
]
