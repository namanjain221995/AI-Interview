"""Prompt utilities shared by every domain prompt module.

* :data:`FAIRNESS_RULES` must be included in every evaluative prompt
  (assessment, evaluation, scoring, reports, profile analysis, vision).
* :func:`render` renders a Jinja2 template with ``StrictUndefined`` (a
  missing variable is an error, never an empty string). The ``untrusted``
  filter fences candidate-supplied text (answers, resumes, code) so it reads
  as data rather than instructions.
* :class:`PromptTemplate` pairs a template with a stable version string for
  ``CallContext.prompt_version`` / ``model_calls.prompt_version``.

Domain prompts live in their own modules (``app.services.<domain>.prompts``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from jinja2 import Environment, StrictUndefined, Template

FAIRNESS_RULES = """\
Fairness and evidence rules (mandatory):
- Judge only the job-relevant content of what the candidate said, wrote or coded, against the stated
  competencies and rubric. Base every judgement on specific, quotable evidence from this interview;
  when the evidence is insufficient, say so instead of guessing.
- Never infer, mention or use emotion, nervousness, confidence of demeanour, personality, honesty or
  deception, intelligence, race, ethnicity, national origin, religion, disability or health, age, gender,
  sexual orientation, family status, attractiveness or appearance, accent, dialect, or any other protected
  or sensitive trait, and never use proxies for them (name, photo, voice, school prestige, address,
  graduation year, employment gaps).
- Do not penalise speaking style: hesitations, filler words, pauses, repetitions, non-native phrasing,
  grammar or transcription errors are not evidence of skill unless clear written or verbal communication
  is itself the competency being assessed, and then only the clarity of the content counts.
- Resume statements are claims to verify, not facts. Treat contradictions as questions for a human
  reviewer, never as proof of dishonesty.
- Your output is advisory. A human reviewer makes every hiring decision."""

_UNTRUSTED_TAG = re.compile(r"[^a-z0-9_]")


def _untrusted(value: Any, label: str = "candidate_input") -> str:
    """Wrap untrusted text in a labelled block the model is told to treat as
    data; occurrences of the closing tag inside the text are neutralised."""
    tag = _UNTRUSTED_TAG.sub("_", str(label).lower()) or "candidate_input"
    text = "" if value is None else str(value)
    text = re.sub(rf"</?\s*{tag}\s*>", "[tag removed]", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{text}\n</{tag}>"


@lru_cache(maxsize=1)
def _environment() -> Environment:
    env = Environment(
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 - prompts are plain text sent to a model, never rendered as HTML
        keep_trailing_newline=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["untrusted"] = _untrusted
    env.globals["FAIRNESS_RULES"] = FAIRNESS_RULES
    return env


@lru_cache(maxsize=512)
def _compile(template: str) -> Template:
    return _environment().from_string(template)


def render(template: str, **variables: Any) -> str:
    """Render ``template`` (Jinja2, StrictUndefined). ``{{ FAIRNESS_RULES }}``
    is always available."""
    return _compile(template).render(**variables).strip()


def prompt_version(name: str, template: str) -> str:
    """``{name}@{sha256(template)[:10]}``, at most 40 characters (the
    model_calls.prompt_version column)."""
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()[:10]
    return f"{name[:29]}@{digest}"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    template: str
    version: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", prompt_version(self.name, self.template))
        _compile(self.template)  # fail fast on syntax errors at import time

    def render(self, **variables: Any) -> str:
        return render(self.template, **variables)


__all__ = ["FAIRNESS_RULES", "PromptTemplate", "prompt_version", "render"]
