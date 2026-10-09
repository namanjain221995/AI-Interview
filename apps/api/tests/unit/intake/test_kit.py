"""Intake kit: loader, structure, coordinator decisions and verbatim fidelity to the DOCX."""

from __future__ import annotations

import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.seed.fairness import fairness_hits
from app.services.intake import scoring
from app.services.intake.kit import (
    MANDATORY_KEYS,
    PASSAGE_KEYS,
    SAMPLES_DIR_ENV,
    STAGE_KEYS,
    IntakeKit,
    IntakeKitError,
    IntakeKitNotFound,
    default_kit_path,
    load_intake_kit,
    parse_intake_kit,
)
from app.services.intake.reading import normalize_tokens

from .docx_source import DocxKit, find_docx, read_docx

# The coordinator's competency table (docs/design/briefs/intake-common.md, decision 2).
BRIEF_COMPETENCIES = [
    ("communication", "Communication & Presentation", "Communication & presentation", "0.20"),
    ("technical_reading", "Technical Reading & Relay", "Doc-reading / proxy ability", "0.20"),
    ("structured_answer", "Structured Answer / Gap-Filling", "Gap-filling / structured answer", "0.15"),
    ("technical", "Actual Technical Ability", "Technical ability (actual)", "0.15"),
    ("trainability", "Trainability & Learning Attitude", "Trainability / learning attitude", "0.15"),
    ("composure", "Confidence & Composure", "Confidence & composure", "0.10"),
    ("adaptability", "Niche-Fit & Adaptability", "Niche-fit / adaptability", "0.05"),
]
MQ1_DEFAULT = (
    "Are you legally authorised to work in {country} for this role?",
    "Will you need sponsorship now or in the future?",
)
MQ4_REFRAMED = (
    "This program prepares people for roles that usually expect around five years of hands-on experience. "
    "Are you comfortable with the responsibilities and skills expected at that level, and can you commit to "
    "the training needed?"
)
STRUCTURED_ANSWER_5 = "Connects all points into a natural flow and handles gaps honestly and gracefully"

# Fields that hold source metadata (kit wording kept for provenance), not text the platform uses.
METADATA_KEYS = frozenset(
    {
        "kit_original", "conduct_kit_original", "kit_text", "kit_title", "kit_label", "kit_scores_into",
        "kit_competencies", "kit_range", "kit_decision", "kit_weight", "kit_note", "suggested_use",
        "excluded_signals", "policy_note", "source",
    }
)  # fmt: skip
BANNED_IN_USABLE_TEXT = (
    "visa", "citizenship", "nationality", "eye contact", "energy", "present your profile", "5-year profile",
    "interview-support", "support model", "proxy", "support hire", "bluff gracefully", "bluffs gaps",
    "nervous", "marketable", "live interview support",
)  # fmt: skip


@pytest.fixture(scope="module")
def kit() -> IntakeKit:
    return load_intake_kit()


@pytest.fixture(scope="module")
def docx() -> DocxKit:
    path = find_docx()
    if path is None:
        pytest.skip("Intake_Interview_Kit.docx not found at the repository root (set INTAKE_KIT_DOCX)")
    return read_docx(path)


@pytest.fixture
def raw() -> dict[str, Any]:
    """A fresh parsed copy of the YAML, for mutation tests."""
    return yaml.safe_load(default_kit_path().read_text(encoding="utf-8"))


def _texts(value: Any, *, skip: frozenset[str] = METADATA_KEYS) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            if key not in skip:
                out.extend(_texts(item, skip=skip))
        return out
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _texts(item, skip=skip)]
    return []


def _originals(value: Any) -> list[str]:
    """Every string kept under a ``kit_original`` block anywhere in the kit."""
    out: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("kit_original", "conduct_kit_original") and isinstance(item, dict):
                out.extend(_texts(item["text"], skip=frozenset()))
            else:
                out.extend(_originals(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_originals(item))
    return out


# ================================================================================================ loading


class TestLoading:
    def test_default_kit_loads_and_is_cached(self) -> None:
        first = load_intake_kit()
        assert first is load_intake_kit()
        assert first.version == "v1"
        assert first.schema_version == 1
        assert first.source.file == "Intake_Interview_Kit.docx"

    def test_explicit_path_and_samples_dir_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        original = load_intake_kit()
        target = tmp_path / "intake" / "intake_kit_v1.yaml"
        target.parent.mkdir()
        shutil.copy(default_kit_path(), target)
        monkeypatch.setenv(SAMPLES_DIR_ENV, str(tmp_path))
        assert default_kit_path() == target.resolve()
        loaded = load_intake_kit()
        assert loaded == load_intake_kit(target)
        assert loaded == original and loaded is not original  # same content, different file

    def test_samples_dir_must_exist(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(SAMPLES_DIR_ENV, str(tmp_path / "missing"))
        with pytest.raises(IntakeKitNotFound):
            load_intake_kit()

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(IntakeKitNotFound):
            load_intake_kit(tmp_path / "nope.yaml")

    def test_cache_follows_file_changes(self, tmp_path: Path, raw: dict[str, Any]) -> None:
        target = tmp_path / "kit.yaml"
        target.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        assert load_intake_kit(target).duration.target_minutes == 45
        raw["duration"]["target_minutes"] = 40
        target.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        assert load_intake_kit(target).duration.target_minutes == 40

    def test_root_must_be_a_mapping(self) -> None:
        with pytest.raises(IntakeKitError, match="mapping"):
            parse_intake_kit(["not", "a", "kit"])

    def test_models_are_frozen(self, kit: IntakeKit) -> None:
        with pytest.raises(ValidationError):
            kit.stages[0].title = "changed"  # type: ignore[misc]
        assert isinstance(kit.passages, tuple)
        assert isinstance(kit.topic_cards[0].bullets, tuple)


# ================================================================================================= stages


class TestStages:
    def test_seven_stages_in_order(self, kit: IntakeKit) -> None:
        assert tuple(s.key for s in kit.stages) == STAGE_KEYS
        assert [s.number for s in kit.stages] == [1, 2, 3, 4, 5, 6, 7]

    def test_kit_timings(self, kit: IntakeKit) -> None:
        minutes = [(s.minutes.min, s.minutes.max) for s in kit.stages]
        assert minutes == [(5, 5), (8, 10), (3, 5), (7, 8), (7, 8), (5, 5), (3, 5)]
        assert (kit.duration.min_minutes, kit.duration.target_minutes, kit.duration.max_minutes) == (
            35,
            45,
            50,
        )
        assert kit.duration.min_minutes <= sum(m[0] for m in minutes) <= sum(m[1] for m in minutes)
        assert sum(m[1] for m in minutes) <= kit.duration.max_minutes

    def test_timers(self, kit: IntakeKit) -> None:
        timers = kit.timers
        assert timers.drill_a_max_seconds == 180
        assert timers.drill_b_read_seconds == 30
        assert (timers.topic_card_prepare_seconds, timers.topic_card_speak_seconds) == (60, 120)
        assert timers.self_intro_seconds == 90

    def test_scores_into(self, kit: IntakeKit) -> None:
        expected = {
            "greeting": ("communication", "composure"),
            "resume": ("technical", "communication", "composure"),
            "mandatory": (),
            "reading": ("technical_reading", "composure", "communication"),
            "topic": ("structured_answer", "communication", "composure", "technical"),
            "learning": ("trainability", "adaptability", "communication"),
            "close": ("composure", "trainability"),
        }
        assert {s.key: s.competencies for s in kit.stages} == expected
        assert kit.stage("reading").primary_competency == "technical_reading"
        assert kit.stage("topic").primary_competency == "structured_answer"
        assert kit.stage("greeting").primary_competency is None

    def test_activities(self, kit: IntakeKit) -> None:
        reading = kit.stage("reading")
        assert [a.key for a in reading.activities] == ["drill_a", "drill_b"]
        assert all(a.material == "passage" and a.required for a in reading.activities)
        assert reading.activity("drill_b").say == (
            "Read this silently for 30 seconds. I'll take it away, then explain it back in your own words, "
            "confidently, without reading.",
        )
        assert kit.stage("topic").activity("topic_card").material == "topic_card"
        with pytest.raises(KeyError):
            reading.activity("drill_c")

    def test_mandatory_stage_holds_all_questions(self, kit: IntakeKit) -> None:
        assert kit.stage("mandatory").mandatory_questions == MANDATORY_KEYS
        assert all(not s.mandatory_questions for s in kit.stages if s.key != "mandatory")

    def test_stage_lookup(self, kit: IntakeKit) -> None:
        assert kit.stage("close").number == 7
        with pytest.raises(KeyError):
            kit.stage("intro")


# ===================================================================================== mandatory questions


class TestMandatoryQuestions:
    def test_order(self, kit: IntakeKit) -> None:
        assert [q.code for q in kit.mandatory_questions] == ["MQ1", "MQ2", "MQ3", "MQ4"]
        assert kit.mandatory_question("MQ3").key == "mq3"
        with pytest.raises(KeyError):
            kit.mandatory_question("mq5")

    def test_mq1_lawful_default(self, kit: IntakeKit) -> None:
        mq1 = kit.mandatory_question("mq1")
        assert mq1.ask == MQ1_DEFAULT
        assert mq1.policy_configured is True
        assert mq1.send_to_model is False
        assert mq1.used_in_scoring is False
        assert mq1.informs == ()
        assert mq1.follow_ups == ()
        assert mq1.placeholders == ("country",)
        assert mq1.review_flags == ("work_authorization_review",)
        assert mq1.render_ask(country="Canada") == (
            "Are you legally authorised to work in Canada for this role?",
            "Will you need sponsorship now or in the future?",
        )
        with pytest.raises(ValueError, match="country"):
            mq1.render_ask()

    def test_mq1_original_is_metadata_only(self, kit: IntakeKit) -> None:
        mq1 = kit.mandatory_question("mq1")
        assert mq1.kit_original is not None and mq1.kit_original.not_for_use is True
        assert mq1.kit_original.text["ask"] == (
            "What is your current work-authorization / visa status, and how long is it valid?"
        )
        usable = " ".join(_texts(mq1.model_dump())).lower()
        for word in ("visa", "citizenship", "nationality", "expire"):
            assert word not in usable

    def test_mq4_reframed_without_misrepresentation(self, kit: IntakeKit) -> None:
        mq4 = kit.mandatory_question("mq4")
        assert mq4.ask == (MQ4_REFRAMED,)
        follow_ups = [f.text for f in mq4.follow_ups]
        assert "How do you feel about our interview-support model?" not in follow_ups
        assert all("5-year profile" not in text for text in follow_ups)
        assert any("years of hands-on experience" in text for text in follow_ups)  # records real experience
        usable = " ".join(_texts(mq4.model_dump()))
        assert "We present your profile" not in usable
        assert "never inflates" in mq4.purpose
        original = mq4.kit_original
        assert original is not None and original.not_for_use is True
        assert str(original.text["ask"]).startswith("We present your profile with around 5 years")
        assert "How do you feel about our interview-support model?" in original.text["follow_ups"]

    def test_mq2_mq3(self, kit: IntakeKit) -> None:
        mq2, mq3 = kit.mandatory_question("mq2"), kit.mandatory_question("mq3")
        pm = [f for f in mq2.follow_ups if f.when]
        assert len(pm) == 1 and pm[0].when == "If PM"
        assert mq3.informs == ("technical",)
        assert mq3.review_flags == ("claims_not_supported",)
        assert [f.instruction for f in mq3.follow_ups] == [False, False, False, False, True]
        assert all(q.send_to_model and q.used_in_scoring for q in (mq2, mq3, kit.mandatory_question("mq4")))


# ================================================================================== language verification


class TestLanguageVerification:
    def test_table(self, kit: IntakeKit) -> None:
        languages = kit.language_verification.languages
        assert [lang.label for lang in languages] == [
            "Python",
            "Java",
            "JavaScript / Node",
            "C# / .NET",
            "SQL",
        ]
        assert all(
            len(lang.questions) == 2 and all(q.key_points for q in lang.questions) for lang in languages
        )
        assert kit.language_verification.expected_from_rating == 6

    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("Python", "python"), ("  python3 ", "python"), ("Node.js", "javascript"), ("TypeScript", "javascript"),
            ("C#", "csharp"), (".NET", "csharp"), ("c sharp", "csharp"), ("Postgres", "sql"), ("SQL Server", "sql"),
            ("Java", "java"),
        ],
    )  # fmt: skip
    def test_lookup_by_alias(self, kit: IntakeKit, name: str, key: str) -> None:
        found = kit.language_check(name)
        assert found is not None and found.key == key

    def test_unknown_language(self, kit: IntakeKit) -> None:
        assert kit.language_check("COBOL") is None


# ======================================================================================= banks and résumé


class TestBanks:
    def test_resume_framework(self, kit: IntakeKit) -> None:
        framework = kit.resume_framework
        assert [s.key for s in framework.depth_ladder] == ["ownership", "mechanics", "stress"]
        assert [p.key for p in framework.claim_probes] == [
            "tool_or_framework", "metric_improvement", "project_or_product", "leadership", "thin_or_vague",
        ]  # fmt: skip
        assert len(framework.honesty_signals) == 5

    def test_probe_bank(self, kit: IntakeKit) -> None:
        assert [c.key for c in kit.probe_bank.categories] == [
            "depth_mechanics", "ownership", "honesty_edges", "trade_offs", "trainability", "application",
        ]  # fmt: skip
        assert sum(len(c.probes) for c in kit.probe_bank.categories) == 16

    def test_scenario_bank(self, kit: IntakeKit) -> None:
        bank = kit.scenario_bank
        assert [(g.key, len(g.scenarios)) for g in bank.groups] == [("A", 3), ("B", 4), ("C", 2), ("D", 3)]
        assert [g.competencies for g in bank.groups] == [
            ("composure", "communication"), ("technical", "structured_answer"), ("trainability",),
            ("composure", "trainability"),
        ]  # fmt: skip
        assert bank.use_in_stages == ("reading", "topic", "learning")
        assert (bank.per_interview.min, bank.per_interview.max) == (2, 3)
        assert bank.scenario("B3").prompt.startswith("Your email classifier is 90% accurate")
        with pytest.raises(KeyError):
            bank.scenario("E1")


# ================================================================================================= rubric


class TestRubric:
    def test_competencies_match_the_brief(self, kit: IntakeKit) -> None:
        actual = [(c.key, c.label, c.kit_label, c.weight) for c in kit.rubric.competencies]
        assert actual == [(k, label, kit_label, Decimal(w)) for k, label, kit_label, w in BRIEF_COMPETENCIES]

    def test_weights_sum_to_one(self, kit: IntakeKit) -> None:
        assert sum(c.weight for c in kit.rubric.competencies) == Decimal("1.00")

    def test_matches_scoring_module(self, kit: IntakeKit) -> None:
        assert [(c.key, c.label, c.kit_label, c.weight) for c in kit.rubric.competencies] == [
            (c.key, c.label, c.kit_label, c.weight) for c in scoring.INTAKE_COMPETENCIES
        ]

    def test_anchor_levels(self, kit: IntakeKit) -> None:
        assert all(set(c.anchors) == {1, 3, 5} for c in kit.rubric.competencies)

    def test_sanitised_anchors(self, kit: IntakeKit) -> None:
        assert kit.competency("structured_answer").anchors[5] == STRUCTURED_ANSWER_5
        banned = ("confident", "nervous", "energy", "engaging", "calm", "eye contact", "accent", "bluff")
        for key in ("communication", "composure", "structured_answer"):
            competency = kit.competency(key)
            text = " ".join([*competency.anchors.values(), competency.description]).lower()
            assert not [word for word in banned if word in text], key
            assert competency.kit_original is not None and competency.kit_original.not_for_use

    def test_composure_excludes_affect(self, kit: IntakeKit) -> None:
        excluded = set(kit.competency("composure").excluded_signals)
        assert {"emotion", "voice affect", "energy", "eye contact", "facial analysis"} <= excluded
        assert {"accent", "appearance"} <= set(kit.competency("communication").excluded_signals)
        assert {"pronunciation", "accent"} <= set(kit.competency("technical_reading").excluded_signals)

    def test_technical_reading_components(self, kit: IntakeKit) -> None:
        components = {c.key: c.weight for c in kit.competency("technical_reading").components}
        assert components == {"drill_a": scoring.DRILL_A_WEIGHT, "drill_b": scoring.DRILL_B_WEIGHT}

    def test_lookup(self, kit: IntakeKit) -> None:
        with pytest.raises(KeyError):
            kit.competency("charisma")


# ========================================================================================= decision guide


class TestDecisionGuide:
    def test_bands_half_open_without_gaps(self, kit: IntakeKit) -> None:
        bands = kit.decision_guide.bands
        assert [(b.key, b.min_inclusive, b.max_exclusive) for b in bands] == [
            ("strong_accept", Decimal("4.20"), None),
            ("accept_with_notes", Decimal("3.30"), Decimal("4.20")),
            ("borderline", Decimal("2.50"), Decimal("3.30")),
            ("reject", None, Decimal("2.50")),
        ]
        assert [(b.min_inclusive, b.key) for b in bands[:3]] == [
            (minimum, band.value) for minimum, band in scoring.BAND_THRESHOLDS
        ]
        assert kit.band("borderline").kit_range == "2.5 – 3.2"

    def test_insufficient_evidence_and_human_decisions(self, kit: IntakeKit) -> None:
        guide = kit.decision_guide
        assert guide.insufficient_evidence.key == "insufficient_evidence"
        assert [d.key for d in guide.human_decisions] == [
            "accept", "accept_with_notes", "second_interview", "reject", "request_evidence",
        ]  # fmt: skip
        assert "recommendation" in guide.note


# =========================================================================================== review flags


class TestReviewFlags:
    def test_never_automatic(self, kit: IntakeKit) -> None:
        flags = kit.review_flags.flags
        assert [f.key for f in flags] == [
            "claims_not_supported", "reading_not_completed", "work_authorization_review", "mandatory_mismatch",
            "communication_unclear",
        ]  # fmt: skip
        assert all(f.automatic_rejection is False and f.evidence for f in flags)
        assert "never" in kit.review_flags.note

    def test_every_kit_red_flag_is_a_review_item(self, kit: IntakeKit, docx: DocxKit) -> None:
        start = docx.paragraphs.index(
            "Any one of these overrides the score — move the candidate to reject or re-interview regardless of the total:"
        )
        red_flags = docx.paragraphs[start + 1 : start + 5]
        assert set(red_flags) == {f.kit_text for f in kit.review_flags.flags}

    def test_lookup(self, kit: IntakeKit) -> None:
        assert kit.review_flag("reading_not_completed").sources == ("reading",)
        with pytest.raises(KeyError):
            kit.review_flag("auto_reject")


# ================================================================================== passages and topic cards


class TestPassagesAndCards:
    def test_passages(self, kit: IntakeKit) -> None:
        assert tuple(p.key for p in kit.passages) == PASSAGE_KEYS
        assert [p.level for p in kit.passages] == [
            "easy",
            "intermediate",
            "intermediate",
            "advanced",
            "advanced",
            "intermediate",
        ]
        assert [p.key for p in kit.passages_for("read_aloud")] == ["A", "B", "D", "E", "F"]
        assert [p.key for p in kit.passages_for("relay")] == ["A", "B", "C", "E", "F"]
        assert kit.passage("passage c").title == "Overfitting"
        assert kit.passage("E").text == "\n\n".join(kit.passage("E").paragraphs)
        with pytest.raises(KeyError):
            kit.passage("G")

    @pytest.mark.parametrize("key", PASSAGE_KEYS)
    def test_relay_concepts_and_terms(self, kit: IntakeKit, key: str) -> None:
        passage = kit.passage(key)
        assert 5 <= len(passage.relay_concepts) <= 8
        assert len({c.key for c in passage.relay_concepts}) == len(passage.relay_concepts)
        words = normalize_tokens(passage.text)
        for term in passage.terms:  # every listed term occurs in the passage
            tokens = normalize_tokens(term)
            assert any(words[i : i + len(tokens)] == tokens for i in range(len(words))), term

    def test_topic_cards(self, kit: IntakeKit) -> None:
        assert [c.number for c in kit.topic_cards] == list(range(1, 9))
        assert all(len(c.bullets) == 4 for c in kit.topic_cards)
        assert {c.type for c in kit.topic_cards} == {
            "compare_decide", "explain_example", "personal_reasoning", "argue_position", "personal_narrative",
        }  # fmt: skip
        assert kit.topic_card(3) == kit.topic_card("Card 3") == kit.topic_card("3")
        with pytest.raises(KeyError):
            kit.topic_card(9)

    def test_selection_index_covers_everything(self, kit: IntakeKit) -> None:
        refs = [row.ref for row in kit.selection_index.rows]
        assert refs == [*PASSAGE_KEYS, *[str(n) for n in range(1, 9)]]


# ==================================================================================== fidelity to the DOCX


def _minutes(cell: str) -> tuple[int, int]:
    value = cell.removesuffix(" min")
    low, _, high = value.partition("–")
    return int(low), int(high or low)


def _verbatim_strings(kit: IntakeKit) -> list[tuple[str, str]]:
    """(where, text) pairs the kit claims are kit wording: each must occur verbatim in the DOCX."""
    out: list[tuple[str, str]] = [
        ("source.title", kit.source.title),
        ("source.subtitle", kit.source.subtitle),
        ("duration.kit_note", kit.duration.kit_note),
    ]
    out += [(f"conduct[{i}]", text) for i, text in enumerate(kit.conduct) if i > 0]
    for stage in kit.stages:
        replaced = set(stage.kit_original.text) if stage.kit_original else set()
        out.append((f"{stage.key}.kit_title", stage.kit_title))
        if "measures" not in replaced:
            out.append((f"{stage.key}.measures", stage.measures))
        out += [(f"{stage.key}.say", f'"{line}"') for line in stage.say]
        if "do" not in replaced:
            out += [(f"{stage.key}.do", line) for line in stage.do]
        if "watch_for" not in replaced:
            out += [(f"{stage.key}.watch_for", item) for item in stage.watch_for]
        for follow_up in stage.follow_ups:
            out.append((f"{stage.key}.follow_up", f'"{follow_up.text}"'))
            if follow_up.purpose:
                out.append((f"{stage.key}.follow_up.purpose", f"({follow_up.purpose})"))
        if stage.kit_scores_into:
            out.append((f"{stage.key}.kit_scores_into", f"Scores into: {stage.kit_scores_into}"))
        for activity in stage.activities:
            if activity.key == "topic_card":
                continue  # platform activity: the kit text lives in the stage
            activity_replaced = set(activity.kit_original.text) if activity.kit_original else set()
            out.append((f"{activity.key}.kit_title", activity.kit_title))
            out += [(f"{activity.key}.say", f'"{line}"') for line in activity.say]
            out += [(f"{activity.key}.do", line) for line in activity.do]
            if "watch_for" not in activity_replaced:
                out += [(f"{activity.key}.watch_for", item) for item in activity.watch_for]
    for question in kit.mandatory_questions:
        replaced = set(question.kit_original.text) if question.kit_original else set()
        out.append((f"{question.key}.kit_title", question.kit_title))
        if "ask" not in replaced:
            out += [(f"{question.key}.ask", f'Ask: "{line}"') for line in question.ask]
        if "purpose" not in replaced:
            out.append((f"{question.key}.purpose", f"Purpose: {question.purpose}"))
        if "record_flag" not in replaced:
            out.append((f"{question.key}.record_flag", f"Record & flag: {question.record} {question.flag}"))
        if "follow_ups" not in replaced:
            for follow_up in question.follow_ups:
                out.append(
                    (
                        f"{question.key}.follow_up",
                        follow_up.text if follow_up.instruction else f'"{follow_up.text}"',
                    )
                )
                if follow_up.when:
                    out.append((f"{question.key}.follow_up.when", f"({follow_up.when})"))
                if follow_up.purpose:
                    out.append((f"{question.key}.follow_up.purpose", follow_up.purpose))
        if question.follow_ups_note:
            out.append((f"{question.key}.follow_ups_note", f"({question.follow_ups_note})"))
    out.append(("language_verification.instruction", kit.language_verification.instruction))
    framework = kit.resume_framework
    out.append(("resume.instruction", framework.instruction))
    out.append(("resume.honesty_signals_title", framework.honesty_signals_title))
    out += [(f"resume.ladder.{s.key}", f'{s.label} — "{s.question}"') for s in framework.depth_ladder]
    out += [
        (f"resume.claim.{p.key}", f'{p.claim}: "{" ".join(p.questions)}"') for p in framework.claim_probes
    ]
    out += [("resume.honesty_signal", signal) for signal in framework.honesty_signals]
    out.append(("probe_bank.instruction", kit.probe_bank.instruction))
    for category in kit.probe_bank.categories:
        probes = "  ·  ".join(f'"{probe}"' for probe in category.probes)
        out.append((f"probe_bank.{category.key}", f"{category.label} — {probes}"))
    out.append(("scenario_bank.instruction", kit.scenario_bank.instruction))
    for group in kit.scenario_bank.groups:
        out.append((f"scenario.{group.key}.title", f"{group.key}. {group.title}"))
        out.append((f"scenario.{group.key}.competencies", f"[{group.kit_competencies}]"))
        for scenario in group.scenarios:
            out.append((f"scenario.{scenario.key}.prompt", f"Scenario:  {scenario.prompt}"))
            out.append((f"scenario.{scenario.key}.tests", f"Tests: {scenario.tests}"))
            out.append((f"scenario.{scenario.key}.strong", f"Strong answer: {scenario.strong}"))
            out.append((f"scenario.{scenario.key}.weak", f"Weak answer: {scenario.weak}"))
    out.append(("rubric.instruction", kit.rubric.instruction))
    out += [(f"review_flag.{f.key}", f.kit_text) for f in kit.review_flags.flags]
    out.append(("selection_index.instruction", kit.selection_index.instruction))
    out.append(("selection_index.notes[1]", kit.selection_index.notes[1]))
    return out


class TestDocxFidelity:
    def test_sha256_pins_the_source(self, kit: IntakeKit, docx: DocxKit) -> None:
        assert docx.sha256 == kit.source.sha256

    def test_passages_verbatim(self, kit: IntakeKit, docx: DocxKit) -> None:
        tables = docx.single_cell_tables("PASSAGE ")
        assert len(tables) == 6
        for passage, cell in zip(kit.passages, tables, strict=True):
            assert passage.header == cell[0]
            assert passage.title == cell[1]
            assert passage.paragraphs == cell[2:]

    def test_topic_cards_verbatim(self, kit: IntakeKit, docx: DocxKit) -> None:
        tables = docx.single_cell_tables("TOPIC CARD ")
        assert len(tables) == 8
        for card, cell in zip(kit.topic_cards, tables, strict=True):
            assert len(cell) == 7  # header, title, instruction, exactly 4 bullets
            assert (card.header, card.title, card.instruction) == cell[:3]
            assert card.bullets == cell[3:]

    def test_selection_index_table(self, kit: IntakeKit, docx: DocxKit) -> None:
        table = docx.table_starting("Item")
        rows = [tuple(cell[0] for cell in row) for row in table[1:]]
        assert rows == [(r.item, r.topic, r.level_type, r.suggested_use) for r in kit.selection_index.rows]

    def test_interview_at_a_glance_table(self, kit: IntakeKit, docx: DocxKit) -> None:
        table = docx.table_starting("#")
        for stage, row in zip(kit.stages, table[1:], strict=True):
            number, title, time, measures = (cell[0] for cell in row)
            assert int(number) == stage.number
            assert title == stage.kit_title
            assert _minutes(time) == (stage.minutes.min, stage.minutes.max)
            original = stage.kit_original.text.get("measures") if stage.kit_original else None
            assert measures == (original or stage.measures)

    def test_language_table(self, kit: IntakeKit, docx: DocxKit) -> None:
        table = docx.table_starting("Language")
        for language, row in zip(kit.language_verification.languages, table[1:], strict=True):
            assert row[0][0] == language.label
            assert row[1][0] == "  ·  ".join(f'"{q.text}"' for q in language.questions)

    def test_rubric_table(self, kit: IntakeKit, docx: DocxKit) -> None:
        table = docx.table_starting("Competency (weight)")
        for competency, row in zip(kit.rubric.competencies, table[1:], strict=True):
            label, weight, one, three, five = (cell[0] for cell in row)
            assert (label, weight) == (competency.kit_label, competency.kit_weight)
            original = competency.kit_original.text.get("anchors") if competency.kit_original else None
            expected = (
                tuple(original)
                if original
                else (competency.anchors[1], competency.anchors[3], competency.anchors[5])
            )
            assert (one, three, five) == expected

    def test_band_table(self, kit: IntakeKit, docx: DocxKit) -> None:
        table = docx.table_starting("Weighted total (1–5)")
        rows = [(row[0][0], row[1][0]) for row in table[1:]]
        assert rows == [(b.kit_range, b.kit_decision) for b in kit.decision_guide.bands]

    def test_kit_wording_is_verbatim(self, kit: IntakeKit, docx: DocxKit) -> None:
        text = docx.all_text
        missing = [(where, value) for where, value in _verbatim_strings(kit) if value not in text]
        assert not missing

    def test_kit_originals_are_verbatim(self, kit: IntakeKit, docx: DocxKit) -> None:
        originals = _originals(kit.model_dump())
        assert len(originals) >= 25
        text = docx.all_text
        assert [value for value in originals if value not in text] == []


# ========================================================================================== sanitisation


class TestSanitisation:
    def test_usable_text_has_no_banned_framing(self, kit: IntakeKit) -> None:
        texts = _texts(kit.model_dump())
        hits = [(phrase, t) for t in texts for phrase in BANNED_IN_USABLE_TEXT if phrase in t.lower()]
        assert hits == []

    def test_pronunciation_only_as_not_assessed(self, kit: IntakeKit) -> None:
        for text in _texts(kit.model_dump()):
            if "pronunciation" in text.lower():
                assert "not assessed" in text.lower() or "never" in text.lower(), text

    def test_candidate_facing_text_passes_the_fairness_lint(self, kit: IntakeKit) -> None:
        texts: list[str] = []
        for stage in kit.stages:
            texts += [*stage.say, *(f.text for f in stage.follow_ups)]
            for activity in stage.activities:
                texts += activity.say
        for question in kit.mandatory_questions:
            if question.key == "mq1":
                continue  # the coordinator-approved lawful wording: policy-gated, never sent to a model
            texts += [*question.ask, *(f.text for f in question.follow_ups)]
        texts += [q.text for lang in kit.language_verification.languages for q in lang.questions]
        texts += [s.question for s in kit.resume_framework.depth_ladder]
        texts += [q for p in kit.resume_framework.claim_probes for q in p.questions]
        texts += [p for c in kit.probe_bank.categories for p in c.probes]
        texts += [s.prompt for g in kit.scenario_bank.groups for s in g.scenarios]
        for passage in kit.passages:
            texts += [passage.title, *passage.paragraphs]
        for card in kit.topic_cards:
            texts += [card.title, card.instruction, *card.bullets]
        assert len(texts) > 150
        assert [hit.describe() for text in texts for hit in fairness_hits(text)] == []

    def test_kit_originals_are_flagged_not_for_use(self, kit: IntakeKit) -> None:
        dump = kit.model_dump()

        def walk(value: Any) -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("kit_original", "conduct_kit_original") and item is not None:
                        found.append(item)
                    else:
                        found += walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    found += walk(item)
            return found

        blocks = walk(dump)
        assert len(blocks) >= 12
        assert all(block["not_for_use"] is True and block["reason"] for block in blocks)


# ============================================================================================ validation


def _invalid(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(IntakeKitError, match=match):
        parse_intake_kit(raw)


class TestValidation:
    def test_the_shipped_kit_round_trips(self, raw: dict[str, Any]) -> None:
        assert parse_intake_kit(raw) == load_intake_kit()

    def test_weight_change_rejected(self, raw: dict[str, Any]) -> None:
        raw["rubric"]["competencies"][0]["weight"] = "0.25"
        raw["rubric"]["competencies"][0]["kit_weight"] = "25%"
        _invalid(raw, "INTAKE_COMPETENCIES")

    def test_kit_weight_must_agree(self, raw: dict[str, Any]) -> None:
        raw["rubric"]["competencies"][6]["kit_weight"] = "6%"
        _invalid(raw, "kit weight")

    def test_label_change_rejected(self, raw: dict[str, Any]) -> None:
        raw["rubric"]["competencies"][1]["label"] = "Doc-reading / proxy ability"
        _invalid(raw, "INTAKE_COMPETENCIES")

    def test_band_threshold_change_rejected(self, raw: dict[str, Any]) -> None:
        raw["decision_guide"]["bands"][0]["min_inclusive"] = "4.25"
        raw["decision_guide"]["bands"][1]["max_exclusive"] = "4.25"
        _invalid(raw, "BAND_THRESHOLDS")

    def test_band_gap_rejected(self, raw: dict[str, Any]) -> None:
        raw["decision_guide"]["bands"][2]["max_exclusive"] = "3.25"
        _invalid(raw, "no gaps")

    def test_stage_order_enforced(self, raw: dict[str, Any]) -> None:
        stages = raw["stages"]
        stages[0], stages[1] = stages[1], stages[0]
        _invalid(raw, "in this order")

    def test_passage_removed(self, raw: dict[str, Any]) -> None:
        raw["passages"].pop()
        _invalid(raw, "passages must be A-F")

    def test_card_needs_four_bullets(self, raw: dict[str, Any]) -> None:
        raw["topic_cards"][0]["bullets"].pop()
        _invalid(raw, "bullets")

    def test_relay_concept_count(self, raw: dict[str, Any]) -> None:
        raw["passages"][0]["relay_concepts"] = raw["passages"][0]["relay_concepts"][:4]
        _invalid(raw, "relay concepts")

    def test_private_question_cannot_be_scored(self, raw: dict[str, Any]) -> None:
        raw["mandatory_questions"][0]["used_in_scoring"] = True
        _invalid(raw, "kept from models")

    def test_placeholder_must_be_used(self, raw: dict[str, Any]) -> None:
        raw["mandatory_questions"][0]["ask"][0] = "Are you legally authorised to work here for this role?"
        _invalid(raw, "placeholder")

    def test_kit_original_must_be_not_for_use(self, raw: dict[str, Any]) -> None:
        raw["mandatory_questions"][3]["kit_original"]["not_for_use"] = False
        _invalid(raw, "not_for_use")

    def test_review_flags_cannot_reject(self, raw: dict[str, Any]) -> None:
        raw["review_flags"]["flags"][0]["automatic_rejection"] = True
        _invalid(raw, "automatic_rejection")

    def test_unknown_competency(self, raw: dict[str, Any]) -> None:
        raw["stages"][0]["scores_into"][0]["competency"] = "charisma"
        _invalid(raw, "competency")

    def test_selection_index_must_agree(self, raw: dict[str, Any]) -> None:
        raw["selection_index"]["rows"][3]["suggested_use"] = "Drill B"
        _invalid(raw, "selection index row for passage D")

    def test_unknown_fields_rejected(self, raw: dict[str, Any]) -> None:
        raw["stages"][0]["surprise"] = True
        _invalid(raw, "surprise")

    def test_unknown_review_flag_reference(self, raw: dict[str, Any]) -> None:
        raw["mandatory_questions"][1]["review_flags"] = ["auto_reject"]
        _invalid(raw, "unknown review flag")
