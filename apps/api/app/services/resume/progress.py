"""Live progress of a resume through the pipeline.

``candidate_resumes.progress`` is one JSON document the UI polls::

    {"stage": "ocr", "percent": 42, "message": "Scanning page 1 of 2",
     "pages_done": 1, "pages_total": 2, "attempt": 1, "updated_at": "...",
     "steps": [{"key": "uploaded", "label": "Uploaded", "state": "done", "at": "..."}, ...]}

Step states: ``pending | active | done | failed | skipped``. The step keys
are the user-visible stages of :class:`app.domain.enums.ResumeStatus`.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import ResumeStatus

STEPS: tuple[tuple[str, str], ...] = (
    (ResumeStatus.UPLOADED.value, "Uploaded"),
    (ResumeStatus.VALIDATING.value, "Security and format checks"),
    (ResumeStatus.EXTRACTING.value, "Reading the document"),
    (ResumeStatus.OCR.value, "Scanning pages"),
    (ResumeStatus.ANALYZING.value, "Analyzing experience and skills"),
    (ResumeStatus.READY.value, "Ready"),
)
STEP_KEYS: tuple[str, ...] = tuple(key for key, _ in STEPS)

# Percent reached when a stage starts.
STAGE_PERCENT: dict[str, int] = {
    "uploaded": 5,
    "validating": 10,
    "extracting": 20,
    "ocr": 30,
    "analyzing": 60,
    "ready": 100,
}
TERMINAL_STATES = frozenset({"done", "failed", "skipped"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def initial_progress(*, validated: bool = True) -> dict[str, Any]:
    """Progress right after a successful upload (validation already ran)."""
    now = _now()
    steps = []
    for key, label in STEPS:
        state = "pending"
        at: str | None = None
        if key == "uploaded" or (key == "validating" and validated):
            state, at = "done", now
        steps.append({"key": key, "label": label, "state": state, "at": at})
    return {
        "stage": "validating" if validated else "uploaded",
        "percent": STAGE_PERCENT["extracting"] - 5 if validated else STAGE_PERCENT["uploaded"],
        "message": "Waiting to be processed",
        "pages_done": None,
        "pages_total": None,
        "attempt": 0,
        "updated_at": now,
        "steps": steps,
    }


def _ensure_steps(progress: dict[str, Any] | None) -> dict[str, Any]:
    data = copy.deepcopy(progress) if progress else {}
    known = {step.get("key"): step for step in data.get("steps", []) if isinstance(step, dict)}
    data["steps"] = [
        known.get(key) or {"key": key, "label": label, "state": "pending", "at": None} for key, label in STEPS
    ]
    return data


def start_stage(
    progress: dict[str, Any] | None,
    stage: str,
    *,
    message: str | None = None,
    percent: int | None = None,
    attempt: int | None = None,
) -> dict[str, Any]:
    """Mark ``stage`` active; every earlier unfinished step becomes done (or
    skipped for OCR when it never ran)."""
    data = _ensure_steps(progress)
    now = _now()
    index = STEP_KEYS.index(stage)
    for i, step in enumerate(data["steps"]):
        if i < index and step["state"] not in TERMINAL_STATES:
            if step["key"] == "ocr" and step["state"] == "pending":
                step["state"] = "skipped"
            else:
                step["state"] = "done"
            step["at"] = step.get("at") or now
        elif i == index:
            step["state"] = "active"
            step["at"] = now
        elif i > index and step["state"] in ("active", "failed"):
            step["state"], step["at"] = "pending", None
    data["stage"] = stage
    data["percent"] = percent if percent is not None else STAGE_PERCENT.get(stage, data.get("percent") or 0)
    data["message"] = message
    if stage == "extracting":
        data["pages_done"] = data["pages_total"] = None
    if attempt is not None:
        data["attempt"] = attempt
    data["updated_at"] = now
    return data


def update_pages(
    progress: dict[str, Any] | None, *, done: int, total: int, message: str | None = None
) -> dict[str, Any]:
    """OCR page counter; percent moves from 30 to 55 across the pages."""
    data = _ensure_steps(progress)
    data["pages_done"] = done
    data["pages_total"] = total
    if total > 0:
        span = STAGE_PERCENT["analyzing"] - 5 - STAGE_PERCENT["ocr"]
        data["percent"] = STAGE_PERCENT["ocr"] + int(span * done / total)
    if message is not None:
        data["message"] = message
    data["updated_at"] = _now()
    return data


def set_percent(progress: dict[str, Any] | None, percent: int, message: str | None = None) -> dict[str, Any]:
    data = _ensure_steps(progress)
    data["percent"] = max(int(data.get("percent") or 0), min(100, percent))
    if message is not None:
        data["message"] = message
    data["updated_at"] = _now()
    return data


def complete(progress: dict[str, Any] | None, *, message: str = "Your resume is ready") -> dict[str, Any]:
    data = start_stage(progress, "ready", message=message, percent=100)
    for step in data["steps"]:
        if step["key"] == "ready":
            step["state"] = "done"
    return data


def fail(progress: dict[str, Any] | None, message: str) -> dict[str, Any]:
    """Mark the active step (or the current stage) failed with a safe message."""
    data = _ensure_steps(progress)
    now = _now()
    target = next((s for s in data["steps"] if s["state"] == "active"), None)
    if target is None:
        stage = data.get("stage") or "extracting"
        target = next((s for s in data["steps"] if s["key"] == stage), data["steps"][2])
    target["state"] = "failed"
    target["at"] = now
    data["message"] = message
    data["updated_at"] = now
    return data


def reset(*, message: str = "Queued for reprocessing") -> dict[str, Any]:
    data = initial_progress(validated=True)
    data["message"] = message
    return data


def is_profile_ready(progress: dict[str, Any] | None) -> bool:
    if not progress:
        return False
    return any(s.get("key") == "ready" and s.get("state") == "done" for s in progress.get("steps", []))


# =============================================================================
# Profile sections (additive: ``progress["sections"]``)
# =============================================================================
#
# The deterministic quick profile is shown first; each model section replaces
# its quick read as soon as its calls finish:
#
#     "sections": [{"key": "experience", "label": "Work experience",
#                   "state": "quick|active|done|fallback|pending", "at": "..."}, ...]
#
# ``quick``: the deterministic read is shown; ``active``: the model is refining
# it; ``done``: the model's version is shown; ``fallback``: the model could not
# finish this section, the deterministic read stays; ``pending``: nothing to
# show yet (``interview`` until the analysis is done).

SECTION_STATES = frozenset({"pending", "quick", "active", "done", "fallback"})


def with_sections(progress: dict[str, Any] | None, sections: list[dict[str, Any]]) -> dict[str, Any]:
    data = _ensure_steps(progress)
    data["sections"] = copy.deepcopy(sections)
    data["updated_at"] = _now()
    return data


def set_section_state(progress: dict[str, Any] | None, key: str, state: str) -> dict[str, Any]:
    """``state`` for section ``key`` (unknown keys are ignored)."""
    data = _ensure_steps(progress)
    if state not in SECTION_STATES:
        raise ValueError(f"unknown section state {state!r}")
    now = _now()
    for section in data.get("sections") or []:
        if isinstance(section, dict) and section.get("key") == key and section.get("state") != state:
            section["state"] = state
            section["at"] = now
    data["updated_at"] = now
    return data


def finish_sections(progress: dict[str, Any] | None) -> dict[str, Any]:
    """Every section still in progress is final now (``done``)."""
    data = _ensure_steps(progress)
    now = _now()
    for section in data.get("sections") or []:
        if isinstance(section, dict) and section.get("state") in ("quick", "active", "pending"):
            section["state"] = "done"
            section["at"] = now
    return data
