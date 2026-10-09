"""Static and live checks for the Grafana dashboards and Prometheus rules.

* the committed dashboard JSON matches infra/grafana/generate_dashboards.py,
* dashboards are structurally sound (ids, grid, datasource),
* every interview_* metric used by a dashboard or an alert exists in
  apps/api/app/core/metrics.py (catches renames in the API),
* every PromQL expression parses on the running Prometheus (skipped when
  127.0.0.1:9191 is not reachable).
"""

from __future__ import annotations

import importlib.util
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO / "infra" / "grafana" / "dashboards"
GENERATOR = REPO / "infra" / "grafana" / "generate_dashboards.py"
ALERTS = REPO / "infra" / "prometheus" / "rules" / "alerts.yml"
PROMETHEUS_YML = REPO / "infra" / "prometheus" / "prometheus.yml"
METRICS_PY = REPO / "apps" / "api" / "app" / "core" / "metrics.py"
DATASOURCE_YML = REPO / "infra" / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
PROM_URL = "http://127.0.0.1:9191"

# Metric families that come from exporters or shared servers, not from metrics.py.
EXTERNAL_PREFIXES = ("up", "pg_", "redis_", "vllm:", "caddy_", "node_", "dgx_gpu_", "process_", "prometheus_")


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("generate_dashboards", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dashboards() -> dict[str, dict[str, Any]]:
    return {path.name: json.loads(path.read_text()) for path in sorted(DASHBOARD_DIR.glob("*.json"))}


def _panel_exprs() -> list[tuple[str, str, str]]:
    found = []
    for name, dashboard in _dashboards().items():
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                found.append((name, panel["title"], target["expr"]))
    return found


def _alert_exprs() -> list[tuple[str, str]]:
    rules = yaml.safe_load(ALERTS.read_text())
    return [(rule["alert"], rule["expr"]) for group in rules["groups"] for rule in group["rules"]]


def _defined_metrics() -> set[str]:
    return set(re.findall(r"""["'](interview_[a-z0-9_]+)["']""", METRICS_PY.read_text()))


def _used_interview_metrics(expr: str) -> set[str]:
    names = set()
    for token in re.findall(r"\binterview_[a-z0-9_]+", expr):
        names.add(re.sub(r"_(bucket|count|sum)$", "", token))
    return names


# ----------------------------------------------------------------------------- dashboards


def test_dashboard_json_is_generated_from_the_generator() -> None:
    rendered = _load_generator().render_all()
    committed = {path.name: path.read_text() for path in DASHBOARD_DIR.glob("*.json")}
    assert committed == rendered, "run: python3 infra/grafana/generate_dashboards.py"


def test_expected_dashboards_exist_with_unique_uids() -> None:
    dashboards = _dashboards()
    titles = {d["title"] for d in dashboards.values()}
    assert {"Interview Platform — Overview", "AI Models", "Workers & Infrastructure"} <= titles
    uids = [d["uid"] for d in dashboards.values()]
    assert len(uids) == len(set(uids))


@pytest.mark.parametrize("name", sorted(p.name for p in DASHBOARD_DIR.glob("*.json")))
def test_dashboard_panels_are_well_formed(name: str) -> None:
    dashboard = _dashboards()[name]
    panels = dashboard["panels"]
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids)), "duplicate panel ids"
    occupied: set[tuple[int, int]] = set()
    for panel in panels:
        grid = panel["gridPos"]
        assert 0 <= grid["x"] and grid["x"] + grid["w"] <= 24, panel["title"]
        cells = {
            (x, y)
            for x in range(grid["x"], grid["x"] + grid["w"])
            for y in range(grid["y"], grid["y"] + grid["h"])
        }
        assert not cells & occupied, f"panel overlaps another: {panel['title']}"
        occupied |= cells
        if panel["type"] == "row":
            continue
        assert panel["title"].strip()
        assert panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}
        assert panel["targets"], panel["title"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "prometheus"
            assert target["expr"].strip()


def test_datasource_provisioning_matches_dashboard_uid() -> None:
    config = yaml.safe_load(DATASOURCE_YML.read_text())
    (datasource,) = config["datasources"]
    assert datasource["uid"] == "prometheus"
    assert datasource["url"] == PROM_URL


# ----------------------------------------------------------------------------- metric names


def test_dashboards_and_alerts_only_use_defined_platform_metrics() -> None:
    defined = _defined_metrics()
    assert "interview_turn_latency_seconds" in defined  # parser sanity check
    used: dict[str, set[str]] = {}
    for source, title, expr in _panel_exprs():
        for metric in _used_interview_metrics(expr):
            used.setdefault(metric, set()).add(f"{source}: {title}")
    for alert, expr in _alert_exprs():
        for metric in _used_interview_metrics(expr):
            used.setdefault(metric, set()).add(f"alerts.yml: {alert}")
    unknown = {metric: sorted(where) for metric, where in used.items() if metric not in defined}
    assert not unknown, f"metrics not defined in app/core/metrics.py: {unknown}"


def test_every_contract_metric_is_charted_somewhere() -> None:
    charted = set()
    for _, _, expr in _panel_exprs():
        charted |= _used_interview_metrics(expr)
    missing = _defined_metrics() - charted
    assert not missing, f"metrics without a dashboard panel: {sorted(missing)}"


def test_alerts_have_severity_and_annotations() -> None:
    rules = yaml.safe_load(ALERTS.read_text())
    for group in rules["groups"]:
        for rule in group["rules"]:
            assert rule["labels"]["severity"] in {"critical", "warning"}, rule["alert"]
            assert rule["annotations"]["summary"], rule["alert"]
            assert rule["annotations"]["description"], rule["alert"]


def test_scrape_config_covers_the_platform_services() -> None:
    config = yaml.safe_load(PROMETHEUS_YML.read_text())
    targets = {
        job["job_name"]: [t for sc in job["static_configs"] for t in sc["targets"]]
        for job in config["scrape_configs"]
    }
    assert targets["interview-api"] == ["127.0.0.1:8200"]
    assert targets["interview-worker"] == ["127.0.0.1:8201"]
    assert targets["interview-tts"] == ["127.0.0.1:8210"]
    assert targets["interview-sandbox"] == ["127.0.0.1:8220"]
    assert sorted(targets["vllm"]) == ["127.0.0.1:8000", "127.0.0.1:8002", "127.0.0.1:8003", "127.0.0.1:8005"]
    assert targets["dgx-gpu"] == ["127.0.0.1:9835"]


# ----------------------------------------------------------------------------- live PromQL


def _prometheus_up() -> bool:
    try:
        with urllib.request.urlopen(f"{PROM_URL}/-/ready", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _substitute_grafana_variables(expr: str) -> str:
    return expr.replace("$__rate_interval", "1m").replace("$__range", "1h").replace("$__interval", "1m")


ALL_EXPRS = [(f"{src}: {title}", expr) for src, title, expr in _panel_exprs()] + [
    (f"alert {name}", expr) for name, expr in _alert_exprs()
]


@pytest.mark.skipif(not _prometheus_up(), reason="Prometheus is not running on 127.0.0.1:9191")
@pytest.mark.parametrize(("where", "expr"), ALL_EXPRS, ids=[w for w, _ in ALL_EXPRS])
def test_promql_parses_on_the_running_prometheus(where: str, expr: str) -> None:
    query = urllib.parse.urlencode({"query": _substitute_grafana_variables(expr)}).encode()
    request = urllib.request.Request(f"{PROM_URL}/api/v1/query", data=query, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        body = json.load(exc)
    assert body["status"] == "success", f"{where}: {body.get('error')}"
