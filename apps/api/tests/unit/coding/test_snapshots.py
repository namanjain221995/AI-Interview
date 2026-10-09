"""Snapshot significance thresholds and unified diffs."""

from __future__ import annotations

import pytest

from app.services.coding.snapshots import (
    MAX_SNAPSHOT_INTERVAL_S,
    MIN_CHANGED_CHARS,
    MIN_CHANGED_LINES,
    change_stats,
    decide_snapshot,
    diff_counts,
    unified_diff,
)

BASE = "def two_sum(nums, target):\n    seen = {}\n    return []\n"


def test_identical_code_is_never_significant() -> None:
    decision = decide_snapshot(BASE, BASE, seconds_since_snapshot=999)
    assert not decision.store and decision.reason == "unchanged"


def test_first_snapshot_is_always_stored() -> None:
    assert decide_snapshot(None, "x = 1\n", seconds_since_snapshot=None).store


def test_three_changed_lines_trigger_a_snapshot() -> None:
    new = BASE.replace("    return []\n", "    for i, v in enumerate(nums):\n        pass\n    return []\n")
    stats = change_stats(BASE, new)
    assert stats.lines_added == 2 and stats.lines_removed == 0
    assert (
        not decide_snapshot(BASE, new, seconds_since_snapshot=1).store
        or stats.chars_changed >= MIN_CHANGED_CHARS
    )
    three = new.replace("    return []\n", "    x = 1\n    return []\n")
    stats3 = change_stats(BASE, three)
    assert stats3.lines_changed >= MIN_CHANGED_LINES
    assert decide_snapshot(BASE, three, seconds_since_snapshot=1).reason == "lines"


def test_small_single_line_edit_is_below_threshold() -> None:
    new = BASE.replace("seen = {}", "seen = {0: 1}")
    stats = change_stats(BASE, new)
    assert stats.lines_added == 1 and stats.lines_removed == 1
    assert stats.chars_changed < 10  # character diff inside the replaced line, not the whole line
    decision = decide_snapshot(BASE, new, seconds_since_snapshot=5)
    assert not decision.store and decision.reason == "below_threshold"


def test_forty_changed_characters_trigger_a_snapshot() -> None:
    new = BASE.replace("seen = {}", "seen = {}  # value -> index of the element seen so far")
    stats = change_stats(BASE, new)
    assert stats.lines_changed == 2 and stats.chars_changed >= MIN_CHANGED_CHARS
    assert decide_snapshot(BASE, new, seconds_since_snapshot=1).reason == "chars"


@pytest.mark.parametrize("elapsed", [MAX_SNAPSHOT_INTERVAL_S, MAX_SNAPSHOT_INTERVAL_S + 30])
def test_any_change_after_twenty_seconds_triggers_a_snapshot(elapsed: float) -> None:
    new = BASE.replace("[]", "[0]")
    decision = decide_snapshot(BASE, new, seconds_since_snapshot=elapsed)
    assert decision.store and decision.reason == "interval"


def test_change_just_before_the_interval_is_not_stored() -> None:
    new = BASE.replace("[]", "[0]")
    assert not decide_snapshot(BASE, new, seconds_since_snapshot=MAX_SNAPSHOT_INTERVAL_S - 0.1).store


def test_unified_diff_and_counts() -> None:
    new = BASE.replace(
        "    return []\n", "    for i, v in enumerate(nums):\n        seen[v] = i\n    return []\n"
    )
    diff = unified_diff(BASE, new, from_label="v1", to_label="v2")
    assert diff.startswith("--- v1\n+++ v2\n@@")
    assert "+    for i, v in enumerate(nums):" in diff
    assert diff_counts(diff) == (2, 0)


def test_diff_counts_ignore_headers_but_count_sql_comment_lines() -> None:
    old = "SELECT 1;\n-- first\n"
    new = "SELECT 2;\n"
    diff = unified_diff(old, new)
    added, removed = diff_counts(diff)
    assert (added, removed) == (1, 2)  # "--- first" removal is a content line, not a header


def test_whitespace_only_change_counts_characters() -> None:
    stats = change_stats("a\n", "a\n\n\n")
    assert stats.changed
