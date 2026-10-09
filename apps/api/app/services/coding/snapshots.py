"""Code snapshot significance and unified diffs (pure functions).

A snapshot is stored when the editor content changed *significantly* since the
last stored snapshot:

* at least :data:`MIN_CHANGED_LINES` lines added + removed, or
* at least :data:`MIN_CHANGED_CHARS` characters inserted + deleted, or
* any change at all once :data:`MAX_SNAPSHOT_INTERVAL_S` seconds have passed
  since the last snapshot.

Runs, submissions, hint requests and language switches always snapshot the
code they act on when it differs from the last snapshot (see
:class:`app.domain.enums.SnapshotTrigger`).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

MIN_CHANGED_LINES = 3
MIN_CHANGED_CHARS = 40
MAX_SNAPSHOT_INTERVAL_S = 20.0
MAX_DIFF_CHARS = 100_000
_CHAR_DIFF_LIMIT = 20_000  # per replaced block; larger blocks are counted by length


@dataclass(frozen=True, slots=True)
class ChangeStats:
    lines_added: int
    lines_removed: int
    chars_changed: int

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_removed

    @property
    def changed(self) -> bool:
        return self.lines_changed > 0 or self.chars_changed > 0


@dataclass(frozen=True, slots=True)
class SnapshotDecision:
    store: bool
    reason: str  # lines | chars | interval | unchanged | below_threshold | first
    stats: ChangeStats


def _lines(code: str | None) -> list[str]:
    return (code or "").splitlines()


def change_stats(previous: str | None, current: str | None) -> ChangeStats:
    """Lines added/removed (line diff) and characters inserted + deleted
    (character diff inside replaced line blocks)."""
    old_text, new_text = previous or "", current or ""
    if old_text == new_text:
        return ChangeStats(0, 0, 0)
    old, new = _lines(old_text), _lines(new_text)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    added = removed = chars = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
        old_block = "\n".join(old[i1:i2])
        new_block = "\n".join(new[j1:j2])
        if tag == "replace" and len(old_block) + len(new_block) <= _CHAR_DIFF_LIMIT:
            inner = difflib.SequenceMatcher(a=old_block, b=new_block, autojunk=False)
            for itag, a1, a2, b1, b2 in inner.get_opcodes():
                if itag != "equal":
                    chars += (a2 - a1) + (b2 - b1)
        else:
            chars += len(old_block) + len(new_block) + (1 if tag != "replace" else 0)
    if chars == 0:  # whitespace-only line changes (e.g. trailing newline)
        chars = abs(len(new_text) - len(old_text))
    return ChangeStats(added, removed, chars)


def decide_snapshot(
    previous: str | None,
    current: str,
    *,
    seconds_since_snapshot: float | None,
) -> SnapshotDecision:
    """Whether ``current`` deserves a new snapshot after ``previous``."""
    if previous is None:
        return SnapshotDecision(True, "first", change_stats("", current))
    stats = change_stats(previous, current)
    if not stats.changed:
        return SnapshotDecision(False, "unchanged", stats)
    if stats.lines_changed >= MIN_CHANGED_LINES:
        return SnapshotDecision(True, "lines", stats)
    if stats.chars_changed >= MIN_CHANGED_CHARS:
        return SnapshotDecision(True, "chars", stats)
    if seconds_since_snapshot is not None and seconds_since_snapshot >= MAX_SNAPSHOT_INTERVAL_S:
        return SnapshotDecision(True, "interval", stats)
    return SnapshotDecision(False, "below_threshold", stats)


def unified_diff(
    previous: str | None, current: str, *, from_label: str = "previous", to_label: str = "current"
) -> str:
    """Unified diff (3 lines of context), capped at :data:`MAX_DIFF_CHARS`."""
    diff = "\n".join(
        difflib.unified_diff(
            _lines(previous), _lines(current), fromfile=from_label, tofile=to_label, n=3, lineterm=""
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)"
    return diff


def diff_counts(diff: str) -> tuple[int, int]:
    """(added, removed) line counts of a unified diff (headers excluded)."""
    added = removed = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:  # file headers (--- / +++) precede the first hunk
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed
