"""Human decisions (INTAKE_INTERVIEW.md 8.9): mirror mapping, override table, input validation codes."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailed
from app.domain.enums import ReviewDecisionValue
from app.services.intake.decisions import ALIGNED, MIRROR, overrides_ai, validate_decision_input

OUTCOMES = ("accept", "accept_with_notes", "second_interview", "reject", "request_evidence")


def test_mirror_mapping() -> None:
    assert MIRROR == {
        "accept": ReviewDecisionValue.ADVANCE,
        "accept_with_notes": ReviewDecisionValue.ADVANCE,
        "second_interview": ReviewDecisionValue.HOLD,
        "reject": ReviewDecisionValue.REJECT,
        "request_evidence": ReviewDecisionValue.NEEDS_MORE_INFORMATION,
    }


@pytest.mark.parametrize(
    ("band", "aligned"),
    [
        ("strong_accept", {"accept", "accept_with_notes"}),
        ("accept_with_notes", {"accept", "accept_with_notes"}),
        ("borderline", {"second_interview", "request_evidence", "accept_with_notes"}),
        ("reject", {"reject"}),
        ("insufficient_evidence", {"request_evidence", "second_interview"}),
    ],
)
def test_override_table(band: str, aligned: set[str]) -> None:
    assert ALIGNED[band] == aligned
    for outcome in OUTCOMES:
        assert overrides_ai(outcome, band) is (outcome not in aligned)


def test_no_evaluation_is_never_an_override() -> None:
    assert not any(overrides_ai(outcome, None) for outcome in OUTCOMES)


def _validate(**overrides):  # noqa: ANN003, ANN202
    values = {
        "outcome": "accept",
        "rationale": "A clear and complete rationale.",
        "review_items": [],
        "competency_notes": {},
        "requested_evidence": [],
        "known_item_ids": {"ri_0123456789"},
    }
    values.update(overrides)
    return validate_decision_input(**values)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"rationale": "too short"}, "rationale_required"),
        ({"rationale": "x" * 5001}, "rationale_required"),
        ({"rationale": "          padded   "}, "rationale_required"),
        ({"outcome": "request_evidence"}, "requested_evidence_required"),
        ({"outcome": "request_evidence", "requested_evidence": ["  "]}, "requested_evidence_required"),
        ({"review_items": [{"item_id": "ri_ffffffffff", "resolution": "confirmed"}]}, "unknown_review_item"),
        ({"competency_notes": {"charisma": "n"}}, "validation_failed"),
        ({"outcome": "hire"}, "validation_failed"),
        ({"review_items": [{"item_id": "ri_0123456789", "resolution": "ignore"}]}, "validation_failed"),
    ],
)
def test_validation_codes(overrides, code: str) -> None:  # noqa: ANN001
    with pytest.raises(ValidationFailed) as error:
        _validate(**overrides)
    assert error.value.code == code


def test_normalisation() -> None:
    rationale, resolutions, notes, evidence = _validate(
        rationale="  Strong relay and pipeline answers.  ",
        review_items=[
            {"item_id": "ri_0123456789", "resolution": "dismissed", "note": "  ok  "},
            {"item_id": "ri_0123456789", "resolution": "confirmed"},
        ],
        competency_notes={"technical": "  Probe modelling.  ", "composure": "   "},
        requested_evidence=["  baseline numbers  "],
    )
    assert rationale == "Strong relay and pipeline answers."
    assert resolutions == [{"item_id": "ri_0123456789", "resolution": "dismissed", "note": "ok"}]
    assert notes == {"technical": "Probe modelling."}
    assert evidence == ["baseline numbers"]
