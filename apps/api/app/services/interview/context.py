"""Prompt context assembly for turn decisions.

``system_prompt(state)`` depends only on frozen session context (job, round,
profile, claims, plan) and is deterministic, so it is byte-identical across
turns (vLLM prefix caching). ``user_prompt`` renders the dynamic part.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from rapidfuzz import fuzz

from app.core.config import get_settings
from app.domain.enums import ClaimStatus, Difficulty
from app.schemas.ai import InterviewPlan
from app.services.interview.memory import recent_window
from app.services.interview.policy import PolicyDirective, count_questions
from app.services.interview.prompts import (
    ASR_NOTE,
    CODING_USER,
    SPEECH_RULES,
    TURN_FIELDS,
    TURN_FIELDS_COMPACT,
    TURN_SYSTEM,
    TURN_USER,
)
from app.services.interview.state import ExchangeTurn, InterviewState, QuestionRecord

_MAX_DESCRIPTION_CHARS = 1200
#: Bank questions shown as inspiration (the policy keeps more for the deterministic fallback).
BANK_IN_PROMPT = 2
_SYSTEM_CACHE: dict[str, tuple[str, str]] = {}
_SYSTEM_CACHE_MAX = 256


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _covered(text: str, covered: Sequence[str]) -> bool:
    return any(fuzz.token_set_ratio(text, c) >= 80 for c in covered)


def compact_profile(profile: dict[str, Any], *, covered: Sequence[str] = (), brief: bool = False) -> str:
    """Interview-relevant, compact rendering of a CandidateProfile document.

    ``brief`` (the turn prompt, sent every turn): achievements already listed as claims (``covered``) are
    skipped, and the profile's derived judgements (job-relevant strengths, recommended topics, a second gap
    list) stay out - the claims and the plan carry what the interviewer needs. The planner gets the full
    rendering."""
    if not profile:
        return "No resume profile is available; rely on the conversation."
    lines: list[str] = []
    if profile.get("headline"):
        lines.append(f"Headline: {_clip(profile['headline'], 200)}")
    exp = profile.get("experience_years")
    rel = profile.get("relevant_experience_years")
    if exp is not None or rel is not None:
        parts = []
        if exp is not None:
            parts.append(f"{exp:g} years total" if isinstance(exp, int | float) else f"{exp} years total")
        if rel is not None:
            parts.append(f"{rel:g} relevant" if isinstance(rel, int | float) else f"{rel} relevant")
        lines.append("Experience: " + ", ".join(parts))
    roles = profile.get("roles") or []
    if roles:
        lines.append("Roles:")
        for role in roles[:5]:
            if not isinstance(role, dict):
                continue
            dates = "-".join(x for x in (str(role.get("start") or ""), str(role.get("end") or "")) if x)
            tech = ", ".join(str(t) for t in (role.get("technologies") or [])[:8])
            entry = f"- {_clip(role.get('title'), 80)} at {_clip(role.get('company'), 80)}"
            if dates:
                entry += f" ({dates})"
            if tech:
                entry += f"; tech: {tech}"
            if role.get("leadership_scope"):
                entry += f"; scope: {_clip(role['leadership_scope'], 100)}"
            lines.append(entry)
            for ach in (role.get("achievements") or [])[:2]:
                if (
                    isinstance(ach, dict)
                    and ach.get("text")
                    and not (brief and _covered(ach["text"], covered))
                ):
                    lines.append(f"  * {_clip(ach['text'], 180)}")
    skills = profile.get("skills") or {}
    if isinstance(skills, dict) and skills:
        names: list[str] = []
        for items in skills.values():
            for item in items or []:
                name = item.get("name") if isinstance(item, dict) else None
                if name and name not in names:
                    names.append(str(name))
        if names:
            lines.append("Skills: " + ", ".join(names[: 16 if brief else 24]))
    projects = profile.get("projects") or []
    if projects:
        lines.append(
            "Projects: "
            + "; ".join(
                _clip(p.get("name"), 60)
                + (
                    f" ({', '.join(str(t) for t in (p.get('technologies') or [])[:4])})"
                    if p.get("technologies")
                    else ""
                )
                for p in projects[:4]
                if isinstance(p, dict) and p.get("name")
            )
        )
    certs = [
        c.get("name") for c in (profile.get("certifications") or []) if isinstance(c, dict) and c.get("name")
    ]
    if certs:
        lines.append("Certifications: " + ", ".join(_clip(c, 60) for c in certs[:5]))
    match = profile.get("job_match") or {}
    if isinstance(match, dict):
        if match.get("strengths") and not brief:
            lines.append(
                "Job-relevant strengths per resume: "
                + "; ".join(_clip(s, 80) for s in match["strengths"][:5])
            )
        if match.get("gaps"):
            lines.append("Possible gaps to explore: " + "; ".join(_clip(s, 80) for s in match["gaps"][:5]))
    for key, label in (
        ("areas_requiring_verification", "Areas requiring verification"),
        ("potential_skill_gaps", "Potential skill gaps"),
    ):
        values = profile.get(key) or []
        if brief and key == "potential_skill_gaps" and isinstance(match, dict) and match.get("gaps"):
            continue
        if values:
            lines.append(f"{label}: " + "; ".join(_clip(v, 80) for v in values[: 3 if brief else 5]))
    topics = profile.get("recommended_topics") or []
    if topics and not brief:
        lines.append(
            "Recommended topics: "
            + "; ".join(
                _clip(t.get("topic"), 60) for t in topics[:6] if isinstance(t, dict) and t.get("topic")
            )
        )
    inconsistencies = profile.get("inconsistencies") or []
    if inconsistencies:
        lines.append(
            "Resume inconsistencies (raise neutrally only if relevant): "
            + "; ".join(_clip(i.get("description"), 120) for i in inconsistencies[:3] if isinstance(i, dict))
        )
    return "\n".join(lines) if lines else "No resume details are available."


def plan_text(plan: InterviewPlan | None, *, round_focus: dict[str, Sequence[str]] | None = None) -> str:
    """What the turn model needs from the plan: the opening focus, focus areas the round does not already
    list and the risks. Segments, the claim order and the difficulty strategy are applied by the policy
    layer and stay out of the prompt."""
    if plan is None:
        return "No plan yet: cover the competencies in priority order."
    lines = [f"Opening focus: {_clip(plan.opening_focus, 160)}"]
    known = {k: {f.replace("_", " ").lower() for f in v} for k, v in (round_focus or {}).items()}
    focus = []
    for t in plan.competency_targets:
        extra = [f for f in t.focus_areas[:3] if f.replace("_", " ").lower() not in known.get(t.key, set())]
        if extra:
            focus.append(f"{t.key}: {', '.join(_clip(f, 60) for f in extra)}")
    if focus:
        lines.append("Focus per competency: " + "; ".join(focus))
    if plan.risks_to_probe:
        lines.append("Risks to probe gently: " + "; ".join(_clip(r, 110) for r in plan.risks_to_probe[:3]))
    return "\n".join(lines)


def _requirements(job: dict[str, Any]) -> str:
    reqs = job.get("requirements") or []
    names = []
    for r in reqs[:14]:
        if isinstance(r, dict) and r.get("name"):
            label = str(r["name"])
            if r.get("kind") == "preferred_skill":
                label += " (preferred)"
            names.append(label)
    return ", ".join(names)


_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_id(value: str | None) -> str:
    """Claim ids come from model output over the resume: keep them inert."""
    return _ID_SAFE.sub("", str(value or ""))[:24]


def prompt_claims(state: InterviewState) -> list[tuple[str, Any]]:
    """Claims shown to the turn model: those the round may verify (priority >= the round's minimum)."""
    minimum = state.round_config.resume_validation.min_priority
    return [
        (cid, cs)
        for cid, cs in sorted(state.claims.items(), key=lambda kv: (-kv[1].claim.priority, kv[0]))
        if cs.claim.priority >= minimum
    ]


def claims_lines(state: InterviewState) -> list[str]:
    lines = []
    for cid, cs in prompt_claims(state):
        topics = ", ".join(cs.claim.evidence_topics[:4])
        line = f"- {safe_id(cid)} [{cs.claim.priority}] {_clip(cs.claim.claim, 220)}"
        if topics:
            line += f" -- {topics}"
        lines.append(line)
    return lines


_OPENER_WORDS = re.compile(r"[A-Za-z0-9']+")


def recent_openers(state: InterviewState, *, count: int = 3, words: int = 3) -> str:
    """The first words of the interviewer's recent replies (to vary how the next one begins)."""
    replies = [
        t.text
        for t in state.conversation
        if t.speaker == "interviewer" and t.text and not t.interrupted and "?" in t.text
    ][-count:]
    if len(replies) < 2:
        return ""
    openers = [" ".join(_OPENER_WORDS.findall(r)[:words]) for r in replies]
    text = ", ".join(f'"{o}..."' for o in openers if o)
    heads = [" ".join(_OPENER_WORDS.findall(r)[:2]).lower() for r in replies]
    repeated = next((h for h in heads if h and heads.count(h) >= 2), None)
    if repeated:
        text += f' (do not start with "{repeated}" again)'
    return text


#: Stock question frames a human interviewer would not reuse turn after turn (label, pattern).
STOCK_FRAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("walk me through", re.compile(r"\bwalk (?:me|us) through\b", re.I)),
    ("can you describe", re.compile(r"\b(?:can|could) you (?:describe|explain)\b", re.I)),
    ("can you share", re.compile(r"\b(?:can|could) you (?:share|tell me|give me)\b", re.I)),
    ("what was your specific role", re.compile(r"\byour (?:specific|personal|own) (?:role|part|contribution)\b", re.I)),
    ("you mentioned", re.compile(r"\byou(?:'ve)? (?:mentioned|said|described|noted)\b", re.I)),
    ("how did you handle", re.compile(r"\bhow did you (?:handle|approach|manage|deal with)\b", re.I)),
    ("switching gears", re.compile(r"\b(?:switching|shifting|changing) gears\b", re.I)),
)  # fmt: skip


def overused_frames(state: InterviewState, *, count: int = 3) -> str:
    """Stock frames used in the interviewer's last ``count`` asking replies (to vary the next question's
    shape); "you mentioned" when it was used in either of the last two (an occasional reference is natural,
    the same preamble turn after turn is not - the attribution guard also rewrites a repeat)."""
    replies = [
        t.text
        for t in state.conversation
        if t.speaker == "interviewer" and t.text and not t.interrupted and "?" in t.text
    ][-count:]
    if not replies:
        return ""
    listed = []
    for label, pattern in STOCK_FRAMES:
        window = replies[-2:] if label == "you mentioned" else replies
        if any(pattern.search(r) for r in window):
            listed.append(f'"{label}"')
    return ", ".join(listed)


def static_variables(state: InterviewState) -> dict[str, Any]:
    rc = state.round_config
    job = state.job_summary or {}
    competencies = []
    for c in rc.competencies:
        comp = state.competencies.get(c.key)
        competencies.append(
            {
                "key": c.key,
                "name": (comp.name if comp and comp.name else c.name) or c.key.replace("_", " "),
                "weight": f"{c.weight:g}",
                "required": c.required,
                "focus": ", ".join(c.focus_areas[:6]),
            }
        )
    claims = claims_lines(state)
    return {
        "interviewer_name": state.interviewer_name,
        "round_name": rc.name,
        "round_type": rc.round_type.value,
        "duration_minutes": rc.duration_minutes,
        "job_title": job.get("title") or "this",
        "company": job.get("company"),
        "seniority": job.get("seniority"),
        "department": job.get("department"),
        "location": job.get("location"),
        "employment_type": (job.get("employment_type") or "").replace("_", " ") or None,
        "requirements": _requirements(job),
        "job_description": _clip(re.sub(r"#+\s*", "", job.get("description") or ""), _MAX_DESCRIPTION_CHARS),
        "interview_instructions": _clip(job.get("interview_instructions"), 400),
        "styles": ", ".join(rc.question_strategy.style),
        "competencies": competencies,
        "interviewer_notes": _clip(rc.interviewer_notes, 900),
        "first_name": state.candidate_first_name,
        "profile": compact_profile(
            state.candidate_profile,
            covered=[
                t for _, cs in prompt_claims(state) for t in (cs.claim.claim, cs.claim.source_quote or "")
            ],
            brief=True,
        ),
        "claims_text": "\n".join(claims),
        "plan": plan_text(state.plan, round_focus={c.key: c.focus_areas for c in rc.competencies}),
        "speech_rules": SPEECH_RULES,
        "asr_note": ASR_NOTE,
        "schema": TURN_FIELDS_COMPACT if get_settings().turn_decision_compact else TURN_FIELDS,
    }


def system_prompt(state: InterviewState) -> str:
    """Per-session static system prompt (cached by content fingerprint)."""
    fingerprint_src = "|".join(
        [
            state.session_id,
            state.plan.model_dump_json() if state.plan else "-",
            str(len(state.claims)),
            state.candidate_name or "",
            TURN_SYSTEM.version,
            str(get_settings().turn_decision_compact),
        ]
    )
    fingerprint = hashlib.sha1(fingerprint_src.encode("utf-8")).hexdigest()  # noqa: S324 - cache key only
    cached = _SYSTEM_CACHE.get(state.session_id)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    text = TURN_SYSTEM.render(**static_variables(state))
    if len(_SYSTEM_CACHE) >= _SYSTEM_CACHE_MAX:
        _SYSTEM_CACHE.pop(next(iter(_SYSTEM_CACHE)))
    _SYSTEM_CACHE[state.session_id] = (fingerprint, text)
    return text


def competency_rows(state: InterviewState) -> list[str]:
    """One compact line per competency (the score estimate stays out: the policy adapts difficulty)."""
    rows = []
    for c in state.round_config.competencies:
        comp = state.competencies.get(c.key)
        if comp is None:
            continue
        row = (
            f"{c.key}: asked {comp.questions_asked}, evidence {comp.evidence_count} of "
            f"{state.round_config.min_evidence_for(c.key)}, confidence {comp.confidence:.2f}, "
            f"level {comp.current_difficulty.value}{', SUFFICIENT' if comp.sufficient_evidence else ''}"
        )
        if comp.unanswered_questions:
            row += f"; not yet heard: {', '.join(_clip(u, 60) for u in comp.unanswered_questions[:3])}"
        rows.append(row)
    return rows


def claims_status(state: InterviewState) -> str:
    if not state.claims:
        return ""
    parts = []
    for cid, cs in sorted(state.claims.items()):
        if cs.status == ClaimStatus.UNVERIFIED and cs.attempts == 0:
            continue
        parts.append(f"{safe_id(cid)} {cs.status.value}" + (f" ({_clip(cs.note, 110)})" if cs.note else ""))
    unverified = sum(
        1 for cs in state.claims.values() if cs.status == ClaimStatus.UNVERIFIED and cs.attempts == 0
    )
    if unverified:
        parts.append(f"{unverified} not yet discussed")
    return "; ".join(parts)


def conversation_view(
    turns: Sequence[ExchangeTurn],
    *,
    limit_chars: int = 900,
    older_limit_chars: int = 260,
    interviewer_limit_chars: int = 160,
    recent: int = 2,
) -> list[dict[str, Any]]:
    """Recent conversation for the prompt: the last ``recent`` entries keep up
    to ``limit_chars``; older candidate turns keep ``older_limit_chars`` and
    older interviewer turns (the model's own questions) less, so the prompt
    stays compact while what the candidate said stays visible."""
    view = []
    count = len(turns)
    for i, t in enumerate(turns):
        candidate = t.speaker == "candidate"
        if i >= count - recent:
            limit = limit_chars
        else:
            limit = older_limit_chars if candidate else interviewer_limit_chars
        text = _clip(t.text, limit)
        if t.interrupted and not candidate:
            text += " [interrupted]"
        view.append({"speaker": "Candidate" if candidate else "You", "text": text, "candidate": candidate})
    return view


def without_introduction(state: InterviewState, turns: Sequence[ExchangeTurn]) -> list[ExchangeTurn]:
    """The spoken introduction (interviewer text before the first question) is not repeated in every prompt."""
    intro: set[int] = set()
    for t in state.conversation:
        if t.speaker != "interviewer" or t.question_id is not None:
            break
        intro.add(id(t))
    return [t for t in turns if id(t) not in intro]


def difficulty_mix(state: InterviewState) -> tuple[str, str]:
    counts = state.difficulty_counts
    dist = state.round_config.difficulty_distribution
    mix = ", ".join(f"{d.value} {counts.get(d.value, 0)}" for d in Difficulty)
    target = f"{dist.easy:.0%}/{dist.medium:.0%}/{dist.hard:.0%}"
    return mix, target


def directive_view(directive: PolicyDirective) -> dict[str, Any]:
    target = directive.target()
    claim = directive.claim_to_verify
    contradiction = directive.open_contradiction
    return {
        "recommended": directive.recommended_action.value
        + (
            " (or "
            + " / ".join(a.value for a in directive.alternatives)
            + ", whichever the answer calls for)"
            if directive.alternatives
            else ""
        ),
        "required": directive.required_action is not None,
        "allowed": ", ".join(a.value for a in directive.allowed_actions),
        "target": f"{target.key} ({target.name})" if target else directive.target_competency,
        "difficulty": directive.target_difficulty.value,
        "topics": ", ".join(target.uncovered_topics) if target and target.uncovered_topics else "",
        "quality": (
            f"{directive.answer_quality.value} (from {directive.quality_source})"
            if directive.answer_quality
            else ""
        ),
        "claim_id": safe_id(claim.id) if claim else "",
        "claim": (
            claim.claim
            + (f" -- evidence topics: {', '.join(claim.evidence_topics)}" if claim.evidence_topics else "")
            if claim
            else ""
        ),
        "contradiction": contradiction.description if contradiction else "",
        "bank": [
            {"id": b.id, "difficulty": b.difficulty.value, "text": _clip(b.text, 220)}
            for b in directive.candidate_bank_questions[:BANK_IN_PROMPT]
        ],
        "instructions": directive.instructions,
    }


def thread_evidence(state: InterviewState, *, last_answer: str | None = None, limit: int = 3) -> list[str]:
    """What the current question thread already holds (Tier 1, "thread memory"): what a complete answer to the
    current question covers, and what the candidate already said earlier in this thread (the current question and
    the questions it follows up). The last answer is shown separately as LAST ANSWER. Fenced as derived data by
    the template; it lets the interviewer ask for what is missing instead of asking again for what was given."""
    current = state.current_question()
    if current is None:
        return []
    out: list[str] = []
    if current.expected_evidence:
        out.append(
            "A complete answer to your current question covers: " + "; ".join(current.expected_evidence[:5])
        )
    by_id = {q.id: q for q in state.questions}
    thread: list[str] = []
    node: QuestionRecord | None = current
    while node is not None and node.id not in thread:
        thread.append(node.id)
        node = by_id.get(node.parent_id) if node.parent_id else None
    last = (last_answer or "").strip()
    earlier = [a for a in state.answers if a.question_id in thread and a.text.strip() and a.text.strip() != last]
    for answer in earlier[-limit:]:
        asked = by_id.get(answer.question_id or "")
        topic = (asked.focus or asked.topic or "") if asked is not None else ""
        about = f" about {_clip(topic, 60)}" if topic else ""
        out.append(f'Already said earlier in this thread{about}: "{_clip(answer.text, 200)}"')
    return out


def user_prompt(
    state: InterviewState,
    directive: PolicyDirective,
    *,
    last_answer: str | None,
    current_question: str | None = None,
    interruption: str | None = None,
    evidence: Sequence[str] = (),
    trimmed: bool = False,
) -> str:
    mix, target = difficulty_mix(state)
    window = without_introduction(state, recent_window(state, exchanges=2 if trimmed else None))
    # The last candidate turn is rendered separately as LAST ANSWER, with the question it answered.
    if last_answer is not None and window and window[-1].speaker == "candidate":
        window = window[:-1]
        if (
            current_question
            and window
            and window[-1].speaker == "interviewer"
            and window[-1].text == current_question
        ):
            window = window[:-1]
    # Questions older than the recent window (the window already shows the recent ones verbatim).
    in_window = {t.question_id for t in window if t.speaker == "interviewer" and t.question_id}
    if last_answer is not None and state.current_question_id:
        in_window.add(state.current_question_id)  # shown with LAST ANSWER
    asked = (
        []
        if trimmed
        else [
            q.focus or q.topic or q.text
            for q in state.questions[-12:]
            if q.action.value not in ("clarify_question",) and q.id not in in_window
        ]
    )
    return TURN_USER.render(
        elapsed=mmss(directive.time.elapsed_s),
        limit=mmss(directive.time.time_limit_s),
        remaining=mmss(directive.time.remaining_s),
        segment=directive.time.segment_key,
        questions_asked=count_questions(state),
        difficulty_mix=mix,
        difficulty_target=target,
        competency_rows=competency_rows(state),
        claims_status=claims_status(state),
        memory=None if trimmed else (state.memory_summary or None),
        evidence="" if trimmed else "\n".join(f"- {e}" for e in list(evidence)[:5]),
        asked="; ".join(_clip(a, 80) for a in asked) if asked else "",
        conversation=conversation_view(window),
        last_answer=last_answer,
        current_question=_clip(current_question, 300) if current_question else None,
        interruption=interruption,
        directive=directive_view(directive),
        recent_openers="" if trimmed else recent_openers(state),
        overused_frames="" if trimmed else overused_frames(state),
    )


def coding_user_prompt(
    state: InterviewState,
    *,
    last_answer: str,
    code: str,
    title: str,
    difficulty: str,
    language: str,
    elapsed_s: float,
    limit_s: float,
    last_run: str | None,
    recommended: str,
    allowed: str,
) -> str:
    window = recent_window(state, exchanges=3)
    if window and window[-1].speaker == "candidate":
        window = window[:-1]
    return CODING_USER.render(
        title=title,
        difficulty=difficulty,
        language=language,
        elapsed=mmss(elapsed_s),
        limit=mmss(limit_s),
        hints_used=state.coding.hints_used,
        last_run=last_run,
        code=code[-6000:] if code else "(empty)",
        conversation=conversation_view(window),
        last_answer=last_answer,
        recommended=recommended,
        allowed=allowed,
    )


def turn_messages(system: str, user: str) -> list[dict[str, Any]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "thread_evidence",
    "claims_lines",
    "claims_status",
    "coding_user_prompt",
    "compact_profile",
    "competency_rows",
    "conversation_view",
    "directive_view",
    "mmss",
    "overused_frames",
    "plan_text",
    "prompt_claims",
    "without_introduction",
    "recent_openers",
    "safe_id",
    "static_variables",
    "system_prompt",
    "turn_messages",
    "user_prompt",
]
