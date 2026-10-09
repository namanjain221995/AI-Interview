"""Deterministic intake stage machine (design contract sections 6.1-6.4).

Pure logic, no I/O: the item catalogue (``ItemSpec``), the plan of one interview (``build_stages``), stage
budgets and compression (6.3), and ``next_move()`` which turns the classified last answer into the next
``Move`` (6.4). ``app.services.intake.conductor`` turns moves into utterances, model calls and persistence;
the ``apply_*`` helpers here mutate ``IntakeState`` (still no I/O) so every rule is unit-testable.

Vocabulary
==========
* Planned items are created by ``build_stages`` (stage 2 ladder items at prepare time, from the claims).
* Dynamic items are created when they happen: follow-up items (``greeting.followup``, ``topic.followup``,
  ``learning.followup``), candidate-question answers (``close.answer``), and conditional follow-on items
  (``mandatory.mq3.verification_retry``, ``mandatory.mq3.language``, ``mandatory.mq2.pm`` when the spoken
  MQ2 answer names a PM role). Résumé rung probes (``resume.C1.ownership.clarify``) are follow-ups charged
  to their rung item (``followups_used``), so the review timeline shows them under the rung.
* "T" items speak fixed template text, "M" items are phrased by the live model inside the item (template
  fallback), "A" items are realtime activities, "X" items are never spoken (MQ1: form only).
* A follow-up is only ever *offered* inside a budget the machine granted. When the next item is
  model-phrased too, the model chooses between ``follow_up`` and ``ask_item`` in the same streamed call;
  when the next item is a template, ``followup_warranted()`` (answer length / hedge) decides before the call
  so a template never has to wait for a model decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.schemas.intake import INTAKE_CANDIDATE_STAGE_TITLES, INTAKE_STAGE_KEYS
from app.services.intake.state import IntakeItemState, IntakeStageState, IntakeState

STAGE_KEYS: tuple[str, ...] = tuple(INTAKE_STAGE_KEYS)
STAGE_NUMBERS: dict[str, int] = {key: index + 1 for index, key in enumerate(STAGE_KEYS)}

#: Candidate-safe stage titles (session.state.intake, the room's stage indicator): the contract constant.
CANDIDATE_TITLES: dict[str, str] = dict(INTAKE_CANDIDATE_STAGE_TITLES)

#: Kit minute midpoints per stage (sum 42 minutes); scaled to the interview's time limit (6.3).
STAGE_MIDPOINT_MINUTES: dict[str, float] = {
    "greeting": 5.0,
    "resume": 9.0,
    "mandatory": 4.0,
    "reading": 7.5,
    "topic": 7.5,
    "learning": 5.0,
    "close": 4.0,
}
CLOSING_RESERVE_S = 60.0
COMPRESSION_FACTOR = 1.15
SOFT_CAP_FACTOR = 1.25
C3_MIN_STAGE_REMAINING_S = 180.0
MAX_TIME_EXTRA_S = 300.0
#: Platform constant (not in the kit): the Drill B relay capture lasts at most this long.
RELAY_MAX_SECONDS = 150
#: Activity restarts allowed after disconnects (attempts = restarts + 1); then the activity ends interrupted.
MAX_ACTIVITY_RESTARTS = 2

ItemKind = Literal["T", "M", "A", "X"]
LADDER_RUNGS: tuple[str, ...] = ("ownership", "mechanics", "stress")
MODEL_MOVES: tuple[str, ...] = ("ask_item", "follow_up", "clarify", "answer_question", "redirect")

# Unclear answers get one clarification per item (6.4).
UNCLEAR_KINDS = frozenset({"clarification", "brief", "garbled", "backchannel", "empty"})
#: Follow-up anchors: answering the key opens a follow-up offer on the value (stage-level follow-up items).
FOLLOWUP_ANCHORS: dict[str, str] = {
    "greeting.project": "greeting.followup",
    "greeting.followup": "greeting.followup",
    "topic.weakest": "topic.followup",
    "topic.followup": "topic.followup",
    "learning.three_weeks": "learning.followup",
    "learning.teach": "learning.followup",
    "learning.scenario": "learning.followup",
    "learning.followup": "learning.followup",
}
FOLLOWUP_LIMIT_NAMES: dict[str, tuple[str, int]] = {
    "greeting.followup": ("greeting", 2),
    "topic.followup": ("topic", 1),
    "learning.followup": ("learning", 2),
}
#: Answers shorter than this (words) to a template-followed item make a follow-up worthwhile.
FOLLOWUP_MIN_WORDS = {"long": 45, "standard": 15}


@dataclass(frozen=True, slots=True)
class ItemSpec:
    key: str
    stage: str
    kind: ItemKind
    mandatory: bool
    compressible: bool = False
    answer_mode: str = "standard"
    soft_limit_s: int | None = None
    estimate_s: float = 45.0
    title: str = ""
    item_kind: str = "prompt"  # questions.decision.intake.item_kind (15.9)
    accepts_brief: bool = False  # a short/backchannel reply is a valid answer (yes/no questions)
    claim_ref: str | None = None
    rung: str | None = None
    mq: str | None = None
    activity: str | None = None  # reading_aloud | read_relay | topic_card
    dynamic: bool = False  # created when it happens (never planned)

    @property
    def model_phrased(self) -> bool:
        return self.kind == "M"


_STATIC: tuple[ItemSpec, ...] = (
    # 1 greeting
    ItemSpec("greeting.intro", "greeting", "T", True, answer_mode="long", soft_limit_s=90, estimate_s=100,
             title="Self-introduction"),
    ItemSpec("greeting.project", "greeting", "T", True, answer_mode="long", estimate_s=90,
             title="Proudest project or role"),
    ItemSpec("greeting.followup", "greeting", "M", False, estimate_s=50, title="Project follow-up",
             item_kind="followup", dynamic=True),
    # 3 mandatory
    ItemSpec("mandatory.mq1", "mandatory", "X", False, estimate_s=0, title="Work authorisation (form only)",
             item_kind="mq", mq="mq1"),
    ItemSpec("mandatory.mq2", "mandatory", "T", True, accepts_brief=True, title="Target role", item_kind="mq",
             mq="mq2"),
    ItemSpec("mandatory.mq2.pm", "mandatory", "T", True, title="Technical vs growth PM", item_kind="mq", mq="mq2"),
    ItemSpec("mandatory.mq3", "mandatory", "T", True, title="Language, scripting and self-rating",
             item_kind="mq", mq="mq3"),
    ItemSpec("mandatory.mq3.language", "mandatory", "T", False, accepts_brief=True,
             title="Language clarification", item_kind="mq", mq="mq3", dynamic=True),
    ItemSpec("mandatory.mq3.verification", "mandatory", "T", True, title="Language reality check",
             item_kind="verification", mq="mq3"),
    ItemSpec("mandatory.mq3.verification_retry", "mandatory", "T", False, title="Second language question",
             item_kind="verification", mq="mq3", dynamic=True),
    ItemSpec("mandatory.mq3.rating_probe", "mandatory", "T", True, compressible=True, title="Self-rating probe",
             item_kind="mq", mq="mq3"),
    ItemSpec("mandatory.mq4", "mandatory", "T", True, accepts_brief=True,
             title="Experience level and training commitment", item_kind="mq", mq="mq4"),
    ItemSpec("mandatory.mq4.followup", "mandatory", "T", True, accepts_brief=True, title="Experience follow-up",
             item_kind="mq", mq="mq4"),
    # 4 reading
    ItemSpec("reading.drill_a", "reading", "A", True, estimate_s=150, title="Drill A: read aloud",
             item_kind="activity", activity="reading_aloud"),
    ItemSpec("reading.drill_b", "reading", "A", True, estimate_s=160, title="Drill B: read and relay",
             item_kind="activity", activity="read_relay"),
    ItemSpec("reading.followup.least_sure", "reading", "T", True, estimate_s=40, title="Least sure part"),
    ItemSpec("reading.followup.non_technical", "reading", "T", True, estimate_s=40,
             title="Explain to a non-technical person"),
    ItemSpec("reading.followup.interviewer_question", "reading", "T", True, compressible=True, estimate_s=40,
             title="Likely interviewer question"),
    # 5 topic
    ItemSpec("topic.card", "topic", "A", True, estimate_s=200, title="Topic card", item_kind="activity",
             activity="topic_card"),
    ItemSpec("topic.weakest", "topic", "M", True, estimate_s=50, title="Least developed point"),
    ItemSpec("topic.followup", "topic", "M", False, estimate_s=50, title="Topic follow-up", item_kind="followup",
             dynamic=True),
    ItemSpec("topic.scenario", "topic", "T", False, compressible=True, answer_mode="long", estimate_s=90,
             title="Scenario question"),
    # 6 learning
    ItemSpec("learning.three_weeks", "learning", "M", True, answer_mode="long", estimate_s=90,
             title="Three weeks of training"),
    ItemSpec("learning.teach", "learning", "T", True, answer_mode="long", estimate_s=120, title="Teach me something"),
    ItemSpec("learning.scenario", "learning", "T", False, compressible=True, answer_mode="long", estimate_s=90,
             title="Learning scenario"),
    ItemSpec("learning.followup", "learning", "M", False, estimate_s=50, title="Learning follow-up",
             item_kind="followup", dynamic=True),
    # 7 close
    ItemSpec("close.questions", "close", "T", True, estimate_s=60, title="Candidate questions"),
    ItemSpec("close.answer", "close", "M", False, estimate_s=30, title="Answer to a candidate question",
             item_kind="answer_question", dynamic=True),
    ItemSpec("close.anything_else", "close", "T", False, compressible=True, accepts_brief=True, estimate_s=40,
             title="Anything else"),
    ItemSpec("close.closing", "close", "T", True, estimate_s=15, title="Closing", item_kind="closing"),
)  # fmt: skip
STATIC_ITEMS: dict[str, ItemSpec] = {spec.key: spec for spec in _STATIC}
_LADDER_KEY = re.compile(r"^resume\.C([1-3])\.(ownership|mechanics|stress)(?:\.clarify)?$")
LADDER_TITLES = {"ownership": "Ownership", "mechanics": "Mechanics", "stress": "Stress test"}


def ladder_spec(claim_ref: str, rung: str, *, mandatory: bool, compressible: bool) -> ItemSpec:
    return ItemSpec(
        f"resume.{claim_ref}.{rung}",
        "resume",
        "M",
        mandatory,
        compressible=compressible,
        answer_mode="long" if rung in ("ownership", "mechanics") else "standard",
        estimate_s=60.0,
        title=f"{claim_ref}: {LADDER_TITLES[rung]}",
        item_kind="ladder",
        claim_ref=claim_ref,
        rung=rung,
    )


def ladder_flags(claim_index: int, rung: str) -> tuple[bool, bool]:
    """(mandatory, compressible) of a ladder rung: C1 all mandatory; C2 mandatory with a compressible
    stress rung; C3 optional."""
    if claim_index == 1:
        return True, False
    if claim_index == 2:
        return True, rung == "stress"
    return False, False


def item_spec(key: str) -> ItemSpec:
    """The spec of any item key (static, ladder or a rung probe ``.clarify``)."""
    spec = STATIC_ITEMS.get(key)
    if spec is not None:
        return spec
    match = _LADDER_KEY.match(key)
    if match is None:
        raise KeyError(f"unknown intake item {key!r}")
    index = int(match.group(1))
    rung = match.group(2)
    mandatory, compressible = ladder_flags(index, rung)
    return ladder_spec(f"C{index}", rung, mandatory=mandatory, compressible=compressible)


def is_ladder(key: str | None) -> bool:
    return bool(key) and _LADDER_KEY.match(key or "") is not None


# =============================================================================================== budgets


def stage_budgets(time_limit_s: float) -> dict[str, float]:
    """Seconds per stage: kit midpoints scaled to ``time_limit_s`` minus the closing reserve (6.3)."""
    total = sum(STAGE_MIDPOINT_MINUTES.values()) * 60.0
    k = max(0.0, (float(time_limit_s) - CLOSING_RESERVE_S)) / total
    return {key: float(round(minutes * 60.0 * k)) for key, minutes in STAGE_MIDPOINT_MINUTES.items()}


def _item_state(spec: ItemSpec, *, followup_limit: int = 0) -> IntakeItemState:
    return IntakeItemState(
        item_key=spec.key,
        title=spec.title,
        mandatory=spec.mandatory,
        compressible=spec.compressible,
        followup_limit=followup_limit,
    )


def build_stages(
    time_limit_s: float,
    *,
    claims: Iterable[dict[str, Any]] = (),
    target_role: str | None = None,
    work_authorization_enabled: bool = False,
    clarify_per_rung: int = 1,
) -> list[IntakeStageState]:
    """The planned items of one interview (stage 2 ladder from ``claims``)."""
    budgets = stage_budgets(time_limit_s)
    stages = [
        IntakeStageState(key=key, number=STAGE_NUMBERS[key], budget_s=budgets[key]) for key in STAGE_KEYS
    ]
    by_key = {stage.key: stage for stage in stages}
    for spec in _STATIC:
        if spec.dynamic or spec.stage == "resume":
            continue
        if spec.key == "mandatory.mq2.pm" and not is_pm_role(target_role):
            continue
        item = _item_state(spec)
        if spec.key == "mandatory.mq1":
            item.mandatory = work_authorization_enabled
            if work_authorization_enabled:
                item.status, item.skip_reason = "prefilled", "prefilled"
            else:
                item.status, item.skip_reason = "not_applicable", "policy_disabled"
        by_key[spec.stage].items.append(item)
    set_ladder(by_key["resume"], claims, clarify_per_rung=clarify_per_rung)
    return stages


def set_ladder(
    stage: IntakeStageState, claims: Iterable[dict[str, Any]], *, clarify_per_rung: int = 1
) -> None:
    """(Re)build the stage 2 ladder items from the selected claims (only while the stage has not started)."""
    stage.items = []
    for index, claim in enumerate(list(claims)[:3], start=1):
        ref = str(claim.get("ref") or f"C{index}")
        for rung in LADDER_RUNGS:
            mandatory, compressible = ladder_flags(index, rung)
            spec = ladder_spec(ref, rung, mandatory=mandatory, compressible=compressible)
            stage.items.append(_item_state(spec, followup_limit=clarify_per_rung))


def is_pm_role(target_role: str | None) -> bool:
    return bool(target_role) and str(target_role).startswith("product_manager")


# =========================================================================================== signals


@dataclass(frozen=True, slots=True)
class AnswerSignal:
    """The classified last candidate answer (``app.services.interview.special.classify`` + hedges)."""

    kind: str = "none"  # SpecialKind value
    words: int = 0
    hedge: str | None = None
    skip_request: bool = False
    typed: bool = False
    mentions_pm: bool = False
    language: str | None = None  # a kit language key named in the answer
    question_like: bool = False  # a statement that asks for information ("I'd like to know about ...")


@dataclass(slots=True)
class Resolution:
    item_key: str
    status: str  # answered | declined | not_reached | interrupted
    signal: str | None = None
    skip_reason: str | None = None


@dataclass(slots=True)
class Move:
    """What the conductor does next.

    ``action``: ``ask`` (item_key: template / model / activity) | ``repeat`` | ``wait`` | ``redirect`` |
    ``defer_question`` | ``clarify`` | ``encourage`` | ``dont_know_probe`` | ``time_up`` | ``close`` |
    ``none``. With ``offered = ("follow_up", "ask_item")`` the model decides: ``follow_up`` charges
    ``followup_item`` (``apply_followup``), ``ask_item`` asks ``item_key`` (``apply_ask``: skips + stage
    changes). With ``offered = ("follow_up",)`` ``item_key`` is the follow-up item itself.
    """

    action: str
    item_key: str | None = None
    offered: tuple[str, ...] = ()
    followup_item: str | None = None
    followup_key: str | None = None
    resolve: Resolution | None = None
    skips: list[tuple[str, str | None]] = field(default_factory=list)
    stage_from: str | None = None
    stage_to: str | None = None
    variant: str | None = None
    compression: bool = False
    dynamic_after: str | None = None  # create ``item_key`` as a dynamic item after this item
    reason: str = ""

    @property
    def model_choice(self) -> bool:
        return "follow_up" in self.offered and "ask_item" in self.offered


# ========================================================================================= variants


def digest_value(state: IntakeState, key: str) -> Any:
    value = (state.onboarding_digest or {}).get(key)
    return None if value in ("", None) else value


def mq2_variant(state: IntakeState) -> str:
    return "confirm" if (state.target_role or digest_value(state, "target_role")) else "kit"


def mq3_variant(state: IntakeState) -> str:
    return "confirm" if (state.language and state.self_rating is not None) else "kit"


def mq4_followup_variant(state: IntakeState) -> str:
    """years unknown -> ask them (mandatory); else commitment not full -> hours (mandatory); else the gap
    question (compressible)."""
    if state.years_hands_on is None:
        return "years"
    commitment = digest_value(state, "training_commitment")
    if commitment and commitment != "full":
        return "commit"
    return "gap"


def rating_probe_variant(state: IntakeState) -> str:
    if state.self_rating is None:
        return "unknown"
    return "high" if state.self_rating >= 9 else "normal"


def effective_flags(state: IntakeState, item: IntakeItemState) -> tuple[bool, bool]:
    """(mandatory, compressible) of an item now (MQ4 follow-up depends on the form values)."""
    if item.item_key == "mandatory.mq4.followup":
        variant = mq4_followup_variant(state)
        return (variant != "gap", variant == "gap")
    return item.mandatory, item.compressible


# ====================================================================================== time / budget


def estimate_s(key: str) -> float:
    try:
        return item_spec(key).estimate_s
    except KeyError:
        return 45.0


def required_seconds(state: IntakeState, *, exclude: str | None = None) -> float:
    """Estimated time of the remaining mandatory, non-compressible items (6.3)."""
    total = 0.0
    for stage in state.stages[state.stage_index :]:
        for item in stage.items:
            if item.item_key == exclude or item.status != "pending":
                continue
            mandatory, compressible = effective_flags(state, item)
            if mandatory and not compressible:
                total += estimate_s(item.item_key)
    return total


def compression_needed(state: IntakeState, now_s: float) -> bool:
    available = state.time_limit_s - now_s - CLOSING_RESERVE_S
    required = required_seconds(state, exclude=state.current_item)
    return available < COMPRESSION_FACTOR * required


def stage_elapsed(stage: IntakeStageState, now_s: float) -> float:
    if stage.started_s is None:
        return 0.0
    return max(0.0, now_s - stage.started_s)


def past_soft_cap(stage: IntakeStageState, now_s: float) -> bool:
    return stage.budget_s > 0 and stage_elapsed(stage, now_s) > SOFT_CAP_FACTOR * stage.budget_s


def stage_remaining(stage: IntakeStageState, now_s: float) -> float:
    return stage.budget_s - stage_elapsed(stage, now_s)


# ======================================================================================= next move


def scenario_limit(state: IntakeState) -> int:
    try:
        return int(state.limits.get("scenarios", 2))
    except (TypeError, ValueError):
        return 2


def closing_questions_max(state: IntakeState) -> int:
    try:
        return int(state.limits.get("closing_questions_max", 3))
    except (TypeError, ValueError):
        return 3


def _skip_reason(
    state: IntakeState,
    stage: IntakeStageState,
    item: IntakeItemState,
    now_s: float,
    *,
    compression: bool,
    past_cap: bool,
) -> str | None:
    key = item.item_key
    mandatory, compressible = effective_flags(state, item)
    if is_ladder(key):
        spec = item_spec(key)
        ref = spec.claim_ref or ""
        if spec.rung == "stress" and (state.hedges.get(ref) or state.claim_signals.get(ref) == "team_level"):
            return "not_hands_on"
        if ref == "C3" and spec.rung == "ownership":
            if compression or past_cap or stage_remaining(stage, now_s) < C3_MIN_STAGE_REMAINING_S:
                return "time_budget"
        if ref == "C3" and compression:
            return "time_budget"
        if ref == "C3" and spec.rung != "ownership":
            found = state.find_item(f"resume.{ref}.ownership")
            if found is None or found[1].status in ("pending", "skipped"):
                return "time_budget"  # C3 never started: its later rungs go with it
    if key in ("topic.scenario", "learning.scenario"):
        if scenario_limit(state) <= 0:
            return "policy_disabled"
        if len(state.scenarios_used) >= scenario_limit(state):
            return "time_budget"
    if key == "close.anything_else" and state.time_up:
        return "time_up"
    if compressible and compression:
        return "time_budget"
    if not mandatory and (compression or past_cap):
        return "time_budget"
    return None


@dataclass(slots=True)
class _Next:
    item_key: str | None
    skips: list[tuple[str, str | None]]
    stage_from: str | None
    stage_to: str | None


def _next_item(state: IntakeState, now_s: float, *, compression: bool) -> _Next:
    skips: list[tuple[str, str | None]] = []
    current = state.stage
    for index in range(state.stage_index, len(state.stages)):
        stage = state.stages[index]
        past_cap = index == state.stage_index and past_soft_cap(stage, now_s)
        for item in stage.items:
            if item.status != "pending":
                continue
            reason = _skip_reason(state, stage, item, now_s, compression=compression, past_cap=past_cap)
            if reason is not None:
                skips.append((item.item_key, reason))
                continue
            moved = index != state.stage_index
            return _Next(
                item.item_key,
                skips,
                current.key if moved and current else None,
                stage.key if moved else None,
            )
    return _Next(None, skips, current.key if current else None, None)


@dataclass(slots=True)
class _Followup:
    item: str  # item charged (rung item or stage follow-up item)
    key: str  # question meta item key


def _followup_used(state: IntakeState, key: str) -> int:
    found = state.find_item(key)
    return found[1].followups_used if found else 0


def followup_limit(state: IntakeState, key: str) -> int:
    name, default = FOLLOWUP_LIMIT_NAMES[key]
    limit = state.limit(name, default)
    if key == "learning.followup":
        found = state.find_item("learning.scenario")
        if found and found[1].status in ("asked", "answered"):
            limit -= 1  # the learning scenario used one of the learning follow-up slots
    return max(0, limit)


def _followup_offer(
    state: IntakeState, resolve: Resolution | None, now_s: float, *, compression: bool
) -> _Followup | None:
    if resolve is None or resolve.status != "answered":
        return None
    if resolve.signal in ("unclear", "dont_know", "declined", "hedge"):
        return None
    stage = state.stage
    if compression or stage is None or past_soft_cap(stage, now_s):
        return None
    key = resolve.item_key
    if is_ladder(key) and not key.endswith(".clarify"):
        found = state.find_item(key)
        if found is None:
            return None
        rung = found[1]
        limit = state.limit("resume_clarify_per_rung", rung.followup_limit or 1)
        if rung.followups_used + rung.clarifications < limit:
            return _Followup(key, f"{key}.clarify")
        return None
    target = FOLLOWUP_ANCHORS.get(key)
    if target is None:
        return None
    if _followup_used(state, target) < followup_limit(state, target):
        return _Followup(target, target)
    return None


def followup_warranted(resolved_key: str, last: AnswerSignal | None) -> bool:
    """Before a template item: a follow-up is worth asking only for a short or hedged answer."""
    if last is None:
        return False
    if last.hedge:
        return True
    try:
        mode = item_spec(resolved_key).answer_mode
    except KeyError:
        mode = "standard"
    return last.words < FOLLOWUP_MIN_WORDS.get(mode, 15)


def _variant_for(state: IntakeState, key: str | None) -> str | None:
    if key == "mandatory.mq2":
        return mq2_variant(state)
    if key == "mandatory.mq3":
        return mq3_variant(state)
    if key == "mandatory.mq4.followup":
        return mq4_followup_variant(state)
    if key == "mandatory.mq3.rating_probe":
        return rating_probe_variant(state)
    return None


def _ask(
    state: IntakeState,
    nxt: _Next,
    *,
    resolve: Resolution | None,
    compression: bool,
    reason: str,
) -> Move:
    if nxt.item_key is None:
        return Move(
            "close",
            item_key="close.closing",
            resolve=resolve,
            skips=nxt.skips,
            stage_from=nxt.stage_from,
            compression=compression,
            reason=reason or "plan_done",
        )
    if nxt.item_key == "close.closing":
        return Move(
            "close",
            item_key="close.closing",
            resolve=resolve,
            skips=nxt.skips,
            stage_from=nxt.stage_from,
            stage_to=nxt.stage_to,
            compression=compression,
            reason=reason or "closing",
        )
    spec = item_spec(nxt.item_key)
    return Move(
        "ask",
        item_key=nxt.item_key,
        offered=("ask_item",) if spec.kind == "M" else (),
        resolve=resolve,
        skips=nxt.skips,
        stage_from=nxt.stage_from,
        stage_to=nxt.stage_to,
        variant=_variant_for(state, nxt.item_key),
        compression=compression,
        reason=reason,
    )


def _followon(
    state: IntakeState, resolve: Resolution | None, last: AnswerSignal | None
) -> tuple[str, str] | None:
    """Conditional template items that follow a specific answer: (item_key, insert_after)."""
    if resolve is None or last is None:
        return None
    key = resolve.item_key
    if key == "mandatory.mq3.verification":
        used = _followup_used(state, "mandatory.mq3.verification_retry")
        limit = state.limit("mandatory", 1)
        short = (
            resolve.signal in ("dont_know", "unclear")
            or last.kind in ("brief", "dont_know")
            or last.words < 6
        )
        if short and used < limit and state.find_item("mandatory.mq3.verification_retry") is None:
            return "mandatory.mq3.verification_retry", key
    if (
        key == "mandatory.mq3"
        and state.language is None
        and state.find_item("mandatory.mq3.language") is None
    ):
        if last.language is None:
            return "mandatory.mq3.language", key
    if key == "mandatory.mq2" and last.mentions_pm and state.find_item("mandatory.mq2.pm") is None:
        return "mandatory.mq2.pm", key
    return None


def next_move(state: IntakeState, last: AnswerSignal | None, now_s: float) -> Move:  # noqa: PLR0911, PLR0912
    """The deterministic next move after the last candidate answer (``None`` = no answer: a timer or a
    resume). Never mutates ``state``."""
    if state.closed:
        return Move("none", reason="closed")
    stage = state.stage
    if stage is None:
        return Move("close", item_key="close.closing", reason="no_stage")
    item = state.current()
    compression = compression_needed(state, now_s)
    asked = item is not None and item.status == "asked"

    # Time is up: everything before the close becomes not_reached; a compressed close follows (6.3).
    if not state.time_up and stage.key != "close" and now_s >= state.time_limit_s:
        resolve = None
        if asked and item is not None:
            resolve = Resolution(item.item_key, "answered" if last is not None else "not_reached",
                                 skip_reason=None if last is not None else "time_up")  # fmt: skip
        return Move("time_up", item_key="close.questions", resolve=resolve, compression=True, reason="time_up",
                    variant="time_up")  # fmt: skip

    if not asked or item is None:
        return _ask(state, _next_item(state, now_s, compression=compression), resolve=None,
                    compression=compression, reason="start" if item is None else "continue")  # fmt: skip
    if last is None:
        last = AnswerSignal(kind="silence")  # no answer at all (silence move-on)

    spec = item_spec(item.item_key)
    kind = last.kind

    # ------------------------------------------------------------------ no answer at all (silence move-on)
    if kind == "silence":
        if stage.key == "close":
            return Move("close", item_key="close.closing", resolve=Resolution(item.item_key, "answered",
                        signal="no_questions"), compression=compression, reason="silence")  # fmt: skip
        resolve = Resolution(item.item_key, "answered", signal="dont_know")
        return _advance(state, resolve, last, now_s, compression)

    # ------------------------------------------------------------------ the closing stage
    if stage.key == "close":
        return _close_stage_move(state, item, last, now_s, compression)

    # ------------------------------------------------------------------ flow turns (not counted)
    if kind == "repeat":
        return Move("repeat", item_key=item.item_key, reason="repeat")
    if kind == "wants_time":
        return Move("wait", item_key=item.item_key, reason="wants_time")
    if kind == "instruction":
        return Move("redirect", item_key=item.item_key, reason="instruction")
    if kind == "candidate_question":
        return Move("defer_question", item_key=item.item_key, reason="candidate_question")

    # ------------------------------------------------------------------ skip requests
    if last.skip_request:
        if spec.mandatory and not item.encouraged:
            return Move("encourage", item_key=item.item_key, reason="skip_request")
        resolve = Resolution(item.item_key, "declined", signal="declined", skip_reason="declined")
        return _advance(state, resolve, last, now_s, compression)

    # ------------------------------------------------------------------ unclear / brief answers
    brief_ok = spec.accepts_brief and kind in ("brief", "backchannel")
    if kind in UNCLEAR_KINDS and not brief_ok:
        if item.clarifications < 1:
            return Move("clarify", item_key=item.item_key, offered=("clarify",) if spec.kind == "M" else (),
                        reason=kind)  # fmt: skip
        resolve = Resolution(item.item_key, "answered", signal="unclear")
        return _advance(state, resolve, last, now_s, compression)

    # ------------------------------------------------------------------ don't know
    if kind == "dont_know":
        if spec.item_kind == "ladder" and item.clarifications < 1:
            return Move("dont_know_probe", item_key=item.item_key, reason="dont_know")
        resolve = Resolution(item.item_key, "answered", signal="dont_know")
        return _advance(state, resolve, last, now_s, compression)

    # ------------------------------------------------------------------ a real answer
    signal = None
    if spec.item_kind == "ladder" and spec.rung == "ownership" and last.hedge:
        signal = "hedge"
    elif brief_ok:
        signal = "brief"
    resolve = Resolution(item.item_key, "answered", signal=signal)
    return _advance(state, resolve, last, now_s, compression)


def _advance(
    state: IntakeState, resolve: Resolution, last: AnswerSignal | None, now_s: float, compression: bool
) -> Move:
    followon = _followon(state, resolve, last)
    if followon is not None:
        key, after = followon
        return Move(
            "ask",
            item_key=key,
            resolve=resolve,
            variant=_variant_for(state, key),
            compression=compression,
            dynamic_after=after,
            reason="followon",
        )
    # A hedge on an ownership rung changes the next rungs (mechanics asks what they observed; stress skipped):
    # project it before choosing, without mutating the caller's state.
    view = state
    if resolve.signal == "hedge":
        spec = item_spec(resolve.item_key)
        if spec.claim_ref:
            view = state.model_copy(
                update={"hedges": {**state.hedges, spec.claim_ref: last.hedge if last else "hedge"}}
            )
    projected = _projected(view, resolve)
    nxt = _next_item(projected, now_s, compression=compression)
    offer = _followup_offer(projected, resolve, now_s, compression=compression)
    if offer is not None:
        next_kind = item_spec(nxt.item_key).kind if nxt.item_key and nxt.item_key != "close.closing" else "T"
        if next_kind == "M":
            move = _ask(projected, nxt, resolve=resolve, compression=compression, reason="followup_offer")
            move.offered = ("follow_up", "ask_item")
            move.followup_item = offer.item
            move.followup_key = offer.key
            return move
        if followup_warranted(resolve.item_key, last):
            return Move(
                "ask",
                item_key=offer.item,
                offered=("follow_up",),
                followup_item=offer.item,
                followup_key=offer.key,
                resolve=resolve,
                compression=compression,
                reason="followup_warranted",
            )
    return _ask(projected, nxt, resolve=resolve, compression=compression, reason="next")


def _projected(state: IntakeState, resolve: Resolution | None) -> IntakeState:
    """A copy of ``state`` with ``resolve`` applied (next-item scans must not see the item as asked)."""
    if resolve is None:
        return state
    copy = state.model_copy(deep=True)
    found = copy.find_item(resolve.item_key)
    if found is not None:
        found[1].status = resolve.status
        found[1].signal = resolve.signal
    return copy


def _close_stage_move(
    state: IntakeState, item: IntakeItemState, last: AnswerSignal, now_s: float, compression: bool
) -> Move:
    kind = last.kind
    key = item.item_key
    if kind == "repeat":
        return Move("repeat", item_key=key, reason="repeat")
    if kind == "wants_time":
        return Move("wait", item_key=key, reason="wants_time")
    if kind == "instruction":
        return Move("redirect", item_key=key, reason="instruction")
    resolve = Resolution(key, "answered", signal="no_questions" if kind == "no_questions" else None)
    max_answers = 1 if state.time_up else closing_questions_max(state)
    asks = kind == "candidate_question" or (kind == "none" and last.question_like)
    if asks and state.closing_answers < max_answers:
        return Move("ask", item_key="close.answer", offered=("answer_question",), resolve=resolve,
                    followup_item="close.answer", followup_key="close.answer", compression=compression,
                    reason="candidate_question")  # fmt: skip
    if asks:
        return Move("close", item_key="close.closing", resolve=resolve, variant="hiring_team",
                    compression=compression, reason="questions_max")  # fmt: skip
    if key == "close.questions" and kind in UNCLEAR_KINDS and kind != "empty" and item.clarifications < 1:
        return Move("clarify", item_key=key, reason=kind)
    if state.time_up:
        return Move("close", item_key="close.closing", resolve=resolve, compression=True, reason="time_up")
    projected = _projected(state, resolve)
    nxt = _next_item(projected, now_s, compression=compression)
    return _ask(projected, nxt, resolve=resolve, compression=compression, reason="close_stage")


# ========================================================================================= applying


def _end_stage(stage: IntakeStageState, now_s: float) -> None:
    if stage.status in ("completed", "incomplete"):
        return
    stage.status = "incomplete" if stage_incomplete(stage) else "completed"
    stage.ended_s = now_s


def stage_incomplete(stage: IntakeStageState) -> bool:
    for item in stage.items:
        if item.mandatory and item.status in ("pending", "asked", "not_reached", "interrupted"):
            return True
    return False


def start_stage(state: IntakeState, key: str, now_s: float) -> IntakeStageState:
    for index, stage in enumerate(state.stages):
        if stage.key == key:
            current = state.stage
            if current is not None and current.key != key:
                _end_stage(current, now_s)
            state.stage_index = index
            if stage.status == "pending":
                stage.status = "in_progress"
                stage.started_s = now_s
            return stage
    raise KeyError(f"unknown stage {key!r}")


def apply_resolution(state: IntakeState, resolve: Resolution | None, now_s: float) -> IntakeItemState | None:
    if resolve is None:
        return None
    found = state.find_item(resolve.item_key)
    if found is None:
        return None
    item = found[1]
    item.status = resolve.status
    if resolve.signal and item.signal is None:
        item.signal = resolve.signal
    if resolve.skip_reason:
        item.skip_reason = resolve.skip_reason
    item.ended_s = now_s
    if resolve.signal == "hedge":
        spec = item_spec(item.item_key)
        if spec.claim_ref:
            state.hedges.setdefault(spec.claim_ref, "hedge")
    return item


def apply_skips(state: IntakeState, skips: Iterable[tuple[str, str | None]], now_s: float) -> None:
    for key, reason in skips:
        found = state.find_item(key)
        if found is None or found[1].status != "pending":
            continue
        found[1].status = "skipped"
        found[1].skip_reason = reason
        found[1].ended_s = now_s


def ensure_item(state: IntakeState, key: str, *, after: str | None = None) -> IntakeItemState:
    """The item ``key`` of its stage, created (after ``after`` when given) for dynamic items."""
    found = state.find_item(key)
    if found is not None:
        return found[1]
    spec = item_spec(key)
    stage = state.stage_by_key(spec.stage)
    if stage is None:
        raise KeyError(f"stage {spec.stage} missing")
    limit = 0
    if key in FOLLOWUP_LIMIT_NAMES:
        limit = followup_limit(state, key)
    elif key == "close.answer":
        limit = closing_questions_max(state)
    elif key == "mandatory.mq3.verification_retry":
        limit = state.limit("mandatory", 1)
    item = _item_state(spec, followup_limit=limit)
    position = len(stage.items)
    if after is not None:
        for index, existing in enumerate(stage.items):
            if existing.item_key == after:
                position = index + 1
                break
    elif key not in ("close.answer",):
        # Stage-level follow-up items sit after the last item that was asked.
        for index, existing in enumerate(stage.items):
            if existing.status in ("asked", "answered", "declined"):
                position = index + 1
    else:
        for index, existing in enumerate(stage.items):
            if existing.item_key == "close.questions":
                position = index + 1
    stage.items.insert(position, item)
    return item


def mark_asked(state: IntakeState, key: str, now_s: float) -> IntakeItemState:
    found = state.find_item(key)
    if found is None:
        raise KeyError(f"item {key!r} not in the plan")
    stage, item = found
    if state.stage is None or state.stage.key != stage.key or stage.status == "pending":
        start_stage(state, stage.key, now_s)
    item.status = "asked"
    if item.started_s is None:
        item.started_s = now_s
    state.current_item = key
    spec = item_spec(key)
    state.answer_mode = spec.answer_mode
    state.soft_limit_s = spec.soft_limit_s
    return item


def apply_ask(state: IntakeState, move: Move, now_s: float) -> IntakeItemState:
    """Mark the move's skips, change stage when it starts a new one, and mark ``move.item_key`` asked."""
    apply_skips(state, move.skips, now_s)
    if move.dynamic_after is not None and move.item_key is not None:
        ensure_item(state, move.item_key, after=move.dynamic_after)
    if move.stage_to:
        start_stage(state, move.stage_to, now_s)
    assert move.item_key is not None
    item = mark_asked(state, move.item_key, now_s)
    if move.variant and not item.variant:
        item.variant = move.variant
    if move.item_key in ("topic.scenario", "learning.scenario") and move.item_key not in state.scenarios_used:
        state.scenarios_used.append(move.item_key)
    state.compression = move.compression
    return item


def apply_followup(state: IntakeState, move: Move, now_s: float) -> IntakeItemState:
    """The model (or the machine) asked a follow-up: charge it to ``followup_item``."""
    key = move.followup_item
    assert key is not None
    if is_ladder(key):
        found = state.find_item(key)
        if found is None:
            raise KeyError(key)
        item = found[1]
    else:
        item = ensure_item(state, key)
    item.followups_used += 1
    item.status = "asked"
    if item.started_s is None:
        item.started_s = now_s
    state.current_item = key
    state.answer_mode = "standard"
    state.soft_limit_s = None
    if key == "close.answer":
        state.closing_answers += 1
    state.compression = move.compression
    return item


def apply_clarification(state: IntakeState, key: str) -> IntakeItemState | None:
    found = state.find_item(key)
    if found is None:
        return None
    found[1].clarifications += 1
    return found[1]


def apply_time_up(state: IntakeState, now_s: float) -> None:
    """Everything before the close is ``not_reached``; stages before the close end ``incomplete`` (or
    completed when nothing mandatory is missing); the close starts with the time-up variant."""
    state.time_up = True
    state.time_up_pending = False
    close_index = len(state.stages) - 1
    for index, stage in enumerate(state.stages):
        if index >= close_index:
            break
        for item in stage.items:
            if item.status in ("pending", "asked"):
                item.status = "not_reached"
                item.skip_reason = "time_up"
                item.ended_s = item.ended_s or now_s
        if stage.status != "completed":
            if stage.status == "in_progress":
                stage.ended_s = now_s
            stage.status = "incomplete"
    close = state.stages[close_index]
    for item in close.items:
        if item.item_key == "close.anything_else" and item.status == "pending":
            item.status, item.skip_reason = "skipped", "time_up"
    state.stage_index = close_index
    if close.status == "pending":
        close.status = "in_progress"
        close.started_s = now_s


def finish_close(state: IntakeState, now_s: float, *, reached: bool = True) -> None:
    """The closing was spoken (``reached``) or the session ended early: resolve every open item and stage
    (the current stage ends completed/incomplete, stages never reached end incomplete)."""
    for stage in state.stages:
        for item in stage.items:
            if item.item_key == "close.closing" and reached:
                item.status = "answered"  # statement items count as answered once spoken
                item.started_s = item.started_s or now_s
                item.ended_s = now_s
            elif item.status in ("pending", "asked"):
                item.status = "not_reached"
                item.skip_reason = item.skip_reason or ("time_up" if state.time_up else None)
                item.ended_s = item.ended_s or now_s
        if stage.status == "in_progress":
            _end_stage(stage, now_s)
        elif stage.status == "pending":
            stage.status = "incomplete"
    state.closed = True
    state.current_item = None


# ======================================================================================= text helpers

_SKIP = re.compile(
    r"\b(?:can we|could we|let'?s|i(?:'d| would) (?:like|rather) (?:to )?|please)\s*(?:just )?skip\b"
    r"|\bskip (?:this|that|it)(?: one| question| part)?\b|\bnext question\b"
    r"|\bi(?:'d| would) rather not (?:answer|say|discuss|talk about)\b|\bpass on (?:this|that)\b"
    r"|^(?:pass|skip)[.!]?$",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "half": 0.5,
}  # fmt: skip
_RATING = re.compile(
    r"\b(10|[1-9](?:\.5)?|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:/|out of|over)\s*(?:10|ten)\b",
    re.IGNORECASE,
)
_RATING_SOFT = re.compile(
    r"\b(?:rate|rating|give)\s+(?:myself|me|it)?\s*(?:a|an|at|around|about)?\s*"
    r"(10|[1-9]|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.IGNORECASE,
)
_YEARS = re.compile(
    r"\b(\d{1,2}(?:\.\d)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|half a)"
    r"\s*(?:\+\s*)?(?:or so\s+)?years?\b",
    re.IGNORECASE,
)
_PM = re.compile(r"\b(?:product manag(?:er|ement)|pm|technical pm|growth pm|product role)\b", re.IGNORECASE)


def wants_skip(text: str) -> bool:
    return bool(_SKIP.search(text or ""))


def _number(token: str) -> float | None:
    token = token.lower().strip()
    if token in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[token])
    if token.startswith("half"):
        return 0.5
    try:
        return float(token)
    except ValueError:
        return None


def parse_self_rating(text: str) -> int | None:
    """A 1-10 self-rating stated in an answer ("seven out of ten", "7/10", "I'd rate myself a 6")."""
    for pattern in (_RATING, _RATING_SOFT):
        match = pattern.search(text or "")
        if match:
            value = _number(match.group(1))
            if value is not None and 1 <= value <= 10:
                return int(round(value))
    return None


def parse_years(text: str) -> float | None:
    match = _YEARS.search(text or "")
    if not match:
        return None
    value = _number(match.group(1))
    if value is None or value > 50:
        return None
    return value


def mentions_pm(text: str) -> bool:
    return bool(_PM.search(text or ""))


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


__all__ = [
    "CANDIDATE_TITLES",
    "CLOSING_RESERVE_S",
    "MAX_ACTIVITY_RESTARTS",
    "MAX_TIME_EXTRA_S",
    "RELAY_MAX_SECONDS",
    "STAGE_KEYS",
    "STAGE_NUMBERS",
    "AnswerSignal",
    "ItemSpec",
    "Move",
    "Resolution",
    "apply_ask",
    "apply_clarification",
    "apply_followup",
    "apply_resolution",
    "apply_skips",
    "apply_time_up",
    "build_stages",
    "compression_needed",
    "ensure_item",
    "finish_close",
    "followup_warranted",
    "item_spec",
    "mark_asked",
    "next_move",
    "parse_self_rating",
    "parse_years",
    "required_seconds",
    "stage_budgets",
    "start_stage",
    "wants_skip",
]
