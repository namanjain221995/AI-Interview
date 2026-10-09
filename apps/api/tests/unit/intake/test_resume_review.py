"""Résumé review (app/services/intake/resume_review.py): the candidate-safe document, corrections diff, re-keying
and the effective profile the live interview uses (INTAKE_INTERVIEW.md 15.4, 6.5.5)."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.intake import ResumeReviewDoc, ResumeReviewRecord, ResumeReviewSource
from app.services.intake.resume_review import (
    diff_documents,
    document_from_profile,
    effective_profile,
    rekey,
    unknown_refs,
)
from app.services.resume.quick import build_quick_profile

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SAMPLES = Path(__file__).resolve().parents[5] / "samples" / "resumes"

PROFILE = {
    "candidate_id": "c-1",
    "candidate_name": "Asha Rao",
    "headline": "Data analyst moving into machine learning",
    "summary": "Analyst with SQL and Python experience.",
    "experience_years": 3.5,
    "roles": [
        {
            "title": "Data Analyst",
            "company": "Acme Retail",
            "start": "2023-01",
            "end": "present",
            "duration_months": 21,
            "location": "Pune",
            "responsibilities": [f"Responsibility {i}" for i in range(1, 9)],
            "achievements": [
                {
                    "text": "Cut weekly report time by 40%",
                    "metric": "report time",
                    "value": "40%",
                    "measurable": True,
                }
            ],
            "technologies": ["SQL", "Python", "Tableau"],
            "leadership_scope": "mentored 2 interns",
        },
        {
            "title": "Junior Analyst",
            "company": "Beta Corp",
            "start": "2021-06",
            "end": "2022-12",
            "responsibilities": ["Automated ETL jobs"],
            "achievements": [],
            "technologies": ["Airflow"],
        },
        {"title": "", "company": "", "responsibilities": ["dropped: no title and no company"]},
    ],
    "companies": ["Acme Retail", "Beta Corp"],
    "skills": {
        "programming_language": [
            {
                "name": "Python",
                "category": "programming_language",
                "claimed_level": "intermediate",
                "years": 3,
            },
            {"name": "SQL", "category": "programming_language"},
        ],
        "tool": [{"name": "Tableau", "category": "tool"}, {"name": "Airflow", "category": "devops"}],
        "ai_ml": [{"name": "scikit-learn", "category": "ai_ml", "evidence": "churn model"}],
        "soft": [{"name": "Stakeholder communication", "category": "soft"}],
    },
    "projects": [
        {
            "name": "Churn model",
            "description": "Predicted subscription churn for the marketing team.",
            "role": "Lead",
            "technologies": ["scikit-learn", "pandas"],
            "outcomes": ["AUC 0.81"],
            "scale_indicators": ["2M rows"],
        }
    ],
    "education": [
        {
            "institution": "Pune University",
            "degree": "BSc",
            "field": "Statistics",
            "start": "2017",
            "end": "2020",
        }
    ],
    "certifications": [{"name": "Google Data Analytics", "issuer": "Google", "year": "2022"}],
    "claims_to_verify": [
        {
            "id": "c1",
            "claim": "Cut weekly report time by 40% at Acme Retail",
            "category": "impact",
            "priority": 5,
        },
        {
            "id": "c2",
            "claim": "Built a churn model with scikit-learn",
            "category": "technical",
            "priority": 4,
        },
        {"id": "c3", "claim": "Automated ETL jobs at Beta Corp", "category": "technical", "priority": 3},
        {
            "id": "c4",
            "claim": "Orchestrated pipelines",
            "evidence_topics": ["Airflow"],
            "category": "technical",
            "priority": 2,
        },
    ],
    "job_match": {"overall_fit": 72.0, "notes": "internal"},
    "inconsistencies": [{"description": "gap between roles", "severity": "low"}],
}


def record(
    original: ResumeReviewDoc, document: ResumeReviewDoc, *, action: str = "confirmed"
) -> ResumeReviewRecord:
    document = rekey(document)
    return ResumeReviewRecord(
        action=action,  # type: ignore[arg-type]
        source=ResumeReviewSource(resume_id=uuid.uuid4(), profile_id=uuid.uuid4(), profile_status="ready"),
        original=original,
        document=document,
        corrections=diff_documents(original, document),
        confirmed_at=NOW,
    )


# -------------------------------------------------------------------------------- the document


def test_document_is_candidate_safe_and_bounded() -> None:
    doc = document_from_profile(PROFILE)
    assert [r.ref for r in doc.roles] == ["r1", "r2"]  # the empty role is skipped
    assert doc.roles[0].responsibilities == [f"Responsibility {i}" for i in range(1, 7)]
    assert doc.roles[0].achievements == ["Cut weekly report time by 40%"]
    assert doc.languages == ["Python", "SQL"] and doc.tools == ["Tableau", "Airflow"]
    assert doc.skills == ["scikit-learn", "Stakeholder communication"]
    assert [p.ref for p in doc.projects] == ["p1"] and doc.projects[0].outcomes == ["AUC 0.81"]
    assert doc.education[0].model_dump() == {
        "ref": "e1", "institution": "Pune University", "degree": "BSc", "field": "Statistics",
    }  # fmt: skip
    assert doc.certifications == ["Google Data Analytics"] and doc.experience_years == 3.5
    dumped = doc.model_dump_json()
    for secret in (
        "claims_to_verify",
        "priority",
        "job_match",
        "overall_fit",
        "inconsistencies",
        "2017",
        "internal",
    ):
        assert secret not in dumped
    assert document_from_profile(None) == ResumeReviewDoc()
    assert document_from_profile({"experience_years": "n/a", "roles": "bad"}).experience_years is None


def test_document_truncates_instead_of_failing() -> None:
    profile = {
        "headline": "h" * 500,
        "roles": [{"title": "t" * 300, "company": "c", "technologies": [f"tech{i}" for i in range(40)]}] * 20,
        "skills": {"other": [{"name": f"skill {i}"} for i in range(60)]},
        "certifications": [{"name": f"cert {i}"} for i in range(30)],
    }
    doc = document_from_profile(profile)
    assert len(doc.headline or "") == 200 and len(doc.roles) == 12 and len(doc.roles[0].title) == 160
    assert len(doc.roles[0].technologies) == 15 and len(doc.skills) == 40 and len(doc.certifications) == 10


def test_quick_profile_documents_build() -> None:
    sample = next(SAMPLES.glob("*.txt"), None)
    if sample is None:
        pytest.skip("no sample text résumé")
    quick = build_quick_profile(sample.read_text(encoding="utf-8"), candidate_id=uuid.uuid4())
    doc = document_from_profile(quick.document)
    assert ResumeReviewDoc.model_validate(json.loads(doc.model_dump_json())) == doc


# -------------------------------------------------------------------------- refs, diff, re-keying


def test_rekey_and_unknown_refs() -> None:
    original = document_from_profile(PROFILE)
    edited = original.model_copy(deep=True)
    data = edited.model_dump()
    data["roles"].append({"ref": "new", "title": "Volunteer tutor", "company": "Code Club"})
    data["projects"].append({"ref": "n7", "name": "Portfolio site", "description": "Personal site"})
    data["education"].append({"ref": "new", "institution": "Online academy"})
    rekeyed = rekey(ResumeReviewDoc.model_validate(data))
    assert [r.ref for r in rekeyed.roles][-1] == "n1"
    assert rekeyed.projects[-1].ref == "n2" and rekeyed.education[-1].ref == "n3"
    data["roles"].append({"ref": "r9", "title": "Ghost role", "company": "Nowhere"})
    assert unknown_refs(original, ResumeReviewDoc.model_validate(data)) == ["document.roles[3].ref"]
    assert unknown_refs(original, original) == []


def test_diff_lists_every_correction() -> None:
    original = document_from_profile(PROFILE)
    data = original.model_dump()
    data["headline"] = "Aspiring ML engineer"
    data["roles"][0]["title"] = "Senior Data Analyst"
    data["roles"].pop(1)
    data["roles"].append({"ref": "new", "title": "Volunteer tutor", "company": "Code Club"})
    data["skills"] = ["Stakeholder communication", "PyTorch"]
    data["certifications"] = []
    corrections = diff_documents(original, rekey(ResumeReviewDoc.model_validate(data)))
    summary = {(c.path, c.op) for c in corrections}
    assert summary == {
        ("headline", "changed"),
        ("roles[r1].title", "changed"),
        ("roles[r2]", "removed"),
        ("roles[n1]", "added"),
        ("skills", "added"),
        ("skills", "removed"),
        ("certifications", "removed"),
    }
    title = next(c for c in corrections if c.path == "roles[r1].title")
    assert (title.before, title.after) == ("Data Analyst", "Senior Data Analyst")
    assert diff_documents(original, original) == []


# ----------------------------------------------------------------------------- effective profile


def test_effective_profile_without_a_confirmed_review_only_tags_sources() -> None:
    for rec in (
        None,
        record(document_from_profile(PROFILE), document_from_profile(PROFILE), action="skipped"),
    ):
        result = effective_profile(PROFILE, rec)
        assert [r["source"] for r in result["roles"]] == ["resume"] * 3
        assert all(c["source"] == "resume" for c in result["claims_to_verify"])
        assert result["intake_review"]["corrections"] == 0
        assert result["experience_years"] == 3.5
    assert PROFILE["roles"][0].get("source") is None  # the input is never mutated


def test_effective_profile_applies_corrections() -> None:
    snapshot = copy.deepcopy(PROFILE)
    original = document_from_profile(PROFILE)
    data = original.model_dump()
    data["experience_years"] = 2.0
    data["roles"][0]["title"] = "Data Analyst"  # unchanged title keeps the profile details
    data["roles"][0]["technologies"] = ["SQL", "Python"]  # Tableau removed from the role, still a tool
    data["roles"].pop(1)  # Beta Corp removed
    data["roles"].append({"ref": "new", "title": "Volunteer tutor", "company": "Code Club"})
    data["tools"] = ["Tableau"]  # Airflow removed everywhere
    data["skills"] = ["scikit-learn", "Stakeholder communication", "PyTorch"]
    data["education"][0]["degree"] = "BSc (Hons)"
    result = effective_profile(PROFILE, record(original, ResumeReviewDoc.model_validate(data)))
    assert PROFILE == snapshot
    roles = result["roles"]
    assert [(r["company"], r["source"]) for r in roles] == [
        ("Acme Retail", "resume"),
        ("Code Club", "candidate_correction"),
    ]
    assert roles[0]["duration_months"] == 21 and roles[0]["leadership_scope"] == "mentored 2 interns"
    assert roles[0]["achievements"][0]["measurable"] is True  # the original achievement object is kept
    assert roles[0]["technologies"] == ["SQL", "Python"]
    assert result["companies"] == ["Acme Retail", "Code Club"] and result["experience_years"] == 2.0
    assert (
        result["projects"][0]["scale_indicators"] == ["2M rows"]
        and result["projects"][0]["source"] == "resume"
    )
    grouped = result["skills"]
    assert [s["name"] for s in grouped["programming_language"]] == ["Python", "SQL"]
    assert grouped["programming_language"][0]["years"] == 3
    assert [s["name"] for s in grouped["tool"]] == ["Tableau"]
    assert {s["name"] for s in grouped["ai_ml"]} == {"scikit-learn"} and grouped["other"][0][
        "name"
    ] == "PyTorch"
    assert result["education"] == [
        {"institution": "Pune University", "degree": "BSc (Hons)", "field": "Statistics"}
    ]
    assert result["certifications"] == [{"name": "Google Data Analytics", "issuer": "Google", "year": "2022"}]
    claim_ids = [c["id"] for c in result["claims_to_verify"]]
    assert claim_ids == ["c1", "c2"]  # c3 (Beta Corp) and c4 (Airflow) were about removed items
    assert all(c["source"] == "resume" for c in result["claims_to_verify"])
    assert result["intake_review"]["removed_claim_ids"] == ["c3", "c4"]
    assert result["job_match"] == PROFILE["job_match"]  # untouched keys stay


def test_removing_a_project_and_its_skill_drops_the_claim() -> None:
    original = document_from_profile(PROFILE)
    data = original.model_dump()
    data["projects"] = []
    data["skills"] = ["Stakeholder communication"]
    rec = record(original, ResumeReviewDoc.model_validate(data))
    result = effective_profile(PROFILE, rec.model_dump(mode="json"))  # a stored (dict) record works too
    assert [c["id"] for c in result["claims_to_verify"]] == ["c1", "c3", "c4"]
    assert result["projects"] == []
