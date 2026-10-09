"""Builds a realistic, completed interview session directly in the test DB.

Scenario: the Senior DevOps Engineer demo job's "Combined AI Round" (round
config resolved from ``samples/``), a candidate who answers most questions
well, one coding problem with a hint, resume claims, an inconsistency and
integrity signals. Every row the scoring/report pipeline reads is created.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.models import (
    Answer,
    AnswerEvaluation,
    Candidate,
    CodeRun,
    CodingHint,
    CodingQuestion,
    CodingSession,
    CompetencyEvidence,
    IntegrityEvent,
    Interview,
    InterviewEvent,
    InterviewSession,
    Job,
    MediaAsset,
    ModelCall,
    Question,
    Round,
    Transcript,
)
from app.db.session import session_scope
from app.schemas.ai import AnswerEvaluation as AnswerEvaluationDoc
from app.schemas.ai import CodingEvaluation, DimensionScores, EvidenceItem
from app.seed.loaders import load_catalog, resolve_job_rounds

_ROUND_CACHE: dict[str, Any] = {}


def devops_round_config() -> dict[str, Any]:
    if "config" not in _ROUND_CACHE:
        catalog = load_catalog()
        job = next(j for j in catalog.jobs if j.key == "senior_devops_engineer")
        _ROUND_CACHE["config"] = resolve_job_rounds(job, catalog.templates_by_type())[0].config.model_dump(
            mode="json"
        )
    return dict(_ROUND_CACHE["config"])


# (competency, difficulty, quality, level, question, answer, evidence[(dimension, signal, strength, conf, text, quote)])
QUESTION_SPECS: list[dict[str, Any]] = [
    {
        "competency": "kubernetes",
        "difficulty": "medium",
        "quality": "strong",
        "level": 8.4,
        "question": "Walk me through the EKS platform you ran: how many nodes, and how did autoscaling work?",
        "answer": "We ran three EKS clusters with about 120 nodes in production, Karpenter for node autoscaling and HPA on CPU and custom queue-depth metrics.",
        "expected": ["node count", "autoscaling", "ingress", "high availability"],
        "evidence": [
            (
                "node_count",
                "positive",
                0.8,
                0.85,
                "Gave concrete scale: three clusters, ~120 nodes.",
                "about 120 nodes in production",
            ),
            (
                "autoscaling",
                "positive",
                0.85,
                0.85,
                "Explained Karpenter plus HPA on custom metrics.",
                "Karpenter for node autoscaling",
            ),
        ],
    },
    {
        "competency": "kubernetes",
        "difficulty": "hard",
        "quality": "strong",
        "level": 8.8,
        "parent": 0,
        "question": "What happened when an availability zone failed, and how did the platform recover?",
        "answer": "Pods were spread with topology spread constraints and PDBs, so we lost a third of capacity but no service went down; Karpenter replaced nodes in the other zones within four minutes.",
        "expected": ["failure recovery", "high availability"],
        "evidence": [
            (
                "high_availability",
                "positive",
                0.85,
                0.85,
                "Topology spread constraints and PDBs kept services up during an AZ loss.",
                "no service went down",
            ),
            (
                "failure_recovery",
                "positive",
                0.8,
                0.8,
                "Quantified recovery: replacement capacity within four minutes.",
                "within four minutes",
            ),
        ],
    },
    {
        "competency": "kubernetes",
        "difficulty": "medium",
        "quality": "adequate",
        "level": 7.6,
        "question": "How did traffic get into the cluster?",
        "answer": "AWS Load Balancer Controller with ALB ingress per team, TLS terminated at the ALB, and NetworkPolicies between namespaces.",
        "expected": ["ingress"],
        "evidence": [
            (
                "ingress",
                "positive",
                0.75,
                0.8,
                "Described ALB ingress with TLS termination and network policies.",
                "ALB ingress per team",
            ),
        ],
    },
    {
        "competency": "aws",
        "difficulty": "medium",
        "quality": "adequate",
        "level": 7.2,
        "question": "How was networking set up across your AWS accounts?",
        "answer": "Each account had its own VPC, connected with Transit Gateway; private subnets for workloads and VPC endpoints for S3 and ECR. IAM was mostly roles, I didn't own the SCPs.",
        "expected": ["networking", "iam"],
        "evidence": [
            (
                "networking",
                "positive",
                0.75,
                0.8,
                "Transit Gateway, private subnets and VPC endpoints.",
                "connected with Transit Gateway",
            ),
            (
                "iam",
                "negative",
                0.5,
                0.7,
                "Limited depth on IAM governance; did not own SCPs.",
                "I didn't own the SCPs",
            ),
        ],
    },
    {
        "competency": "aws",
        "difficulty": "hard",
        "quality": "strong",
        "level": 8.2,
        "question": "How would you make the order database highly available across regions?",
        "answer": "Aurora Global Database with a secondary region, write forwarding off, and a runbook for managed failover; RPO about a second and RTO under a minute, tested quarterly.",
        "expected": ["high availability", "failover testing"],
        "evidence": [
            (
                "high_availability",
                "positive",
                0.85,
                0.8,
                "Aurora Global Database with measured RPO/RTO and tested failover.",
                "RPO about a second",
            ),
            ("failover_testing", "positive", 0.7, 0.75, "Quarterly failover tests.", "tested quarterly"),
        ],
    },
    {
        "competency": "terraform",
        "difficulty": "medium",
        "quality": "partial",
        "level": 6.0,
        "question": "How did you manage Terraform state and protect against risky changes?",
        "answer": "State lived in S3 with DynamoDB locking, one state per environment. Changes went through plan in CI, but we didn't have policy checks.",
        "expected": ["state management", "change safety"],
        "evidence": [
            (
                "state_management",
                "positive",
                0.7,
                0.8,
                "Remote state in S3 with DynamoDB locking, per environment.",
                "S3 with DynamoDB locking",
            ),
            (
                "change_safety",
                "negative",
                0.6,
                0.75,
                "No policy-as-code or guardrails on risky changes.",
                "we didn't have policy checks",
            ),
        ],
    },
    {
        "competency": "terraform",
        "difficulty": "medium",
        "quality": "adequate",
        "level": 6.8,
        "question": "How did you structure reusable modules?",
        "answer": "Versioned modules in a separate repo with semantic versioning, and Terratest for the networking module.",
        "expected": ["module design"],
        "evidence": [
            (
                "module_design",
                "positive",
                0.7,
                0.75,
                "Versioned modules with semantic versioning and Terratest.",
                "Terratest for the networking module",
            ),
        ],
    },
    {
        "competency": "ci_cd",
        "difficulty": "medium",
        "quality": "strong",
        "level": 8.0,
        "question": "Describe your deployment strategy and how rollbacks worked.",
        "answer": "Argo Rollouts canaries at 10, 30 and 100 percent gated on error rate and p99 latency from Prometheus; automatic rollback when the analysis failed.",
        "expected": ["deployment strategy", "rollback"],
        "evidence": [
            (
                "deployment_strategy",
                "positive",
                0.85,
                0.85,
                "Progressive canaries gated on SLO metrics.",
                "canaries at 10, 30 and 100 percent",
            ),
            (
                "rollback",
                "positive",
                0.8,
                0.85,
                "Automatic rollback on failed analysis.",
                "automatic rollback",
            ),
        ],
    },
    {
        "competency": "observability",
        "difficulty": "medium",
        "quality": "adequate",
        "level": 7.0,
        "question": "How did you define SLOs and keep alerting actionable?",
        "answer": "Availability and latency SLOs per user journey, multi-window burn-rate alerts, and we cut pages by about half by deleting cause-based alerts.",
        "expected": ["slo design", "alerting"],
        "evidence": [
            (
                "slo_design",
                "positive",
                0.75,
                0.8,
                "Journey-based availability and latency SLOs.",
                "SLOs per user journey",
            ),
            ("alerting", "positive", 0.75, 0.8, "Burn-rate alerts; halved pages.", "cut pages by about half"),
        ],
    },
    {
        "competency": "ownership",
        "difficulty": "medium",
        "quality": "strong",
        "level": 8.0,
        "question": "Tell me about a time you owned a problem end to end.",
        "answer": "I led the EKS migration: wrote the plan, migrated 40 services over six weeks, and ran the postmortem when one migration caused a 20-minute outage.",
        "expected": ["accountability"],
        "evidence": [
            (
                "accountability",
                "positive",
                0.8,
                0.85,
                "Owned migration plan and the postmortem of an outage.",
                "ran the postmortem",
            ),
            (
                "delivery",
                "positive",
                0.75,
                0.8,
                "Migrated 40 services in six weeks.",
                "40 services over six weeks",
            ),
        ],
    },
    {
        "competency": "ci_cd",
        "difficulty": "hard",
        "quality": "strong",
        "level": 8.3,
        "question": "How would you roll out a breaking database schema change with zero downtime?",
        "answer": "Expand and contract: add nullable columns, dual-write behind a flag, backfill in batches, switch reads, then drop the old columns a release later.",
        "expected": ["schema migration"],
        "evidence": [
            (
                "schema_migration",
                "positive",
                0.85,
                0.85,
                "Expand/contract with dual writes and batched backfill.",
                "Expand and contract",
            ),
            (
                "rollback",
                "positive",
                0.75,
                0.8,
                "Kept the old columns for a release to allow rollback.",
                "drop the old columns a release later",
            ),
        ],
    },
    {
        "competency": "observability",
        "difficulty": "medium",
        "quality": "adequate",
        "level": 7.2,
        "question": "How would you find the cause of a latency regression across services?",
        "answer": "Start from the SLO dashboard, use exemplars to jump to traces in Tempo, compare span durations before and after the deploy.",
        "expected": ["tracing"],
        "evidence": [
            (
                "tracing",
                "positive",
                0.75,
                0.8,
                "Uses exemplars to pivot from metrics to traces.",
                "exemplars to jump to traces",
            ),
            (
                "debugging",
                "positive",
                0.7,
                0.75,
                "Compares span durations around the deploy.",
                "compare span durations",
            ),
        ],
    },
    {
        "competency": "ownership",
        "difficulty": "medium",
        "quality": "strong",
        "level": 7.8,
        "question": "What did you change after that outage?",
        "answer": "I added a pre-migration checklist and automated canary checks; we had no migration-related incidents in the next two quarters.",
        "expected": ["follow through"],
        "evidence": [
            (
                "follow_through",
                "positive",
                0.8,
                0.8,
                "Turned the postmortem into automated checks with a measured outcome.",
                "no migration-related incidents",
            ),
        ],
    },
    {
        "competency": "data_structures",
        "difficulty": "medium",
        "quality": "strong",
        "level": 8.0,
        "question": "Which data structure would you use for a sliding-window rate limiter, and why?",
        "answer": "A deque of timestamps per client, popping expired entries from the left, so each request is amortized O(1); or a fixed-window counter in Redis if memory matters.",
        "expected": ["selection"],
        "evidence": [
            (
                "selection",
                "positive",
                0.85,
                0.85,
                "Chose a deque with amortized O(1) operations and a memory trade-off.",
                "a deque of timestamps",
            ),
            ("complexity", "positive", 0.75, 0.8, "Explained amortized complexity.", "amortized O(1)"),
        ],
    },
]


@dataclass(slots=True)
class SessionFixture:
    organization_id: uuid.UUID
    session_id: uuid.UUID
    interview_id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    round_id: uuid.UUID
    question_ids: list[uuid.UUID] = field(default_factory=list)
    answer_ids: list[uuid.UUID] = field(default_factory=list)
    evidence_ids: list[uuid.UUID] = field(default_factory=list)
    coding_session_id: uuid.UUID | None = None
    audio_media_id: uuid.UUID | None = None
    integrity_event_ids: list[uuid.UUID] = field(default_factory=list)


def _dims(level: float, quality: str) -> dict[str, float | None]:
    return {
        "correctness": min(10.0, level + 0.2),
        "depth": level,
        "practical_experience": min(10.0, level + 0.4),
        "specificity": level - 0.2 if quality != "partial" else level - 1.0,
        "reasoning": level,
        "completeness": level - 0.5,
        "consistency": 8.0,
        "communication_clarity": 7.5,
        "relevance": 8.5,
    }


async def create_completed_session(
    organization_id: uuid.UUID,
    *,
    candidate_report_mode: str = "immediate",
    status: str = "completed",
    flagged_integrity: bool = True,
    integrity_reviewed: bool = False,
    with_coding: bool = True,
    question_count: int | None = None,
    candidate_name: str = "Jane Doe",
    end_reason: str = "sufficient_evidence",
    started_ago_minutes: float = 40.0,
    with_audio: bool = True,
) -> SessionFixture:
    config = devops_round_config()
    specs = QUESTION_SPECS[: question_count if question_count is not None else len(QUESTION_SPECS)]
    now = datetime.now(UTC)
    started_at = now - timedelta(minutes=started_ago_minutes)
    async with session_scope() as db:
        job = Job(
            id=uuid.uuid4(),
            organization_id=organization_id,
            title="Senior DevOps Engineer",
            description="Platform engineering role.",
            status="open",
            candidate_report_mode=candidate_report_mode,
        )
        round_row = Round(
            id=uuid.uuid4(),
            job_id=job.id,
            name=config["name"],
            round_type=config["round_type"],
            duration_minutes=config["duration_minutes"],
            max_duration_minutes=config["max_duration_minutes"],
            config=config,
        )
        candidate = Candidate(
            id=uuid.uuid4(),
            organization_id=organization_id,
            full_name=candidate_name,
            email=f"{candidate_name.split()[0].lower()}-{secrets.token_hex(3)}@example.test",
        )
        db.add_all([job, candidate])
        await db.flush()
        db.add(round_row)
        await db.flush()
        interview = Interview(
            id=uuid.uuid4(),
            organization_id=organization_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_row.id,
            status="completed" if status == "completed" else "in_progress",
            invite_token_hash=secrets.token_hex(32),
            invite_expires_at=now + timedelta(days=5),
            invite_opened_at=started_at - timedelta(minutes=10),
            verified_at=started_at - timedelta(minutes=9),
            completed_at=now - timedelta(minutes=5) if status == "completed" else None,
            policy_snapshot={
                "candidate_report_mode": candidate_report_mode,
                "retention": {"report_days": 400},
            },
        )
        db.add(interview)
        await db.flush()
        session = InterviewSession(
            id=uuid.uuid4(),
            interview_id=interview.id,
            organization_id=organization_id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_row.id,
            status=status,
            round_config=config,
            plan={
                "opening_focus": "EKS platform scale",
                "segments": [{"key": "tech", "kind": "technical", "time_budget_minutes": 12}],
                "competency_targets": [
                    {"key": "kubernetes", "focus_areas": ["upgrades"]},
                    {"key": "observability", "focus_areas": ["tracing"]},
                ],
            },
            started_at=started_at,
            ended_at=now - timedelta(minutes=5)
            if status in ("completed", "aborted", "failed", "completing")
            else None,
            end_reason=end_reason if status in ("completed", "aborted", "failed", "completing") else None,
            elapsed_seconds=1720.0,
            reconnect_count=1,
            state={
                "version": 42,
                "status": status,
                "turn_state": "ended" if status == "completed" else "listening",
                "avatar_state": "IDLE",
                "time_limit_s": 1800,
                "elapsed_before_pause_s": 1720,
                "claims": {
                    "c1": {
                        "claim": {
                            "id": "c1",
                            "claim": "Ran EKS with 120+ nodes",
                            "category": "scale",
                            "priority": 5,
                        },
                        "status": "verified",
                        "note": "Consistent detail on scale and autoscaling.",
                        "question_ids": [],
                        "evidence_ids": [],
                    },
                    "c2": {
                        "claim": {
                            "id": "c2",
                            "claim": "Cut cloud cost by 40%",
                            "category": "impact",
                            "priority": 4,
                        },
                        "status": "unverified",
                        "question_ids": [],
                        "evidence_ids": [],
                    },
                },
                "contradictions": [
                    {
                        "id": "x1",
                        "description": "Resume says SCP ownership; answer says they did not own SCPs.",
                        "severity": "medium",
                        "status": "open",
                        "refs": ["resume"],
                    },
                    {
                        "id": "x2",
                        "description": "Node count differed between two answers.",
                        "severity": "high",
                        "status": "resolved",
                        "refs": [],
                    },
                ],
                "conversation": [
                    {
                        "speaker": "interviewer",
                        "text": "Tell me about a time you owned a problem end to end.",
                        "at_s": 1500,
                    },
                    {"speaker": "candidate", "text": "I led the EKS migration...", "at_s": 1510},
                ],
                "integrity": {
                    "counts": {"possible_phone_visible": 1},
                    "warnings_issued": 0,
                    "flagged_for_review": 1,
                },
            },
        )
        db.add(session)
        await db.flush()
        fixture = SessionFixture(
            organization_id=organization_id,
            session_id=session.id,
            interview_id=interview.id,
            candidate_id=candidate.id,
            job_id=job.id,
            round_id=round_row.id,
        )
        seq = 0
        tseq = 0
        elapsed = 60.0
        for index, spec in enumerate(specs):
            seq += 1
            q = Question(
                id=uuid.uuid4(),
                session_id=session.id,
                sequence=seq,
                parent_question_id=fixture.question_ids[spec["parent"]] if "parent" in spec else None,
                kind="followup" if "parent" in spec else "technical",
                action="ask_followup" if "parent" in spec else "ask_question",
                source="generated",
                segment="technical",
                topic=spec["competency"],
                competency_keys=[spec["competency"]],
                difficulty=spec["difficulty"],
                text=spec["question"],
                question_focus=spec["question"][:80],
                reason="Probe the competency with the largest evidence gap.",
                expected_evidence=spec.get("expected", []),
                elapsed_seconds=elapsed,
                asked_at=started_at + timedelta(seconds=elapsed),
            )
            db.add(q)
            await db.flush()
            fixture.question_ids.append(q.id)
            start_ms = int((elapsed + 8) * 1000)
            end_ms = start_ms + 45_000
            answer = Answer(
                id=uuid.uuid4(),
                session_id=session.id,
                question_id=q.id,
                sequence=seq,
                transcript_text=spec["answer"],
                started_ms=start_ms,
                ended_ms=end_ms,
                duration_ms=45_000,
                word_count=len(spec["answer"].split()),
                stt_confidence=0.93,
                status="evaluated",
                interrupted_ai=index == 2,
                audio_storage_key=f"orgs/{organization_id}/sessions/{session.id}/audio/{seq}.wav"
                if with_audio and index == 0
                else None,
            )
            db.add(answer)
            await db.flush()
            fixture.answer_ids.append(answer.id)
            tseq += 1
            db.add(
                Transcript(
                    session_id=session.id,
                    seq=tseq,
                    speaker="interviewer",
                    text=spec["question"],
                    start_ms=int(elapsed * 1000),
                    end_ms=start_ms - 1000,
                    question_id=q.id,
                    interrupted=index == 2,
                )
            )
            tseq += 1
            db.add(
                Transcript(
                    session_id=session.id,
                    seq=tseq,
                    speaker="candidate",
                    text=spec["answer"],
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=0.93,
                    question_id=q.id,
                    answer_id=answer.id,
                )
            )
            dims = _dims(spec["level"], spec["quality"])
            items = [
                EvidenceItem(
                    competency_key=spec["competency"],
                    dimension=d,
                    signal=s,
                    evidence=t,
                    quote=qt,
                    strength=st,
                    confidence=c,
                )
                for d, s, st, c, t, qt in spec["evidence"]
            ]
            doc = AnswerEvaluationDoc(
                overall_quality=spec["quality"],
                scores=DimensionScores(**dims),
                summary=f"{spec['quality'].title()} answer on {spec['competency']}.",
                technical_evidence=[t for *_, t, _q in spec["evidence"]],
                missing_details=["policy checks"] if spec["quality"] == "partial" else [],
                evidence=items,
                evaluation_confidence=0.85,
            )
            evaluation = AnswerEvaluation(
                id=uuid.uuid4(),
                answer_id=answer.id,
                question_id=q.id,
                session_id=session.id,
                overall_quality=spec["quality"],
                confidence=0.85,
                evaluation=doc.model_dump(mode="json"),
                **dims,
            )
            db.add(evaluation)
            await db.flush()
            for d, s, st, c, t, qt in spec["evidence"]:
                ev = CompetencyEvidence(
                    id=uuid.uuid4(),
                    session_id=session.id,
                    competency_key=spec["competency"],
                    question_id=q.id,
                    answer_id=answer.id,
                    evaluation_id=evaluation.id,
                    source="answer",
                    signal=s,
                    dimension=d,
                    evidence=t,
                    strength=st,
                    confidence=c,
                    difficulty=spec["difficulty"],
                    elapsed_seconds=end_ms / 1000.0,
                    transcript_ref={
                        "answer_id": str(answer.id),
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "quote": qt,
                    },
                )
                db.add(ev)
                fixture.evidence_ids.append(ev.id)
            elapsed += 95.0
        if with_audio and fixture.answer_ids:
            media = MediaAsset(
                id=uuid.uuid4(),
                organization_id=organization_id,
                session_id=session.id,
                candidate_id=candidate.id,
                kind="answer_audio",
                storage_key=f"orgs/{organization_id}/sessions/{session.id}/audio/1.wav",
                content_type="audio/wav",
                size_bytes=1024,
            )
            db.add(media)
            fixture.audio_media_id = media.id
        if with_coding:
            cq = CodingQuestion(
                id=uuid.uuid4(),
                organization_id=organization_id,
                slug=f"noisy-service-{secrets.token_hex(3)}",
                title="Noisiest Service in the Logs",
                difficulty="easy",
                competency_keys=["data_structures", "code_quality", "problem_solving"],
                prompt_markdown="Find the service with the most errors.",
                languages=["python"],
                tests=[{"id": "t1", "args": [["a"]], "expected": "a", "hidden": False}],
                hints=["Count", "Use a dict", "max by count"],
            )
            db.add(cq)
            await db.flush()
            coding_q = Question(
                id=uuid.uuid4(),
                session_id=session.id,
                sequence=seq + 1,
                kind="coding",
                action="start_coding",
                source="curated",
                competency_keys=["data_structures"],
                difficulty="easy",
                text="Let's do a short coding exercise: find the noisiest service in these logs.",
                coding_question_id=cq.id,
                elapsed_seconds=elapsed,
            )
            db.add(coding_q)
            await db.flush()
            fixture.question_ids.append(coding_q.id)
            evaluation = CodingEvaluation(
                correctness_summary="Correct single-pass solution using a hash map; all tests pass.",
                tests_passed=8,
                tests_total=8,
                time_complexity="O(n)",
                space_complexity="O(k)",
                code_quality=8.0,
                problem_solving=8.0,
                edge_case_handling=7.0,
                testing_approach=6.5,
                strengths=["Clear counting with a dictionary"],
                weaknesses=["Did not discuss ties until asked"],
                hints_impact="One level-1 hint about counting; minor assistance.",
                score_estimate=8.5,
                confidence=0.9,
            )
            cs = CodingSession(
                id=uuid.uuid4(),
                session_id=session.id,
                coding_question_id=cq.id,
                question_id=coding_q.id,
                language="python",
                status="submitted",
                started_at=started_at + timedelta(seconds=elapsed),
                ended_at=started_at + timedelta(seconds=elapsed + 300),
                time_limit_seconds=600,
                hints_available=3,
                hints_used=1,
                paste_event_count=0,
                final_code="def noisiest(logs):\n    counts = {}\n    ...",
                # Shape written by app.services.coding.evaluation (schema fields + bookkeeping).
                evaluation={
                    **evaluation.model_dump(mode="json"),
                    "status": "completed",
                    "is_fallback": False,
                    "raw_score_estimate": 8.5,
                    "score_cap": 10.0,
                    "score_cap_applied": False,
                    "hints_used": 1,
                    "assistance_factor": 0.93,
                    "adjusted_score": 7.9,
                    "competency_keys": ["data_structures"],
                    "difficulty": "easy",
                    "language": "python",
                    "final_run_status": "passed",
                    "model_call_id": None,
                    "evidence_ids": [],
                    "needs_human_review": False,
                },
            )
            db.add(cs)
            await db.flush()
            fixture.coding_session_id = cs.id
            db.add(
                CodingHint(
                    coding_session_id=cs.id,
                    level=1,
                    content="Count errors per service.",
                    elapsed_seconds=elapsed + 200,
                )
            )
            db.add(
                CodeRun(
                    coding_session_id=cs.id,
                    kind="submit",
                    language="python",
                    status="passed",
                    passed_count=8,
                    total_count=8,
                    runtime_ms=40,
                    elapsed_seconds=elapsed + 290,
                    test_results=[
                        {"id": "h1", "passed": True, "hidden": True, "args": ["secret"], "expected": "secret"}
                    ],
                )
            )
            for d, s, st, c, t in [
                ("selection", "positive", 0.85, 0.9, "Chose a hash map for O(n) counting."),
                ("complexity", "positive", 0.8, 0.85, "Stated O(n) time, O(k) space correctly."),
                ("edge_cases", "negative", 0.4, 0.7, "Ties only handled after a prompt."),
            ]:
                db.add(
                    CompetencyEvidence(
                        session_id=session.id,
                        competency_key="data_structures",
                        coding_session_id=cs.id,
                        question_id=coding_q.id,
                        source="coding",
                        signal=s,
                        dimension=d,
                        evidence=t,
                        strength=st,
                        confidence=c,
                        difficulty="easy",
                        elapsed_seconds=elapsed + 300,
                    )
                )
            db.add(
                CompetencyEvidence(
                    session_id=session.id,
                    competency_key="data_structures",
                    coding_session_id=cs.id,
                    source="hint_usage",
                    signal="neutral",
                    dimension="assistance",
                    evidence="Used hint level 1 (counting).",
                    strength=0.3,
                    confidence=1.0,
                    elapsed_seconds=elapsed + 200,
                )
            )
        events = [
            IntegrityEvent(
                id=uuid.uuid4(),
                session_id=session.id,
                organization_id=organization_id,
                type="tab_hidden",
                source="browser",
                severity="low",
                confidence=1.0,
                elapsed_seconds=300,
                action="log_only",
                count=2,
            )
        ]
        if flagged_integrity:
            events.append(
                IntegrityEvent(
                    id=uuid.uuid4(),
                    session_id=session.id,
                    organization_id=organization_id,
                    type="possible_phone_visible",
                    source="vision",
                    severity="medium",
                    confidence=0.74,
                    elapsed_seconds=900,
                    action="flag_for_human_review",
                    review_status="noted" if integrity_reviewed else "unreviewed",
                )
            )
        db.add_all(events)
        fixture.integrity_event_ids = [e.id for e in events]
        for i, kind in enumerate(["session.started", "question.asked", "coding.started", "session.ended"]):
            db.add(
                InterviewEvent(
                    session_id=session.id, seq=i + 1, type=kind, elapsed_seconds=i * 400.0, payload={"n": i}
                )
            )
        for task, latency, status_ in [
            ("turn_decision", 900.0, "ok"),
            ("turn_decision", 1300.0, "ok"),
            ("answer_evaluation", 6000.0, "ok"),
            ("answer_evaluation", 7000.0, "timeout"),
        ]:
            db.add(
                ModelCall(
                    organization_id=organization_id,
                    session_id=session.id,
                    task=task,
                    endpoint="main",
                    model="m",
                    status=status_,
                    latency_ms=latency,
                    ttft_ms=200.0,
                    prompt_tokens=1000,
                    completion_tokens=100,
                )
            )
    return fixture
