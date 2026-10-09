"""The curated catalog under samples/ satisfies the platform contracts.

Counts are lower bounds and proportions (the library grows; competencies are
appended), never exact totals.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.domain.enums import CandidateReportMode, CompetencyCategory, Difficulty, RequirementKind, RoundType
from app.schemas.rounds import RoundConfig
from app.seed.checks import question_style_problems
from app.seed.loaders import ALLOWED_RUBRIC_DIMENSIONS, SeedCatalog, resolve_job_rounds, round_config_problems
from app.seed.validate import Report

from .conftest import protected_hits

REQUIRED_COMPETENCIES = {
    "kubernetes", "aws", "terraform", "docker", "ci_cd", "linux", "networking", "observability",
    "incident_response", "python", "javascript", "typescript", "java", "go", "sql", "data_structures",
    "algorithms", "system_design", "api_design", "databases", "distributed_systems", "security",
    "cloud_cost_optimization", "machine_learning", "llm_engineering", "mlops", "frontend_react", "testing",
    "problem_solving", "communication", "collaboration", "ownership", "leadership", "stakeholder_management",
    "mentoring", "project_management", "product_sense", "recruiter_fit", "motivation", "culture_values",
}  # fmt: skip


# ---------------------------------------------------------------- competencies


def test_catalog_validates_without_errors(validation: Report) -> None:
    """Schemas, cross-references, role tags, fairness, near-duplicates, coding
    structure and executed references (python -m app.seed.validate)."""
    errors = [f"{f.file}: {f.message}" for f in validation.findings if f.severity == "error"]
    assert errors == []


def test_competency_catalog_covers_required_keys(catalog: SeedCatalog) -> None:
    keys = [c.key for c in catalog.competencies]
    assert len(keys) == len(set(keys))
    assert len(keys) >= 40
    assert REQUIRED_COMPETENCIES <= set(keys)


def test_competency_rubrics_have_anchors_and_dimensions(catalog: SeedCatalog) -> None:
    for competency in catalog.competencies:
        assert isinstance(competency.category, CompetencyCategory)
        assert {"1", "3", "5"} <= set(competency.rubric.levels), competency.key
        assert len(competency.rubric.dimensions) >= 2, competency.key
        assert len(set(competency.rubric.levels.values())) == len(competency.rubric.levels), competency.key


def test_every_competency_category_is_used(catalog: SeedCatalog) -> None:
    assert {c.category for c in catalog.competencies} == set(CompetencyCategory)


# ------------------------------------------------------------- round templates


def test_one_default_template_per_round_type(catalog: SeedCatalog) -> None:
    assert sorted(t.round_type.value for t in catalog.round_templates) == sorted(r.value for r in RoundType)
    assert all(t.is_default for t in catalog.round_templates)


@pytest.mark.parametrize("round_type", list(RoundType), ids=lambda r: r.value)
def test_template_is_a_valid_round_config(catalog: SeedCatalog, round_type: RoundType) -> None:
    template = catalog.templates_by_type()[round_type]
    config = RoundConfig.model_validate(template.config.model_dump(mode="json"))  # JSON round trip
    assert config.round_type == round_type
    assert config.rubric and abs(sum(c.weight for c in config.rubric) - 100) < 1e-9
    assert config.max_duration_minutes >= config.duration_minutes
    dist = config.difficulty_distribution
    assert abs(dist.easy + dist.medium + dist.hard - 1) < 1e-9
    assert config.instructions.strip() and (config.interviewer_notes or "").strip()
    assert round_config_problems(config, catalog.competency_keys()) == []
    for category in config.rubric:
        assert set(category.dimensions) <= ALLOWED_RUBRIC_DIMENSIONS
    assert config.coding.enabled == (round_type in (RoundType.CODING, RoundType.COMBINED))


def test_technical_screening_rubric_matches_platform_baseline(catalog: SeedCatalog) -> None:
    rubric = {
        c.label: c.weight for c in catalog.templates_by_type()[RoundType.TECHNICAL_SCREENING].config.rubric
    }
    assert rubric == {
        "Technical Knowledge": 30,
        "Problem Solving": 20,
        "Practical Experience": 20,
        "System Design": 15,
        "Communication Content": 10,
        "Resume Consistency": 5,
    }


def test_behavioral_rubric_is_star_based(catalog: SeedCatalog) -> None:
    labels = [c.label for c in catalog.templates_by_type()[RoundType.BEHAVIORAL].config.rubric]
    assert labels[:4] == [
        "Situation & Task",
        "Actions & Ownership",
        "Results & Impact",
        "Reflection & Learning",
    ]


# --------------------------------------------------------------- question bank


def test_question_bank_size_and_balance(catalog: SeedCatalog) -> None:
    questions = catalog.questions
    assert len(questions) >= 120
    by_difficulty = Counter(q.difficulty for q in questions)
    for difficulty in Difficulty:
        assert by_difficulty[difficulty] / len(questions) >= 0.2, by_difficulty


def test_question_bank_covers_domains_and_competencies(catalog: SeedCatalog) -> None:
    domains = Counter(q.external_key.split(".")[0] for q in catalog.questions)
    assert set(domains) >= {
        "devops", "cloud", "backend", "frontend", "ml", "design", "general", "behavioral", "leadership", "recruiter",
    }  # fmt: skip
    covered = {q.competency_key for q in catalog.questions}
    assert covered <= catalog.competency_keys()
    # Competencies used by the default templates and demo jobs have bank
    # questions. Appended competencies and roles.yaml coverage are reported by
    # `python -m app.seed.validate --stats` while content is being authored.
    # Intake rounds never draw from the bank (the deterministic intake kit drives them), so
    # competencies used only by intake templates/jobs need no bank questions.
    bank_templates = [t for t in catalog.round_templates if t.round_type is not RoundType.INTAKE]
    bank_jobs = [j for j in catalog.jobs if any(r.template is not RoundType.INTAKE for r in j.rounds)]
    used = {k for t in bank_templates for k in t.config.competency_keys()}
    used |= {r.competency_key for j in bank_jobs for r in j.requirements if r.competency_key}
    assert sorted(used - covered) == []
    for round_type in (
        RoundType.TECHNICAL_SCREENING,
        RoundType.DEEP_TECHNICAL,
        RoundType.SYSTEM_DESIGN,
        RoundType.BEHAVIORAL,
        RoundType.RECRUITER_SCREENING,
        RoundType.LEADERSHIP,
        RoundType.MANAGERIAL,
        RoundType.PROBLEM_SOLVING,
        RoundType.COMBINED,
    ):
        assert sum(round_type in q.round_types for q in catalog.questions) >= 5, round_type


def test_questions_are_conversational_and_complete(catalog: SeedCatalog) -> None:
    failures = [f"{q.external_key}: {m}" for q in catalog.questions for m in question_style_problems(q)]
    assert failures == []
    assert all(q.followups for q in catalog.questions)


def test_scenario_questions_exist_for_technical_domains(catalog: SeedCatalog) -> None:
    scenario = [q for q in catalog.questions if q.kind.value == "scenario"]
    assert len(scenario) >= 40
    assert any("availability zone becomes unavailable" in q.text for q in scenario)


# --------------------------------------------------------------------- fairness


def test_no_protected_trait_probing_in_content(catalog: SeedCatalog) -> None:
    from app.seed import checks
    from app.seed.fairness import lint_fields

    hits = []
    for q in catalog.questions:
        hits += [f"{q.external_key}: {h.describe()}" for h in lint_fields(checks.question_fields(q))]
    for c in catalog.coding_questions:
        hits += [f"{c.slug}: {h.describe()}" for h in lint_fields(checks.coding_fields(c))]
    for comp in catalog.competencies:
        hits += [f"{comp.key}: {h.describe()}" for h in lint_fields(checks.competency_fields(comp))]
    for role in catalog.roles:
        hits += [f"{role.key}: {h.describe()}" for h in lint_fields(checks.role_fields(role))]
    assert hits == []


def test_candidate_facing_round_text_is_neutral(catalog: SeedCatalog) -> None:
    texts = [t.config.instructions for t in catalog.round_templates]
    texts += [j.description for j in catalog.jobs] + [j.interview_instructions or "" for j in catalog.jobs]
    assert protected_hits(texts) == []


# ------------------------------------------------------------------------- jobs


def test_demo_jobs_present(catalog: SeedCatalog) -> None:
    assert {j.title for j in catalog.jobs} == {
        "Senior DevOps Engineer",
        "Backend Engineer (Python)",
        "Frontend Engineer (React)",
        "ML Engineer (LLM)",
        "AI/ML Training Program — Intake",
    }
    assert sum(j.allow_candidate_round_selection for j in catalog.jobs) == 1


def test_devops_demo_job_matches_brief(catalog: SeedCatalog) -> None:
    job = next(j for j in catalog.jobs if j.key == "senior_devops_engineer")
    required = {r.competency_key for r in job.requirements if r.kind == RequirementKind.REQUIRED_SKILL}
    assert required == {"kubernetes", "aws", "terraform", "ci_cd", "observability", "incident_response"}
    assert job.candidate_report_mode == CandidateReportMode.NEVER  # owner: candidates never see reports
    rounds = resolve_job_rounds(job, catalog.templates_by_type())
    assert [(r.name, r.round_type) for r in rounds] == [
        ("Combined AI Round", RoundType.COMBINED),
        ("Technical Screening", RoundType.TECHNICAL_SCREENING),
        ("Behavioral", RoundType.BEHAVIORAL),
    ]
    combined = rounds[0]
    assert combined.config.duration_minutes == 30 and combined.config.coding.enabled
    assert combined.pinned_coding_slugs == ("most_frequent_error_service",)
    assert {"kubernetes", "aws", "terraform"} <= set(combined.config.competency_keys())


def test_every_job_round_resolves_to_a_valid_config(catalog: SeedCatalog) -> None:
    for job in catalog.jobs:
        kinds = {r.kind for r in job.requirements}
        assert {
            RequirementKind.REQUIRED_SKILL,
            RequirementKind.RESPONSIBILITY,
            RequirementKind.QUALIFICATION,
        } <= kinds
        for resolved in resolve_job_rounds(job, catalog.templates_by_type()):
            config = RoundConfig.model_validate(resolved.to_row()["config"])
            assert round_config_problems(config, catalog.competency_keys()) == [], (job.key, resolved.name)
            if config.coding.enabled:
                assert set(config.coding.languages) <= set(job.coding_requirements.languages)


def test_round_selection_job_has_selectable_rounds(catalog: SeedCatalog) -> None:
    job = next(j for j in catalog.jobs if j.allow_candidate_round_selection)
    assert sum(r.candidate_selectable for r in job.rounds) >= 2
    for other in catalog.jobs:
        if other is not job:
            assert not any(r.candidate_selectable for r in other.rounds)
