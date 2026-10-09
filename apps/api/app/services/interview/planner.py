"""Interview planning: ``InterviewPlan`` from the main model with a
deterministic fallback built from the ``RoundConfig`` and the candidate
profile. The plan is a time budget and priorities, never a fixed question
list; ``normalize_plan`` makes any model plan consistent with the round
(known competency keys and claim ids, a coding segment exactly when coding is
enabled, budgets that fit the duration)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai.base import AIError, AIServices, CallContext
from app.ai.registry import get_ai
from app.ai.structured import describe_schema
from app.core.logging import get_logger
from app.domain.enums import ClaimStatus, Difficulty, ModelTask, RoundType, SegmentKind
from app.schemas.ai import CompetencyTarget, InterviewPlan, PlanSegment
from app.services.interview.context import compact_profile, safe_id
from app.services.interview.policy import NON_TECHNICAL_KEYS, is_coding_competency
from app.services.interview.prompts import PLAN_SYSTEM, PLAN_USER
from app.services.interview.state import InterviewState

log = get_logger(__name__)

_BUDGET = re.compile(
    r"(?P<phrase>[^,;.()]+?)\s*\((?:about\s+|approx(?:imately)?\.?\s+|~\s*|around\s+)?(?P<minutes>\d+(?:\.\d+)?)\s*min(?:ute)?s?\)",
    re.IGNORECASE,
)
# Ordered: the first matching kind wins for single-kind phrases.
_KIND_KEYWORDS: list[tuple[SegmentKind, re.Pattern[str]]] = [
    (SegmentKind.INTRO, re.compile(r"\b(?:intro|introduction|welcome|rapport)\b", re.I)),
    (
        SegmentKind.CANDIDATE_QUESTIONS,
        re.compile(r"\bcandidate'?s? questions\b|\bquestions from the candidate\b", re.I),
    ),
    (SegmentKind.CODING, re.compile(r"\b(?:coding|code|exercise|programming|algorithm\w*)\b", re.I)),
    (SegmentKind.SYSTEM_DESIGN, re.compile(r"\b(?:system design|design|architecture)\b", re.I)),
    (SegmentKind.RESUME_VALIDATION, re.compile(r"\b(?:resume|claims?|cv)\b", re.I)),
    (
        SegmentKind.BEHAVIORAL,
        re.compile(r"\b(?:behaviou?ral|ownership|collaboration|star|teamwork|conflict)\b", re.I),
    ),
    (SegmentKind.LEADERSHIP, re.compile(r"\b(?:leadership|managerial|management|mentoring)\b", re.I)),
    (
        SegmentKind.PROBLEM_SOLVING,
        re.compile(r"\b(?:problem[- ]solving|puzzle|estimation|debugging)\b", re.I),
    ),
    (
        SegmentKind.RECRUITER,
        re.compile(r"\b(?:motivation|logistics|availability|salary|expectations)\b", re.I),
    ),
    (
        SegmentKind.TECHNICAL,
        re.compile(r"\b(?:technical|scenarios?|deep[- ]dive|conceptual|platform)\b", re.I),
    ),
    (SegmentKind.CLOSING, re.compile(r"\b(?:closing|wrap[- ]up)\b", re.I)),
]

MAIN_KIND: dict[RoundType, SegmentKind] = {
    RoundType.RESUME_SCREENING: SegmentKind.RESUME_VALIDATION,
    RoundType.RECRUITER_SCREENING: SegmentKind.RECRUITER,
    RoundType.TECHNICAL_SCREENING: SegmentKind.TECHNICAL,
    RoundType.DEEP_TECHNICAL: SegmentKind.TECHNICAL,
    RoundType.CODING: SegmentKind.TECHNICAL,
    RoundType.SYSTEM_DESIGN: SegmentKind.SYSTEM_DESIGN,
    RoundType.PROBLEM_SOLVING: SegmentKind.PROBLEM_SOLVING,
    RoundType.BEHAVIORAL: SegmentKind.BEHAVIORAL,
    RoundType.MANAGERIAL: SegmentKind.LEADERSHIP,
    RoundType.LEADERSHIP: SegmentKind.LEADERSHIP,
    RoundType.CUSTOM: SegmentKind.TECHNICAL,
    RoundType.COMBINED: SegmentKind.TECHNICAL,
}
_BEHAVIORAL_KINDS = {SegmentKind.BEHAVIORAL, SegmentKind.LEADERSHIP}
_BEHAVIORAL_KEYS = NON_TECHNICAL_KEYS - {"communication", "motivation", "recruiter_fit"}


def parse_segment_budgets(notes: str | None) -> list[tuple[SegmentKind, float]]:
    """Suggested per-segment budgets from interviewer notes, e.g.
    "brief introduction (1 min), resume claim validation and technical
    questions (about 12 min), a short coding exercise (about 10 min)"."""
    if not notes:
        return []
    out: list[tuple[SegmentKind, float]] = []
    for match in _BUDGET.finditer(notes):
        phrase = match.group("phrase")
        minutes = float(match.group("minutes"))
        kinds = [kind for kind, rx in _KIND_KEYWORDS if rx.search(phrase)]
        if not kinds:
            kinds = [SegmentKind.TECHNICAL]
        if SegmentKind.RESUME_VALIDATION in kinds and SegmentKind.TECHNICAL in kinds:
            third = max(1.0, round(minutes / 3.0, 1))
            out.append((SegmentKind.RESUME_VALIDATION, third))
            out.append((SegmentKind.TECHNICAL, max(1.0, minutes - third)))
            continue
        out.append((kinds[0], minutes))
    return out


def _eligible_claims(state: InterviewState) -> list[str]:
    rv = state.round_config.resume_validation
    if not rv.enabled:
        return []
    items = [
        (cid, c)
        for cid, c in state.claims.items()
        if c.claim.priority >= rv.min_priority and c.status == ClaimStatus.UNVERIFIED
    ]
    items.sort(key=lambda kv: (-kv[1].claim.priority, kv[0]))
    return [cid for cid, _ in items[: max(rv.max_claims, 0)]]


def _comp_name(state: InterviewState, key: str) -> str:
    comp = state.competencies.get(key)
    for c in state.round_config.competencies:
        if c.key == key and c.name:
            return c.name
    return (comp.name if comp and comp.name else None) or key.replace("_", " ")


def _segment_competencies(state: InterviewState, kind: SegmentKind) -> list[str]:
    keys = state.round_config.competency_keys()
    comps = state.competencies
    if kind == SegmentKind.CODING:
        selected = [k for k in keys if is_coding_competency(k, comps.get(k))]
    elif kind in _BEHAVIORAL_KINDS:
        selected = [
            k
            for k in keys
            if k in _BEHAVIORAL_KEYS or (comps.get(k) and comps[k].category in ("behavioral", "leadership"))
        ]
    elif kind in (SegmentKind.INTRO, SegmentKind.CANDIDATE_QUESTIONS, SegmentKind.CLOSING):
        selected = []
    elif kind == SegmentKind.RECRUITER:
        selected = list(keys)
    else:
        selected = [
            k
            for k in keys
            if not is_coding_competency(k, comps.get(k))
            and k not in _BEHAVIORAL_KEYS
            and not (comps.get(k) and comps[k].category in ("behavioral", "leadership"))
            and k != "communication"
        ]
        if kind == SegmentKind.RESUME_VALIDATION:
            claim_keys = {ck for c in state.claims.values() for ck in c.claim.competency_keys}
            preferred = [k for k in selected if k in claim_keys]
            selected = preferred or selected
    return selected[:10]


def _target_difficulty(state: InterviewState, key: str) -> Difficulty:
    for c in state.round_config.competencies:
        if c.key == key and c.target_difficulty is not None:
            return c.target_difficulty
    return state.round_config.expected_difficulty


def fallback_plan(state: InterviewState) -> InterviewPlan:
    rc = state.round_config
    duration = float(rc.duration_minutes)
    main_kind = MAIN_KIND.get(rc.round_type, SegmentKind.TECHNICAL)
    claims = _eligible_claims(state)
    suggested = parse_segment_budgets(rc.interviewer_notes)
    segments_spec: list[tuple[SegmentKind, float]] = []
    if suggested:
        segments_spec = [(k, m) for k, m in suggested if k != SegmentKind.CODING or rc.coding.enabled]
        if rc.coding.enabled and not any(k == SegmentKind.CODING for k, _ in segments_spec):
            segments_spec.append((SegmentKind.CODING, float(rc.coding.duration_minutes)))
        if not any(k == SegmentKind.INTRO for k, _ in segments_spec):
            segments_spec.insert(0, (SegmentKind.INTRO, 1.0))
    else:
        segments_spec.append((SegmentKind.INTRO, 1.0))
        fixed = 1.0
        coding_minutes = float(rc.coding.duration_minutes) if rc.coding.enabled else 0.0
        fixed += coding_minutes
        candidate_q = 2.0 if rc.question_strategy.allow_candidate_questions_at_end and duration >= 15 else 0.0
        fixed += candidate_q + 1.0
        behavioral = 0.0
        if rc.round_type == RoundType.COMBINED and _segment_competencies(state, SegmentKind.BEHAVIORAL):
            behavioral = min(6.0, max(3.0, round(duration * 0.15)))
            fixed += behavioral
        main = max(2.0, duration - fixed)
        if claims and main_kind != SegmentKind.RESUME_VALIDATION and rc.resume_validation.enabled:
            rv_minutes = max(2.0, round(main * 0.3, 1))
            segments_spec.append((SegmentKind.RESUME_VALIDATION, rv_minutes))
            main = max(2.0, main - rv_minutes)
        segments_spec.append((main_kind, main))
        if coding_minutes:
            segments_spec.append((SegmentKind.CODING, coding_minutes))
        if behavioral:
            segments_spec.append((SegmentKind.BEHAVIORAL, behavioral))
    if rc.question_strategy.allow_candidate_questions_at_end and not any(
        k == SegmentKind.CANDIDATE_QUESTIONS for k, _ in segments_spec
    ):
        segments_spec.append((SegmentKind.CANDIDATE_QUESTIONS, 2.0 if duration >= 15 else 1.0))
    if not any(k == SegmentKind.CLOSING for k, _ in segments_spec):
        segments_spec.append((SegmentKind.CLOSING, 1.0))

    segments: list[PlanSegment] = []
    used: dict[str, int] = {}
    for kind, minutes in segments_spec[:10]:
        base = kind.value
        used[base] = used.get(base, 0) + 1
        key = base if used[base] == 1 else f"{base}_{used[base]}"
        goals = {
            SegmentKind.INTRO: ["Introduce the round and set expectations"],
            SegmentKind.RESUME_VALIDATION: ["Verify the highest-priority resume claims with specifics"],
            SegmentKind.CODING: ["Short coding exercise, then discuss complexity and edge cases"],
            SegmentKind.CANDIDATE_QUESTIONS: ["Answer the candidate's questions about the role and process"],
            SegmentKind.CLOSING: ["Thank the candidate and explain next steps"],
        }.get(kind, ["Gather evidence on the segment's competencies"])
        segments.append(
            PlanSegment(
                key=key[:40],
                kind=kind,
                competency_keys=_segment_competencies(state, kind),
                time_budget_minutes=max(0.5, min(120.0, round(minutes, 1))),
                goals=goals,
            )
        )

    max_weight = max((c.weight for c in rc.competencies), default=1.0) or 1.0
    recommended = state.candidate_profile.get("recommended_topics") or []
    targets: list[CompetencyTarget] = []
    for c in rc.competencies:
        priority = 1 + round(4 * (c.weight / max_weight))
        if not c.required:
            priority = min(priority, 3)
        focus = list(c.focus_areas)
        for topic in recommended:
            if isinstance(topic, dict) and topic.get("competency_key") == c.key and topic.get("topic"):
                if topic["topic"] not in focus:
                    focus.append(str(topic["topic"])[:80])
        targets.append(
            CompetencyTarget(
                key=c.key,
                priority=max(1, min(5, priority)),
                starting_difficulty=_target_difficulty(state, c.key),
                focus_areas=[f.replace("_", " ") for f in focus][:8],
                rationale=("Required" if c.required else "Optional") + f" competency, weight {c.weight:g}",
            )
        )
    if claims:
        top = state.claims[claims[0]].claim
        opening_focus = f"Verify the resume claim: {top.claim}"[:300]
    elif recommended and isinstance(recommended[0], dict) and recommended[0].get("topic"):
        opening_focus = f"The candidate's recent experience with {recommended[0]['topic']}"[:300]
    elif rc.competencies:
        top_key = max(rc.competencies, key=lambda c: (c.required, c.weight)).key
        opening_focus = f"The candidate's most relevant recent experience with {_comp_name(state, top_key)}"[
            :300
        ]
    else:
        opening_focus = "The candidate's most relevant recent work"
    risks: list[str] = []
    for item in state.candidate_profile.get("inconsistencies") or []:
        if isinstance(item, dict) and item.get("description"):
            risks.append(str(item["description"])[:200])
    for gap in state.candidate_profile.get("potential_skill_gaps") or []:
        risks.append(f"Possible gap: {gap}"[:200])
    coding_plan = None
    if rc.coding.enabled:
        start = 0.0
        for seg in segments:
            if seg.kind == SegmentKind.CODING:
                break
            start += seg.time_budget_minutes
        coding_plan = (
            f"Start the {rc.coding.duration_minutes}-minute {rc.coding.difficulty.value} coding exercise around minute "
            f"{start:g}; afterwards ask about complexity and edge cases."
        )
    dist = rc.difficulty_distribution
    initial = rc.expected_difficulty
    return InterviewPlan(
        opening_focus=opening_focus,
        segments=segments,
        competency_targets=targets[:20],
        claims_priority=claims[:15],
        initial_difficulty=initial,
        difficulty_strategy=(
            f"Start at {initial.value}; step up one level after strong answers and down after two weak answers; "
            f"balance toward {dist.easy:.0%} easy / {dist.medium:.0%} medium / {dist.hard:.0%} hard."
        ),
        coding_plan=coding_plan,
        risks_to_probe=risks[:8],
    )


def normalize_plan(plan: InterviewPlan, state: InterviewState) -> InterviewPlan:
    rc = state.round_config
    keys = set(rc.competency_keys())
    fallback = fallback_plan(state)
    targets: list[CompetencyTarget] = []
    seen: set[str] = set()
    for t in plan.competency_targets:
        if t.key in keys and t.key not in seen:
            targets.append(t)
            seen.add(t.key)
    for t in fallback.competency_targets:
        if t.key not in seen:
            targets.append(t)
            seen.add(t.key)
    eligible = _eligible_claims(state)
    claims = [cid for cid in plan.claims_priority if cid in state.claims]
    claims += [cid for cid in eligible if cid not in claims]
    segments: list[PlanSegment] = []
    for seg in plan.segments:
        if seg.kind == SegmentKind.CODING and not rc.coding.enabled:
            continue
        seg = seg.model_copy(update={"competency_keys": [k for k in seg.competency_keys if k in keys]})
        segments.append(seg)
    if rc.coding.enabled and not any(s.kind == SegmentKind.CODING for s in segments):
        coding_seg = next(s for s in fallback.segments if s.kind == SegmentKind.CODING)
        insert_at = next(
            (
                i
                for i, s in enumerate(segments)
                if s.kind in (SegmentKind.CANDIDATE_QUESTIONS, SegmentKind.CLOSING)
            ),
            len(segments),
        )
        segments.insert(insert_at, coding_seg)
    if not segments:
        segments = list(fallback.segments)
    total = sum(s.time_budget_minutes for s in segments)
    if total > 0 and abs(total - rc.duration_minutes) / rc.duration_minutes > 0.25:
        scale = rc.duration_minutes / total
        segments = [
            s.model_copy(update={"time_budget_minutes": max(0.5, round(s.time_budget_minutes * scale, 1))})
            for s in segments
        ]
    return plan.model_copy(
        update={
            "competency_targets": targets[:20],
            "claims_priority": claims[:15],
            "segments": segments[:10],
            "coding_plan": plan.coding_plan if rc.coding.enabled else None,
        }
    )


def _plan_variables(state: InterviewState) -> dict[str, Any]:
    rc = state.round_config
    job = state.job_summary or {}
    competencies = [
        {
            "key": c.key,
            "name": _comp_name(state, c.key),
            "weight": f"{c.weight:g}",
            "required": c.required,
            "focus": ", ".join(c.focus_areas),
        }
        for c in rc.competencies
    ]
    requirements = ", ".join(
        str(r.get("name"))
        for r in (job.get("requirements") or [])[:14]
        if isinstance(r, dict) and r.get("name")
    )
    budgets = parse_segment_budgets(rc.interviewer_notes)
    claims = [
        f"- {safe_id(cid)} [{c.claim.priority}] {c.claim.claim[:240]}"
        for cid, c in sorted(state.claims.items(), key=lambda kv: (-kv[1].claim.priority, kv[0]))
    ]
    return {
        "job_title": job.get("title") or "the role",
        "seniority": job.get("seniority"),
        "requirements": requirements,
        "round_name": rc.name,
        "round_type": rc.round_type.value,
        "duration_minutes": rc.duration_minutes,
        "expected_difficulty": rc.expected_difficulty.value,
        "competencies": competencies,
        "coding_enabled": rc.coding.enabled,
        "coding_minutes": rc.coding.duration_minutes,
        "rv_enabled": rc.resume_validation.enabled,
        "rv_max": rc.resume_validation.max_claims,
        "notes": rc.interviewer_notes,
        "budgets": ", ".join(f"{k.value} {m:g} min" for k, m in budgets),
        "profile": compact_profile(state.candidate_profile),
        "claims_text": "\n".join(claims),
    }


# Guided-decoding wire schema for the plan: only the fields the engine uses, every one required
# (guided JSON skips optional properties) and tightly bounded, so the plan finishes under shared
# load (this vLLM build also pads JSON whitespace). Converted into the InterviewPlan contract.
class _WireSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., max_length=40)
    kind: SegmentKind
    competency_keys: list[str] = Field(..., max_length=6)
    time_budget_minutes: float = Field(..., ge=0.5, le=120)


class _WireTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., max_length=80)
    priority: int = Field(..., ge=1, le=5)
    starting_difficulty: Difficulty
    focus_areas: list[str] = Field(..., max_length=3)


class GuidedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening_focus: str = Field(..., max_length=160)
    segments: list[_WireSegment] = Field(..., min_length=1, max_length=6)
    competency_targets: list[_WireTarget] = Field(..., max_length=10)
    claims_priority: list[str] = Field(..., max_length=6)
    initial_difficulty: Difficulty
    difficulty_strategy: str = Field(..., max_length=120)
    risks_to_probe: list[str] = Field(..., max_length=3)

    def to_plan(self) -> InterviewPlan:
        return InterviewPlan.model_validate(self.model_dump(mode="json"))


PLAN_MAX_TOKENS = 1500


async def generate_plan(
    state: InterviewState, *, ai: AIServices | None = None, timeout_s: float = 30.0
) -> tuple[InterviewPlan, str]:
    """(plan, source) where source is ``model`` or ``fallback``."""
    services = ai or get_ai()
    system = PLAN_SYSTEM.render(
        schema=describe_schema(GuidedPlan),
        segment_kinds=", ".join(k.value for k in SegmentKind),
    )
    user = PLAN_USER.render(**_plan_variables(state))
    try:
        result = await services.llm.structured(
            GuidedPlan,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            context=CallContext(
                task=ModelTask.INTERVIEW_PLANNING,
                session_id=state.session_id,
                organization_id=state.organization_id,
                prompt_version=PLAN_SYSTEM.version,
            ),
            temperature=0.2,
            max_tokens=PLAN_MAX_TOKENS,
            timeout=timeout_s,  # total budget (both attempts)
            max_attempts=2,
        )
        plan = result.value.to_plan()
    except (AIError, ValueError) as exc:
        log.warning(
            "engine.plan_fallback", session_id=state.session_id, error=f"{type(exc).__name__}: {exc}"[:200]
        )
        return fallback_plan(state), "fallback"
    return normalize_plan(plan, state), "model"


__all__ = ["GuidedPlan", "fallback_plan", "generate_plan", "normalize_plan", "parse_segment_budgets"]
