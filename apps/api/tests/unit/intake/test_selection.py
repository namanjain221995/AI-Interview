"""Deterministic intake selections (INTAKE_INTERVIEW.md 6.5): level, passages, card, language, claims, scenarios."""

from __future__ import annotations

import pytest

from app.services.intake import selection as sel
from app.services.intake.kit import load_intake_kit


@pytest.fixture(scope="module")
def kit():
    return load_intake_kit()


# =========================================================================================== level


@pytest.mark.parametrize(
    ("kwargs", "level"),
    [
        ({"self_rating": 8, "verification_quality": "strong", "resume_ai_ml": True, "years": 2, "thin": False}, "advanced"),
        ({"self_rating": 7, "verification_quality": "adequate", "resume_ai_ml": False, "years": 5, "thin": False}, "advanced"),
        ({"self_rating": 8, "verification_quality": "partial", "resume_ai_ml": True, "years": 6, "thin": False}, "intermediate"),
        ({"self_rating": 8, "verification_quality": "incorrect", "resume_ai_ml": True, "years": 6, "thin": False}, "easy"),
        ({"self_rating": 4, "verification_quality": "strong", "resume_ai_ml": True, "years": 6, "thin": False}, "easy"),
        ({"self_rating": 6, "verification_quality": "adequate", "resume_ai_ml": False, "years": 1, "thin": True}, "easy"),
        ({"self_rating": 6, "verification_quality": "adequate", "resume_ai_ml": True, "years": 3, "thin": False}, "intermediate"),
        # No quick grade: self-rating and résumé only.
        ({"self_rating": 8, "verification_quality": None, "resume_ai_ml": True, "years": 1, "thin": False}, "advanced"),
        ({"self_rating": None, "verification_quality": None, "resume_ai_ml": False, "years": None, "thin": False}, "intermediate"),
    ],
)  # fmt: skip
def test_level_estimate(kwargs: dict, level: str) -> None:
    assert sel.level_estimate(**kwargs) == level


# ======================================================================================== passages


def test_passages_per_level_and_drill_b_never_equals_drill_a(kit) -> None:
    for i in range(60):
        sid = f"session-{i}"
        a, b = sel.select_passages(kit, "easy", sid)
        assert (a, b) == ("A", "F")
        a, b = sel.select_passages(kit, "intermediate", sid)
        assert a in ("B", "F") and b == "C"
        a, b = sel.select_passages(kit, "advanced", sid)
        assert a in ("D", "E") and b == ("C" if a == "E" else "E")
        assert a != b


def test_passage_tie_break_is_deterministic_and_spreads(kit) -> None:
    assert sel.select_passages(kit, "intermediate", "abc") == sel.select_passages(kit, "intermediate", "abc")
    picks = {sel.select_passages(kit, "intermediate", f"s{i}")[0] for i in range(40)}
    assert picks == {"B", "F"}
    assert sel.tie_break("x", "drill_a", ["B", "F"]) in ("B", "F")


def test_passage_overrides_win_and_drill_b_falls_back_to_the_next_in_pool(kit) -> None:
    assert sel.select_passages(kit, "easy", "s", drill_a="E", drill_b="auto") == ("E", "F")
    assert sel.select_passages(kit, "advanced", "s", drill_a="B", drill_b="C") == ("B", "C")
    a, b = sel.select_passages(kit, "advanced", "s", drill_a="E", drill_b="E")
    assert a == "E" and b == "F"  # relay pool A B C E F: next after E
    a, b = sel.select_passages(kit, "advanced", "s", drill_a="F", drill_b="F")
    assert a == "F" and b == "A"  # wraps around
    # Drill A pool never contains C (relay only); Drill B pool never contains D (read-aloud only).
    assert sel.select_passages(kit, "easy", "s", drill_a="C")[0] == "A"
    assert sel.select_passages(kit, "easy", "s", drill_b="D")[1] == "F"


def test_cards_per_level_and_override() -> None:
    for i in range(40):
        assert sel.select_card("easy", f"s{i}") in {"2", "5", "6", "7"}
        assert sel.select_card("intermediate", f"s{i}") in {"1", "2", "3", "5", "6"}
        assert sel.select_card("advanced", f"s{i}") in {"1", "3", "4", "8"}
    assert sel.select_card("easy", "s", "8") == "8"
    assert sel.select_card("easy", "s", "9") in {"2", "5", "6", "7"}


# ======================================================================================== language


@pytest.mark.parametrize(
    ("names", "key"),
    [
        (("Python",), "python"),
        (("C#",), "csharp"),
        (("JavaScript / Node",), "javascript"),
        (("node.js",), "javascript"),
        (("Other", "PostgreSQL"), "sql"),
        (("Rust",), None),
        ((None, ""), None),
    ],
)
def test_resolve_language(kit, names: tuple, key: str | None) -> None:
    found = sel.resolve_language(kit, *names)
    assert (found.key if found else None) == key


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Mostly Java, some SQL on the side.", "java"),
        ("I write JavaScript every day", "javascript"),
        ("I'd say C sharp and a bit of dot net", "csharp"),
        ("python python python", "python"),
        ("Go and Rust", None),
    ],
)
def test_language_in_text(kit, text: str, key: str | None) -> None:
    found = sel.language_in_text(kit, text)
    assert (found.key if found else None) == key


def test_fallback_language_prefers_sql_from_the_resume(kit) -> None:
    sql_profile = {"skills": {"database": [{"name": "SQL Server"}]}}
    assert sel.fallback_language(kit, sql_profile).key == "sql"
    assert sel.fallback_language(kit, {"skills": {"tool": [{"name": "Excel"}]}}).key == "python"


# ========================================================================================== claims


def claim(cid: str, text: str, category: str, priority: int, **extra) -> dict:
    return {"id": cid, "claim": text, "category": category, "priority": priority, **extra}


def test_claims_by_priority_with_probe_mapping_and_diversity() -> None:
    profile = {
        "claims_to_verify": [
            claim("c1", "Cut cloud spend by 30%", "impact", 5),
            claim("c2", "Built the ETL in Airflow", "technical", 4),
            claim("c3", "Led a team of six", "leadership", 4),
            claim("c4", "Led the migration squad", "leadership", 3),
            claim("c5", "Owned the billing service", "ownership", 2),
        ]
    }
    claims = sel.select_claims(profile, min_claims=2, max_claims=3)
    assert [c["ref"] for c in claims] == ["C1", "C2", "C3"]
    assert claims[0]["probe"] == "tool_or_framework"  # first claim: a tool/framework or project
    assert claims[0]["text"] == "Built the ETL in Airflow"
    assert claims[1]["probe"] != claims[0]["probe"]
    assert sum(1 for c in claims if c["probe"] == "leadership") <= 1
    assert {c["profile_claim_id"] for c in claims} <= {"c1", "c2", "c3", "c4", "c5"}


def test_removed_claims_are_dropped_and_corrections_rank_after_resume_items() -> None:
    profile = {
        "claims_to_verify": [
            claim("c1", "Built a churn model in scikit-learn", "technical", 5, source="candidate_correction"),
            claim("c2", "Deployed services on Kubernetes", "technical", 3),
            claim("c3", "Removed item", "technical", 5, removed=True),
        ]
    }
    claims = sel.select_claims(profile, min_claims=2, max_claims=2)
    assert [c["text"] for c in claims] == [
        "Deployed services on Kubernetes",
        "Built a churn model in scikit-learn",
    ]
    assert claims[1]["source"] == "candidate_correction"


def test_few_claims_are_completed_from_the_corrected_document() -> None:
    profile = {
        "claims_to_verify": [],
        "roles": [
            {"title": "Analyst", "company": "Old Co", "end": "2021", "technologies": ["Excel"]},
            {
                "title": "Data Engineer",
                "company": "Acme",
                "end": "present",
                "technologies": ["Spark", "Airflow"],
            },
        ],
        "projects": [{"name": "Churn dashboard", "outcomes": ["Cut churn 5%"], "technologies": ["Tableau"]}],
    }
    claims = sel.select_claims(profile, min_claims=2, max_claims=3)
    assert (
        claims[0]["text"] == "You listed Spark, Airflow at Acme" and claims[0]["probe"] == "tool_or_framework"
    )
    assert (
        claims[1]["text"].startswith("The project Churn dashboard")
        and claims[1]["probe"] == "project_or_product"
    )
    assert len(claims) == 2


def test_nothing_usable_gives_one_thin_or_vague_claim() -> None:
    claims = sel.select_claims({}, min_claims=2, max_claims=3)
    assert len(claims) == 1
    assert (
        claims[0]["probe"] == "thin_or_vague"
        and claims[0]["source"] == "thin_resume"
        and claims[0]["ref"] == "C1"
    )
    assert sel.thin_resume({})
    assert not sel.thin_resume({"projects": [{"name": "x"}]})


def test_ai_ml_and_experience_helpers() -> None:
    assert sel.profile_mentions_ai_ml({"skills": {"ai_ml": [{"name": "PyTorch"}]}})
    assert sel.profile_mentions_ai_ml({"roles": [{"technologies": ["scikit-learn", "pandas"]}]})
    assert not sel.profile_mentions_ai_ml({"roles": [{"technologies": ["Excel"]}]})
    assert sel.experience_years({"experience_years": 4}) == 4.0
    assert sel.experience_years({}, 2.0) == 2.0


# ======================================================================================= scenarios


def test_scenarios_by_level_and_limit(kit) -> None:
    out = sel.select_scenarios(kit, "intermediate", "s1", limit=2)
    assert set(out) == {"topic.scenario", "learning.scenario"}
    assert out["topic.scenario"].startswith("B") and out["learning.scenario"].startswith("C")
    assert sel.select_scenarios(kit, "easy", "s1", limit=2)["topic.scenario"].startswith("A")
    assert list(sel.select_scenarios(kit, "advanced", "s1", limit=1)) == ["topic.scenario"]
    assert sel.select_scenarios(kit, "advanced", "s1", limit=0) == {}
    assert sel.select_scenarios(kit, "advanced", "s1") == sel.select_scenarios(kit, "advanced", "s1")
