"""Intake scoring: weights, exact weighted totals, half-open decision bands, drill mix, conversions."""

from __future__ import annotations

import itertools
import math
from decimal import Decimal

import pytest

from app.services.intake.scoring import (
    ACCEPT_WITH_NOTES_MIN,
    BAND_LABELS,
    BORDERLINE_MIN,
    DRILL_A_WEIGHT,
    DRILL_B_WEIGHT,
    INTAKE_COMPETENCIES,
    INTAKE_COMPETENCY_KEYS,
    INTAKE_LABELS,
    INTAKE_WEIGHTS,
    STRONG_ACCEPT_MIN,
    DecisionBand,
    decision_band,
    normalized_100,
    partial_weighted_total,
    summarize_scores,
    technical_reading_score,
    to_score,
    weighted_total,
)

KEYS = INTAKE_COMPETENCY_KEYS


def scores(default: object = 4, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {key: default for key in KEYS}
    values.update(overrides)
    return values


class TestCompetencies:
    def test_table(self) -> None:
        assert [(c.key, c.label, c.kit_label, c.weight) for c in INTAKE_COMPETENCIES] == [
            (
                "communication",
                "Communication & Presentation",
                "Communication & presentation",
                Decimal("0.20"),
            ),
            (
                "technical_reading",
                "Technical Reading & Relay",
                "Doc-reading / proxy ability",
                Decimal("0.20"),
            ),
            (
                "structured_answer",
                "Structured Answer / Gap-Filling",
                "Gap-filling / structured answer",
                Decimal("0.15"),
            ),
            ("technical", "Actual Technical Ability", "Technical ability (actual)", Decimal("0.15")),
            (
                "trainability",
                "Trainability & Learning Attitude",
                "Trainability / learning attitude",
                Decimal("0.15"),
            ),
            ("composure", "Confidence & Composure", "Confidence & composure", Decimal("0.10")),
            ("adaptability", "Niche-Fit & Adaptability", "Niche-fit / adaptability", Decimal("0.05")),
        ]

    def test_weights_sum_to_exactly_one(self) -> None:
        assert sum(INTAKE_WEIGHTS.values()) == Decimal("1.00")
        assert all(isinstance(w, Decimal) for w in INTAKE_WEIGHTS.values())

    def test_mappings_are_read_only(self) -> None:
        with pytest.raises(TypeError):
            INTAKE_WEIGHTS["communication"] = Decimal("0.5")  # type: ignore[index]
        assert INTAKE_LABELS["composure"] == "Confidence & Composure"


class TestWeightedTotal:
    def test_all_same_score(self) -> None:
        for value in (1, 2, 3, 4, 5):
            assert weighted_total(scores(value)) == Decimal(value)

    def test_exact_formula(self) -> None:
        values = {
            "communication": 4, "technical_reading": Decimal("3.4"), "structured_answer": 3, "technical": 2,
            "trainability": 5, "composure": 4, "adaptability": 1,
        }  # fmt: skip
        expected = (
            Decimal("0.20") * 4 + Decimal("0.20") * Decimal("3.4") + Decimal("0.15") * 3 + Decimal("0.15") * 2
            + Decimal("0.15") * 5 + Decimal("0.10") * 4 + Decimal("0.05") * 1
        )  # fmt: skip
        assert weighted_total(values) == expected == Decimal("3.43")

    def test_maximum_is_five(self) -> None:
        assert weighted_total(scores(5)) == Decimal(5)

    def test_decimal_exactness_where_floats_drift(self) -> None:
        values = scores(4, communication=5)  # exactly 4.20
        total = weighted_total(values)
        assert total == Decimal("4.20")
        assert decision_band(total) == DecisionBand.STRONG_ACCEPT
        float_total = sum(float(INTAKE_WEIGHTS[k]) * v for k, v in values.items())  # type: ignore[misc]
        assert math.isclose(float_total, 4.2)  # binary floats only approximate the boundary

    def test_accepts_strings_and_floats(self) -> None:
        assert weighted_total(scores("4")) == Decimal(4)
        assert weighted_total(scores(4.0)) == Decimal(4)
        assert weighted_total(scores(4, technical_reading=3.4)) == Decimal("3.88")

    def test_missing_competency_is_insufficient_evidence(self) -> None:
        values = scores(4)
        del values["adaptability"]
        assert weighted_total(values) is None
        assert weighted_total(scores(4, technical_reading=None)) is None

    def test_unknown_competency_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown competencies: charisma"):
            weighted_total(scores(4, charisma=5))

    @pytest.mark.parametrize("bad", [0, Decimal("0.99"), 5.01, 6, -1, "5.5"])
    def test_scores_outside_scale_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="1-5"):
            weighted_total(scores(4, communication=bad))

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", Decimal("NaN"), "Infinity"])
    def test_non_numbers_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError):
            weighted_total(scores(4, communication=bad))

    def test_booleans_and_objects_rejected(self) -> None:
        with pytest.raises(TypeError):
            weighted_total(scores(4, communication=True))
        with pytest.raises(TypeError):
            weighted_total(scores(4, communication=[4]))

    def test_custom_weights(self) -> None:
        weights = {key: Decimal("0.1") for key in KEYS}
        weights["communication"] = Decimal("0.4")
        assert weighted_total(scores(3), weights=weights) == Decimal(3)
        assert weighted_total(scores(3, communication=5), weights=weights) == Decimal("3.8")
        with pytest.raises(ValueError, match="sum to exactly 1"):
            weighted_total(scores(3), weights={key: Decimal("0.1") for key in KEYS})
        with pytest.raises(ValueError, match="positive"):
            weighted_total({"a": 3, "b": 3}, weights={"a": Decimal("1.5"), "b": Decimal("-0.5")})


class TestPartialTotal:
    def test_renormalised_over_scored_competencies(self) -> None:
        values = scores(4, technical_reading=None, adaptability=None)
        scored_weight = Decimal("0.75")
        expected = sum(INTAKE_WEIGHTS[k] * 4 for k in KEYS if values[k] is not None) / scored_weight
        assert partial_weighted_total(values) == expected == Decimal(4)

    def test_mixed_values(self) -> None:
        values = {"communication": 5, "composure": 2}
        assert partial_weighted_total(values) == (Decimal("0.20") * 5 + Decimal("0.10") * 2) / Decimal("0.30")

    def test_equals_full_total_when_complete(self) -> None:
        values = scores(3, communication=5, technical=2)
        assert partial_weighted_total(values) == weighted_total(values)

    def test_nothing_scored(self) -> None:
        assert partial_weighted_total({}) is None
        assert partial_weighted_total(scores(None)) is None


class TestDecisionBand:
    @pytest.mark.parametrize(
        ("total", "band"),
        [
            ("5", DecisionBand.STRONG_ACCEPT),
            ("4.2", DecisionBand.STRONG_ACCEPT),
            ("4.20", DecisionBand.STRONG_ACCEPT),
            ("4.1999", DecisionBand.ACCEPT_WITH_NOTES),
            ("4.15", DecisionBand.ACCEPT_WITH_NOTES),  # inside the kit's printed gap (4.1 / 4.2)
            ("3.3", DecisionBand.ACCEPT_WITH_NOTES),
            ("3.2999", DecisionBand.BORDERLINE),
            ("3.25", DecisionBand.BORDERLINE),  # inside the kit's printed gap (3.2 / 3.3)
            ("2.5", DecisionBand.BORDERLINE),
            ("2.4999", DecisionBand.REJECT),
            ("1", DecisionBand.REJECT),
        ],
    )
    def test_half_open_boundaries(self, total: str, band: DecisionBand) -> None:
        assert decision_band(Decimal(total)) == band
        assert decision_band(total) == band
        assert decision_band(float(total)) == band

    def test_no_rounding_before_banding(self) -> None:
        assert decision_band(Decimal("4.19999999999999999999")) == DecisionBand.ACCEPT_WITH_NOTES
        assert decision_band(4.199999999999999) == DecisionBand.ACCEPT_WITH_NOTES
        assert decision_band(Decimal("2.49999999999999999999")) == DecisionBand.REJECT

    def test_thresholds(self) -> None:
        assert (STRONG_ACCEPT_MIN, ACCEPT_WITH_NOTES_MIN, BORDERLINE_MIN) == (
            Decimal("4.20"), Decimal("3.30"), Decimal("2.50"),
        )  # fmt: skip

    def test_every_total_has_exactly_one_band(self) -> None:
        bands = []
        for step in range(100, 501):
            total = Decimal(step) / 100
            bands.append(decision_band(total))
        changes = [i for i in range(1, len(bands)) if bands[i] != bands[i - 1]]
        assert [Decimal(100 + i) / 100 for i in changes] == [Decimal("2.5"), Decimal("3.3"), Decimal("4.2")]

    def test_none_is_insufficient_evidence(self) -> None:
        assert decision_band(None) == DecisionBand.INSUFFICIENT_EVIDENCE

    @pytest.mark.parametrize("bad", [Decimal("0.99"), Decimal("5.0001"), 84, -1])
    def test_out_of_scale_total_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="1-5"):
            decision_band(bad)  # type: ignore[arg-type]

    def test_exact_competency_combinations_on_boundaries(self) -> None:
        assert weighted_total(scores(4, communication=5)) == Decimal("4.20")
        assert decision_band(weighted_total(scores(4, communication=5))) == DecisionBand.STRONG_ACCEPT
        on_330 = scores(3, communication=4, adaptability=5)
        assert weighted_total(on_330) == Decimal("3.30")
        assert decision_band(weighted_total(on_330)) == DecisionBand.ACCEPT_WITH_NOTES
        on_250 = scores(2, communication=3, technical_reading=3, composure=3)
        assert weighted_total(on_250) == Decimal("2.50")
        assert decision_band(weighted_total(on_250)) == DecisionBand.BORDERLINE

    def test_monotone_in_every_competency(self) -> None:
        order = list(DecisionBand)[:4][::-1]  # reject < borderline < accept_with_notes < strong_accept
        for base, key in itertools.product((1, 2, 3, 4), KEYS):
            lower = decision_band(weighted_total(scores(base)))
            higher = decision_band(weighted_total(scores(base, **{key: base + 1})))
            assert order.index(higher) >= order.index(lower)

    def test_labels(self) -> None:
        assert BAND_LABELS[DecisionBand.ACCEPT_WITH_NOTES] == "Accept with notes"
        assert set(BAND_LABELS) == set(DecisionBand)


class TestTechnicalReading:
    def test_documented_mix(self) -> None:
        assert (DRILL_A_WEIGHT, DRILL_B_WEIGHT) == (Decimal("0.4"), Decimal("0.6"))
        assert technical_reading_score(4, 3) == Decimal("3.4")
        assert technical_reading_score(3, 4) == Decimal("3.6")  # relay weighs more
        assert technical_reading_score(5, 5) == Decimal(5)
        assert technical_reading_score(1, 1) == Decimal(1)
        assert technical_reading_score("2.5", Decimal("4.5")) == Decimal("3.70")

    def test_missing_drill_is_insufficient(self) -> None:
        assert technical_reading_score(None, 4) is None
        assert technical_reading_score(4, None) is None

    def test_drill_scale_enforced(self) -> None:
        with pytest.raises(ValueError, match="drill_a"):
            technical_reading_score(0, 3)
        with pytest.raises(ValueError, match="drill_b"):
            technical_reading_score(3, 6)

    def test_feeds_the_weighted_total(self) -> None:
        mixed = technical_reading_score(5, 3)
        assert mixed == Decimal("3.8")
        assert weighted_total(scores(4, technical_reading=mixed)) == Decimal("3.96")


class TestNormalized:
    @pytest.mark.parametrize(("total", "expected"), [("5", "100"), ("4.2", "84"), ("2.5", "50"), ("1", "20")])
    def test_conversion(self, total: str, expected: str) -> None:
        assert normalized_100(Decimal(total)) == Decimal(expected)

    def test_none_and_scale(self) -> None:
        assert normalized_100(None) is None
        with pytest.raises(ValueError):
            normalized_100(Decimal("0.5"))


class TestSummary:
    def test_complete(self) -> None:
        summary = summarize_scores(scores(4, communication=5))
        assert summary.band == DecisionBand.STRONG_ACCEPT
        assert summary.band_label == "Strong accept"
        assert summary.total == Decimal("4.20")
        assert summary.normalized_100 == Decimal("84")
        assert "conversion" in summary.normalized_label
        assert summary.partial_total is None and summary.partial_label is None
        assert summary.missing == ()
        assert summary.scored == KEYS
        assert summary.scored_weight == Decimal("1.00")
        assert sum(summary.contributions.values()) == summary.total
        assert summary.contributions["communication"] == Decimal("1.00")

    def test_insufficient_evidence_shows_labelled_partial(self) -> None:
        summary = summarize_scores(scores(4, technical_reading=None, communication=5))
        assert summary.band == DecisionBand.INSUFFICIENT_EVIDENCE
        assert summary.total is None and summary.normalized_100 is None
        assert summary.missing == ("technical_reading",)
        assert summary.scored_weight == Decimal("0.80")
        assert summary.partial_total == (Decimal("0.20") * 5 + Decimal("0.60") * 4) / Decimal("0.80")
        assert summary.partial_label is not None
        assert "Partial total re-normalised over 6 of 7 competencies" in summary.partial_label
        assert "80%" in summary.partial_label and "not a decision band" in summary.partial_label

    def test_nothing_scored(self) -> None:
        summary = summarize_scores({})
        assert summary.band == DecisionBand.INSUFFICIENT_EVIDENCE
        assert summary.partial_total is None and summary.partial_label is None
        assert summary.missing == KEYS

    def test_serialises(self) -> None:
        dumped = summarize_scores(scores(3)).model_dump(mode="json")
        assert dumped["band"] == "borderline"
        assert Decimal(dumped["total"]) == Decimal(3)


class TestToScore:
    def test_values(self) -> None:
        assert to_score(None) is None
        assert to_score(3) == Decimal(3)
        assert to_score(" 4.5 ") == Decimal("4.5")
        assert to_score(4.1999) == Decimal("4.1999")
