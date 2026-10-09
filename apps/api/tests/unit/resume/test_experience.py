"""Deterministic experience computation."""

from __future__ import annotations

from datetime import date

import pytest

from app.schemas.ai import RoleEntry
from app.services.resume.experience import (
    MonthPoint,
    compute_experience,
    experience_inconsistencies,
    parse_month,
)

TODAY = date(2026, 10, 7)


def role(
    title: str, start: str | None, end: str | None, company: str = "Acme", months: int | None = None
) -> RoleEntry:
    return RoleEntry(title=title, company=company, start=start, end=end, duration_months=months)


@pytest.mark.parametrize(
    ("value", "is_end", "expected"),
    [
        ("2023-01", False, MonthPoint(2023, 1)),
        ("2023/7", False, MonthPoint(2023, 7)),
        ("07/2021", False, MonthPoint(2021, 7)),
        ("Jan 2023", False, MonthPoint(2023, 1)),
        ("September 2019", False, MonthPoint(2019, 9)),
        ("Sept. 2019", False, MonthPoint(2019, 9)),
        ("Mar '21", False, MonthPoint(2021, 3)),
        ("2020", False, MonthPoint(2020, 1, approximate=True)),
        ("2020", True, MonthPoint(2020, 12, approximate=True)),
        ("Present", True, MonthPoint(2026, 10)),
        ("current", True, MonthPoint(2026, 10)),
        ("Summer 2018", False, MonthPoint(2018, 6, approximate=True)),
        ("2023-13", False, None),
        ("soon", False, None),
        ("", False, None),
        (None, False, None),
    ],
)
def test_parse_month(value, is_end, expected) -> None:
    assert parse_month(value, is_end=is_end, today=TODAY) == expected


def test_jane_doe_merges_overlap() -> None:
    roles = [
        role("Senior DevOps Engineer", "2023-01", "present", "Bluepeak Analytics"),
        role("DevOps Engineer", "2021-08", "2023-03", "Harborline Logistics"),
        role("Junior Systems Engineer", "2021-01", "2021-07", "Cobalt Retail Group"),
    ]
    result = compute_experience(roles, today=TODAY)
    assert result.total_months == 70  # Jan 2021 .. Oct 2026 inclusive, overlap counted once
    assert result.years == 5.8
    assert len(result.overlaps) == 1
    overlap = result.overlaps[0]
    assert overlap.months == 3
    assert {overlap.first.company, overlap.second.company} == {"Bluepeak Analytics", "Harborline Logistics"}
    assert result.gaps == []
    findings = experience_inconsistencies(result, stated_total_years=5.8)
    assert len(findings) == 1
    assert findings[0].severity == "low"
    assert "Overlapping employment dates" in findings[0].description
    assert "ask neutrally" in findings[0].description


def test_sum_without_merge_would_double_count() -> None:
    roles = [role("A", "2020-01", "2020-12"), role("B", "2020-06", "2021-05")]
    result = compute_experience(roles, today=TODAY)
    assert result.total_months == 17  # not 12 + 12
    assert result.overlaps[0].months == 7
    assert experience_inconsistencies(result, None)[0].severity == "medium"


def test_gap_is_informational_and_fairness_safe() -> None:
    roles = [role("A", "2015-01", "2016-12"), role("B", "2018-01", "2019-12")]
    result = compute_experience(roles, today=TODAY)
    assert result.total_months == 48
    assert len(result.gaps) == 1 and result.gaps[0].months == 12
    finding = experience_inconsistencies(result, None)[0]
    assert finding.severity == "low"
    assert "not evidence of anything" in finding.description
    assert "do not ask about personal reasons" in finding.description


def test_short_gap_is_not_reported() -> None:
    roles = [role("Intern", "2024-06", "2024-12"), role("Engineer", "2025-03", "present")]
    result = compute_experience(roles, today=TODAY)
    assert result.gaps == []


def test_stated_total_mismatch() -> None:
    roles = [role("A", "2022-01", "2023-12")]
    result = compute_experience(roles, today=TODAY)
    findings = experience_inconsistencies(result, stated_total_years=8.0)
    assert any("Stated total experience (8 years)" in f.description for f in findings)
    assert not experience_inconsistencies(result, stated_total_years=2.5)


def test_undated_approximate_and_future_dates() -> None:
    roles = [
        role("Undated", None, None),
        role("Year only", "2019", "2020"),
        role("Future end", "2026-01", "2030-01"),
        role("By duration", "2010-01", None, months=12),
        role("Start only", "2005-05", None),
    ]
    result = compute_experience(roles, today=TODAY)
    assert result.undated_roles == [0]
    assert result.approximate is True
    by_title = {r.title: r for r in result.dated_roles}
    assert by_title["Year only"].months == 24
    assert by_title["Future end"].end == MonthPoint(2026, 10)
    assert by_title["By duration"].months == 12
    assert by_title["Start only"].months == 1


def test_no_roles() -> None:
    result = compute_experience([], today=TODAY)
    assert result.years is None and result.total_months == 0
    assert experience_inconsistencies(result, 5) == []
