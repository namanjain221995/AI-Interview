from __future__ import annotations

import pytest

from app.services.scoring.timeline import TimelinePoint, bucket_timeline, competency_series, window_label
from app.services.scoring.topics import content_tokens, dedupe_topics, topic_covered, topic_matches


def _p(t: float, key: str, score: float, conf: float = 0.5) -> TimelinePoint:
    return TimelinePoint(
        elapsed_seconds=t, competency_key=key, score=score, confidence=conf, evidence_count=1
    )


def test_last_value_per_window() -> None:
    points = [
        _p(60, "aws", 6.0),
        _p(400, "aws", 7.2),
        _p(300, "kubernetes", 6.5),
        _p(700, "kubernetes", 7.0),
        _p(1250, "aws", 7.8),
    ]
    windows = bucket_timeline(
        points, window_minutes=10, duration_seconds=1500, names={"aws": "AWS", "kubernetes": "Kubernetes"}
    )
    assert [w["label"] for w in windows] == ["Minute 0-10", "Minute 10-20", "Minute 20-30"]
    assert windows[0]["scores"] == {"aws": 7.2, "kubernetes": 6.5}
    assert windows[0]["summary"] == "Minute 0-10: AWS 7.2, Kubernetes 6.5"
    assert windows[1]["scores"] == {"kubernetes": 7.0}
    assert windows[2]["scores"] == {"aws": 7.8}
    assert windows[0]["details"]["aws"]["elapsed_seconds"] == 400


def test_empty_windows_kept_and_custom_width() -> None:
    windows = bucket_timeline([_p(30, "aws", 5.5), _p(890, "aws", 6.0)], window_minutes=5)
    assert len(windows) == 3
    assert windows[1]["scores"] == {} and windows[1]["summary"] == ""
    assert windows[2]["label"] == "Minute 10-15"


def test_boundary_point_opens_next_window() -> None:
    windows = bucket_timeline([_p(600, "aws", 6.0)], window_minutes=10)
    assert len(windows) == 2 and windows[1]["scores"] == {"aws": 6.0}


def test_no_points() -> None:
    windows = bucket_timeline([], window_minutes=10)
    assert len(windows) == 1 and windows[0]["scores"] == {}
    with pytest.raises(ValueError):
        bucket_timeline([], window_minutes=0)


def test_series_and_label() -> None:
    series = competency_series([_p(20, "aws", 6.0), _p(10, "aws", 5.0), _p(15, "k8s", 4.0)])
    assert [p["score"] for p in series["aws"]] == [5.0, 6.0]
    assert window_label(0, 150) == "Minute 0-2.5"


def test_topic_matching() -> None:
    assert content_tokens("Node Counts and autoscaling") == {"node", "count", "autoscal"}
    assert topic_matches("node count", "node_count")
    assert topic_matches("state management", "state_management")
    assert topic_matches("monitoring", "monitoring_and_alerting")
    assert topic_matches("ingress", "general", "Explained the ingress controller setup")
    assert not topic_matches("high availability", "cost_measurement", "talked about cost")
    assert topic_covered("autoscaling", [("hpa", ""), ("autoscaling", "")])
    assert topic_matches("scaling", "autoscaling")
    assert not topic_matches("ops", "devops_culture")
    assert not topic_matches("", "anything")


def test_dedupe_topics() -> None:
    assert dedupe_topics(["Node count", "node_count", " ", "Autoscaling", "autoscaling"]) == [
        "Node count",
        "Autoscaling",
    ]


def test_humanize_key() -> None:
    from app.services.scoring.persistence import humanize_key

    assert humanize_key("aws") == "AWS"
    assert humanize_key("ci_cd") == "CI/CD"
    assert humanize_key("api_design") == "API Design"
    assert humanize_key("slo_and_alerting") == "SLO and Alerting"
    assert humanize_key("cloud_cost_optimization") == "Cloud Cost Optimization"
