"""Deterministic experience computation from dated roles.

Every role's ``start``/``end`` (``YYYY-MM``, ``YYYY``, ``Jan 2023``,
``01/2023``, ``present``...) becomes a half-open month interval. Overlapping
intervals are merged before summing, so concurrent jobs are never counted
twice. Year-only dates are approximate (start = January, end = December) and
reported as such.

Findings (all neutral; a human decides whether they matter):

* overlaps between two roles of at least one month (often a notice period or
  a part-time handover) -> ``Inconsistency`` (low; medium above six months);
* gaps between merged periods of at least ``GAP_MIN_MONTHS`` -> informational
  ``Inconsistency`` (low) that explicitly says gaps are not evidence of
  anything and must not be probed for personal reasons (FAIRNESS_RULES);
* a stated total that differs from the dated roles by more than a year and
  20 % -> ``Inconsistency`` (low).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.schemas.ai import Inconsistency, RoleEntry

GAP_MIN_MONTHS = 6
TOTAL_MISMATCH_YEARS = 1.0
TOTAL_MISMATCH_RATIO = 0.2

_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}
_PRESENT = re.compile(r"^(present|current|now|today|ongoing|to date|till date|currently)$", re.IGNORECASE)
_YYYY_MM = re.compile(r"^(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?$")
_MM_YYYY = re.compile(r"^(\d{1,2})[-/.](\d{4})$")
_YYYY = re.compile(r"^(\d{4})$")
_MONTH_YEAR = re.compile(r"^([A-Za-z]+)\.?,?\s+'?(\d{2}|\d{4})$")


@dataclass(frozen=True, slots=True)
class MonthPoint:
    year: int
    month: int
    approximate: bool = False

    @property
    def index(self) -> int:
        return self.year * 12 + (self.month - 1)

    def label(self) -> str:
        return f"{self.year}" if self.approximate else f"{self.year}-{self.month:02d}"


def _year(value: int) -> int:
    if value < 100:
        return 2000 + value if value <= 50 else 1900 + value
    return value


def parse_month(value: str | None, *, is_end: bool = False, today: date | None = None) -> MonthPoint | None:
    """Parse a resume date. ``None`` for unknown/unparseable values; the
    current month for "present"."""
    if value is None:
        return None
    text = value.strip().strip(".").strip()
    if not text:
        return None
    current = today or datetime.now(UTC).date()
    if _PRESENT.match(text):
        return MonthPoint(current.year, current.month)
    match = _YYYY_MM.match(text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return MonthPoint(year, month) if 1 <= month <= 12 and 1900 <= year <= 2100 else None
    match = _MM_YYYY.match(text)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        return MonthPoint(year, month) if 1 <= month <= 12 and 1900 <= year <= 2100 else None
    match = _YYYY.match(text)
    if match:
        year = int(match.group(1))
        if not 1900 <= year <= 2100:
            return None
        return MonthPoint(year, 12 if is_end else 1, approximate=True)
    match = _MONTH_YEAR.match(text)
    if match:
        word = match.group(1).lower()
        month = _MONTHS.get(word) or _SEASONS.get(word)
        if month is None:
            return None
        year = _year(int(match.group(2)))
        return MonthPoint(year, month, approximate=word in _SEASONS) if 1900 <= year <= 2100 else None
    return None


@dataclass(slots=True)
class DatedRole:
    index: int
    title: str
    company: str
    start: MonthPoint
    end: MonthPoint

    @property
    def start_index(self) -> int:
        return self.start.index

    @property
    def end_index(self) -> int:  # exclusive
        return self.end.index + 1

    @property
    def months(self) -> int:
        return max(0, self.end_index - self.start_index)

    def describe(self) -> str:
        return f"{self.title} at {self.company} ({self.start.label()} to {self.end.label()})"


@dataclass(slots=True)
class Overlap:
    first: DatedRole
    second: DatedRole
    months: int


@dataclass(slots=True)
class Gap:
    start_index: int
    end_index: int

    @property
    def months(self) -> int:
        return self.end_index - self.start_index

    def label(self) -> str:
        sy, sm = divmod(self.start_index, 12)
        ey, em = divmod(self.end_index - 1, 12)
        return f"{sy}-{sm + 1:02d} to {ey}-{em + 1:02d}"


@dataclass(slots=True)
class ExperienceComputation:
    total_months: int = 0
    dated_roles: list[DatedRole] = field(default_factory=list)
    undated_roles: list[int] = field(default_factory=list)
    merged: list[tuple[int, int]] = field(default_factory=list)
    overlaps: list[Overlap] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    approximate: bool = False

    @property
    def years(self) -> float | None:
        if not self.dated_roles:
            return None
        return round(self.total_months / 12.0, 1)

    def role_months(self) -> dict[int, int]:
        return {role.index: role.months for role in self.dated_roles}

    def as_dict(self) -> dict[str, object]:
        return {
            "total_months": self.total_months,
            "years": self.years,
            "dated_roles": len(self.dated_roles),
            "undated_roles": len(self.undated_roles),
            "overlaps": [
                {"roles": [o.first.index, o.second.index], "months": o.months} for o in self.overlaps
            ],
            "gaps": [{"from_to": g.label(), "months": g.months} for g in self.gaps],
            "approximate": self.approximate,
        }


def compute_experience(roles: list[RoleEntry], *, today: date | None = None) -> ExperienceComputation:
    result = ExperienceComputation()
    current = today or datetime.now(UTC).date()
    now_point = MonthPoint(current.year, current.month)
    for index, role in enumerate(roles):
        start = parse_month(role.start, today=current)
        end = parse_month(role.end, is_end=True, today=current) if role.end else None
        if start is None:
            result.undated_roles.append(index)
            continue
        if end is None:
            if role.end is None and role.duration_months:
                end_index = start.index + max(0, role.duration_months - 1)
                end = MonthPoint(end_index // 12, end_index % 12 + 1, approximate=True)
            elif role.end is None:
                # A start without an end is most often the current role, but we
                # cannot know: count it as a single month and flag it approximate.
                end = start
                result.approximate = True
            else:
                result.undated_roles.append(index)
                continue
        if end.index > now_point.index:
            end = now_point
        if end.index < start.index:
            result.undated_roles.append(index)
            continue
        if start.approximate or end.approximate:
            result.approximate = True
        result.dated_roles.append(DatedRole(index, role.title, role.company, start, end))

    intervals = sorted((r.start_index, r.end_index) for r in result.dated_roles)
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    result.merged = [(a, b) for a, b in merged]
    result.total_months = sum(b - a for a, b in merged)

    ordered = sorted(result.dated_roles, key=lambda r: (r.start_index, r.end_index))
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            if second.start_index >= first.end_index:
                break
            months = min(first.end_index, second.end_index) - second.start_index
            if months >= 1:
                result.overlaps.append(Overlap(first, second, months))
    for (_, prev_end), (next_start, _) in zip(merged, merged[1:], strict=False):
        if next_start - prev_end >= GAP_MIN_MONTHS:
            result.gaps.append(Gap(prev_end, next_start))
    return result


def experience_inconsistencies(
    computation: ExperienceComputation, stated_total_years: float | None
) -> list[Inconsistency]:
    """Neutral findings for a human reviewer (see module docstring)."""
    findings: list[Inconsistency] = []
    for overlap in computation.overlaps:
        severity = "medium" if overlap.months > 6 else "low"
        findings.append(
            Inconsistency(
                description=(
                    f"Overlapping employment dates: {overlap.first.describe()} overlaps "
                    f"{overlap.second.describe()} by {overlap.months} month{'s' if overlap.months != 1 else ''}. "
                    "This often has a simple explanation (notice period, part-time or handover); ask neutrally."
                )[:400],
                severity=severity,
            )
        )
    for gap in computation.gaps:
        findings.append(
            Inconsistency(
                description=(
                    f"Timeline note: no dated role from {gap.label()} ({gap.months} months). Gaps are common and "
                    "are not evidence of anything; do not ask about personal reasons."
                )[:400],
                severity="low",
            )
        )
    computed = computation.years
    if computed is not None and stated_total_years is not None and computation.undated_roles == []:
        difference = abs(computed - stated_total_years)
        if difference > TOTAL_MISMATCH_YEARS and difference > TOTAL_MISMATCH_RATIO * max(
            computed, stated_total_years
        ):
            findings.append(
                Inconsistency(
                    description=(
                        f"Stated total experience ({stated_total_years:g} years) differs from the dated roles "
                        f"({computed:g} years after merging overlaps)."
                    ),
                    severity="low",
                )
            )
    return findings
