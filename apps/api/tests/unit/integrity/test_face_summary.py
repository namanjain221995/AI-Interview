"""Face-presence summary for reviewers and the recruiter report."""

from __future__ import annotations

from app.services.integrity.queries import summarize_face_presence


def test_summary_merges_page_runs_intervals_and_static_faces() -> None:
    rows = [
        {
            "type": "face_check_summary",
            "details": {
                "runs": {
                    "run_a": {
                        "monitored_ms": 600_000,
                        "one_face_ms": 570_000,
                        "no_face_ms": 20_000,
                        "multi_face_ms": 10_000,
                    },
                    "run_b": {"monitored_ms": 300_000, "one_face_ms": 300_000, "static_faces": 1},
                }
            },
        },
        # The same run seen again after a review started a new row: counters are cumulative (max wins).
        {"type": "face_check_summary", "details": {"runs": {"run_b": {"monitored_ms": 200_000}}}},
        {
            "type": "face_not_visible",
            "duration_ms": 26_000,
            "details": {"episodes": {"n1": 6_000, "n2": 20_000}, "server_checks": {"n1": "confirmed"}},
        },
        {
            "type": "multiple_faces_visible",
            "duration_ms": 10_000,
            "details": {"episodes": {"m1": 10_000}, "server_checks": {"m1": "confirmed"}},
        },
        {"type": "static_face_ignored", "count": 1, "details": {}},
        {"type": "tab_hidden", "count": 3, "details": {}},
    ]
    summary = summarize_face_presence(rows)
    assert summary is not None
    assert summary["monitored_ms"] == 900_000 and summary["one_face_ms"] == 870_000
    assert summary["one_face_share"] == round(870_000 / 900_000, 4)
    assert summary["no_face"] == {
        "intervals": 2,
        "total_ms": 26_000,
        "longest_ms": 20_000,
        "confirmed_by_server": 1,
    }
    assert (
        summary["multiple_faces"]["intervals"] == 1 and summary["multiple_faces"]["confirmed_by_server"] == 1
    )
    assert summary["static_faces_ignored"] == 1 and summary["page_runs"] == 2


def test_summary_is_none_without_face_check_rows_and_tolerates_missing_counters() -> None:
    assert summarize_face_presence([{"type": "tab_hidden", "details": {}}]) is None
    partial = summarize_face_presence([{"type": "face_not_visible", "duration_ms": 7000, "details": {}}])
    assert partial is not None
    assert partial["one_face_share"] is None and partial["monitored_ms"] == 0
    assert partial["no_face"]["intervals"] == 1 and partial["no_face"]["total_ms"] == 7000
