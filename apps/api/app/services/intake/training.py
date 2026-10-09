"""Personalised three-week training plan of an intake evaluation (design contract section 9.2, shape 15.10).

* **Catalog** (platform content in code, not kit text): ten modules, each with a title, default objectives and
  default week 1-3 milestones with a measure.
* **Selection** (deterministic): every competency scored <= 3 or without sufficient evidence maps to modules
  (communication -> communication_practice; technical_reading -> technical_reading_practice, plus
  technical_explanations when Drill B <= 3; structured_answer -> structured_speaking; technical -> the language-check
  language's fundamentals or programming_fundamentals; trainability -> learning_habits; composure ->
  interview_preparation; adaptability -> ai_ml_fundamentals). ``ai_ml_fundamentals`` is always included. At most
  five modules, ordered by ``weight x (5 - score)`` (a competency without sufficient evidence counts as 3, the
  midpoint: unknown, not weak).
* **Personalisation**: one ``REPORT_GENERATION`` call (``prompt_version = intake-training@…``) writes ``why``,
  objectives and measurable milestones per selected module from the (fenced) strengths and improvement notes. The
  fallback is the catalog defaults (``generated_by = deterministic``). The plan never mentions the band, a decision
  or work authorisation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.ai.base import AIError, AIOutputInvalid, CallContext
from app.domain.enums import ModelTask
from app.services.intake.eval_prompts import TRAINING_PROMPT_VERSION, TRAINING_SYSTEM, TRAINING_USER
from app.services.intake.eval_schemas import TrainingPlanDraft, describe_nested
from app.services.intake.evidence import assert_prompt_safe
from app.services.intake.scoring import INTAKE_LABELS, INTAKE_WEIGHTS
from app.services.reports.wording import neutral

MAX_MODULES = 5
INSUFFICIENT_AS = Decimal(3)
SELECT_AT_OR_BELOW = Decimal(3)
TRAINING_TEMPERATURE = 0.2
TRAINING_MAX_TOKENS = 1800
TRAINING_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class TrainingModuleDef:
    key: str
    title: str
    objectives: tuple[str, ...]
    milestones: tuple[tuple[int, str, str], ...]


CATALOG: dict[str, TrainingModuleDef] = {
    m.key: m
    for m in (
        TrainingModuleDef(
            "programming_fundamentals",
            "Programming fundamentals",
            (
                "Write small programs with functions, loops and core data structures without help",
                "Read and debug unfamiliar code methodically",
            ),
            (
                (
                    1,
                    "Complete ten short exercises on functions, loops and collections",
                    "exercises pass their tests",
                ),
                (
                    2,
                    "Build a command-line tool that reads, transforms and writes a data file",
                    "tool runs on new input",
                ),
                (
                    3,
                    "Refactor the tool into tested functions and explain the design",
                    "code review with the trainer",
                ),
            ),
        ),
        TrainingModuleDef(
            "python_fundamentals",
            "Python fundamentals",
            (
                "Use lists, dictionaries, comprehensions and functions idiomatically",
                "Structure a small Python project with tests",
            ),
            (
                (1, "Solve fifteen Python exercises on core data structures", "exercises pass their tests"),
                (2, "Write a data-cleaning script with pandas and unit tests", "tests pass in CI"),
                (3, "Package the script and walk a trainer through it", "trainer walkthrough"),
            ),
        ),
        TrainingModuleDef(
            "sql_fundamentals",
            "SQL fundamentals",
            (
                "Write joins, aggregations and window functions for analysis questions",
                "Explain query results and check them for errors",
            ),
            (
                (1, "Answer twenty analysis questions on a practice schema", "answers match the key"),
                (
                    2,
                    "Write queries with window functions for a reporting task",
                    "queries reviewed by the trainer",
                ),
                (3, "Present one analysis with its queries and checks", "short presentation"),
            ),
        ),
        TrainingModuleDef(
            "ai_ml_fundamentals",
            "AI/ML fundamentals",
            (
                "Explain the supervised learning workflow: data split, training, evaluation",
                "Train and evaluate a baseline model on a real dataset",
            ),
            (
                (
                    1,
                    "Complete the core lessons on regression, classification and evaluation metrics",
                    "quiz score",
                ),
                (
                    2,
                    "Train a baseline model on a practice dataset and report its metrics",
                    "notebook reviewed",
                ),
                (3, "Improve the baseline and explain what changed and why", "walkthrough with a mentor"),
            ),
        ),
        TrainingModuleDef(
            "technical_reading_practice",
            "Technical reading practice",
            (
                "Read technical passages aloud completely and at a steady pace",
                "Summarise the key ideas of a passage after one reading",
            ),
            (
                (1, "Read two short technical passages aloud each day", "recorded readings complete"),
                (2, "Summarise one article a day in five sentences", "summaries checked against the source"),
                (3, "Relay a new passage after 30 seconds of reading", "key ideas covered in a mock drill"),
            ),
        ),
        TrainingModuleDef(
            "technical_explanations",
            "Explaining technical ideas",
            (
                "Explain a technical concept in plain language with an example",
                "Answer follow-up questions about an explanation",
            ),
            (
                (1, "Write plain-language explanations of three ML concepts", "peer review"),
                (2, "Give two-minute spoken explanations and take questions", "mentor feedback"),
                (3, "Explain a new concept from a paper or article", "mock interview question"),
            ),
        ),
        TrainingModuleDef(
            "structured_speaking",
            "Structured speaking",
            (
                "Connect several points into one structured answer",
                "Say clearly what you do not know instead of guessing",
            ),
            (
                (1, "Practise four-point answers with a 60-second preparation", "recorded practice"),
                (2, "Use a fixed structure (point, example, link) on new topics", "mentor feedback"),
                (3, "Answer unseen topic cards in two minutes", "mock exercise"),
            ),
        ),
        TrainingModuleDef(
            "communication_practice",
            "Communication practice",
            (
                "Answer the question asked, in a logical order",
                "Lead with the main point, then the supporting detail",
            ),
            (
                (1, "Practise answers that start with the main point", "recorded practice"),
                (2, "Summarise project work in one minute for a listener", "peer feedback"),
                (3, "Hold a mock project discussion with follow-up questions", "mentor feedback"),
            ),
        ),
        TrainingModuleDef(
            "interview_preparation",
            "Interview preparation",
            (
                "Keep going on unfamiliar questions by reasoning aloud",
                "Handle follow-up questions and correct mistakes clearly",
            ),
            (
                (1, "Practise answering unfamiliar questions with a reasoning framework", "mock questions"),
                (2, "Run two mock interviews with challenging follow-ups", "mentor feedback"),
                (3, "Review recordings and practise the weakest answers again", "improvement on repeat"),
            ),
        ),
        TrainingModuleDef(
            "learning_habits",
            "Learning habits",
            (
                "Plan learning goals for each week and track progress",
                "Check understanding by teaching back what was learned",
            ),
            (
                (1, "Write a weekly learning plan with three concrete goals", "plan reviewed"),
                (2, "Teach back one topic each week to a peer", "peer feedback"),
                (3, "Review progress against the plan and adjust it", "progress review"),
            ),
        ),
    )
}

COMPETENCY_MODULES: dict[str, tuple[str, ...]] = {
    "communication": ("communication_practice",),
    "technical_reading": ("technical_reading_practice",),
    "structured_answer": ("structured_speaking",),
    "technical": ("programming_fundamentals",),
    "trainability": ("learning_habits",),
    "composure": ("interview_preparation",),
    "adaptability": ("ai_ml_fundamentals",),
}
LANGUAGE_MODULES = {"python": "python_fundamentals", "sql": "sql_fundamentals"}


@dataclass(slots=True)
class SelectedModule:
    key: str
    gap: Decimal
    competencies: list[str]


def _gap(key: str, score: Decimal | None) -> Decimal:
    value = INSUFFICIENT_AS if score is None else score
    return INTAKE_WEIGHTS[key] * (Decimal(5) - value)


def select_modules(
    scores: Mapping[str, Decimal | None],
    *,
    drill_b: int | None = None,
    language: str | None = None,
) -> list[SelectedModule]:
    """Deterministic module selection (see the module docstring); returns at most five, most important first."""
    chosen: dict[str, SelectedModule] = {}
    for key, score in scores.items():
        if key not in COMPETENCY_MODULES or (score is not None and score > SELECT_AT_OR_BELOW):
            continue
        modules = list(COMPETENCY_MODULES[key])
        if key == "technical":
            modules = [LANGUAGE_MODULES.get((language or "").lower(), "programming_fundamentals")]
        if key == "technical_reading" and drill_b is not None and drill_b <= 3:
            modules.append("technical_explanations")
        for module in modules:
            entry = chosen.setdefault(module, SelectedModule(module, Decimal(0), []))
            entry.gap = max(entry.gap, _gap(key, score))
            if key not in entry.competencies:
                entry.competencies.append(key)
    if "ai_ml_fundamentals" not in chosen:
        adaptability = scores.get("adaptability")
        chosen["ai_ml_fundamentals"] = SelectedModule(
            "ai_ml_fundamentals",
            _gap("adaptability", adaptability) if adaptability is not None else Decimal(0),
            [],
        )
    ordered = sorted(chosen.values(), key=lambda m: (-m.gap, m.key))
    if len(ordered) > MAX_MODULES:
        keep = ordered[:MAX_MODULES]
        if not any(m.key == "ai_ml_fundamentals" for m in keep):
            keep[-1] = chosen["ai_ml_fundamentals"]
        ordered = keep
    return ordered


def _default_why(module: SelectedModule, improvements: Mapping[str, Sequence[str]]) -> str:
    if not module.competencies:
        return "Included for every participant: the program builds on these fundamentals."
    labels = ", ".join(INTAKE_LABELS[k] for k in module.competencies)
    note = next((str(n) for k in module.competencies for n in improvements.get(k, ()) if str(n).strip()), "")
    base = f"Builds on the interview evidence for {labels}."
    return f"{base} Focus: {neutral(note.rstrip('.'))}." if note else base


def deterministic_plan(
    modules: Sequence[SelectedModule], *, improvements: Mapping[str, Sequence[str]] | None = None
) -> dict[str, Any]:
    """Catalog defaults for the selected modules (``TrainingPlan`` dict, ``generated_by = deterministic``)."""
    improvements = improvements or {}
    items = []
    for priority, module in enumerate(modules, start=1):
        definition = CATALOG[module.key]
        items.append(
            {
                "key": module.key,
                "title": definition.title,
                "priority": priority,
                "competencies": list(module.competencies),
                "why": _default_why(module, improvements)[:400],
                "objectives": list(definition.objectives),
                "milestones": [
                    {"week": week, "milestone": milestone, "measure": measure}
                    for week, milestone, measure in definition.milestones
                ],
            }
        )
    titles = ", ".join(CATALOG[m.key].title for m in modules)
    return {
        "generated_by": "deterministic",
        "summary": f"Three-week plan with {len(modules)} module(s): {titles}.",
        "modules": items,
    }


def merge_plan(base: Mapping[str, Any], draft: TrainingPlanDraft) -> dict[str, Any]:
    """The model's ``why``/objectives/milestones on top of the deterministic plan (modules, order, titles and
    priorities stay deterministic). Unknown or missing module keys keep the defaults."""
    by_key = {m.key: m for m in draft.modules}
    used = 0
    modules = []
    for module in base["modules"]:
        item = dict(module)
        written = by_key.get(module["key"])
        if written is not None:
            used += 1
            item["why"] = neutral(written.why.strip())[:400] or item["why"]
            item["objectives"] = [neutral(o.strip()) for o in written.objectives if o.strip()][:4] or item[
                "objectives"
            ]
            milestones = [
                {
                    "week": m.week,
                    "milestone": neutral(m.milestone.strip()),
                    "measure": neutral(m.measure.strip()),
                }
                for m in written.milestones
                if m.milestone.strip() and m.measure.strip()
            ][:6]
            if len(milestones) >= 3 and len(item["objectives"]) >= 2:
                item["milestones"] = milestones
            else:
                item["objectives"] = list(module["objectives"])
        modules.append(item)
    summary = neutral(draft.summary.strip())[:600] or base["summary"]
    return {"generated_by": "model" if used else "deterministic", "summary": summary, "modules": modules}


def training_messages(
    modules: Sequence[SelectedModule],
    *,
    strengths: Mapping[str, Sequence[str]],
    improvements: Mapping[str, Sequence[str]],
) -> list[dict[str, str]]:
    lines = []
    for module in modules:
        definition = CATALOG[module.key]
        lines.append(f"{module.key}: {definition.title} — {'; '.join(definition.objectives)}")
    notes = {
        INTAKE_LABELS.get(k, k): {
            "strengths": list(strengths.get(k, ()))[:3],
            "improve": list(improvements.get(k, ()))[:3],
        }
        for k in INTAKE_LABELS
        if strengths.get(k) or improvements.get(k)
    }
    system = TRAINING_SYSTEM.render(schema=describe_nested(TrainingPlanDraft))
    user = TRAINING_USER.render(
        modules="\n".join(lines), notes=json.dumps(notes, ensure_ascii=False, indent=1)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


@dataclass(slots=True)
class TrainingOutcome:
    plan: dict[str, Any]
    model_call_id: str | None = None
    error: str | None = None
    retryable: bool = False


async def generate_training_plan(
    *,
    scores: Mapping[str, Decimal | None],
    strengths: Mapping[str, Sequence[str]],
    improvements: Mapping[str, Sequence[str]],
    drill_b: int | None,
    language: str | None,
    session_id: str,
    organization_id: str,
    use_model: bool = True,
    services: Any | None = None,
) -> TrainingOutcome:
    """Select modules, then personalise them with one model call (catalog defaults on any failure)."""
    modules = select_modules(scores, drill_b=drill_b, language=language)
    base = deterministic_plan(modules, improvements=improvements)
    if not use_model:
        return TrainingOutcome(plan=base)
    messages = training_messages(modules, strengths=strengths, improvements=improvements)
    assert_prompt_safe(messages)
    if services is None:
        from app.ai.registry import get_ai

        services = get_ai()
    try:
        result = await services.llm.structured(
            TrainingPlanDraft,
            messages,
            context=CallContext(
                task=ModelTask.REPORT_GENERATION,
                session_id=session_id,
                organization_id=organization_id,
                prompt_version=TRAINING_PROMPT_VERSION,
            ),
            temperature=TRAINING_TEMPERATURE,
            max_tokens=TRAINING_MAX_TOKENS,
            timeout=TRAINING_TIMEOUT_SECONDS,
            max_attempts=2,
        )
    except AIError as exc:
        retryable = bool(getattr(exc, "retryable", False)) and not isinstance(exc, AIOutputInvalid)
        return TrainingOutcome(
            plan=base, error=f"{type(exc).__name__}: {str(exc)[:300]}", retryable=retryable
        )
    return TrainingOutcome(plan=merge_plan(base, result.value), model_call_id=result.model_call_id)


__all__ = [
    "CATALOG",
    "COMPETENCY_MODULES",
    "MAX_MODULES",
    "SelectedModule",
    "TrainingModuleDef",
    "TrainingOutcome",
    "deterministic_plan",
    "generate_training_plan",
    "merge_plan",
    "select_modules",
    "training_messages",
]
