"""Skill normalisation and the job-match blend formula."""

from __future__ import annotations

import pytest

from app.schemas.ai import JobMatch, RequirementMatch, RoleEntry, SkillItem
from app.services.resume.matching import (
    RequirementEvidence,
    RequirementInput,
    band,
    blend_job_match,
    calibrate_cosine,
    lexical_score,
)
from app.services.resume.skills import (
    canonical_key,
    category_for,
    group_skills,
    merge_skills,
    skills_from_roles,
    top_skills,
)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("K8s", "kubernetes"),
        ("Kubernetes (EKS)", "kubernetes"),
        ("Postgres", "postgresql"),
        ("Node.js", "node js"),
        ("React.js", "react"),
        ("Golang", "go"),
        ("C++", "c++"),
        ("C#", "c#"),
        (".NET", ".net"),
        ("Amazon EKS", "eks"),
        ("CI/CD", "ci/cd"),
    ],
)
def test_canonical_keys(name: str, key: str) -> None:
    assert canonical_key(name) == key


def test_merge_keeps_best_attributes() -> None:
    merged = merge_skills(
        [
            SkillItem(name="Kubernetes", category="other", claimed_level="intermediate", years=2),
            SkillItem(
                name="k8s",
                category="devops",
                claimed_level="expert",
                evidence="ran 4 clusters",
                last_used_year=2026,
            ),
            SkillItem(name="Python", category="programming_language"),
            SkillItem(name="python", years=6),
            SkillItem(name="  ", category="other"),
        ]
    )
    assert [s.name for s in merged] == ["Kubernetes", "Python"]
    kubernetes = merged[0]
    assert kubernetes.claimed_level == "expert"
    assert kubernetes.years == 2
    assert kubernetes.category == "devops"
    assert kubernetes.evidence == "ran 4 clusters"
    assert merged[1].years == 6


def test_grouping_and_role_technologies() -> None:
    roles = [RoleEntry(title="SRE", company="Acme", technologies=["Terraform", "PostgreSQL", "Kubernetes"])]
    skills = merge_skills([SkillItem(name="Kubernetes", claimed_level="advanced"), *skills_from_roles(roles)])
    grouped = group_skills(skills)
    assert list(grouped) == ["database", "devops"]  # category order
    assert [s.name for s in grouped["devops"]] == ["Kubernetes", "Terraform"]
    assert grouped["database"][0].evidence == "Listed for SRE at Acme"
    assert top_skills(grouped)[0] == "Kubernetes"
    assert category_for("Leadership") == "soft"
    assert category_for("Unknown Tool", "nonsense") == "other"


def test_lexical_and_cosine_calibration() -> None:
    text = "operated kubernetes clusters with terraform and argo cd on aws"
    assert lexical_score("Kubernetes", text, set()) == 1.0
    assert lexical_score("K8s", text, {"kubernetes"}) == 1.0
    assert lexical_score("Kafka", text, set()) == 0.0
    partial = lexical_score("AWS cost optimization", text, set())
    assert 0 < partial < 0.7
    assert calibrate_cosine(0.35) == 0.0
    assert calibrate_cosine(0.75) == pytest.approx(1.0)
    assert calibrate_cosine(0.55) == pytest.approx(0.5)
    assert calibrate_cosine(None) == 0.0
    assert band(0.7) == "strong" and band(0.45) == "partial" and band(0.2) == "weak" and band(0.19) == "none"


def _evidence(
    name: str, kind: str, strength: float, weight: float = 1.0, chunk: str | None = None
) -> RequirementEvidence:
    return RequirementEvidence(
        requirement=RequirementInput(id=name, name=name, kind=kind, weight=weight),
        semantic=strength,
        lexical=0.0,
        strength=strength,
        best_chunk=chunk,
        best_section="experience" if chunk else None,
    )


def test_blend_formula_exact_numbers() -> None:
    evidence = [
        _evidence("Kubernetes", "required_skill", 0.9, weight=2.0, chunk="Operated 4 clusters"),
        _evidence("Terraform", "required_skill", 0.6),
        _evidence("Go", "preferred_skill", 0.2),
        _evidence("Kafka", "required_skill", 0.0),
    ]
    model = JobMatch(
        overall_fit=70,
        relevant_experience_years=9,
        matched_requirements=[
            RequirementMatch(
                requirement="Kubernetes", evidence="EKS platform for 10,000 users", strength="strong"
            ),
            RequirementMatch(requirement="Terraform modules", strength="partial"),
        ],
        missing_requirements=["Kafka", "Service mesh"],
        strengths=["Platform scale"],
        notes="Solid platform background.",
    )
    match, breakdown = blend_job_match(evidence, model, experience_years=5.8, experience_min_years=5)
    # coverage: (2*0.9 + 1*0.6 + 0.5*0.2 + 1*0) / (2 + 1 + 0.5 + 1) = 2.5 / 4.5
    assert breakdown.coverage == pytest.approx(55.6, abs=0.05)
    assert breakdown.model_fit == 70
    assert breakdown.experience_fit == 100
    expected = round(0.5 * 55.6 + 0.35 * 70 + 0.15 * 100, 1)
    assert breakdown.score == pytest.approx(expected, abs=0.1)
    assert match.overall_fit == breakdown.score
    assert breakdown.weights == {"coverage": 0.5, "model": 0.35, "experience": 0.15}
    strengths = {m.requirement: m.strength for m in match.matched_requirements}
    # Kubernetes: 0.5*0.9 + 0.5*1.0 = 0.95; Terraform: 0.5*0.6 + 0.5*0.6 = 0.6 (fuzzy name match)
    assert strengths == {"Kubernetes": "strong", "Terraform": "partial", "Go": "weak", "Kafka": "none"}
    assert match.matched_requirements[0].evidence == "EKS platform for 10,000 users"
    assert "Kafka" in match.missing_requirements and "Service mesh" in match.missing_requirements
    assert "Go" not in match.missing_requirements  # preferred skills are never "missing"
    assert match.relevant_experience_years == 5.8  # clamped to computed total
    # notes are prose; the arithmetic lives in the structured breakdown
    assert match.notes == (
        "Solid platform background. Resume evidence for the 4 job requirements: "
        "1 strongly, 1 partially, 1 weakly supported, 1 not found."
    )
    assert "Score" not in match.notes and "x" not in match.notes.split(".")[1]
    assert breakdown.bands == {"strong": 1, "partial": 1, "weak": 1, "none": 1}


def test_blend_redistributes_missing_components() -> None:
    evidence = [_evidence("Kubernetes", "required_skill", 0.8)]
    match, breakdown = blend_job_match(evidence, None, experience_years=None, experience_min_years=None)
    assert breakdown.weights == {"coverage": 1.0}
    assert breakdown.score == 80.0 and match.overall_fit == 80.0
    match, breakdown = blend_job_match(
        [], JobMatch(overall_fit=64), experience_years=2, experience_min_years=4
    )
    assert breakdown.weights == {"model": 0.7, "experience": 0.3}
    assert breakdown.score == pytest.approx(0.7 * 64 + 0.3 * 50, abs=0.05)


def test_experience_above_maximum_is_not_penalised() -> None:
    _, breakdown = blend_job_match(
        [_evidence("Kubernetes", "required_skill", 1.0)], None, experience_years=20, experience_min_years=3
    )
    assert breakdown.experience_fit == 100
