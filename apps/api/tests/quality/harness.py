"""Harness orchestration shared by the live pytest entry point and the CLI.

Options come from environment variables so ``python -m tests.quality.run``
can drive the pytest test (which owns the throwaway database fixtures):

* ``QUALITY_PERSONAS``  comma list (default: all eight; ``scoring`` = strong,mid,vague,weak)
* ``QUALITY_MAX_TURNS`` safety cap on candidate turns per interview (default 12)
* ``QUALITY_JUDGE``     1/0 - run the LLM judge (default 1)
* ``QUALITY_REPORTS``   1/0 - generate report narratives for each session (default 0)
* ``QUALITY_SPECULATE`` 1/0 - emulate the speculative quick assessment (default 1)
* ``QUALITY_OUT``       output directory for the JSON/text results (default: system temp dir)
* ``QUALITY_ASSERT``    1/0 - fail the test when the scorecard fails (default 1)
* ``QUALITY_WALL_BUDGET_S`` stop taking new candidate turns after this many seconds of wall time per
  interview (default 420, so a one-persona invocation finishes within ~10 minutes)
* ``QUALITY_CAPTURE``   JSONL path: append every turn-decision prompt (messages + persona) for offline
  latency probes (replaying real prompts against the model; nothing is captured by default)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checks import run_checks
from .judge import judge_run
from .personas import DEFAULT_PERSONAS, PERSONAS, SCORING_ORDER, Persona, judge_resume_digest
from .runner import InterviewRunner
from .scorecard import PersonaScore, Scorecard, latency_check, scoring_order_check
from .world import build_quality_world


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(slots=True)
class HarnessOptions:
    personas: list[Persona]
    max_turns: int = 12
    judge: bool = True
    reports: bool = False
    speculate: bool = True
    out_dir: Path = Path(tempfile.gettempdir()) / "interview-quality"
    enforce: bool = True
    wall_budget_s: float | None = 420.0

    @classmethod
    def from_env(cls) -> HarnessOptions:
        raw = os.environ.get("QUALITY_PERSONAS", "").strip()
        if not raw or raw == "all":
            keys = list(DEFAULT_PERSONAS)
        elif raw == "scoring":
            keys = list(SCORING_ORDER)
        else:
            keys = [k.strip() for k in raw.split(",") if k.strip()]
        unknown = [k for k in keys if k not in PERSONAS]
        if unknown:
            raise ValueError(f"unknown personas {unknown}; known: {sorted(PERSONAS)}")
        return cls(
            personas=[PERSONAS[k] for k in keys],
            max_turns=int(os.environ.get("QUALITY_MAX_TURNS", "12")),
            judge=_flag("QUALITY_JUDGE", True),
            reports=_flag("QUALITY_REPORTS", False),
            speculate=_flag("QUALITY_SPECULATE", True),
            out_dir=Path(os.environ.get("QUALITY_OUT") or Path(tempfile.gettempdir()) / "interview-quality"),
            enforce=_flag("QUALITY_ASSERT", True),
            wall_budget_s=float(os.environ.get("QUALITY_WALL_BUDGET_S", "420")) or None,
        )


def job_facts(round_config: Any | None = None) -> str:
    """The job facts the interviewer may state (from samples/, identical to the seeded job), plus the
    round's duration. Used by the candidate-question check and given to the judge."""
    from types import SimpleNamespace

    from app.schemas.rounds import RoundConfig
    from app.seed.loaders import SeedValidationError, load_catalog

    from .world import JOB_KEY, QUALITY_ROUND

    rc = round_config or RoundConfig.model_validate(QUALITY_ROUND)
    try:
        job: Any = next(j for j in load_catalog().jobs if j.key == JOB_KEY)
    except SeedValidationError:
        # Another part of samples/ is being edited: read just this job (rescoring needs no database).
        import yaml

        path = Path(__file__).resolve().parents[4] / "samples" / "jobs" / f"{JOB_KEY}.yaml"
        job = SimpleNamespace(**yaml.safe_load(path.read_text(encoding="utf-8")))
    parts = [
        f"Title: {job.title}",
        f"This interview round: {rc.name}, {rc.duration_minutes} minutes, conducted by the AI interviewer.",
    ]
    for key in ("department", "location", "employment_type", "seniority"):
        value = getattr(job, key, None)
        if value:
            parts.append(f"{key.replace('_', ' ').title()}: {getattr(value, 'value', value)}")
    parts.append(str(job.description))
    if getattr(job, "interview_instructions", None):
        parts.append("Process notes: " + str(job.interview_instructions))
    return "\n".join(parts)


def capture_turn_prompts(ai: Any, path: Path, persona: dict[str, str]) -> None:
    """Record the messages of every turn-decision stream (the live turn prompt) to ``path`` (JSONL).
    ``persona`` is a mutable holder: the current persona key is written with each record."""
    from app.domain.enums import ModelTask

    llm = ai.llm
    original = llm.stream

    def stream(messages: Any, **kwargs: Any) -> Any:
        context = kwargs.get("context")
        if context is not None and context.task == ModelTask.TURN_DECISION:
            record = {
                "persona": persona.get("key"),
                "at": time.time(),
                "chars": sum(len(str(m.get("content", ""))) for m in messages),
                "messages": list(messages),
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        return original(messages, **kwargs)

    llm.stream = stream


async def run_harness(ai: Any, options: HarnessOptions, *, log: Any = print) -> Scorecard:
    capture = os.environ.get("QUALITY_CAPTURE", "").strip()
    current: dict[str, str] = {}
    if capture:
        capture_turn_prompts(ai, Path(capture), current)
    world = await build_quality_world(ai)
    facts = job_facts(world.round_config)
    runner = InterviewRunner(
        ai,
        world,
        max_turns=options.max_turns,
        speculate=options.speculate,
        reports=options.reports,
        wall_budget_s=options.wall_budget_s,
        log=log,
    )
    scores: list[PersonaScore] = []
    for persona in options.personas:
        current["key"] = persona.key
        run = await runner.run(persona)
        profile = persona.profile_doc()
        checks = run_checks(
            run,
            persona_tags=persona.tags,
            profile=profile,
            job_facts=facts,
            time_limit_s=world.round_config.duration_minutes * 60.0,
        )
        item = PersonaScore(run=run, checks=checks)
        if options.judge:
            try:
                item.verdict = await judge_run(ai, run, job_facts=facts, resume=judge_resume_digest(profile))
            except Exception as exc:  # noqa: BLE001 - the deterministic checks still count
                item.judge_error = f"{type(exc).__name__}: {exc}"[:300]
        scores.append(item)
        log(
            f"[{persona.key}] done: combined {item.combined:.1f} (determ {item.deterministic:.1f}, judge "
            f"{item.judge if item.judge is not None else '-'}), overall score {run.scores['overall']}, "
            f"rec {run.scores['recommendation']}, end {run.end_reason}, wall {run.wall_s.get('total_s')}s"
        )
    card = Scorecard(
        personas=scores,
        meta={
            "personas": [p.key for p in options.personas],
            "max_turns": options.max_turns,
            "judge": options.judge,
            "reports": options.reports,
            "speculate": options.speculate,
            "round": world.round_config.model_dump(mode="json"),
            "embedded_questions": world.embedded_questions,
            "job_facts": facts,
            "time_limit_s": world.round_config.duration_minutes * 60.0,
            "load_average": os.getloadavg(),
        },
    )
    card.global_checks.append(scoring_order_check(scores))
    card.global_checks.append(latency_check(card))
    return card


__all__ = ["HarnessOptions", "job_facts", "run_harness"]
