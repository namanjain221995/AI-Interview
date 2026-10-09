"""Intake Interview kit: typed, validated content of ``samples/intake/intake_kit_v1.yaml``.

``load_intake_kit()`` returns the :class:`IntakeKit` (cached per file path + modification time, so edits are
picked up and normal calls cost one ``stat``). The samples directory is ``$SAMPLES_DIR`` when set (the compose
services mount it at ``/srv/interview/samples``), otherwise the nearest ``samples/`` directory above this
package that contains ``intake/``.

Content rules (see the YAML header): passages A-F and topic cards 1-8 are the exact kit text; any wording the
coordinator's non-negotiable decisions required changing keeps the original under ``kit_original`` with
``not_for_use: true`` (source metadata only — never shown, asked, sent to a model or scored). Rubric keys,
labels, kit labels and weights must equal :data:`app.services.intake.scoring.INTAKE_COMPETENCIES`, and the
decision bands its thresholds; the loader rejects a kit that disagrees.

All models are frozen and use tuples, so the cached kit cannot be mutated by callers.
"""

from __future__ import annotations

import functools
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.services.intake.scoring import BAND_THRESHOLDS, INTAKE_COMPETENCIES

SAMPLES_DIR_ENV = "SAMPLES_DIR"
KIT_RELATIVE_PATH = Path("intake") / "intake_kit_v1.yaml"

StageKey = Literal["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"]
STAGE_KEYS: tuple[str, ...] = ("greeting", "resume", "mandatory", "reading", "topic", "learning", "close")
CompetencyKey = Literal[
    "communication", "technical_reading", "structured_answer", "technical", "trainability", "composure",
    "adaptability",
]  # fmt: skip
MandatoryKey = Literal["mq1", "mq2", "mq3", "mq4"]
MANDATORY_KEYS: tuple[str, ...] = ("mq1", "mq2", "mq3", "mq4")
PassageKey = Literal["A", "B", "C", "D", "E", "F"]
PASSAGE_KEYS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F")
Drill = Literal["read_aloud", "relay"]
BandKey = Literal["strong_accept", "accept_with_notes", "borderline", "reject"]
HumanDecisionKey = Literal["accept", "accept_with_notes", "second_interview", "reject", "request_evidence"]
CardType = Literal[
    "compare_decide", "explain_example", "personal_reasoning", "argue_position", "personal_narrative"
]

MIN_RELAY_CONCEPTS = 5
MAX_RELAY_CONCEPTS = 8


class IntakeKitError(Exception):
    """The kit file is missing or invalid."""


class IntakeKitNotFound(IntakeKitError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=False)


def _require_text(value: str) -> str:
    if not value or not value.strip() or value != value.strip():
        raise ValueError("text must be non-empty without leading/trailing whitespace")
    return value


class KitOriginal(_Model):
    """Verbatim kit wording the platform replaced. Source metadata only: never shown to a candidate, asked,
    sent to a model or used in a score."""

    not_for_use: Literal[True]
    reason: str
    text: dict[str, str | tuple[str, ...]]


class KitSource(_Model):
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: str
    title: str
    subtitle: str


class Overview(_Model):
    purpose: str
    kit_original: KitOriginal | None = None


class Duration(_Model):
    min_minutes: int = Field(gt=0)
    max_minutes: int = Field(gt=0)
    target_minutes: int = Field(gt=0)
    kit_note: str

    @model_validator(mode="after")
    def _ordered(self) -> Duration:
        if not self.min_minutes <= self.target_minutes <= self.max_minutes:
            raise ValueError("duration must satisfy min <= target <= max")
        return self


class Timers(_Model):
    self_intro_seconds: int = Field(gt=0)
    drill_a_max_seconds: int = Field(gt=0)
    drill_b_read_seconds: int = Field(gt=0)
    topic_card_prepare_seconds: int = Field(gt=0)
    topic_card_speak_seconds: int = Field(gt=0)


class Range(_Model):
    min: int = Field(ge=0)
    max: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class FollowUp(_Model):
    text: str
    when: str | None = None  # condition, e.g. "If PM"
    purpose: str | None = None  # e.g. "self-awareness"
    instruction: bool = False  # an interviewer instruction rather than a question to ask

    _text = field_validator("text")(_require_text)


class ScoreLink(_Model):
    competency: CompetencyKey
    primary: bool = False
    note: str | None = None


class Activity(_Model):
    key: str
    title: str
    kit_title: str
    minutes: Range
    material: Literal["passage", "topic_card"] | None = None
    required: bool = True
    say: tuple[str, ...] = ()
    do: tuple[str, ...] = ()
    watch_for: tuple[str, ...] = ()
    platform_notes: tuple[str, ...] = ()
    kit_original: KitOriginal | None = None


class Stage(_Model):
    key: StageKey
    number: int = Field(ge=1, le=7)
    title: str
    kit_title: str
    minutes: Range
    measures: str
    say: tuple[str, ...] = ()
    do: tuple[str, ...] = ()
    follow_ups: tuple[FollowUp, ...] = ()
    watch_for: tuple[str, ...] = ()
    activities: tuple[Activity, ...] = ()
    mandatory_questions: tuple[MandatoryKey, ...] = ()
    scores_into: tuple[ScoreLink, ...] = ()
    kit_scores_into: str | None = None
    kit_original: KitOriginal | None = None

    @property
    def competencies(self) -> tuple[str, ...]:
        return tuple(link.competency for link in self.scores_into)

    @property
    def primary_competency(self) -> str | None:
        return next((link.competency for link in self.scores_into if link.primary), None)

    def activity(self, key: str) -> Activity:
        for activity in self.activities:
            if activity.key == key:
                return activity
        raise KeyError(f"stage {self.key} has no activity {key!r}")


class MandatoryQuestion(_Model):
    key: MandatoryKey
    code: Literal["MQ1", "MQ2", "MQ3", "MQ4"]
    title: str
    kit_title: str
    ask: tuple[str, ...] = Field(min_length=1)
    placeholders: tuple[str, ...] = ()
    purpose: str
    policy_note: str | None = None  # guard rules for policy-configured questions (MQ1)
    follow_ups_note: str | None = None
    follow_ups: tuple[FollowUp, ...] = ()
    record: str
    flag: str
    policy_configured: bool = False  # enabled/worded per organisation / job / jurisdiction policy
    send_to_model: bool = True  # False: answers never reach any model (MQ1)
    used_in_scoring: bool = True  # False: answers never feed a score (MQ1)
    informs: tuple[CompetencyKey, ...] = ()  # platform mapping: competencies the answers are evidence for
    review_flags: tuple[str, ...] = ()
    kit_original: KitOriginal | None = None

    @model_validator(mode="after")
    def _privacy(self) -> MandatoryQuestion:
        if self.code.lower() != self.key:
            raise ValueError(f"{self.key}: code {self.code} does not match the key")
        if not self.send_to_model and (self.used_in_scoring or self.informs):
            raise ValueError(
                f"{self.key}: answers kept from models can neither be scored nor inform competencies"
            )
        for placeholder in self.placeholders:
            if not any("{" + placeholder + "}" in line for line in self.ask):
                raise ValueError(f"{self.key}: placeholder {{{placeholder}}} is not used in the question")
        return self

    def render_ask(self, **values: str) -> tuple[str, ...]:
        """The question lines with ``{placeholder}`` values filled in (all placeholders are required)."""
        missing = [p for p in self.placeholders if not values.get(p)]
        if missing:
            raise ValueError(f"{self.key}: missing placeholder values: {', '.join(missing)}")
        lines = []
        for line in self.ask:
            for placeholder in self.placeholders:
                line = line.replace("{" + placeholder + "}", values[placeholder])
            lines.append(line)
        return tuple(lines)


class LanguageQuestion(_Model):
    text: str
    key_points: tuple[str, ...] = Field(min_length=1)


class LanguageCheck(_Model):
    key: str
    label: str
    aliases: tuple[str, ...] = Field(min_length=1)
    questions: tuple[LanguageQuestion, LanguageQuestion]


class LanguageVerification(_Model):
    instruction: str
    expected_from_rating: int = Field(ge=1, le=10)
    languages: tuple[LanguageCheck, ...]


class LadderStep(_Model):
    key: str
    label: str
    question: str


class ClaimProbe(_Model):
    key: str
    claim: str
    questions: tuple[str, ...] = Field(min_length=1)


class ResumeFramework(_Model):
    instruction: str
    depth_ladder: tuple[LadderStep, LadderStep, LadderStep]
    claim_probes: tuple[ClaimProbe, ...]
    honesty_signals_title: str
    honesty_signals_note: str
    honesty_signals: tuple[str, ...]


class ProbeCategory(_Model):
    key: str
    label: str
    probes: tuple[str, ...] = Field(min_length=1)


class ProbeBank(_Model):
    instruction: str
    categories: tuple[ProbeCategory, ...]


class Scenario(_Model):
    key: str
    prompt: str
    tests: str
    strong: str
    weak: str


class ScenarioGroup(_Model):
    key: Literal["A", "B", "C", "D"]
    title: str
    kit_competencies: str
    competencies: tuple[CompetencyKey, ...] = Field(min_length=1)
    scenarios: tuple[Scenario, ...] = Field(min_length=1)


class ScenarioBank(_Model):
    instruction: str
    use_in_stages: tuple[StageKey, ...]
    per_interview: Range
    signals_note: str
    groups: tuple[ScenarioGroup, ...]

    def scenario(self, key: str) -> Scenario:
        for group in self.groups:
            for scenario in group.scenarios:
                if scenario.key == key:
                    return scenario
        raise KeyError(f"unknown scenario {key!r}")


class RubricComponent(_Model):
    key: str
    label: str
    weight: Decimal
    method: str


class RubricCompetency(_Model):
    key: CompetencyKey
    label: str
    kit_label: str
    weight: Decimal
    kit_weight: str
    description: str
    anchors: dict[int, str]
    excluded_signals: tuple[str, ...] = ()
    components: tuple[RubricComponent, ...] = ()
    kit_original: KitOriginal | None = None

    @field_validator("anchors")
    @classmethod
    def _anchor_levels(cls, value: dict[int, str]) -> dict[int, str]:
        if set(value) != {1, 3, 5}:
            raise ValueError("anchors must define exactly levels 1, 3 and 5")
        for text in value.values():
            _require_text(text)
        return value

    @model_validator(mode="after")
    def _components(self) -> RubricCompetency:
        if self.components and sum((c.weight for c in self.components), Decimal(0)) != Decimal(1):
            raise ValueError(f"{self.key}: component weights must sum to 1")
        if Decimal(self.kit_weight.rstrip("%")) / 100 != self.weight:
            raise ValueError(f"{self.key}: kit weight {self.kit_weight} differs from {self.weight}")
        return self


class Rubric(_Model):
    instruction: str
    scale: Range
    competencies: tuple[RubricCompetency, ...]


class BandDef(_Model):
    key: BandKey
    label: str
    min_inclusive: Decimal | None
    max_exclusive: Decimal | None
    kit_range: str
    kit_decision: str
    guidance: str


class InsufficientEvidenceDef(_Model):
    key: Literal["insufficient_evidence"]
    label: str
    description: str


class HumanDecision(_Model):
    key: HumanDecisionKey
    label: str


class DecisionGuide(_Model):
    note: str
    bands: tuple[BandDef, BandDef, BandDef, BandDef]
    insufficient_evidence: InsufficientEvidenceDef
    normalized_100_note: str
    human_decisions: tuple[HumanDecision, ...]
    human_decision_rule: str


class ReviewFlag(_Model):
    key: str
    label: str
    description: str
    trigger: str
    evidence: tuple[str, ...] = Field(min_length=1)
    sources: tuple[StageKey, ...] = Field(min_length=1)
    kit_text: str  # the kit's "automatic red flag" wording (source metadata)
    automatic_rejection: Literal[False]
    note: str | None = None


class ReviewFlags(_Model):
    note: str
    kit_original: KitOriginal
    flags: tuple[ReviewFlag, ...]


class RelayConcept(_Model):
    key: str
    text: str
    cues: tuple[str, ...] = Field(min_length=1)


class Passage(_Model):
    key: PassageKey
    header: str
    title: str
    index_topic: str
    level: Literal["easy", "intermediate", "advanced"]
    level_label: str
    suggested_use: str
    drills: tuple[Drill, ...] = Field(min_length=1)
    platform_note: str | None = None
    paragraphs: tuple[str, ...] = Field(min_length=1)
    terms: tuple[str, ...] = ()
    relay_concepts: tuple[RelayConcept, ...]

    @field_validator("paragraphs")
    @classmethod
    def _paragraphs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for paragraph in value:
            _require_text(paragraph)
        return value

    @field_validator("relay_concepts")
    @classmethod
    def _concepts(cls, value: tuple[RelayConcept, ...]) -> tuple[RelayConcept, ...]:
        if not MIN_RELAY_CONCEPTS <= len(value) <= MAX_RELAY_CONCEPTS:
            raise ValueError(f"a passage needs {MIN_RELAY_CONCEPTS}-{MAX_RELAY_CONCEPTS} relay concepts")
        keys = [c.key for c in value]
        if len(set(keys)) != len(keys):
            raise ValueError("relay concept keys must be unique")
        return value

    @property
    def text(self) -> str:
        """The passage body (paragraphs separated by a blank line), as shown to the candidate."""
        return "\n\n".join(self.paragraphs)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class TopicCard(_Model):
    number: int = Field(ge=1, le=8)
    header: str
    title: str
    index_topic: str
    type: CardType
    type_label: str
    suggested_use: str
    instruction: str
    bullets: tuple[str, str, str, str]

    @property
    def key(self) -> str:
        return str(self.number)


class SelectionRow(_Model):
    item: str
    ref: str
    topic: str
    level_type: str
    suggested_use: str


class SelectionIndex(_Model):
    instruction: str
    rows: tuple[SelectionRow, ...]
    notes: tuple[str, ...] = ()
    kit_original: KitOriginal | None = None


def _normalize_alias(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


class IntakeKit(_Model):
    schema_version: Literal[1]
    source: KitSource
    overview: Overview
    duration: Duration
    timers: Timers
    conduct: tuple[str, ...]
    conduct_kit_original: KitOriginal | None = None
    stages: tuple[Stage, ...]
    mandatory_questions: tuple[MandatoryQuestion, ...]
    language_verification: LanguageVerification
    resume_framework: ResumeFramework
    probe_bank: ProbeBank
    scenario_bank: ScenarioBank
    rubric: Rubric
    decision_guide: DecisionGuide
    review_flags: ReviewFlags
    passages: tuple[Passage, ...]
    topic_cards: tuple[TopicCard, ...]
    selection_index: SelectionIndex

    # ------------------------------------------------------------------ cross-reference validation
    @model_validator(mode="after")
    def _cross_references(self) -> IntakeKit:
        errors: list[str] = []
        errors += self._check_stages()
        errors += self._check_rubric()
        errors += self._check_bands()
        errors += self._check_materials()
        errors += self._check_references()
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def _check_stages(self) -> list[str]:
        errors: list[str] = []
        if tuple(s.key for s in self.stages) != STAGE_KEYS:
            errors.append(f"stages must be {', '.join(STAGE_KEYS)} in this order")
        if tuple(s.number for s in self.stages) != tuple(range(1, len(self.stages) + 1)):
            errors.append("stage numbers must run 1..7 in order")
        if tuple(q.key for q in self.mandatory_questions) != MANDATORY_KEYS:
            errors.append("mandatory questions must be mq1..mq4 in order")
        assigned = [key for stage in self.stages for key in stage.mandatory_questions]
        if sorted(assigned) != sorted(MANDATORY_KEYS):
            errors.append("every mandatory question must be asked in exactly one stage")
        for stage in self.stages:
            primaries = [link for link in stage.scores_into if link.primary]
            if len(primaries) > 1:
                errors.append(f"stage {stage.key}: at most one primary competency")
            keys = [a.key for a in stage.activities]
            if len(set(keys)) != len(keys):
                errors.append(f"stage {stage.key}: duplicate activity keys")
        return errors

    def _check_rubric(self) -> list[str]:
        errors: list[str] = []
        expected = [(c.key, c.label, c.kit_label, c.weight) for c in INTAKE_COMPETENCIES]
        actual = [(c.key, c.label, c.kit_label, c.weight) for c in self.rubric.competencies]
        if actual != expected:
            errors.append(
                "rubric competencies (keys, labels, kit labels, weights, order) must equal INTAKE_COMPETENCIES"
            )
        if sum((c.weight for c in self.rubric.competencies), Decimal(0)) != Decimal(1):
            errors.append("rubric weights must sum to 1.00")
        if (self.rubric.scale.min, self.rubric.scale.max) != (1, 5):
            errors.append("rubric scale must be 1-5")
        return errors

    def _check_bands(self) -> list[str]:
        errors: list[str] = []
        bands = self.decision_guide.bands
        expected = [(band.value, minimum) for minimum, band in BAND_THRESHOLDS]
        actual = [(b.key, b.min_inclusive) for b in bands[: len(expected)]]
        if actual != expected:
            errors.append("decision bands must match scoring.BAND_THRESHOLDS (keys, lower bounds, order)")
        if bands[-1].key != "reject" or bands[-1].min_inclusive is not None:
            errors.append("the last band must be an open-ended reject band")
        if bands[0].max_exclusive is not None:
            errors.append("the top band must be open-ended")
        for upper, lower in zip(bands, bands[1:], strict=False):
            if lower.max_exclusive != upper.min_inclusive:
                errors.append(f"band {lower.key} must end where {upper.key} starts (no gaps)")
        return errors

    def _check_materials(self) -> list[str]:
        errors: list[str] = []
        if tuple(p.key for p in self.passages) != PASSAGE_KEYS:
            errors.append("passages must be A-F in order")
        if tuple(c.number for c in self.topic_cards) != tuple(range(1, 9)):
            errors.append("topic cards must be 1-8 in order")
        rows = {row.ref: row for row in self.selection_index.rows}
        if len(rows) != len(self.selection_index.rows):
            errors.append("selection index refs must be unique")
        for passage in self.passages:
            row = rows.get(passage.key)
            if row is None:
                errors.append(f"selection index lacks passage {passage.key}")
            elif (row.item, row.topic, row.level_type, row.suggested_use) != (
                f"Passage {passage.key}",
                passage.index_topic,
                passage.level_label,
                passage.suggested_use,
            ):
                errors.append(f"selection index row for passage {passage.key} disagrees with the passage")
        for card in self.topic_cards:
            row = rows.get(card.key)
            if row is None:
                errors.append(f"selection index lacks card {card.number}")
            elif (row.item, row.topic, row.level_type, row.suggested_use) != (
                f"Card {card.number}",
                card.index_topic,
                card.type_label,
                card.suggested_use,
            ):
                errors.append(f"selection index row for card {card.number} disagrees with the card")
        if len(rows) != len(self.passages) + len(self.topic_cards):
            errors.append("selection index must list every passage and card exactly once")
        aliases: dict[str, str] = {}
        for language in self.language_verification.languages:
            for alias in language.aliases:
                owner = aliases.setdefault(_normalize_alias(alias), language.key)
                if owner != language.key:
                    errors.append(f"language alias {alias!r} is used by {owner} and {language.key}")
        return errors

    def _check_references(self) -> list[str]:
        errors: list[str] = []
        flag_keys = [f.key for f in self.review_flags.flags]
        if len(set(flag_keys)) != len(flag_keys):
            errors.append("review flag keys must be unique")
        for question in self.mandatory_questions:
            for key in question.review_flags:
                if key not in flag_keys:
                    errors.append(f"{question.key}: unknown review flag {key!r}")
        scenario_keys = [s.key for g in self.scenario_bank.groups for s in g.scenarios]
        if len(set(scenario_keys)) != len(scenario_keys):
            errors.append("scenario keys must be unique")
        if tuple(g.key for g in self.scenario_bank.groups) != ("A", "B", "C", "D"):
            errors.append("scenario groups must be A-D in order")
        return errors

    # ------------------------------------------------------------------------------- lookups
    @property
    def version(self) -> str:
        return self.source.version

    def stage(self, key: str) -> Stage:
        for stage in self.stages:
            if stage.key == key:
                return stage
        raise KeyError(f"unknown intake stage {key!r}")

    def mandatory_question(self, key: str) -> MandatoryQuestion:
        wanted = key.strip().lower()
        for question in self.mandatory_questions:
            if question.key == wanted:
                return question
        raise KeyError(f"unknown mandatory question {key!r}")

    def competency(self, key: str) -> RubricCompetency:
        for competency in self.rubric.competencies:
            if competency.key == key:
                return competency
        raise KeyError(f"unknown intake competency {key!r}")

    def passage(self, key: str) -> Passage:
        wanted = key.strip().upper().removeprefix("PASSAGE").strip(" _-")
        for passage in self.passages:
            if passage.key == wanted:
                return passage
        raise KeyError(f"unknown passage {key!r}")

    def passages_for(self, drill: Drill) -> tuple[Passage, ...]:
        """Passages whose suggested use includes ``drill`` (``read_aloud`` = Drill A, ``relay`` = Drill B)."""
        return tuple(p for p in self.passages if drill in p.drills)

    def topic_card(self, key: int | str) -> TopicCard:
        wanted = str(key).strip().lower().removeprefix("card").strip(" _-")
        for card in self.topic_cards:
            if card.key == wanted:
                return card
        raise KeyError(f"unknown topic card {key!r}")

    def band(self, key: str) -> BandDef:
        for band in self.decision_guide.bands:
            if band.key == key:
                return band
        raise KeyError(f"unknown decision band {key!r}")

    def review_flag(self, key: str) -> ReviewFlag:
        for flag in self.review_flags.flags:
            if flag.key == key:
                return flag
        raise KeyError(f"unknown review flag {key!r}")

    def language_check(self, name: str) -> LanguageCheck | None:
        """The verification questions for a language named by the candidate ("Python", "node.js", "C#")."""
        wanted = _normalize_alias(name)
        for language in self.language_verification.languages:
            if wanted == language.key or wanted in (_normalize_alias(a) for a in language.aliases):
                return language
        return None


# =============================================================================================== loading

try:  # libyaml is ~10x faster when available
    _YamlLoader: type[yaml.SafeLoader] = yaml.CSafeLoader  # type: ignore[assignment]
except AttributeError:  # pragma: no cover
    _YamlLoader = yaml.SafeLoader


def intake_samples_dir() -> Path:
    """``$SAMPLES_DIR`` when set, otherwise the nearest ``samples/`` above this package containing ``intake/``."""
    override = os.environ.get(SAMPLES_DIR_ENV)
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_dir():
            raise IntakeKitNotFound(f"{SAMPLES_DIR_ENV}={override} is not a directory")
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "samples"
        if (candidate / "intake").is_dir():
            return candidate
    raise IntakeKitNotFound(f"samples/intake not found; set {SAMPLES_DIR_ENV} to the samples path")


def default_kit_path() -> Path:
    return intake_samples_dir() / KIT_RELATIVE_PATH


def parse_intake_kit(data: Any, *, origin: str = "<data>") -> IntakeKit:
    """Validate already-parsed YAML/JSON data (raises :class:`IntakeKitError` listing every problem)."""
    if not isinstance(data, dict):
        raise IntakeKitError(f"{origin}: the kit must be a mapping")
    try:
        return IntakeKit.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        )
        raise IntakeKitError(f"{origin}: invalid intake kit: {problems}") from exc


@functools.lru_cache(maxsize=8)
def _load_cached(path: str, mtime_ns: int, size: int) -> IntakeKit:
    with open(path, "rb") as handle:
        data = yaml.load(handle, Loader=_YamlLoader)  # noqa: S506 - SafeLoader/CSafeLoader only
    return parse_intake_kit(data, origin=path)


def load_intake_kit(path: str | Path | None = None) -> IntakeKit:
    """The validated kit (default: ``<samples>/intake/intake_kit_v1.yaml``), cached per path + mtime."""
    kit_path = Path(path).expanduser().resolve() if path is not None else default_kit_path()
    try:
        stat = kit_path.stat()
    except FileNotFoundError as exc:
        raise IntakeKitNotFound(f"intake kit not found: {kit_path}") from exc
    return _load_cached(str(kit_path), stat.st_mtime_ns, stat.st_size)


def clear_intake_kit_cache() -> None:
    _load_cached.cache_clear()


__all__ = [
    "KIT_RELATIVE_PATH",
    "MANDATORY_KEYS",
    "MAX_RELAY_CONCEPTS",
    "MIN_RELAY_CONCEPTS",
    "PASSAGE_KEYS",
    "SAMPLES_DIR_ENV",
    "STAGE_KEYS",
    "Activity",
    "BandDef",
    "ClaimProbe",
    "DecisionGuide",
    "FollowUp",
    "IntakeKit",
    "IntakeKitError",
    "IntakeKitNotFound",
    "KitOriginal",
    "KitSource",
    "LanguageCheck",
    "LanguageQuestion",
    "MandatoryQuestion",
    "Passage",
    "RelayConcept",
    "ReviewFlag",
    "RubricCompetency",
    "Scenario",
    "ScenarioGroup",
    "ScoreLink",
    "Stage",
    "TopicCard",
    "clear_intake_kit_cache",
    "default_kit_path",
    "intake_samples_dir",
    "load_intake_kit",
    "parse_intake_kit",
]
