"""Realtime intake activities (design contract 7.2): phase plans, material content and the descriptors that
travel engine -> runtime in ``ActivityStarted`` / ``ActivityPhaseChanged``.

| kind            | phase     | duration                   | capture | content visible   | finish button        |
|-----------------|-----------|----------------------------|---------|-------------------|----------------------|
| reading_aloud   | read      | <= 180 s (kit drill A)     | yes     | passage           | after 5 s            |
| read_relay      | read      | 30 s (kit drill B)         | no      | passage           | after 10 s           |
|                 | relay     | <= 150 s (RELAY_MAX)       | yes     | never (hide)      | yes                  |
| topic_card      | prepare   | 60 s (kit)                 | no      | card              | yes                  |
|                 | speak     | <= 120 s (kit)             | yes     | card              | yes                  |

The descriptor's ``content`` never goes to the client in ``activity.start`` or ``session.state``; the runtime puts
it only into the ``activity.phase`` message that starts a phase where it may be visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.intake.kit import IntakeKit
from app.services.intake.stages import RELAY_MAX_SECONDS

ACTIVITY_ITEMS: dict[str, str] = {
    "reading.drill_a": "reading_aloud",
    "reading.drill_b": "read_relay",
    "topic.card": "topic_card",
}
ACTIVITY_STAGES: dict[str, str] = {"reading_aloud": "reading", "read_relay": "reading", "topic_card": "topic"}
CAPTURE_PHASES: frozenset[tuple[str, str]] = frozenset(
    {("reading_aloud", "read"), ("read_relay", "relay"), ("topic_card", "speak")}
)
#: Minimum time left for a non-capture phase to be re-shown after a disconnect (else straight to capture).
RESHOW_MIN_S = 3.0
#: The passage is re-shown for slightly less than the exact remainder (clock skew safety margin).
RESHOW_MARGIN_S = 0.25


@dataclass(frozen=True, slots=True)
class PhasePlan:
    phase: str
    duration_ms: int
    capture: bool
    content_visible: bool
    finish_after_ms: int  # the finish button is enabled after this much of the phase
    hide_on_entry: bool = False

    def record(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "duration_ms": self.duration_ms,
            "capture": self.capture,
            "content_visible": self.content_visible,
            "finish_after_ms": self.finish_after_ms,
            "hide_on_entry": self.hide_on_entry,
        }


def phase_plans(kit: IntakeKit, kind: str) -> list[PhasePlan]:
    timers = kit.timers
    if kind == "reading_aloud":
        return [PhasePlan("read", timers.drill_a_max_seconds * 1000, True, True, 5000)]
    if kind == "read_relay":
        return [
            PhasePlan("read", timers.drill_b_read_seconds * 1000, False, True, 10_000),
            PhasePlan("relay", RELAY_MAX_SECONDS * 1000, True, False, 0, hide_on_entry=True),
        ]
    if kind == "topic_card":
        return [
            PhasePlan("prepare", timers.topic_card_prepare_seconds * 1000, False, True, 0),
            PhasePlan("speak", timers.topic_card_speak_seconds * 1000, True, True, 0),
        ]
    raise KeyError(f"unknown activity kind {kind!r}")


def phase_plan(kit: IntakeKit, kind: str, phase: str) -> PhasePlan:
    for plan in phase_plans(kit, kind):
        if plan.phase == phase:
            return plan
    raise KeyError(f"{kind} has no phase {phase!r}")


def next_phase(kit: IntakeKit, kind: str, phase: str) -> PhasePlan | None:
    plans = phase_plans(kit, kind)
    for index, plan in enumerate(plans):
        if plan.phase == phase:
            return plans[index + 1] if index + 1 < len(plans) else None
    return None


def is_capture(kind: str, phase: str) -> bool:
    return (kind, phase) in CAPTURE_PHASES


def material_content(kit: IntakeKit, kind: str, material_key: str) -> dict[str, Any]:
    """What the candidate may see: exact kit passage paragraphs, or the card title, instruction and 4 bullets."""
    if kind in ("reading_aloud", "read_relay"):
        passage = kit.passage(material_key)
        return {
            "title": passage.title,
            "paragraphs": list(passage.paragraphs),
            "bullets": None,
            "instruction": None,
        }
    card = kit.topic_card(material_key)
    return {
        "title": card.title,
        "paragraphs": None,
        "bullets": list(card.bullets),
        "instruction": card.instruction,
    }


def material_title(kit: IntakeKit, kind: str, material_key: str) -> str:
    if kind in ("reading_aloud", "read_relay"):
        return kit.passage(material_key).title
    return kit.topic_card(material_key).title


def reference_words(kit: IntakeKit, kind: str, material_key: str) -> int:
    if kind in ("reading_aloud", "read_relay"):
        return kit.passage(material_key).word_count
    return 0


def descriptor(
    kit: IntakeKit,
    *,
    activity_id: str,
    kind: str,
    material_key: str,
    attempt: int = 1,
    resumed: bool = False,
    start_phase: str | None = None,
    start_duration_ms: int | None = None,
    item_key: str = "",
) -> dict[str, Any]:
    """``ActivityStarted.activity``: the contract keys plus engine -> runtime extras (``start_phase``,
    ``start_duration_ms``, ``reference_words``, ``phase_rules``); ``content`` is never forwarded as is."""
    plans = phase_plans(kit, kind)
    first = start_phase or plans[0].phase
    phases = [p for p in plans if _phase_index(plans, p.phase) >= _phase_index(plans, first)]
    return {
        "activity_id": activity_id,
        "kind": kind,
        "stage": ACTIVITY_STAGES[kind],
        "title": material_title(kit, kind, material_key),
        "phases": [
            {
                "phase": p.phase,
                "duration_ms": int(start_duration_ms)
                if p.phase == first and start_duration_ms
                else p.duration_ms,
                "capture": p.capture,
            }
            for p in phases
        ],
        "attempt": attempt,
        "resumed": resumed,
        "content": material_content(kit, kind, material_key),
        "start_phase": first,
        "start_duration_ms": int(start_duration_ms) if start_duration_ms else None,
        "reference_words": reference_words(kit, kind, material_key),
        "phase_rules": {p.phase: p.record() for p in plans},
        "material_key": material_key,
        "item_key": item_key,
    }


def _phase_index(plans: list[PhasePlan], phase: str) -> int:
    for index, plan in enumerate(plans):
        if plan.phase == phase:
            return index
    return 0


#: Capture end reason -> ``activity.end`` reason.
END_REASONS: dict[str, str] = {
    "finish_button": "finished_early",
    "deadline": "deadline",
    "silence": "completed",
    "max_audio": "completed",
    "disconnect": "interrupted",
    "session_end": "session_ended",
}


def end_reason(capture_end_reason: str) -> str:
    return END_REASONS.get(capture_end_reason, "completed")


__all__ = [
    "ACTIVITY_ITEMS",
    "ACTIVITY_STAGES",
    "CAPTURE_PHASES",
    "RESHOW_MARGIN_S",
    "RESHOW_MIN_S",
    "PhasePlan",
    "descriptor",
    "end_reason",
    "is_capture",
    "material_content",
    "material_title",
    "next_phase",
    "phase_plan",
    "phase_plans",
    "reference_words",
]
