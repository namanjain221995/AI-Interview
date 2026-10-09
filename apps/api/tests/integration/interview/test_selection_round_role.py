"""Round- and role-aware bank retrieval (``selection.bank_candidates``) on real Postgres/pgvector with mock
embeddings and the mock (token-overlap) reranker.

With ~1,000 bank questions per round type sharing competencies, a technical screening must not be served a
question written for a behavioural or recruiter round; combined/custom rounds mix technical, behavioural and
problem-solving items; the job's role boosts matching ``role_tags``; too few on-round items relax the filter
with a penalty instead of returning nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.db.models import BankQuestion
from app.domain.enums import Difficulty, RoundType
from app.services.interview.selection import (
    MIXED_ROUND_SOURCES,
    OFF_ROUND_PENALTY,
    ROLE_BOOST,
    accepted_round_types,
    bank_candidates,
    build_query,
    fits_round,
    job_role_tags,
)

pytestmark = pytest.mark.integration

QUERY_WORDS = "kubernetes autoscaling interview question"


async def _rows(
    db, mock_ai, specs: list[dict[str, Any]], *, key: str, embedded: bool = True
) -> dict[str, str]:
    """Insert bank rows; returns label -> id. Each spec: label, text, round_types, role_tags (+ overrides)."""
    vectors = await mock_ai.embeddings.embed_documents([s["text"] for s in specs]) if embedded else None
    ids: dict[str, str] = {}
    for i, spec in enumerate(specs):
        row = BankQuestion(
            organization_id=spec.get("organization_id"),
            external_key=f"sel-{key}-{i}",
            competency_key=key,
            difficulty=spec.get("difficulty", "medium"),
            kind="technical",
            text=spec["text"],
            topic="autoscaling",
            round_types=spec.get("round_types", []),
            role_tags=spec.get("role_tags", []),
            source="curated",
            status=spec.get("status", "active"),
            embedding=vectors[i] if vectors is not None else None,
        )
        db.add(row)
        await db.flush()
        ids[spec["label"]] = str(row.id)
    await db.commit()
    return ids


async def _find(db, mock_ai, key: str, round_type: RoundType | None, **kw: Any):
    return await bank_candidates(
        db,
        organization_id=kw.pop("organization_id", str(uuid.uuid4())),
        competency_key=key,
        competency_name="Kubernetes autoscaling",
        difficulty=Difficulty.MEDIUM,
        topics=["autoscaling"],
        job_title="Senior DevOps Engineer",
        round_type=round_type,
        limit=kw.pop("limit", 10),
        ai=mock_ai,
        **kw,
    )


def _key() -> str:
    return f"k8s_{uuid.uuid4().hex[:8]}"


@pytest.mark.parametrize("embedded", [True, False])
async def test_round_filter_keeps_items_written_for_the_round(db_session, mock_ai, embedded: bool) -> None:
    key = _key()
    ids = await _rows(
        db_session,
        mock_ai,
        [
            {
                "label": "tech1",
                "text": f"{QUERY_WORDS} on node pools",
                "round_types": ["technical_screening"],
            },
            {
                "label": "tech2",
                "text": f"{QUERY_WORDS} with HPA",
                "round_types": ["deep_technical", "technical_screening"],
            },
            {"label": "any", "text": f"{QUERY_WORDS} for spiky load", "round_types": []},
            # Best lexical match, but written for other rounds: never served to a technical screening.
            {
                "label": "beh",
                "text": f"{QUERY_WORDS} kubernetes autoscaling conflict",
                "round_types": ["behavioral"],
            },
            {
                "label": "rec",
                "text": f"{QUERY_WORDS} autoscaling salary",
                "round_types": ["recruiter_screening"],
            },
        ],
        key=key,
        embedded=embedded,
    )
    found = await _find(db_session, mock_ai, key, RoundType.TECHNICAL_SCREENING, min_round_matches=3)
    assert {c.id for c in found} == {ids["tech1"], ids["tech2"], ids["any"]}
    # No round type (legacy callers): no filter.
    unfiltered = await _find(db_session, mock_ai, key, None)
    assert {c.id for c in unfiltered} == set(ids.values())


async def test_too_few_round_matches_relax_with_a_penalty(db_session, mock_ai) -> None:
    key = _key()
    ids = await _rows(
        db_session,
        mock_ai,
        [
            {"label": "tech", "text": "kubernetes question", "round_types": ["technical_screening"]},
            {
                "label": "design",
                "text": f"{QUERY_WORDS} kubernetes autoscaling",
                "round_types": ["system_design"],
            },
            {"label": "beh", "text": f"{QUERY_WORDS} autoscaling", "round_types": ["behavioral"]},
        ],
        key=key,
    )
    strict = await _find(db_session, mock_ai, key, RoundType.TECHNICAL_SCREENING, min_round_matches=1)
    assert [c.id for c in strict] == [ids["tech"]]
    relaxed = await _find(db_session, mock_ai, key, RoundType.TECHNICAL_SCREENING)  # default minimum (6)
    assert [c.id for c in relaxed][0] == ids["tech"]  # the on-round item stays first despite a lower score
    assert {c.id for c in relaxed} == set(ids.values())
    query = build_query(
        competency_name="Kubernetes autoscaling",
        topics=["autoscaling"],
        job_title="Senior DevOps Engineer",
        round_type="technical_screening",
        difficulty=Difficulty.MEDIUM,
    )
    raw = await mock_ai.reranker.rerank(query, [f"{QUERY_WORDS} kubernetes autoscaling", "x"])
    raw_design = next(r.score for r in raw if r.index == 0)
    off = next(c for c in relaxed if c.id == ids["design"])
    assert off.score == pytest.approx(raw_design - OFF_ROUND_PENALTY, abs=1e-3)


async def test_combined_round_mixes_technical_behavioral_and_problem_solving(db_session, mock_ai) -> None:
    key = _key()
    ids = await _rows(
        db_session,
        mock_ai,
        [
            {"label": "tech", "text": f"{QUERY_WORDS} a", "round_types": ["technical_screening"]},
            {"label": "deep", "text": f"{QUERY_WORDS} b", "round_types": ["deep_technical"]},
            {"label": "beh", "text": f"{QUERY_WORDS} c", "round_types": ["behavioral"]},
            {"label": "ps", "text": f"{QUERY_WORDS} d", "round_types": ["problem_solving"]},
            {"label": "comb", "text": f"{QUERY_WORDS} e", "round_types": ["combined"]},
            {"label": "rec", "text": f"{QUERY_WORDS} f", "round_types": ["recruiter_screening"]},
            {"label": "mgr", "text": f"{QUERY_WORDS} g", "round_types": ["managerial"]},
        ],
        key=key,
    )
    found = await _find(db_session, mock_ai, key, RoundType.COMBINED, min_round_matches=3)
    assert {c.id for c in found} == {ids[k] for k in ("tech", "deep", "beh", "ps", "comb")}
    custom = await _find(db_session, mock_ai, key, RoundType.CUSTOM, min_round_matches=3)
    assert {c.id for c in custom} == {ids[k] for k in ("tech", "deep", "beh", "ps")}


async def test_job_role_boosts_matching_role_tags(db_session, mock_ai) -> None:
    key = _key()
    text = f"{QUERY_WORDS} for the same workload"
    ids = await _rows(
        db_session,
        mock_ai,
        [
            {
                "label": "frontend",
                "text": text,
                "round_types": ["technical_screening"],
                "role_tags": ["frontend"],
            },
            {
                "label": "devops",
                "text": text,
                "round_types": ["technical_screening"],
                "role_tags": ["devops", "sre"],
            },
            {
                "label": "general",
                "text": text,
                "round_types": ["technical_screening"],
                "role_tags": ["general"],
            },
        ],
        key=key,
    )
    found = await _find(
        db_session, mock_ai, key, RoundType.TECHNICAL_SCREENING, role_tags=["devops"], min_round_matches=1
    )
    assert found[0].id == ids["devops"]
    scores = {c.id: c.score for c in found}
    assert scores[ids["devops"]] == pytest.approx(scores[ids["frontend"]] + ROLE_BOOST, abs=1e-3)
    assert scores[ids["general"]] == pytest.approx(scores[ids["frontend"]], abs=1e-3)


async def test_org_scope_status_and_asked_exclusions_still_apply(db_session, mock_ai) -> None:
    from app.db.models import Organization

    key = _key()
    org = uuid.uuid4()
    db_session.add(Organization(id=org, name="Org A", slug=f"org-a-{org.hex[:8]}"))
    other = uuid.uuid4()
    db_session.add(Organization(id=other, name="Org B", slug=f"org-b-{other.hex[:8]}"))
    await db_session.flush()
    ids = await _rows(
        db_session,
        mock_ai,
        [
            {
                "label": "mine",
                "text": f"{QUERY_WORDS} 1",
                "round_types": ["technical_screening"],
                "organization_id": org,
            },
            {"label": "global", "text": f"{QUERY_WORDS} 2", "round_types": ["technical_screening"]},
            {
                "label": "theirs",
                "text": f"{QUERY_WORDS} 3",
                "round_types": ["technical_screening"],
                "organization_id": other,
            },
            {
                "label": "retired",
                "text": f"{QUERY_WORDS} 4",
                "round_types": ["technical_screening"],
                "status": "retired",
            },
            {"label": "asked", "text": f"{QUERY_WORDS} 5", "round_types": ["technical_screening"]},
        ],
        key=key,
    )
    found = await _find(
        db_session,
        mock_ai,
        key,
        RoundType.TECHNICAL_SCREENING,
        organization_id=str(org),
        exclude_ids=[ids["asked"]],
        min_round_matches=1,
    )
    assert {c.id for c in found} == {ids["mine"], ids["global"]}


def test_accepted_round_types_and_fits_round() -> None:
    assert accepted_round_types(None) == [] and accepted_round_types("") == []
    assert accepted_round_types(RoundType.BEHAVIORAL) == ["behavioral"]
    assert accepted_round_types("technical_screening") == ["technical_screening"]
    mixed = [rt.value for rt in MIXED_ROUND_SOURCES]
    assert accepted_round_types(RoundType.COMBINED) == ["combined", *mixed]
    assert accepted_round_types("custom") == ["custom", *mixed]
    assert fits_round([], ["behavioral"]) and fits_round(["behavioral", "combined"], ["behavioral"])
    assert not fits_round(["system_design"], ["technical_screening"])
    assert fits_round(["system_design"], [])


@pytest.mark.parametrize(
    ("job", "roles"),
    [
        ({"title": "Senior DevOps Engineer"}, ["devops"]),
        ({"title": "DevOps / SRE Engineer"}, ["devops", "sre"]),
        ({"title": "Site Reliability Engineer II"}, ["sre"]),
        ({"title": "Platform Engineer", "department": "Cloud Infrastructure"}, ["platform", "cloud"]),
        ({"title": "Back-end Developer (Python)"}, ["backend"]),
        ({"title": "Full-Stack Engineer"}, ["fullstack"]),
        ({"title": "Frontend Engineer, React"}, ["frontend"]),
        ({"title": "Machine Learning Engineer"}, ["ml"]),
        ({"title": "Senior Data Engineer"}, ["data"]),
        ({"title": "Data Scientist"}, ["data_scientist"]),
        ({"title": "Engineering Manager, Payments"}, ["engineering_manager"]),
        ({"title": "Tech Lead"}, ["tech_lead"]),
        ({"title": "QA Automation Engineer"}, ["qa"]),
        ({"title": "Generative AI Engineer"}, ["llm"]),
        ({"title": "Software Engineer"}, []),
        ({"title": ""}, []),
        (None, []),
    ],
)
def test_job_role_tags_from_title_and_department(job: dict[str, Any] | None, roles: list[str]) -> None:
    assert job_role_tags(job) == roles
