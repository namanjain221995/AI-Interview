"""Deterministic intake selections (design contract section 6.5).

Pure functions over the kit, the effective candidate profile and the session id: the level estimate (6.5.1),
Drill A / Drill B passages (6.5.2), the topic card (6.5.3), the language verification question (6.5.4), the
résumé claims laddered in stage 2 (6.5.5) and the scenario questions (6.5.6). Ties are broken by
``int(sha256(session_id + drill), 16) % len(pool)`` so a session always gets the same material.

The level only selects material; it is never a score.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services.intake.kit import IntakeKit, LanguageCheck, ScenarioGroup

LEVELS: tuple[str, ...] = ("easy", "intermediate", "advanced")
READ_ALOUD_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "easy": ("A",),
    "intermediate": ("B", "F"),
    "advanced": ("D", "E"),
}
CARDS_BY_LEVEL: dict[str, tuple[int, ...]] = {
    "easy": (2, 5, 6, 7),
    "intermediate": (1, 2, 3, 5, 6),
    "advanced": (1, 3, 4, 8),
}
CATEGORY_PROBES: dict[str, str] = {
    "technical": "tool_or_framework",
    "impact": "metric_improvement",
    "scale": "metric_improvement",
    "ownership": "project_or_product",
    "tenure": "project_or_product",
    "other": "project_or_product",
    "leadership": "leadership",
}
PREFERRED_FIRST = ("tool_or_framework", "project_or_product")
GOOD_VERIFICATION = frozenset({"strong", "adequate", "correct"})
POOR_VERIFICATION = frozenset({"incorrect", "no_answer", "vague"})
LANGUAGE_CLARIFIER = "Of Python, Java, JavaScript, C# or SQL, which do you know best?"
_AI_ML_TERMS = re.compile(
    r"\b(?:machine learning|deep learning|ml|ai|artificial intelligence|tensorflow|pytorch|keras|scikit|sklearn|"
    r"xgboost|lightgbm|llms?|nlp|computer vision|hugging ?face|transformers|langchain|neural networks?|"
    r"data science|mlops|generative ai|rag)\b",
    re.IGNORECASE,
)


def tie_break(session_id: str, drill: str, pool: Sequence[str]) -> str:
    if not pool:
        raise ValueError("empty pool")
    digest = hashlib.sha256(f"{session_id}{drill}".encode()).hexdigest()
    return pool[int(digest, 16) % len(pool)]


# ====================================================================================== level (6.5.1)


def _profile_texts(profile: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    skills = profile.get("skills")
    if isinstance(skills, Mapping):
        for group, items in skills.items():
            texts.append(str(group))
            for item in items or []:
                if isinstance(item, Mapping) and item.get("name"):
                    texts.append(str(item["name"]))
                elif isinstance(item, str):
                    texts.append(item)
    elif isinstance(skills, list):
        texts.extend(str(s.get("name") if isinstance(s, Mapping) else s) for s in skills)
    for key in ("roles", "projects"):
        for entry in profile.get(key) or []:
            if isinstance(entry, Mapping):
                texts.extend(str(t) for t in entry.get("technologies") or [])
                texts.append(str(entry.get("title") or entry.get("name") or ""))
                texts.append(str(entry.get("description") or ""))
    for key in ("headline", "summary"):
        if profile.get(key):
            texts.append(str(profile[key]))
    texts.extend(str(t) for t in profile.get("tools") or [])
    texts.extend(str(t) for t in profile.get("languages") or [])
    return [t for t in texts if t]


def profile_mentions_ai_ml(profile: Mapping[str, Any]) -> bool:
    skills = profile.get("skills")
    if isinstance(skills, Mapping) and skills.get("ai_ml"):
        return True
    return any(_AI_ML_TERMS.search(text) for text in _profile_texts(profile))


def profile_mentions(profile: Mapping[str, Any], term: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    return any(pattern.search(text) for text in _profile_texts(profile))


def thin_resume(profile: Mapping[str, Any]) -> bool:
    """No role with technologies and no project: little to ask about (6.5.5 step 4)."""
    roles = [r for r in profile.get("roles") or [] if isinstance(r, Mapping)]
    projects = [p for p in profile.get("projects") or [] if isinstance(p, Mapping)]
    has_role_tech = any(r.get("technologies") for r in roles)
    return not has_role_tech and not projects


def experience_years(profile: Mapping[str, Any], fallback: float | None = None) -> float | None:
    for key in ("experience_years", "relevant_experience_years"):
        value = profile.get(key)
        if isinstance(value, int | float):
            return float(value)
    return fallback


def level_estimate(
    *,
    self_rating: int | None,
    verification_quality: str | None,
    resume_ai_ml: bool,
    years: float | None,
    thin: bool,
) -> str:
    """``advanced`` when self-rating >= 7, the verification answer was strong/adequate and (AI/ML on the résumé
    or >= 4 years); ``easy`` when the verification answer was incorrect/missing/vague, self-rating <= 4 or a
    thin résumé; else ``intermediate``. Without a quick grade only self-rating and résumé are used."""
    good = verification_quality in GOOD_VERIFICATION
    poor = verification_quality in POOR_VERIFICATION
    rating = self_rating if self_rating is not None else 0
    if poor or (self_rating is not None and self_rating <= 4) or thin:
        return "easy"
    experienced = resume_ai_ml or (years is not None and years >= 4)
    if rating >= 7 and experienced and (good or verification_quality is None):
        return "advanced"
    return "intermediate"


# ================================================================================ passages (6.5.2)


def _next_in_pool(pool: Sequence[str], start: str, avoid: str) -> str:
    if start not in pool:
        return next(k for k in pool if k != avoid)
    index = pool.index(start)
    for offset in range(1, len(pool) + 1):
        candidate = pool[(index + offset) % len(pool)]
        if candidate != avoid:
            return candidate
    raise ValueError("no passage available")


def select_passages(
    kit: IntakeKit, level: str, session_id: str, *, drill_a: str = "auto", drill_b: str = "auto"
) -> tuple[str, str]:
    """(Drill A passage, Drill B passage); overrides win, Drill B never equals Drill A."""
    pool_a = [p.key for p in kit.passages_for("read_aloud")]
    pool_b = [p.key for p in kit.passages_for("relay")]
    level = level if level in LEVELS else "intermediate"
    a: str
    if drill_a and drill_a != "auto" and drill_a in pool_a:
        a = drill_a
    else:
        candidates = [k for k in READ_ALOUD_BY_LEVEL[level] if k in pool_a] or pool_a
        a = tie_break(session_id, "drill_a", candidates)
    if drill_b and drill_b != "auto" and drill_b in pool_b:
        b = drill_b if drill_b != a else _next_in_pool(pool_b, drill_b, a)
        return a, b
    if level == "easy":
        candidates = [k for k in ("A", "F") if k != a and k in pool_b]
        b = tie_break(session_id, "drill_b", candidates) if candidates else _next_in_pool(pool_b, a, a)
    elif level == "intermediate":
        b = "C" if a != "C" and "C" in pool_b else "B"
    else:
        b = "E" if a != "E" and "E" in pool_b else "C"
    if b == a or b not in pool_b:
        b = _next_in_pool(pool_b, b, a)
    return a, b


def select_card(level: str, session_id: str, override: str = "auto") -> str:
    if override and override != "auto" and override.isdigit() and 1 <= int(override) <= 8:
        return override
    level = level if level in LEVELS else "intermediate"
    return tie_break(session_id, "card", [str(n) for n in CARDS_BY_LEVEL[level]])


# ================================================================================ language (6.5.4)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w#+.]){re.escape(alias.lower())}(?![\w#+])")


def resolve_language(kit: IntakeKit, *names: str | None) -> LanguageCheck | None:
    """The kit language for a form value ("Python", "C#", "JavaScript / Node", "node.js")."""
    for name in names:
        if not name or not str(name).strip():
            continue
        found = kit.language_check(str(name))
        if found is not None:
            return found
        for token in re.split(r"[/,()]|\band\b|\bor\b", str(name)):
            token = token.strip()
            if token:
                found = kit.language_check(token)
                if found is not None:
                    return found
    return None


def language_in_text(kit: IntakeKit, text: str | None) -> LanguageCheck | None:
    """The first kit language named in an answer (aliases, word boundaries)."""
    if not text:
        return None
    lowered = text.lower()
    best: tuple[int, LanguageCheck] | None = None
    for language in kit.language_verification.languages:
        for alias in (*language.aliases, language.key):
            match = _alias_pattern(alias).search(lowered)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), language)
    return best[1] if best else None


def fallback_language(kit: IntakeKit, profile: Mapping[str, Any]) -> LanguageCheck:
    """SQL when the résumé mentions SQL, else Python (recorded as ``language_fallback``)."""
    key = "sql" if profile_mentions(profile, "sql") or profile_mentions(profile, "postgresql") else "python"
    found = kit.language_check(key)
    assert found is not None
    return found


# ================================================================================== claims (6.5.5)


def _source(entry: Mapping[str, Any]) -> str:
    source = str(entry.get("source") or "resume")
    return "candidate_correction" if source == "candidate_correction" else "resume"


def _removed(entry: Mapping[str, Any]) -> bool:
    return bool(entry.get("removed")) or entry.get("source") == "removed"


def select_claims(
    profile: Mapping[str, Any] | None, *, min_claims: int = 2, max_claims: int = 3
) -> list[dict[str, Any]]:
    """The 2-3 claims laddered in stage 2, from the effective profile (résumé review corrections applied).

    1. ``claims_to_verify`` by priority (removed items dropped), category mapped to a kit claim probe, at most
       one leadership claim; 2. diversity: C1 a tool/framework or project claim, C2 a different probe type;
    3. fewer than ``min_claims``: derived from the newest role with technologies and projects with outcomes
       (candidate-added items after résumé items); 4. nothing usable: one ``thin_or_vague`` claim.
    """
    profile = profile or {}
    max_claims = max(1, min(3, max_claims))
    min_claims = max(1, min(max_claims, min_claims))
    candidates: list[dict[str, Any]] = []
    for raw in profile.get("claims_to_verify") or []:
        if not isinstance(raw, Mapping) or _removed(raw):
            continue
        text = " ".join(str(raw.get("claim") or "").split())
        if not text:
            continue
        candidates.append(
            {
                "text": text[:400],
                "probe": CATEGORY_PROBES.get(str(raw.get("category") or "technical"), "project_or_product"),
                "source": _source(raw),
                "source_quote": (str(raw.get("source_quote"))[:400] if raw.get("source_quote") else None),
                "profile_claim_id": str(raw.get("id")) if raw.get("id") is not None else None,
                "evidence_topics": [str(t)[:80] for t in (raw.get("evidence_topics") or [])][:6],
                "_priority": int(raw.get("priority") or 3),
            }
        )
    candidates.sort(key=lambda c: (c["source"] != "resume", -c["_priority"], c["profile_claim_id"] or ""))
    picked: list[dict[str, Any]] = []
    leadership_used = False

    def take(predicate: Any) -> bool:
        nonlocal leadership_used
        for candidate in candidates:
            if candidate in picked:
                continue
            if candidate["probe"] == "leadership" and leadership_used:
                continue
            if predicate(candidate):
                picked.append(candidate)
                leadership_used = leadership_used or candidate["probe"] == "leadership"
                return True
        return False

    if candidates and not take(lambda c: c["probe"] in PREFERRED_FIRST):
        take(lambda c: True)
    if len(picked) < max_claims and picked:
        used = {picked[0]["probe"]}
        if not take(lambda c: c["probe"] not in used):
            take(lambda c: True)
    while len(picked) < max_claims and take(lambda c: c["probe"] not in {p["probe"] for p in picked}):
        pass
    while len(picked) < max_claims and take(lambda c: True):
        pass

    if len(picked) < min_claims:
        for derived in _derived_claims(profile):
            if len(picked) >= min_claims:
                break
            if any(p["text"] == derived["text"] for p in picked):
                continue
            picked.append(derived)
    if not picked:
        picked.append(
            {
                "text": "Thin or general résumé: ask about the most technical thing they have done",
                "probe": "thin_or_vague",
                "source": "thin_resume",
                "source_quote": None,
                "profile_claim_id": None,
                "evidence_topics": [],
            }
        )
    out: list[dict[str, Any]] = []
    for index, claim in enumerate(picked[:max_claims], start=1):
        record = {k: v for k, v in claim.items() if not k.startswith("_")}
        record["ref"] = f"C{index}"
        out.append(record)
    return out


def _derived_claims(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Claims built from the corrected document: newest role with technologies, then projects with outcomes;
    candidate-added items rank after résumé items."""
    derived: list[dict[str, Any]] = []
    roles = [r for r in profile.get("roles") or [] if isinstance(r, Mapping) and not _removed(r)]
    roles.sort(key=lambda r: (_source(r) != "resume", not _is_current(r)))
    for role in roles:
        tech = [str(t) for t in role.get("technologies") or [] if str(t).strip()]
        if not tech:
            continue
        company = str(role.get("company") or "").strip()
        listed = ", ".join(tech[:2])
        text = f"You listed {listed} at {company}" if company else f"You listed {listed}"
        derived.append(
            {
                "text": text[:400],
                "probe": "tool_or_framework",
                "source": _source(role),
                "source_quote": str(role.get("title") or "")[:400] or None,
                "profile_claim_id": None,
                "evidence_topics": tech[:4],
            }
        )
        break
    projects = [p for p in profile.get("projects") or [] if isinstance(p, Mapping) and not _removed(p)]
    projects.sort(key=lambda p: (_source(p) != "resume", not p.get("outcomes")))
    for project in projects:
        name = str(project.get("name") or "").strip()
        if not name:
            continue
        outcomes = [str(o) for o in project.get("outcomes") or [] if str(o).strip()]
        text = f"The project {name}" + (f": {outcomes[0]}" if outcomes else "")
        derived.append(
            {
                "text": text[:400],
                "probe": "project_or_product",
                "source": _source(project),
                "source_quote": str(project.get("description") or "")[:400] or None,
                "profile_claim_id": None,
                "evidence_topics": [str(t) for t in project.get("technologies") or []][:4],
            }
        )
    return derived


def _is_current(role: Mapping[str, Any]) -> bool:
    end = str(role.get("end") or "").strip().lower()
    return end in ("", "present", "current", "now")


# =============================================================================== scenarios (6.5.6)


def select_scenarios(
    kit: IntakeKit, level: str, session_id: str, *, limit: int = 2, used: Iterable[str] = ()
) -> dict[str, str]:
    """``{"topic.scenario": key, "learning.scenario": key}`` (at most ``limit``): group B for intermediate or
    advanced candidates (else A) in stage 5, group C in stage 6; the first unused scenario by hash."""
    taken = set(used)
    out: dict[str, str] = {}
    plan = [
        ("topic.scenario", "B" if level in ("intermediate", "advanced") else "A"),
        ("learning.scenario", "C"),
    ]
    groups: dict[str, ScenarioGroup] = {g.key: g for g in kit.scenario_bank.groups}
    for item_key, group_key in plan[: max(0, limit)]:
        group = groups.get(group_key)
        if group is None:
            continue
        pool = [s.key for s in group.scenarios if s.key not in taken]
        if not pool:
            continue
        key = tie_break(session_id, item_key, pool)
        taken.add(key)
        out[item_key] = key
    return out


__all__ = [
    "CARDS_BY_LEVEL",
    "LANGUAGE_CLARIFIER",
    "READ_ALOUD_BY_LEVEL",
    "experience_years",
    "fallback_language",
    "language_in_text",
    "level_estimate",
    "profile_mentions",
    "profile_mentions_ai_ml",
    "resolve_language",
    "select_card",
    "select_claims",
    "select_passages",
    "select_scenarios",
    "thin_resume",
    "tie_break",
]
