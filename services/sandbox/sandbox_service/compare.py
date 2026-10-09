"""Result comparison, done in the service so expected values never enter a sandbox.

Semantics are shared with the question bank (``compare_results`` in
apps/api/app/seed/loaders.py, which validated every expected value):

* exact            deep equality; ints and floats compare numerically, booleans
                   only equal booleans, strings exactly.
* unordered        order-insensitive at every array nesting level (both sides are
                   canonicalised: inner arrays sorted, then the outer array), e.g.
                   "indices in any order" or group anagrams.
* float_tolerance  like exact, numbers within ``tolerance * max(1, |expected|)``.

For array return types a ``null`` result (e.g. a nil Go slice) counts as ``[]``.
SQL result sets compare row by row (cells: numbers within 1e-6, NULL == NULL,
booleans equal 1/0) and are order-insensitive unless the test is ``ordered``.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

SQL_TOLERANCE = 1e-6


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numbers_equal(expected: float, actual: float, tolerance: float | None) -> bool:
    if tolerance is None:
        return expected == actual
    if math.isnan(float(expected)) or math.isnan(float(actual)):
        return False
    return abs(float(actual) - float(expected)) <= tolerance * max(1.0, abs(float(expected)))


def deep_equal(expected: Any, actual: Any, tolerance: float | None = None) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if _is_number(expected) and _is_number(actual):
        return numbers_equal(expected, actual, tolerance)
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, str) or isinstance(actual, str):
        return isinstance(expected, str) and isinstance(actual, str) and expected == actual
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(deep_equal(e, a, tolerance) for e, a in zip(expected, actual, strict=True))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(deep_equal(expected[k], actual[k], tolerance) for k in expected)
    return False


def canonical(value: Any, nested: bool = False) -> tuple[Any, ...]:
    """Hashable, order-normalised key used for multiset comparisons."""
    if isinstance(value, bool):
        return ("b", value)
    if _is_number(value):
        if isinstance(value, float) and value.is_integer():
            return ("n", int(value))
        return ("n", value)
    if value is None:
        return ("z",)
    if isinstance(value, str):
        return ("s", value)
    if isinstance(value, list):
        items = [canonical(v, nested) for v in value]
        if nested:
            items.sort(key=repr)
        return ("l", tuple(items))
    if isinstance(value, dict):
        return ("d", tuple(sorted((str(k), canonical(v, nested)) for k, v in value.items())))
    return ("o", repr(value))


def values_equal(
    expected: Any, actual: Any, comparison: str = "exact", tolerance: float = 1e-6, return_type: str | None = None
) -> bool:
    if actual is None and return_type is not None and return_type.endswith("[]"):
        actual = []
    if comparison == "float_tolerance":
        return deep_equal(expected, actual, tolerance)
    if comparison == "unordered" and isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        return Counter(canonical(v, nested=True) for v in expected) == Counter(canonical(v, nested=True) for v in actual)
    return deep_equal(expected, actual, None)


def _cell_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) and _is_number(actual):
        return bool(int(expected) == actual)
    if isinstance(actual, bool) and _is_number(expected):
        return bool(int(actual) == expected)
    if _is_number(expected) and _is_number(actual):
        return numbers_equal(expected, actual, SQL_TOLERANCE)
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, str) and isinstance(actual, str):
        return expected == actual
    if (_is_number(expected) and isinstance(actual, str)) or (isinstance(expected, str) and _is_number(actual)):
        try:
            return numbers_equal(float(expected), float(actual), SQL_TOLERANCE)
        except ValueError:
            return False
    return bool(expected == actual)


def _row_equal(expected: list[Any], actual: list[Any]) -> bool:
    return len(expected) == len(actual) and all(_cell_equal(e, a) for e, a in zip(expected, actual, strict=True))


def _row_key(row: list[Any]) -> tuple[Any, ...]:
    key = []
    for value in row:
        if value is None:
            key.append((0, ""))
        elif _is_number(value) or isinstance(value, bool):
            key.append((1, f"{float(value):.6e}"))
        else:
            key.append((2, str(value)))
    return tuple(key)


def rows_equal(expected_rows: list[list[Any]], actual_rows: list[list[Any]], ordered: bool) -> bool:
    if len(expected_rows) != len(actual_rows):
        return False
    if not all(isinstance(r, list) for r in actual_rows):
        return False
    if ordered:
        return all(_row_equal(e, a) for e, a in zip(expected_rows, actual_rows, strict=True))
    by_key = zip(sorted(expected_rows, key=_row_key), sorted(actual_rows, key=_row_key), strict=True)
    if all(_row_equal(e, a) for e, a in by_key):
        return True
    if len(expected_rows) > 2000:  # mixed cell types defeat the sort; greedy matching is O(n^2)
        return False
    remaining = list(actual_rows)
    for row in sorted(expected_rows, key=_row_key):
        for index, candidate in enumerate(remaining):
            if _row_equal(row, candidate):
                del remaining[index]
                break
        else:
            return False
    return True
