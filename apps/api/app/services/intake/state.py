"""Live intake state, persisted inside ``InterviewState.intake`` (design contract section 6.7).

This module imports only pydantic and ``app.domain.enums`` so ``app.services.interview.state`` can import it
without cycles. Everything here is hot state for the live interview; the durable checkpoint the evaluation
pipeline and the review page read is ``intake_stage_results`` / ``intake_activity_results`` (written by the
conductor), never this object.

MQ1 (work authorisation) answers never enter this state: only ``work_authorization_enabled`` (whether the
form asked them) is kept, and ``onboarding_digest`` is the prompt-safe subset of the onboarding form
(no eligibility, no full name).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _State(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)


class IntakeItemState(_State):
    """One planned item of a stage; mirrors ``StageItemResult`` (contract 15.5) while live."""

    item_key: str
    title: str = ""
    mandatory: bool = False
    compressible: bool = False
    status: str = "pending"  # ItemStatus: pending|asked|answered|prefilled|skipped|declined|not_reached|interrupted|not_applicable
    followups_used: int = 0
    followup_limit: int = 0
    clarifications: int = 0
    question_ids: list[str] = Field(default_factory=list)
    answer_ids: list[str] = Field(default_factory=list)
    skip_reason: str | None = None
    started_s: float | None = None
    ended_s: float | None = None
    # Additive (live only): what the machine learnt from the answers to this item.
    signal: str | None = None  # unclear | dont_know | hedge | declined | brief
    encouraged: bool = False  # a skip request on a mandatory item was answered with encouragement once
    variant: str | None = (
        None  # template variant actually used (mq2 confirm/kit, mq4.followup years/commit/gap ...)
    )

    def record(self) -> dict[str, Any]:
        """The persisted ``StageItemResult`` shape (contract 15.5)."""
        return {
            "item_key": self.item_key,
            "title": self.title,
            "mandatory": self.mandatory,
            "compressible": self.compressible,
            "status": self.status,
            "question_ids": list(self.question_ids),
            "answer_ids": list(self.answer_ids),
            "followups_used": self.followups_used,
            "followup_limit": self.followup_limit,
            "clarifications": self.clarifications,
            "skip_reason": self.skip_reason,
            "started_s": self.started_s,
            "ended_s": self.ended_s,
        }


class IntakeStageState(_State):
    key: str
    number: int
    status: str = "pending"  # pending | in_progress | completed | incomplete
    budget_s: float = 0.0
    started_s: float | None = None
    ended_s: float | None = None
    items: list[IntakeItemState] = Field(default_factory=list)

    def item(self, key: str) -> IntakeItemState | None:
        for item in self.items:
            if item.item_key == key:
                return item
        return None


class IntakeActivityState(_State):
    activity_id: str
    kind: str  # reading_aloud | read_relay | topic_card
    phase: str  # read | relay | prepare | speak
    status: str  # armed | running | ended
    material_key: str
    attempt: int = 1
    phase_started_s: float | None = None
    phase_duration_s: float | None = None
    reveal_shown_s: float = 0.0  # read_relay: cumulative seconds the passage was visible
    start_after_utterance_id: str | None = None
    # Additive (live only)
    item_key: str = ""
    question_id: str | None = None  # the activity prompt question
    restarts: int = 0  # attempts restarted after a disconnect
    interrupted_phase: str | None = (
        None  # the phase a disconnect interrupted (resume decides what to restart)
    )
    phase_remaining_s: float | None = None  # non-capture phase time left when a disconnect paused it


class IntakeState(_State):
    intake_id: str
    kit_version: str
    kit_sha256: str
    stage_index: int = 0
    stages: list[IntakeStageState] = Field(default_factory=list)
    current_item: str | None = None
    compression: bool = False
    claims: list[dict[str, Any]] = Field(default_factory=list)
    level: str | None = None
    language: str | None = None
    passages: dict[str, str] = Field(default_factory=dict)  # {"drill_a": "B", "drill_b": "C"}
    card: str | None = None
    scenarios_used: list[str] = Field(default_factory=list)
    onboarding_digest: dict[str, Any] = Field(default_factory=dict)  # prompt-safe subset, no MQ1
    work_authorization_enabled: bool = False  # never the answers
    program_name: str = "AI/ML Training Program"
    approved_info: list[dict[str, Any]] = Field(default_factory=list)
    presence: str = "orb"
    activity: IntakeActivityState | None = None
    closing_answers: int = 0
    answer_mode: str = "standard"
    soft_limit_s: int | None = None

    # ------------------------------------------------------------------ additive (live only)
    time_limit_s: float = 2700.0
    limits: dict[str, Any] = Field(
        default_factory=dict
    )  # IntakeRoundConfig dump (follow-up/claim/scenario limits)
    overrides: dict[str, str] = Field(default_factory=dict)  # passage_drill_a / passage_drill_b / topic_card
    target_role: str | None = None  # staff preset or the candidate's form value
    first_name: str | None = None
    self_rating: int | None = None  # from the form, else parsed from the MQ3 answer
    years_hands_on: float | None = None
    verification: dict[str, Any] = Field(
        default_factory=dict
    )  # {"quality": .., "matched_key_points": [..], "answer_id": ..}
    language_fallback: bool = False
    hedges: dict[str, str] = Field(default_factory=dict)  # claim ref -> hedge kind on its ownership rung
    claim_signals: dict[str, str] = Field(
        default_factory=dict
    )  # claim ref -> model claim_signal of the ownership rung
    time_up: bool = False  # the compressed close has been entered
    time_up_pending: bool = False  # time ran out during an activity; the close follows when it ends
    pending_candidate_turn: bool = False  # a candidate answer arrived while paused (answered on resume)
    pending_signal: dict[str, Any] = Field(default_factory=dict)  # AnswerSignal of that answer
    template_next: bool = (
        False  # the last model utterance failed a post-check: next M utterance uses the template
    )
    scenario_keys: dict[str, str] = Field(
        default_factory=dict
    )  # {"topic.scenario": "B2", "learning.scenario": "C1"}
    closing_lead: str | None = None  # one sentence spoken before the closing (e.g. the hiring-team answer)
    weakest_bullet: int | None = None  # topic.weakest: the bullet the model asked about
    closed: bool = False
    prepared: bool = False
    deviations: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    @property
    def stage(self) -> IntakeStageState | None:
        if 0 <= self.stage_index < len(self.stages):
            return self.stages[self.stage_index]
        return None

    def stage_by_key(self, key: str) -> IntakeStageState | None:
        for stage in self.stages:
            if stage.key == key:
                return stage
        return None

    def find_item(self, key: str | None) -> tuple[IntakeStageState, IntakeItemState] | None:
        if not key:
            return None
        for stage in self.stages:
            item = stage.item(key)
            if item is not None:
                return stage, item
        return None

    def current(self) -> IntakeItemState | None:
        found = self.find_item(self.current_item)
        return found[1] if found else None

    def limit(self, name: str, default: int) -> int:
        """A follow-up limit from ``IntakeRoundConfig.followup_limits`` (``greeting``, ``topic`` ...)."""
        limits = self.limits.get("followup_limits") or {}
        try:
            return int(limits.get(name, default))
        except (TypeError, ValueError):
            return default


__all__ = ["IntakeActivityState", "IntakeItemState", "IntakeStageState", "IntakeState"]
