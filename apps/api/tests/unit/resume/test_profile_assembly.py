"""Deterministic profile assembly, heuristic fallback parser and prompts."""

from __future__ import annotations

from datetime import date

from app.schemas.ai import (
    Achievement,
    ClaimToVerify,
    Inconsistency,
    JobMatch,
    ProfileAnalysis,
    ProjectEntry,
    ResumeExtraction,
    RoleEntry,
    SkillItem,
)
from app.services.resume.fallback import heuristic_analysis, heuristic_extraction
from app.services.resume.normalize import detect_sections, normalize_text
from app.services.resume.profile import build_profile_document
from app.services.resume.service import ResumeServiceImpl
from tests.unit.resume import corpus

TODAY = date(2026, 10, 7)


def jane_extraction() -> ResumeExtraction:
    return ResumeExtraction(
        candidate_name="Jane Doe",
        headline="Senior DevOps Engineer",
        summary="Senior DevOps engineer with 5.8 years of experience.",
        total_experience_years=5.8,
        roles=[
            RoleEntry(
                title="Senior DevOps Engineer",
                company="Bluepeak Analytics",
                start="2023-01",
                end="present",
                achievements=[
                    Achievement(
                        text="Reduced cloud costs by 40%", metric="cloud cost", value="40%", measurable=True
                    )
                ],
                technologies=["Kubernetes", "Amazon EKS", "Karpenter"],
            ),
            RoleEntry(
                title="DevOps Engineer",
                company="Harborline Logistics",
                start="2021-08",
                end="2023-03",
                technologies=["Terraform", "GitHub Actions"],
            ),
            RoleEntry(
                title="Junior Systems Engineer", company="Cobalt Retail Group", start="2021-01", end="2021-07"
            ),
        ],
        projects=[ProjectEntry(name="Internal developer platform", outcomes=["Adopted by 8 product teams"])],
        skills=[
            SkillItem(name="Kubernetes", category="devops"),
            SkillItem(name="k8s", category="devops", claimed_level="advanced"),
            SkillItem(name="Python", category="programming_language"),
            SkillItem(name="PostgreSQL", category="other"),
        ],
        inconsistencies=[
            Inconsistency(description="Employment gap of two months", severity="low"),
            Inconsistency(description="Two different team sizes are stated", severity="low"),
        ],
    )


def jane_analysis() -> ProfileAnalysis:
    return ProfileAnalysis(
        claims_to_verify=[
            ClaimToVerify(
                id="x",
                claim="Reduced cloud costs by 40%",
                priority=4,
                competency_keys=["cost_optimization", "made_up"],
            ),
            ClaimToVerify(
                id="x",
                claim="Designed a Kubernetes platform for 10,000 users",
                priority=5,
                competency_keys=["kubernetes"],
            ),
            ClaimToVerify(id="y", claim="reduced cloud costs   by 40%", priority=2),
        ],
        job_match=JobMatch(overall_fit=81, relevant_experience_years=7.0),
    )


def test_build_profile_document() -> None:
    match = JobMatch(overall_fit=77.5, relevant_experience_years=9.0)
    document, facts = build_profile_document(
        candidate_id="00000000-0000-0000-0000-000000000001",
        extraction=jane_extraction(),
        analysis=jane_analysis(),
        job_match=match,
        known_competency_keys={"kubernetes", "cost_optimization"},
        today=TODAY,
    )
    assert document.experience_years == 5.8
    assert facts["experience_source"] == "dated_roles" and facts["experience"]["overlaps"][0]["months"] == 3
    assert document.relevant_experience_years == 5.8  # clamped to the total
    assert document.companies == ["Bluepeak Analytics", "Harborline Logistics", "Cobalt Retail Group"]
    assert [s.name for s in document.skills["devops"]][:2] == ["Kubernetes", "Karpenter"]
    assert [s.name for s in document.skills["cloud"]] == ["Amazon EKS"]
    assert document.skills["devops"][0].claimed_level == "advanced"  # merged with "k8s"
    assert "database" in document.skills and document.skills["database"][0].name == "PostgreSQL"
    assert [a.text for a in document.achievements] == [
        "Reduced cloud costs by 40%",
        "Adopted by 8 product teams",
    ]
    descriptions = [i.description for i in document.inconsistencies]
    assert any(d.startswith("Overlapping employment dates") for d in descriptions)
    assert not any("gap of two months" in d for d in descriptions)  # gaps from the model are dropped
    assert "Two different team sizes are stated" in descriptions
    claims = document.claims_to_verify
    assert [c.id for c in claims] == ["c1", "c2"]  # renumbered, duplicate removed
    assert claims[0].claim.startswith("Designed a Kubernetes platform")  # priority order
    assert claims[1].competency_keys == ["cost_optimization"]  # unknown key dropped
    assert document.job_match == match


def test_stated_total_used_without_dated_roles() -> None:
    extraction = ResumeExtraction(
        total_experience_years=4.0, roles=[RoleEntry(title="Engineer", company="Acme")]
    )
    document, facts = build_profile_document(
        candidate_id="c",
        extraction=extraction,
        analysis=ProfileAnalysis(job_match=JobMatch(overall_fit=0)),
        job_match=None,
        known_competency_keys=set(),
        today=TODAY,
    )
    assert document.experience_years == 4.0 and facts["experience_source"] == "stated_total"
    assert document.job_match is None


def test_heuristic_fallback_on_sample() -> None:
    text = normalize_text(corpus.sample("jane_doe_devops.txt").decode())
    extraction = heuristic_extraction(text, detect_sections(text))
    assert extraction.candidate_name == "Jane Doe"
    assert extraction.total_experience_years == 5.8
    assert [(r.company, r.start, r.end) for r in extraction.roles] == [
        ("Bluepeak Analytics", "2023-01", "present"),
        ("Harborline Logistics", "2021-08", "2023-03"),
        ("Cobalt Retail Group", "2021-01", "2021-07"),
    ]
    names = {s.name for s in extraction.skills}
    assert {"AWS", "EKS", "Kubernetes", "Terraform", "Python", "PostgreSQL"} <= names
    analysis = heuristic_analysis(extraction, has_job=True)
    assert analysis.claims_to_verify and all(c.priority == 3 for c in analysis.claims_to_verify)
    assert any("10,000 users" in c.claim for c in analysis.claims_to_verify)
    document, _ = build_profile_document(
        candidate_id="c",
        extraction=extraction,
        analysis=analysis,
        job_match=None,
        known_competency_keys=set(),
        today=TODAY,
    )
    assert document.experience_years == 5.8


def test_candidate_safe_summary_hides_internals() -> None:
    document, _ = build_profile_document(
        candidate_id="c",
        extraction=jane_extraction(),
        analysis=jane_analysis(),
        job_match=JobMatch(overall_fit=80),
        known_competency_keys={"kubernetes"},
        today=TODAY,
    )
    summary = ResumeServiceImpl().candidate_safe_summary(document.model_dump(mode="json"))
    assert summary["candidate_name"] == "Jane Doe" and summary["experience_years"] == 5.8
    assert summary["roles"][0] == {
        "title": "Senior DevOps Engineer",
        "company": "Bluepeak Analytics",
        "start": "2023-01",
        "end": "present",
    }
    assert "Kubernetes" in summary["top_skills"]
    flat = repr(summary)
    for secret in ("claims_to_verify", "job_match", "inconsistencies", "Reduced cloud costs", "overall_fit"):
        assert secret not in flat


def test_wire_schemas_require_dates_and_drop_education_years() -> None:
    from app.ai.structured import schema_for
    from app.services.resume.llm_schemas import analysis_wire_model, extraction_wire_model, to_contract

    schema = schema_for(extraction_wire_model())
    assert {"start", "end", "duration_months"} <= set(schema["$defs"]["RoleEntry"]["required"])
    assert "location" not in schema["$defs"]["RoleEntry"]["required"]
    assert set(schema["$defs"]["EducationEntry"]["properties"]) == {"institution", "degree", "field"}
    assert {"evidence_topics", "priority", "source_quote"} <= set(
        schema_for(analysis_wire_model())["$defs"]["ClaimToVerify"]["required"]
    )
    wire_value = extraction_wire_model().model_validate_json(jane_extraction().model_dump_json())
    assert to_contract(wire_value, ResumeExtraction) == jane_extraction()


def test_backfill_role_dates_from_text() -> None:
    from app.services.resume.fallback import backfill_role_dates

    text = normalize_text(corpus.sample("jane_doe_devops.txt").decode())
    extraction = ResumeExtraction(
        roles=[
            RoleEntry(title="Senior DevOps Engineer", company="Bluepeak Analytics"),
            RoleEntry(title="DevOps Engineer", company="Harborline Logistics", start="2021-09"),
            RoleEntry(
                title="Junior Systems Engineer", company="Cobalt Retail Group", start="2021-01", end="2021-07"
            ),
            RoleEntry(title="Freelancer", company="Self-employed"),
        ]
    )
    filled, count = backfill_role_dates(extraction, detect_sections(text))
    assert count == 2
    assert [(r.start, r.end) for r in filled.roles] == [
        ("2023-01", "present"),
        ("2021-09", "2023-03"),  # the model's own start is kept
        ("2021-01", "2021-07"),
        (None, None),
    ]
    unchanged, none = backfill_role_dates(jane_extraction(), detect_sections(text))
    assert none == 0 and unchanged == jane_extraction()
