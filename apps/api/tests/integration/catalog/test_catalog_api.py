"""Competencies and question bank over HTTP: CRUD, ownership, semantic search,
AI generation with near-duplicate skipping."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update

from app.db.models import AuditLog, BankQuestion, Competency
from app.domain.enums import ModelTask, Role
from app.schemas.ai import GeneratedQuestion, GeneratedQuestionSet
from app.services.question_bank.embeddings import question_text
from app.services.retrieval import embed_and_store
from tests.integration.resume.helpers import API

pytestmark = pytest.mark.integration


async def test_competencies(auth_client, org_factory, mock_ai, seeded_catalog, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    listing = (await client.get(f"{API}/competencies", params={"limit": 200})).json()
    seeded = await db_session.scalar(
        select(func.count()).select_from(Competency).where(Competency.organization_id.is_(None))
    )
    assert seeded and listing["total"] == seeded and all(c["is_global"] for c in listing["items"])
    technical = (
        await client.get(f"{API}/competencies", params={"category": "technical", "q": "kube"})
    ).json()
    assert any(c["key"] == "kubernetes" for c in technical["items"])

    created = await client.post(
        f"{API}/competencies",
        json={
            "key": "Data-Mesh",
            "name": "Data mesh",
            "category": "domain",
            "description": "Domain-owned data products",
            "rubric": {"levels": {"1": "aware", "5": "designed one"}, "dimensions": ["ownership"]},
        },
    )
    assert created.status_code == 201, created.text
    competency = created.json()
    assert (
        competency["key"] == "data_mesh" and competency["is_global"] is False and competency["has_embedding"]
    )
    duplicate = await client.post(
        f"{API}/competencies", json={"key": "data_mesh", "name": "X", "category": "domain"}
    )
    assert duplicate.status_code == 409
    shadow = await client.post(
        f"{API}/competencies", json={"key": "kubernetes", "name": "K", "category": "technical"}
    )
    assert shadow.status_code == 409  # a global key cannot be shadowed
    row = await db_session.get(Competency, uuid.UUID(competency["id"]))
    before = list(row.embedding)
    patched = await client.patch(
        f"{API}/competencies/{competency['id']}", json={"description": "Federated data ownership"}
    )
    assert patched.status_code == 200 and patched.json()["description"] == "Federated data ownership"
    await db_session.refresh(row)
    assert list(row.embedding) != before
    deactivated = await client.patch(f"{API}/competencies/{competency['id']}", json={"is_active": False})
    assert deactivated.json()["is_active"] is False
    assert (await client.get(f"{API}/competencies", params={"scope": "organization"})).json()["total"] == 0
    assert (
        await client.get(f"{API}/competencies", params={"scope": "organization", "include_inactive": True})
    ).json()["total"] == 1

    global_id = listing["items"][0]["id"]
    forbidden = await client.patch(f"{API}/competencies/{global_id}", json={"name": "Mine"})
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "global_read_only"
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (
        await outsider.patch(f"{API}/competencies/{competency['id']}", json={"name": "x"})
    ).status_code == 404
    interviewer = await auth_client(Role.INTERVIEWER, organization_id=client.user.organization_id)
    assert (await interviewer.get(f"{API}/competencies")).status_code == 200
    assert (
        await interviewer.post(f"{API}/competencies", json={"key": "abc", "name": "A", "category": "domain"})
    ).status_code == 403


async def _embed_all_questions(db_session) -> None:
    rows = (await db_session.scalars(select(BankQuestion))).all()
    await embed_and_store(db_session, rows, text=question_text)
    await db_session.commit()


async def test_question_bank_crud_and_search(auth_client, mock_ai, seeded_catalog, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    listing = (
        await client.get(f"{API}/question-bank", params={"competency_key": "kubernetes", "limit": 200})
    ).json()
    assert listing["total"] > 0 and all(q["competency_key"] == "kubernetes" for q in listing["items"])

    # No embeddings yet (fresh seed): search falls back to lexical matching.
    lexical = (
        await client.post(f"{API}/question-bank/search", json={"query": "kubernetes autoscaling"})
    ).json()
    assert lexical["mode"] == "lexical" and lexical["items"]

    created = await client.post(
        f"{API}/question-bank",
        json={
            "competency_key": "kubernetes",
            "difficulty": "hard",
            "kind": "scenario",
            "topic": "Karpenter consolidation",
            "text": "Your Karpenter consolidation keeps evicting latency-sensitive pods during peak hours. How do you fix it?",
            "expected_concepts": ["disruption budgets", "consolidation policy", "pod priority"],
            "rubric": {"excellent": "Covers PDBs and consolidation windows", "weak": "Disables autoscaling"},
            "role_tags": ["DevOps Engineer"],
            "round_types": ["deep_technical"],
        },
    )
    assert created.status_code == 201, created.text
    question = created.json()
    assert question["source"] == "curated" and question["status"] == "active" and question["has_embedding"]
    assert question["role_tags"] == ["devops_engineer"] and question["is_global"] is False
    bad = await client.post(
        f"{API}/question-bank",
        json={
            "competency_key": "basket_weaving",
            "difficulty": "easy",
            "kind": "technical",
            "text": "What is a basket weave?",
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unknown_competency"

    await _embed_all_questions(db_session)
    semantic = (
        await client.post(
            f"{API}/question-bank/search",
            json={
                "query": "Karpenter consolidation evicting latency-sensitive pods",
                "competency_key": "kubernetes",
                "limit": 5,
            },
        )
    ).json()
    assert semantic["mode"] == "semantic" and semantic["reranked"] is True
    assert semantic["items"][0]["id"] == question["id"]
    assert semantic["items"][0]["score"] > 0 and semantic["items"][0]["vector_similarity"] > 0
    filtered = (
        await client.post(
            f"{API}/question-bank/search", json={"query": "kubernetes", "difficulty": "easy", "limit": 50}
        )
    ).json()
    assert filtered["items"] and all(q["difficulty"] == "easy" for q in filtered["items"])
    by_round = (
        await client.post(
            f"{API}/question-bank/search",
            json={"query": "Karpenter", "round_type": "deep_technical", "limit": 50},
        )
    ).json()
    assert question["id"] in [q["id"] for q in by_round["items"]]

    patched = await client.patch(
        f"{API}/question-bank/{question['id']}", json={"status": "retired", "difficulty": "medium"}
    )
    assert patched.status_code == 200 and patched.json()["status"] == "retired"
    active_only = (
        await client.post(f"{API}/question-bank/search", json={"query": "Karpenter consolidation"})
    ).json()
    assert question["id"] not in [q["id"] for q in active_only["items"]]
    global_id = listing["items"][0]["id"]
    assert (
        await client.patch(f"{API}/question-bank/{global_id}", json={"status": "draft"})
    ).status_code == 403
    assert (await client.get(f"{API}/question-bank/{global_id}")).status_code == 200
    org_only = (await client.get(f"{API}/question-bank", params={"scope": "organization"})).json()
    assert org_only["total"] == 1
    assert org_only["items"][0]["external_key"] == f"curated:{question['id']}"
    # A second organization question must not collide on (organization_id, external_key).
    second = await client.post(
        f"{API}/question-bank",
        json={
            "competency_key": "kubernetes",
            "difficulty": "easy",
            "kind": "technical",
            "text": "What does a Kubernetes liveness probe do, and when can it hurt you?",
        },
    )
    assert second.status_code == 201, second.text


async def test_generate_skips_near_duplicates(auth_client, mock_ai, seeded_catalog, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    await _embed_all_questions(db_session)
    existing = await db_session.scalar(
        select(BankQuestion)
        .where(BankQuestion.competency_key == "kubernetes")
        .order_by(BankQuestion.external_key)
        .limit(1)
    )
    duplicate = GeneratedQuestion(
        competency_key="kubernetes",
        topic=existing.topic or "k8s",
        difficulty="medium",
        kind="technical",
        text=existing.text,
        expected_concepts=list(existing.expected_concepts),
    )
    fresh = GeneratedQuestion(
        competency_key="kubernetes",
        topic="Multi-tenant clusters",
        difficulty="medium",
        kind="scenario",
        text="How would you isolate noisy tenants sharing one EKS cluster without giving each its own nodes?",
        expected_concepts=["resource quotas", "network policies", "priority classes"],
        rubric_excellent="Quotas, limits, network policies and fair scheduling",
        rubric_weak="One cluster per tenant",
        followups=["How do you bill tenants?"],
    )
    twin = fresh.model_copy(update={"topic": "Multi-tenant clusters"})
    mock_ai.llm.script(
        ModelTask.QUESTION_GENERATION, GeneratedQuestionSet(questions=[duplicate, fresh, twin])
    )
    response = await client.post(
        f"{API}/question-bank/generate",
        json={
            "competency_key": "kubernetes",
            "difficulty": "medium",
            "count": 3,
            "role": "Senior DevOps Engineer",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["requested"] == 3 and body["generated"] == 3
    assert [q["text"] for q in body["created"]] == [fresh.text]
    assert body["created"][0]["external_key"] == f"generated:{body['created'][0]['id']}"
    created = body["created"][0]
    assert created["source"] == "generated" and created["status"] == "draft" and created["has_embedding"]
    assert created["role_tags"] == ["senior_devops_engineer"]
    assert created["rubric"] == {"excellent": fresh.rubric_excellent, "weak": fresh.rubric_weak}
    reasons = sorted((s["reason"], s["duplicate_of"]) for s in body["skipped"])
    assert reasons == [("duplicate_in_batch", None), ("near_duplicate", str(existing.id))]
    call = mock_ai.llm.calls_for(ModelTask.QUESTION_GENERATION)[0]
    assert "Fairness and evidence rules" in call.messages[0]["content"]
    assert "Senior DevOps Engineer" in call.messages[1]["content"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "question_bank.generate")
        )
    ) == 1

    unknown = await client.post(
        f"{API}/question-bank/generate",
        json={"competency_key": "nope", "difficulty": "easy", "count": 1, "role": "x y"},
    )
    assert unknown.status_code == 422
    from app.ai.base import AIUnavailable

    mock_ai.llm.script(ModelTask.QUESTION_GENERATION, AIUnavailable("down", endpoint="main"))
    down = await client.post(
        f"{API}/question-bank/generate",
        json={"competency_key": "kubernetes", "difficulty": "easy", "count": 1, "role": "SRE"},
    )
    assert down.status_code == 503 and down.json()["error"]["code"] == "generation_unavailable"


async def test_generation_rate_limit(auth_client, mock_ai, seeded_catalog, monkeypatch) -> None:
    from app.api.routers import question_bank as router_module

    monkeypatch.setattr(router_module, "GENERATE_LIMIT", 1)
    client = await auth_client(Role.RECRUITER)
    body = {"competency_key": "kubernetes", "difficulty": "easy", "count": 1, "role": "SRE"}
    assert (await client.post(f"{API}/question-bank/generate", json=body)).status_code == 201
    limited = await client.post(f"{API}/question-bank/generate", json=body)
    assert limited.status_code == 429 and "Retry-After" in limited.headers


async def test_question_bank_org_isolation(
    auth_client, org_factory, mock_ai, seeded_catalog, db_session
) -> None:
    client = await auth_client(Role.RECRUITER)
    created = (
        await client.post(
            f"{API}/question-bank",
            json={
                "competency_key": "kubernetes",
                "difficulty": "easy",
                "kind": "technical",
                "text": "Explain what a Kubernetes readiness probe protects against.",
            },
        )
    ).json()
    outsider = await auth_client(Role.ADMIN, organization_id=await org_factory())
    assert (await outsider.get(f"{API}/question-bank/{created['id']}")).status_code == 404
    search = (
        await outsider.post(f"{API}/question-bank/search", json={"query": "readiness probe", "limit": 50})
    ).json()
    assert created["id"] not in [q["id"] for q in search["items"]]
    await db_session.execute(update(BankQuestion).values(embedding=None))
    await db_session.commit()
    lexical = (
        await outsider.post(f"{API}/question-bank/search", json={"query": "readiness probe", "limit": 50})
    ).json()
    assert created["id"] not in [q["id"] for q in lexical["items"]]


async def test_generate_creates_several_questions(auth_client, mock_ai, seeded_catalog, db_session) -> None:
    client = await auth_client(Role.RECRUITER)
    questions = [
        GeneratedQuestion(
            competency_key="kubernetes",
            topic=f"Topic {i}",
            difficulty="hard",
            kind="scenario",
            text=text,
            expected_concepts=concepts,
        )
        for i, (text, concepts) in enumerate(
            [
                (
                    "Design a zero-downtime node pool upgrade for a cluster running stateful databases.",
                    ["pdb", "surge"],
                ),
                (
                    "Explain how you would cap the blast radius of a bad admission webhook in production.",
                    ["failurePolicy"],
                ),
                (
                    "Walk me through debugging intermittent DNS timeouts between pods across availability zones.",
                    ["coredns"],
                ),
            ]
        )
    ]
    mock_ai.llm.script(ModelTask.QUESTION_GENERATION, GeneratedQuestionSet(questions=questions))
    response = await client.post(
        f"{API}/question-bank/generate",
        json={
            "competency_key": "kubernetes",
            "difficulty": "hard",
            "count": 3,
            "role": "Platform Engineer",
            "round_type": "deep_technical",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()["created"]
    assert len(created) == 3 and len({q["external_key"] for q in created}) == 3
    assert all(q["round_types"] == ["deep_technical"] and q["difficulty"] == "hard" for q in created)
    drafts = (
        await client.get(f"{API}/question-bank", params={"status": "draft", "source": "generated"})
    ).json()
    assert drafts["total"] == 3
    activated = await client.patch(f"{API}/question-bank/{created[0]['id']}", json={"status": "active"})
    assert activated.status_code == 200 and activated.json()["status"] == "active"
