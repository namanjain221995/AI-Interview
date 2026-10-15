"""Deterministic quick profile (app.services.resume.quick)."""

from __future__ import annotations

import json
import time
from datetime import date

import pytest

from app.schemas.ai import CandidateProfile as CandidateProfileDocument
from app.services.resume.extraction import docx_text, pdf_text_pages
from app.services.resume.normalize import detect_sections, heading_section, normalize_text
from app.services.resume.quick import (
    QUICK_STATUS,
    build_quick_profile,
    contact_presence,
    identity,
    initial_sections,
    parse_certifications,
    parse_education,
    parse_projects,
    parse_skill_items,
)
from app.services.resume.skills import find_vocabulary_skills
from tests.unit.resume import corpus


def sample_text(name: str) -> str:
    data = corpus.sample(name)
    if name.endswith(".pdf"):
        return normalize_text("\n\n".join(pdf_text_pages(data)))
    if name.endswith(".docx"):
        return normalize_text(docx_text(data))
    return normalize_text(data.decode())


@pytest.mark.parametrize("name", ["jane_doe_devops.txt", "jane_doe_devops.pdf", "jane_doe_devops.docx"])
def test_jane_quick_profile_in_every_text_format(name: str) -> None:
    text = sample_text(name)
    build_quick_profile(
        text, candidate_id="c-0", today=date(2026, 10, 8)
    )  # warm-up: lazy imports, regex caches
    started = time.perf_counter()
    quick = build_quick_profile(text, candidate_id="c-1", today=date(2026, 10, 8))
    elapsed_ms = (time.perf_counter() - started) * 1000
    doc = quick.document
    CandidateProfileDocument.model_validate(doc)  # contract fields only
    assert elapsed_ms < 250  # milliseconds, not seconds (measured ~1-3 ms)
    assert doc["candidate_name"] == "Jane Doe" and doc["headline"] == "Senior DevOps Engineer"
    assert [(r["company"], r["start"], r["end"]) for r in doc["roles"]] == [
        ("Bluepeak Analytics", "2023-01", "present"),
        ("Harborline Logistics", "2021-08", "2023-03"),
        ("Cobalt Retail Group", "2021-01", "2021-07"),
    ]
    assert doc["experience_years"] == 5.8
    names = {s["name"] for group in doc["skills"].values() for s in group}
    assert {"Kubernetes", "Terraform", "AWS", "EKS", "GitHub Actions", "Python"} <= names
    assert "Cloud & IaC" not in names  # line labels are not skills
    assert doc["education"] == [
        {
            "institution": "Lakeview State University",
            "degree": "B.S.",
            "field": "Computer Science",
            "start": None,
            "end": None,
        }
    ]
    assert [(c["name"], c["year"]) for c in doc["certifications"]] == [
        ("AWS Certified Solutions Architect – Associate", "2022"),
        ("Certified Kubernetes Administrator (CKA)", "2023"),
    ]
    assert [p["name"] for p in doc["projects"]] == ["Internal developer platform"]
    assert any(i["description"].startswith("Overlapping employment dates") for i in doc["inconsistencies"])
    assert doc["job_match"] is None and doc["recommended_topics"] == []
    assert quick.meta["contact"] == {"email": True, "phone": True, "links": True}


def test_contact_details_are_presence_flags_only() -> None:
    text = sample_text("jane_doe_devops.txt")
    quick = build_quick_profile(text, candidate_id="c-1")
    blob = json.dumps(quick.document) + json.dumps(quick.meta)
    assert "jane.doe@example.com" not in blob and "555-0147" not in blob and "janedoe.example.com" not in blob
    assert contact_presence("Ana Ruiz\nana@example.org") == {"email": True, "phone": False, "links": False}


def test_protected_details_never_reach_the_quick_profile() -> None:
    text = normalize_text(
        "Sam Lee\nBackend Engineer\nDate of birth: 04/05/1990 · Nationality: Exampleland · Married\n\n"
        "EXPERIENCE\nBackend Engineer — Acme, Remote\nJan 2020 – Present\n- Built APIs\n\n"
        "EDUCATION\nB.Sc. Computer Science, Example University, 2008 – 2012 (Class of 2012)\n"
    )
    doc = build_quick_profile(text, candidate_id="c-1").document
    blob = json.dumps(doc).lower()
    for fragment in ("1990", "birth", "nationality", "exampleland", "married", "2008", "2012"):
        assert fragment not in blob, fragment
    assert doc["education"][0]["institution"] == "Example University"
    assert doc["education"][0]["degree"] == "B.Sc." and doc["education"][0]["field"] == "Computer Science"


def test_other_samples() -> None:
    marcus = build_quick_profile(sample_text("marcus_okafor_backend.pdf"), candidate_id="c").document
    assert marcus["candidate_name"] == "Marcus Okafor" and len(marcus["roles"]) == 2
    skills = {s["name"]: s for group in marcus["skills"].values() for s in group}
    assert skills["Kubernetes"]["claimed_level"] == "beginner"  # "Kubernetes (basic)"
    assert {"asyncio", "typing", "FastAPI"} <= set(skills)
    sofia = build_quick_profile(sample_text("sofia_martinez_frontend.docx"), candidate_id="c").document
    assert sofia["candidate_name"] == "Sofia Martinez" and len(sofia["roles"]) == 2
    priya = build_quick_profile(sample_text("priya_raman_ml.txt"), candidate_id="c").document
    assert priya["candidate_name"] == "Priya Raman" and len(priya["roles"]) == 3
    assert priya["education"][0]["degree"] == "M.S."


def test_skill_items_parse_labels_parentheses_and_levels() -> None:
    items = parse_skill_items(
        "Cloud & IaC: AWS (EKS, IAM), Terraform\nLanguages: Python (advanced); Rust (basic) | Go\n- Kubernetes"
    )
    by_name = {i.name: i for i in items}
    assert list(by_name) == ["AWS", "EKS", "IAM", "Terraform", "Python", "Rust", "Go", "Kubernetes"]
    assert by_name["Python"].claimed_level == "advanced" and by_name["Rust"].claimed_level == "beginner"
    assert by_name["AWS"].category == "cloud" and by_name["Kubernetes"].category == "devops"


def test_education_and_certification_parsing() -> None:
    education = parse_education(
        "Example State University\nBachelor of Science in Physics, 2015 - 2019\nGPA 3.8\n"
        "- MBA, Example Business School (2021)"
    )
    assert [(e.institution, e.degree, e.field) for e in education] == [
        ("Example State University", "Bachelor of Science", "Physics"),
        ("Example Business School", "MBA", None),
    ]
    certs = parse_certifications("- CKA, 2023\nAWS SAA")
    assert [(c.name, c.year) for c in certs] == [("CKA", "2023"), ("AWS SAA", None)]


def test_projects_parsing() -> None:
    bullets = parse_projects(
        "- Rate limiter: token bucket in Go; 400 stars\n- Habit tracker: PWA with 1,200 users"
    )
    assert [p.name for p in bullets] == ["Rate limiter", "Habit tracker"]
    titled = parse_projects(
        "Rate Limiter\n- Token bucket in Go\n- 400 GitHub stars\nCache Proxy\n- LRU in Rust"
    )
    assert [p.name for p in titled] == ["Rate Limiter", "Cache Proxy"]
    assert titled[0].outcomes == ["400 GitHub stars"]


def test_identity_skips_document_titles_and_contact_lines() -> None:
    sections = detect_sections(
        "Curriculum Vitae\nMARÍA LÓPEZ\nmaria@example.com\nData Engineer\n\nSKILLS\nSQL"
    )
    assert identity(sections, "") == ("María López", "Data Engineer")


def test_vocabulary_scan_avoids_ordinary_words() -> None:
    found = {
        s.name
        for s in find_vocabulary_skills("We react to incidents and go live with React, Node.js and k8s.")
    }
    assert "React" in found and "Node.js" in found and "Kubernetes" not in found
    assert not any(n.lower() == "go" for n in found)
    assert "Kubernetes" in {s.name.title() for s in find_vocabulary_skills("Ran Kubernetes on EKS")}


def test_lenient_section_headings() -> None:
    assert heading_section("Academic Projects") == "projects"
    assert heading_section("INDUSTRY EXPERIENCE") == "experience"
    assert heading_section("Technical Skills:") == "skills"
    assert heading_section("Project Manager") is None  # a job title, not a heading
    assert heading_section("Volunteer Experience") == "other"  # exact vocabulary wins


def test_initial_sections() -> None:
    sections = initial_sections()
    assert QUICK_STATUS == "quick"
    assert {s["key"]: s["state"] for s in sections} == {
        "basics": "quick",
        "experience": "quick",
        "skills": "quick",
        "education": "quick",
        "projects": "quick",
        "interview": "pending",
    }
    assert all(s["partial"] for s in initial_sections(partial=True) if s["key"] != "interview")
