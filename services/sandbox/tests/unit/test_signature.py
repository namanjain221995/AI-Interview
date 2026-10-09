from __future__ import annotations

import pytest

from sandbox_service.signature import SUPPORTED_TYPES, SignatureError, parse_signature, to_camel, to_pascal, to_snake


def test_naming_conventions():
    assert to_camel("two_sum") == "twoSum"
    assert to_camel("is_valid_bst") == "isValidBst"
    assert to_camel("twoSum") == "twoSum"
    assert to_camel("max") == "max"
    assert to_snake("twoSum") == "two_sum"
    assert to_snake("two_sum") == "two_sum"
    assert to_pascal("two_sum") == "TwoSum"


def test_parse_valid_signature():
    sig = parse_signature(
        {
            "function_name": "two_sum",
            "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
            "return_type": "int[]",
            "comparison": "unordered",
        }
    )
    assert sig.camel_name == "twoSum"
    assert [p.type for p in sig.params] == ["int[]", "int"]
    assert sig.comparison == "unordered" and sig.tolerance == 1e-6
    assert sig.entry_candidates(sig.snake_name)[:2] == ["two_sum", "twoSum"]
    assert sig.entry_candidates(sig.camel_name)[0] == "twoSum"


def test_all_supported_types_accepted():
    params = [{"name": f"p{i}", "type": t} for i, t in enumerate(SUPPORTED_TYPES)]
    sig = parse_signature({"function_name": "f", "params": params, "return_type": "string[][]"})
    assert len(sig.params) == len(SUPPORTED_TYPES)
    assert sig.comparison == "exact"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"function_name": "two-sum", "params": [], "return_type": "int"},
        {"function_name": "x; drop", "params": [], "return_type": "int"},
        {"function_name": "f", "params": [{"name": "a", "type": "map"}], "return_type": "int"},
        {"function_name": "f", "params": [{"name": "a b", "type": "int"}], "return_type": "int"},
        {"function_name": "f", "params": "nope", "return_type": "int"},
        {"function_name": "f", "params": [], "return_type": "void"},
        {"function_name": "f", "params": [], "return_type": "int", "comparison": "fuzzy"},
        {"function_name": "f", "params": [], "return_type": "int", "comparison": "unordered_nested"},
        {"function_name": "f", "params": [], "return_type": "int", "tolerance": "big"},
        {"function_name": "f", "params": [{"name": f"p{i}", "type": "int"} for i in range(17)], "return_type": "int"},
    ],
)
def test_invalid_signatures_are_rejected(raw):
    with pytest.raises(SignatureError):
        parse_signature(raw)
