"""Embedding text definitions, seed batching and question-bank helpers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.seed.embeddings import embedding_batches
from app.services.question_bank.embeddings import (
    coding_question_text,
    competency_text,
    job_text,
    question_text,
    requirement_text,
)
from app.services.question_bank.service import role_tag


def test_question_text_matches_seed_definition() -> None:
    from app.seed.loaders import load_catalog

    seed = load_catalog().questions[0]
    row = SimpleNamespace(**seed.to_row())
    assert question_text(row) == seed.embedding_text()


def test_texts() -> None:
    competency = SimpleNamespace(
        name="Kubernetes",
        description="Container orchestration.",
        rubric={"dimensions": ["scaling"], "levels": {"1": "basic", "5": "expert"}},
    )
    assert (
        competency_text(competency)
        == "Kubernetes: Container orchestration. Dimensions: scaling Levels: 1: basic 5: expert"
    )
    assert requirement_text(SimpleNamespace(name="Terraform", description=None)) == "Terraform"
    assert (
        requirement_text(SimpleNamespace(name="Terraform", description="IaC at scale"))
        == "Terraform. IaC at scale"
    )
    job = SimpleNamespace(title="SRE", seniority="Senior", department=None, description="Run production.")
    assert job_text(job, ["Kubernetes", "Go"]) == "SRE (Senior). Run production. Requirements: Kubernetes, Go"
    coding = SimpleNamespace(
        title="Two Sum", prompt_markdown="Find two numbers." * 400, expected_concepts=["hash map"]
    )
    text = coding_question_text(coding)
    assert (
        text.startswith("Two Sum. Find two numbers.")
        and text.endswith("Concepts: hash map")
        and len(text) < 1600
    )
    assert (
        question_text(SimpleNamespace(topic=None, text="Explain DNS.", expected_concepts=[]))
        == "Explain DNS. Concepts: "
    )


def test_coding_question_text_matches_seed_definition() -> None:
    from app.seed.loaders import load_catalog

    for seed in load_catalog().coding_questions:
        assert coding_question_text(SimpleNamespace(**seed.to_row())) == seed.embedding_text()


def test_seed_embedding_batches() -> None:
    ids = [str(uuid.uuid4()) for _ in range(130)]
    context = {
        "question_ids_needing_embedding": ids,
        "coding_question_ids_needing_embedding": ids[:3],
        "competency_ids_needing_embedding": [],
    }
    batches = embedding_batches(context, batch_size=64)
    assert [len(next(iter(p.values()))) for p, _ in batches] == [64, 64, 2, 3]
    assert [next(iter(p)) for p, _ in batches] == ["question_ids"] * 3 + ["coding_question_ids"]
    assert len({key for _, key in batches}) == 4
    assert embedding_batches(context, batch_size=64) == batches  # stable dedupe keys
    assert embedding_batches({}) == []


def test_role_tag() -> None:
    assert role_tag("Senior DevOps Engineer") == "senior_devops_engineer"
    assert role_tag("  !!! ") == "general"
