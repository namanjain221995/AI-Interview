"""Unknown stored round types never crash a reader (G14): 409 unsupported_round_type instead of a 500."""

from __future__ import annotations

import pytest

from app.core.errors import UnsupportedRoundType
from app.domain.enums import RoundType
from app.schemas.rounds import RoundConfig, load_round_config
from app.services.jobs.catalog import parse_round_type, round_type_label


def test_parse_round_type_accepts_known_values_and_enums() -> None:
    assert parse_round_type("behavioral") is RoundType.BEHAVIORAL
    assert parse_round_type(RoundType.CODING) is RoundType.CODING


def test_parse_round_type_unknown_is_a_409_not_a_500() -> None:
    with pytest.raises(UnsupportedRoundType) as excinfo:
        parse_round_type("intro_call_from_the_future")
    err = excinfo.value
    assert err.status_code == 409 and err.code == "unsupported_round_type"
    assert err.details == {"round_type": "intro_call_from_the_future"}
    assert err.retryable is False  # background jobs do not retry it


def test_round_type_label_falls_back_for_unknown_values() -> None:
    assert round_type_label(RoundType.TECHNICAL_SCREENING) == "Technical screening"
    assert round_type_label("hiring_call") == "Hiring Call"


def test_load_round_config_maps_only_the_round_type_enum_error() -> None:
    with pytest.raises(UnsupportedRoundType):
        load_round_config({"round_type": "hiring_call", "name": "x"})
    # other validation errors propagate unchanged
    with pytest.raises(Exception) as excinfo:
        load_round_config({"round_type": "behavioral", "name": "x", "duration_minutes": 1})
    assert not isinstance(excinfo.value, UnsupportedRoundType)
    config = load_round_config({"round_type": "behavioral", "name": "x"})
    assert isinstance(config, RoundConfig) and config.round_type is RoundType.BEHAVIORAL
