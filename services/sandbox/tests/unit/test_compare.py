from __future__ import annotations

import pytest

from sandbox_service.compare import canonical, deep_equal, rows_equal, values_equal


@pytest.mark.parametrize(
    ("expected", "actual", "equal"),
    [
        ([0, 1], [0, 1], True),
        ([0, 1], [1, 0], False),
        (3, 3.0, True),
        (True, 1, False),
        (1, True, False),
        (False, False, True),
        (None, None, True),
        (None, [], False),
        ("a", "a", True),
        ("1", 1, False),
        ([[1, 2], [3]], [[1, 2], [3]], True),
        ([[1, 2], [3]], [[1, 2], [3, 4]], False),
        ({"a": 1}, {"a": 1.0}, True),
        ({"a": 1}, {"b": 1}, False),
        (0.1 + 0.2, 0.3, False),
    ],
)
def test_exact(expected, actual, equal):
    assert values_equal(expected, actual, "exact") is equal


def test_float_tolerance():
    assert values_equal(0.3, 0.1 + 0.2, "float_tolerance", 1e-6)
    assert values_equal([1.0, 2.0000001], [1, 2], "float_tolerance", 1e-6)
    assert not values_equal(2.0, 2.01, "float_tolerance", 1e-6)
    assert values_equal(1e12, 1e12 + 100, "float_tolerance", 1e-6)  # tolerance * max(1, |expected|)
    assert values_equal(0.0, 5e-7, "float_tolerance", 1e-6)
    assert not values_equal(0.0, 2e-6, "float_tolerance", 1e-6)
    assert not values_equal(True, 1.0, "float_tolerance", 1e-6)
    assert not values_equal(float("nan"), float("nan"), "float_tolerance", 1e-6)


def test_unordered_is_order_insensitive_at_every_level():
    assert values_equal([0, 1], [1, 0], "unordered")
    assert values_equal([[1, 2], [3, 4]], [[4, 3], [1, 2]], "unordered")
    groups = [["eat", "tea", "ate"], ["tan", "nat"], ["bat"]]
    shuffled = [["bat"], ["nat", "tan"], ["ate", "eat", "tea"]]
    assert values_equal(groups, shuffled, "unordered")
    assert not values_equal([["a", "b"]], [["a", "a"]], "unordered")
    assert not values_equal([1, 1, 2], [1, 2, 2], "unordered")  # multiset, not set
    assert not values_equal([1, 2], [1, 2, 3], "unordered")
    assert values_equal([1.0, 2], [2, 1], "unordered")
    assert not values_equal([True], [1], "unordered")
    assert values_equal(5, 5, "unordered")  # non-lists fall back to exact
    assert not values_equal(groups, shuffled, "exact")


def test_null_counts_as_empty_array_for_array_return_types():
    assert values_equal([], None, "exact", return_type="int[]")
    assert values_equal([], None, "unordered", return_type="string[][]")
    assert not values_equal([], None, "exact", return_type="int")
    assert not values_equal([], None, "exact")


def test_canonical_is_hashable_and_type_aware():
    assert canonical(1) == canonical(1.0)
    assert canonical(True) != canonical(1)
    assert hash(canonical([1, [2, {"a": None}]]))


def test_deep_equal_rejects_mismatched_types():
    assert not deep_equal([1], (1,))
    assert not deep_equal({"a": 1}, [("a", 1)])


def test_rows_ordered_and_unordered():
    expected = [["eng", 120], ["ops", 90]]
    assert rows_equal(expected, [["eng", 120], ["ops", 90]], ordered=True)
    assert not rows_equal(expected, [["ops", 90], ["eng", 120]], ordered=True)
    assert rows_equal(expected, [["ops", 90], ["eng", 120]], ordered=False)
    assert not rows_equal(expected, [["eng", 120]], ordered=False)
    assert not rows_equal(expected, [["eng", 120], ["ops", 91]], ordered=False)


def test_rows_cell_leniency():
    assert rows_equal([[2.5]], [[2.5000000001]], ordered=True)
    assert rows_equal([[True, None]], [[1, None]], ordered=True)
    assert rows_equal([[3]], [["3"]], ordered=True)
    assert not rows_equal([["a"]], [[None]], ordered=True)
    assert not rows_equal([[1, 2]], [[1]], ordered=True)
    # duplicates are respected
    assert not rows_equal([[1], [1], [2]], [[1], [2], [2]], ordered=False)


def test_rows_unordered_large_sets_are_fast():
    expected = [[i, f"n{i}"] for i in range(10_000)]
    actual = list(reversed(expected))
    assert rows_equal(expected, actual, ordered=False)
