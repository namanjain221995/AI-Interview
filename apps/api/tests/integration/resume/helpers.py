"""Shared helpers for the resume / jobs / catalog integration tests."""

from __future__ import annotations

from typing import Any

import httpx

from app.domain.enums import ModelTask
from app.schemas.ai import (
    Achievement,
    ClaimToVerify,
    Inconsistency,
    JobMatch,
    ProfileAnalysis,
    ProjectEntry,
    RecommendedTopic,
    RequirementMatch,
    ResumeExtraction,
    RoleEntry,
    SkillItem,
)

API = "/api/v1"

DEVOPS_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "kind": "required_skill",
        "name": "Kubernetes",
        "competency_key": "kubernetes",
        "weight": 2.0,
        "min_years": 3,
    },
    {"kind": "required_skill", "name": "Terraform", "competency_key": "terraform", "weight": 1.5},
    {"kind": "required_skill", "name": "AWS", "weight": 1.0},
    {"kind": "preferred_skill", "name": "Go", "weight": 0.5},
    {"kind": "required_skill", "name": "Apache Kafka streaming", "weight": 1.0},
]


async def create_job(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {
        "title": "Senior DevOps Engineer",
        "description": "Own our Kubernetes platform on AWS, infrastructure as code with Terraform, CI/CD and observability.",
        "seniority": "senior",
        "experience_min_years": 5,
        "experience_max_years": 10,
        "status": "open",
        "requirements": DEVOPS_REQUIREMENTS,
        **overrides,
    }
    response = await client.post(f"{API}/jobs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def create_candidate(
    client: httpx.AsyncClient, email: str = "jane.doe@example.com", **extra: Any
) -> dict[str, Any]:
    response = await client.post(f"{API}/candidates", json={"full_name": "Jane Doe", "email": email, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def upload(
    client: httpx.AsyncClient,
    candidate_id: str,
    filename: str,
    data: bytes,
    *,
    job_id: str | None = None,
    content_type: str = "application/octet-stream",
) -> httpx.Response:
    form = {"job_id": job_id} if job_id else None
    return await client.post(
        f"{API}/candidates/{candidate_id}/resumes",
        files={"file": (filename, data, content_type)},
        data=form,
    )


def jane_extraction() -> ResumeExtraction:
    return ResumeExtraction(
        candidate_name="Jane Doe",
        headline="Senior DevOps Engineer",
        summary="Senior DevOps engineer with 5.8 years of experience building cloud-native platforms on AWS and Kubernetes.",
        total_experience_years=5.8,
        roles=[
            RoleEntry(
                title="Senior DevOps Engineer",
                company="Bluepeak Analytics",
                start="2023-01",
                end="present",
                responsibilities=["Lead a team of 3 DevOps engineers"],
                achievements=[
                    Achievement(
                        text="Reduced cloud costs by 40%", metric="cloud cost", value="40%", measurable=True
                    ),
                    Achievement(
                        text="Designed production Kubernetes platform supporting 10,000 users",
                        metric="users",
                        value="10,000",
                        measurable=True,
                    ),
                ],
                technologies=["Kubernetes", "Amazon EKS", "Karpenter", "Argo CD"],
                leadership_scope="team of 3 DevOps engineers",
            ),
            RoleEntry(
                title="DevOps Engineer",
                company="Harborline Logistics",
                start="2021-08",
                end="2023-03",
                technologies=["Terraform", "Terragrunt", "GitHub Actions"],
            ),
            RoleEntry(
                title="Junior Systems Engineer",
                company="Cobalt Retail Group",
                start="2021-01",
                end="2021-07",
                technologies=["Ansible", "Python"],
            ),
        ],
        projects=[ProjectEntry(name="Internal developer platform", outcomes=["Adopted by 8 product teams"])],
        skills=[
            SkillItem(
                name="Kubernetes", category="devops", evidence="Designed production Kubernetes platform"
            ),
            SkillItem(name="Terraform", category="devops"),
            SkillItem(name="AWS", category="cloud"),
            SkillItem(name="Python", category="programming_language"),
            SkillItem(name="Go", category="programming_language"),
        ],
        inconsistencies=[Inconsistency(description="Harborline and Bluepeak dates overlap", severity="low")],
    )


def jane_analysis() -> ProfileAnalysis:
    return ProfileAnalysis(
        claims_to_verify=[
            ClaimToVerify(
                id="c1",
                claim="Designed a production Kubernetes platform supporting 10,000 users",
                source_quote="Designed production Kubernetes platform supporting 10,000 users",
                category="scale",
                competency_keys=["kubernetes"],
                evidence_topics=[
                    "cluster count and size",
                    "autoscaling",
                    "AZ failure handling",
                    "personal role",
                ],
                priority=5,
            ),
            ClaimToVerify(
                id="c2",
                claim="Reduced cloud costs by 40%",
                category="impact",
                priority=4,
                evidence_topics=["baseline", "normalisation for growth"],
            ),
        ],
        job_match=JobMatch(
            overall_fit=78,
            relevant_experience_years=5.8,
            matched_requirements=[
                RequirementMatch(
                    requirement="Kubernetes", evidence="EKS platform for 10,000 users", strength="strong"
                ),
                RequirementMatch(
                    requirement="Terraform", evidence="60+ Terraform modules", strength="strong"
                ),
                RequirementMatch(requirement="AWS", evidence="4 AWS accounts", strength="strong"),
                RequirementMatch(requirement="Go", strength="weak"),
                RequirementMatch(requirement="Apache Kafka streaming", strength="none"),
            ],
            missing_requirements=["Apache Kafka streaming"],
            strengths=["Kubernetes platform at scale"],
            gaps=["No streaming experience listed"],
            notes="Strong platform background.",
        ),
        recommended_topics=[
            RecommendedTopic(
                topic="EKS scaling", competency_key="kubernetes", reason="Core claim", priority=5
            )
        ],
        potential_skill_gaps=["Kafka"],
    )


def part_responder(extraction: ResumeExtraction, analysis: ProfileAnalysis | None = None) -> Any:
    """Mock-LLM responder for the fine-grained profile calls
    (``app.services.resume.calls``): answers each part from one full extraction
    and analysis, the way the real model splits its work."""
    import re

    from app.services.resume.calls import anchor_range, role_range

    analysis = analysis or ProfileAnalysis(job_match=JobMatch(overall_fit=0))

    def respond(call: Any) -> Any:
        name = call.schema.__name__
        prompt = call.messages[-1]["content"]
        if name == "ResumeIdentity":
            return {
                "candidate_name": extraction.candidate_name,
                "headline": extraction.headline,
                "total_experience_years": extraction.total_experience_years,
                "leadership_experience": extraction.leadership_experience[:5],
                "career_progression": extraction.career_progression,
                "inconsistencies": [i.model_dump() for i in extraction.inconsistencies[:5]],
            }
        if name == "ResumeRoles":
            task = prompt.split("TASK:", 1)[1]
            if "NOT the" in task:
                return {"roles": []}
            anchors = re.findall("\u00ab(.*?)\u00bb", task)
            if not anchors:
                return {"roles": [r.model_dump() for r in extraction.roles]}
            wanted = [anchor_range(a) for a in anchors]
            roles = [r for w in wanted for r in extraction.roles if role_range(r) == w]
            return {"roles": [r.model_dump() for r in roles[: len(anchors)]]}
        if name == "ResumeProjects":
            return {"projects": [p.model_dump() for p in extraction.projects]}
        if name == "ResumeSkills":
            by_category: dict[str, list[str]] = {
                c: []
                for c in (
                    "programming_language",
                    "framework",
                    "database",
                    "cloud",
                    "devops",
                    "ai_ml",
                    "tool",
                    "architecture",
                    "soft",
                    "other",
                )
            }
            for skill in extraction.skills:
                by_category.setdefault(skill.category, []).append(skill.name)
            return {
                "by_category": by_category,
                "levels": [
                    {"name": s.name, "level": s.claimed_level}
                    for s in extraction.skills
                    if s.claimed_level in ("beginner", "intermediate", "advanced", "expert")
                ],
                "details": [
                    {
                        "name": s.name,
                        "years": s.years,
                        "last_used_year": s.last_used_year,
                        "evidence": s.evidence,
                    }
                    for s in extraction.skills
                    if s.years is not None or s.last_used_year is not None or s.evidence
                ],
            }
        if name == "ResumeBackground":
            return {
                "education": [
                    {"institution": e.institution, "degree": e.degree, "field": e.field}
                    for e in extraction.education
                ],
                "certifications": [c.model_dump() for c in extraction.certifications],
            }
        if name == "ResumeClaims":
            item = call.schema.model_fields["claims"].annotation.__args__[0]
            allowed = set(item.model_fields["category"].annotation.__args__)
            picked = [c for c in analysis.claims_to_verify if c.category in allowed][:3]
            return {
                "claims": [
                    {
                        "claim": c.claim,
                        "category": c.category,
                        "competency_keys": c.competency_keys[:2],
                        "evidence_topics": (c.evidence_topics + ["details", "personal role"])[:3],
                        "priority": c.priority,
                    }
                    for c in picked
                ]
            }
        if name == "JobFit":
            match = analysis.job_match
            return {
                "overall_fit": match.overall_fit,
                "relevant_experience_years": match.relevant_experience_years,
                "strengths": match.strengths[:4],
                "gaps": match.gaps[:4],
                "notes": match.notes,
            }
        if name == "RequirementAssessments":
            names = re.findall(r"^\d+\. \[[a-z_]+\] (.+?)(?: \(\d[\d.]*\+ years\))?(?::.*)?$", prompt, re.M)
            by_name = {m.requirement: m for m in analysis.job_match.matched_requirements}
            return {
                "matched_requirements": [
                    {
                        "n": number,
                        "strength": by_name[n].strength if n in by_name else "none",
                        "evidence": (by_name[n].evidence or "")[:80] or None if n in by_name else None,
                    }
                    for number, n in enumerate(names, start=1)
                ]
            }
        if name == "InterviewTopics":
            return {"recommended_topics": [t.model_dump() for t in analysis.recommended_topics[:6]]}
        if name == "ProfileGaps":
            return {
                "potential_skill_gaps": analysis.potential_skill_gaps[:3],
                "areas_requiring_verification": analysis.areas_requiring_verification[:3],
                "job_relevant_strengths": analysis.job_relevant_strengths[:3],
                "job_relevant_missing_areas": analysis.job_relevant_missing_areas[:3],
            }
        raise AssertionError(f"unexpected profile part {name}")

    return respond


def script_extraction(mock_ai: Any, extraction: Any, analysis: Any = None) -> None:
    """Answer every profile part call from one extraction (and analysis)."""
    responder = part_responder(extraction, analysis)
    mock_ai.llm.set_default(ModelTask.RESUME_EXTRACTION, responder)
    mock_ai.llm.set_default(ModelTask.PROFILE_ANALYSIS, responder)


def script_jane(mock_ai: Any) -> None:
    script_extraction(mock_ai, jane_extraction(), jane_analysis())
