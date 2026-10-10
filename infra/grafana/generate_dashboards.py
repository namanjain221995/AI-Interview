#!/usr/bin/env python3
"""Generates the provisioned Grafana dashboards in infra/grafana/dashboards/.

    python3 infra/grafana/generate_dashboards.py          # write the JSON files
    python3 infra/grafana/generate_dashboards.py --check  # exit 1 if they are stale

Edit this file, not the JSON. Conventions:

* One y-axis per panel: measures with different units or scales get their own
  panels (small multiples), never a second axis.
* Colour follows the entity: endpoints, languages, services and tasks keep the
  same categorical slot on every dashboard (validated palette, dark steps,
  which pass the colour checks on both Grafana's dark and light panels).
  Unmapped series fall back to muted grey.
* Status colours (good / warning / serious / critical) only appear where the
  colour means good or bad: thresholds, HTTP status classes, job outcomes.
* p95 is the emphasised series; p50 is muted context. Threshold lines are
  dashed and use the critical colour.
* Headline numbers are stat tiles; magnitude across nominal categories is a
  sorted single-hue bar gauge; ratios against a limit are meters.

Metric names: docs/design/CONTRACTS.md section 8 and apps/api/app/core/metrics.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}

# Categorical slots (fixed order) and reserved status colours.
SLOT = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
MUTED = "#898781"
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"

ENDPOINT_COLORS = {
    "main": SLOT[0], "vision": SLOT[1], "embedding": SLOT[2], "reranker": SLOT[3],
    "whisper": SLOT[4], "ocr": SLOT[5], "tts": SLOT[6],
}  # fmt: skip
LANGUAGE_COLORS = {
    "python": SLOT[0], "javascript": SLOT[1], "typescript": SLOT[2], "java": SLOT[3],
    "go": SLOT[4], "cpp": SLOT[5], "sql": SLOT[6],
}  # fmt: skip
SERVICE_COLORS = {"api": SLOT[0], "worker": SLOT[1], "sandbox": SLOT[2], "edge": SLOT[3], "tts": SLOT[6]}
TURN_TASKS = ["turn_decision", "answer_assessment", "code_observation", "hint_generation"]
TASK_COLORS = {task: SLOT[i] for i, task in enumerate(TURN_TASKS)}
SOURCE_COLORS = {"browser": SLOT[0], "vision": SLOT[1], "screen": SLOT[2], "server": SLOT[3]}
STATUS_CLASS_COLORS = {"2xx": GOOD, "3xx": MUTED, "4xx": WARNING, "5xx": CRITICAL}
JOB_STATUS_COLORS = {
    "succeeded": GOOD, "success": GOOD, "ok": GOOD, "completed": GOOD,
    "retried": WARNING, "retry": WARNING,
    "failed": SERIOUS, "error": SERIOUS,
    "dead": CRITICAL,
}  # fmt: skip
TTS_PROVIDER_COLORS = {"kokoro": SLOT[6], "openai_compatible": SLOT[0]}
PG_STATE_COLORS = {
    "active": SLOT[0], "idle": SLOT[1], "idle in transaction": SLOT[3],
    "idle in transaction (aborted)": SLOT[7], "fastpath function call": SLOT[4], "disabled": MUTED,
}  # fmt: skip

TURN_TASK_RE = "|".join(TURN_TASKS)
APP_JOBS = 'job=~"interview-(api|worker|tts|sandbox|edge)"'


# =============================================================================
# Panel builders
# =============================================================================


def _target(expr: str, legend: str = "", ref: str = "A", instant: bool = False) -> dict[str, Any]:
    return {
        "refId": ref,
        "datasource": DS,
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend or "__auto",
        "range": not instant,
        "instant": instant,
    }


def _color_overrides(colors: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
        }
        for name, color in colors.items()
    ]


def _thresholds(steps: list[tuple[float | None, str]]) -> dict[str, Any]:
    return {"mode": "absolute", "steps": [{"value": value, "color": color} for value, color in steps]}


def status_thresholds(warn: float, crit: float) -> dict[str, Any]:
    # The base step only colours "No data": keep it muted, never green.
    return _thresholds([(None, MUTED), (0, GOOD), (warn, WARNING), (crit, CRITICAL)])


def stat(
    title: str,
    expr: str,
    *,
    unit: str = "short",
    description: str = "",
    decimals: int | None = None,
    thresholds: dict[str, Any] | None = None,
    graph: bool = True,
    no_value: str = "No data",
) -> dict[str, Any]:
    defaults: dict[str, Any] = {"unit": unit, "noValue": no_value}
    if decimals is not None:
        defaults["decimals"] = decimals
    if thresholds:
        defaults["color"] = {"mode": "thresholds"}
        defaults["thresholds"] = thresholds
    else:
        defaults["color"] = {"mode": "fixed", "fixedColor": SLOT[0]}
        defaults["thresholds"] = _thresholds([(None, SLOT[0])])
    return {
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": [_target(expr)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            # Status-coded tiles colour the value; neutral tiles keep text ink.
            "colorMode": "value" if thresholds else "none",
            "graphMode": "area" if graph else "none",
            "textMode": "value",
            "justifyMode": "auto",
            "orientation": "auto",
            "wideLayout": True,
            "showPercentChange": False,
        },
    }


def service_health(title: str, expr: str, legend: str, description: str) -> dict[str, Any]:
    """UP/DOWN tiles per scrape target: status colour always paired with a text label."""
    panel = stat(title, expr, description=description, graph=False)
    panel["targets"] = [_target(expr, legend)]
    panel["fieldConfig"]["defaults"].update(
        {
            "color": {"mode": "thresholds"},
            "thresholds": _thresholds([(None, MUTED), (0, CRITICAL), (1, GOOD)]),
            "mappings": [
                {"type": "value", "options": {"0": {"text": "DOWN", "color": CRITICAL, "index": 0}}},
                {"type": "value", "options": {"1": {"text": "UP", "color": GOOD, "index": 1}}},
            ],
            "noValue": "not scraped",
        }
    )
    panel["options"].update(
        {"colorMode": "background", "textMode": "value_and_name", "orientation": "vertical"}
    )
    return panel


def timeseries(
    title: str,
    targets: list[tuple[str, str]],
    *,
    unit: str = "short",
    description: str = "",
    colors: dict[str, str] | None = None,
    single_color: str | None = None,
    threshold: float | None = None,
    min_value: float | None = 0,
    max_value: float | None = None,
    decimals: int | None = None,
    fill: int = 0,
    stack: bool = False,
    axis_label: str = "",
    dashed: tuple[str, ...] = (),
) -> dict[str, Any]:
    single = len(targets) == 1 and not colors
    custom: dict[str, Any] = {
        "drawStyle": "line",
        "lineInterpolation": "linear",
        "lineWidth": 2,
        "fillOpacity": 10 if single and fill == 0 else fill,
        "gradientMode": "none",
        "showPoints": "never",
        "pointSize": 8,
        "spanNulls": False,
        "axisPlacement": "auto",
        "axisLabel": axis_label,
        "axisBorderShow": False,
        "axisCenteredZero": False,
        "scaleDistribution": {"type": "linear"},
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        "thresholdsStyle": {"mode": "dashed" if threshold is not None else "off"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    defaults: dict[str, Any] = {
        "unit": unit,
        "custom": custom,
        "color": {"mode": "fixed", "fixedColor": single_color or (SLOT[0] if single else MUTED)},
        "thresholds": _thresholds(
            [(None, "transparent")] + ([(threshold, CRITICAL)] if threshold is not None else [])
        ),
    }
    if min_value is not None:
        defaults["min"] = min_value
    if max_value is not None:
        defaults["max"] = max_value
    if decimals is not None:
        defaults["decimals"] = decimals
    overrides = _color_overrides(colors or {})
    for name in dashed:
        overrides.append(
            {
                "matcher": {"id": "byName", "options": name},
                "properties": [
                    {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                    {"id": "custom.lineWidth", "value": 1},
                    {"id": "color", "value": {"mode": "fixed", "fixedColor": MUTED}},
                ],
            }
        )
    refs = "ABCDEFGHIJ"
    return {
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": [_target(expr, legend, refs[i]) for i, (expr, legend) in enumerate(targets)],
        "fieldConfig": {"defaults": defaults, "overrides": overrides},
        "options": {
            # A single series is named by the title; two or more get a legend.
            "legend": {
                "showLegend": not single,
                "displayMode": "list",
                "placement": "bottom",
                "calcs": [],
            },
            "tooltip": {"mode": "multi", "sort": "desc", "hideZeros": False},
        },
    }


def quantiles(
    title: str,
    metric: str,
    *,
    selector: str = "",
    color: str = SLOT[0],
    threshold: float | None = None,
    description: str = "",
) -> dict[str, Any]:
    """p95 (emphasised) and p50 (muted context) of one histogram."""
    sel = f"{{{selector}}}" if selector else ""
    return timeseries(
        title,
        [
            (f"histogram_quantile(0.95, sum by (le) (rate({metric}_bucket{sel}[$__rate_interval])))", "p95"),
            (f"histogram_quantile(0.50, sum by (le) (rate({metric}_bucket{sel}[$__rate_interval])))", "p50"),
        ],
        unit="s",
        colors={"p95": color, "p50": MUTED},
        threshold=threshold,
        description=description,
    )


def meter(
    title: str,
    expr: str,
    legend: str,
    *,
    unit: str = "percentunit",
    description: str = "",
    decimals: int | None = None,
    meter: tuple[float, float],
    max_value: float | None = None,
) -> dict[str, Any]:
    """A single ratio against a limit: thin status-coloured bar plus the value."""
    defaults: dict[str, Any] = {
        "unit": unit,
        "min": 0,
        "noValue": "No data",
        "color": {"mode": "thresholds"},
        "thresholds": status_thresholds(*meter),
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    if max_value is not None:
        defaults["max"] = max_value
    return {
        "type": "bargauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": [_target(expr, legend, instant=True)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "orientation": "horizontal",
            "displayMode": "basic",
            "valueMode": "text",
            "namePlacement": "hidden",
            "showUnfilled": True,
            "sizing": "manual",
            "minVizHeight": 12,
            "maxVizHeight": 14,
            "minVizWidth": 8,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom", "calcs": []},
        },
    }


def ranking(
    title: str,
    expr: str,
    legend: str,
    *,
    unit: str = "short",
    description: str = "",
    decimals: int | None = None,
) -> dict[str, Any]:
    """Magnitude across nominal categories: a table sorted by value with an
    in-cell single-hue bar, so long names (queues, tasks, routes) stay whole
    and every value is readable as text (the table view of the chart)."""
    labels = re.findall(r"\{\{\s*(\w+)\s*\}\}", legend)
    if "p95" in title:
        value_label = "p95"
    elif "selected range" in title:
        value_label = "total"
    elif "(now)" in title:
        value_label = "current"
    else:
        value_label = "value"
    target = _target(expr, legend, instant=True)
    target["format"] = "table"
    value_props: list[dict[str, Any]] = [
        {"id": "displayName", "value": value_label},
        {"id": "unit", "value": unit},
        {"id": "min", "value": 0},
        {"id": "color", "value": {"mode": "fixed", "fixedColor": SLOT[0]}},
        {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
    ]
    if decimals is not None:
        value_props.append({"id": "decimals", "value": decimals})
    return {
        "type": "table",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": [target],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"Time": True},
                    "indexByName": {**{label: i for i, label in enumerate(labels)}, "Value": len(labels)},
                    "renameByName": {},
                },
            },
            {"id": "sortBy", "options": {"fields": {}, "sort": [{"field": "Value", "desc": True}]}},
        ],
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "inspect": False,
                    "filterable": False,
                },
                "noValue": "No data",
                "color": {"mode": "fixed", "fixedColor": SLOT[0]},
                "thresholds": _thresholds([(None, SLOT[0])]),
            },
            "overrides": [{"matcher": {"id": "byName", "options": "Value"}, "properties": value_props}],
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "fields": "", "countRows": False},
            "sortBy": [],
        },
    }


# =============================================================================
# Layout
# =============================================================================


class Dashboard:
    def __init__(self, uid: str, title: str, description: str, time_from: str = "now-6h") -> None:
        self.uid, self.title, self.description, self.time_from = uid, title, description, time_from
        self.panels: list[dict[str, Any]] = []
        self._x = 0
        self._y = 0
        self._row_h = 0

    def row(self, title: str) -> None:
        self._newline()
        self.panels.append(
            {
                "type": "row",
                "title": title,
                "collapsed": False,
                "panels": [],
                "gridPos": {"x": 0, "y": self._y, "w": 24, "h": 1},
            }
        )
        self._y += 1

    def add(self, panel: dict[str, Any], w: int, h: int) -> None:
        if self._x + w > 24:
            self._newline()
        panel["gridPos"] = {"x": self._x, "y": self._y, "w": w, "h": h}
        self.panels.append(panel)
        self._x += w
        self._row_h = max(self._row_h, h)

    def add_stacked(self, panels: list[dict[str, Any]], w: int, h: int) -> None:
        """Place panels on top of each other in one column of width w."""
        if self._x + w > 24:
            self._newline()
        for index, panel in enumerate(panels):
            panel["gridPos"] = {"x": self._x, "y": self._y + index * h, "w": w, "h": h}
            self.panels.append(panel)
        self._x += w
        self._row_h = max(self._row_h, h * len(panels))

    def _newline(self) -> None:
        if self._x:
            self._y += self._row_h
        self._x, self._row_h = 0, 0

    def render(self) -> dict[str, Any]:
        for index, panel in enumerate(self.panels, start=1):
            panel["id"] = index
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": ["interview"],
            "timezone": "browser",
            "editable": True,
            "graphTooltip": 1,
            "fiscalYearStartMonth": 0,
            "liveNow": False,
            "refresh": "30s",
            "schemaVersion": 40,
            "version": 1,
            "time": {"from": self.time_from, "to": "now"},
            "timepicker": {},
            "templating": {"list": []},
            "annotations": {
                "list": [
                    {
                        "builtIn": 1,
                        "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                        "enable": True,
                        "hide": True,
                        "iconColor": "rgba(0, 211, 255, 1)",
                        "name": "Annotations & Alerts",
                        "type": "dashboard",
                    }
                ]
            },
            "links": [
                {
                    "type": "dashboards",
                    "tags": ["interview"],
                    "title": "Interview dashboards",
                    "asDropdown": False,
                    "includeVars": False,
                    "keepTime": True,
                    "targetBlank": False,
                    "icon": "external link",
                }
            ],
            "panels": self.panels,
        }


def status_class(metric: str, selector: str, status_label: str) -> str:
    return (
        f"sum by (code_class) (label_replace(rate({metric}{{{selector}}}[$__rate_interval]), "
        f'"code_class", "${{1}}xx", "{status_label}", "([0-9])..")) '
    ).strip()


# =============================================================================
# Dashboards
# =============================================================================


def overview() -> Dashboard:
    d = Dashboard(
        "interview-overview",
        "Interview Platform — Overview",
        "Live interviews, voice-loop latency SLOs, API and edge health.",
        time_from="now-1h",
    )
    d.row("Service health")
    d.add(
        service_health(
            "Scrape targets",
            'max by (service) (up{job=~"interview-.*|postgres|redis"})',
            "{{service}}",
            "UP when Prometheus scraped the target in the last interval. postgres/redis reflect their exporters.",
        ),
        16, 3,
    )  # fmt: skip
    d.add(
        service_health(
            "Model endpoints",
            'max by (endpoint) (up{job="vllm"})',
            "{{endpoint}}",
            "Shared vLLM servers (read-only). Whisper exposes no metrics; see the AI Models dashboard.",
        ),
        8, 3,
    )  # fmt: skip

    d.row("Live interviews")
    d.add(stat("Active sessions", "sum(interview_active_sessions)", decimals=0,
               description="Interview sessions currently live across API processes."), 4, 4)  # fmt: skip
    d.add(stat("Open WebSockets", "sum(interview_ws_connections)", decimals=0,
               description="Open realtime connections (candidate rooms)."), 4, 4)  # fmt: skip
    d.add(
        stat(
            "Turn latency p95",
            "histogram_quantile(0.95, sum by (le) (rate(interview_turn_latency_seconds_bucket[5m])))",
            unit="s", thresholds=status_thresholds(2.5, 3),
            description="Last 5 minutes: candidate end-of-turn to first interviewer audio. SLO: p95 < 3 s.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        stat(
            "First audio p95",
            "histogram_quantile(0.95, sum by (le) (rate(interview_time_to_first_audio_seconds_bucket[5m])))",
            unit="s", thresholds=status_thresholds(1.5, 2),
            description="Last 5 minutes: interviewer utterance start to first TTS chunk sent. SLO: p95 < 2 s.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        stat(
            "API 5xx (5m)",
            '(sum(rate(interview_http_requests_total{status=~"5.."}[5m])) or vector(0))'
            " / sum(rate(interview_http_requests_total[5m]))",
            unit="percentunit", decimals=2, thresholds=status_thresholds(0.01, 0.05),
            description="Share of API responses with status 5xx. Alert above 5%.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(stat("Barge-ins (1h)", "sum(increase(interview_barge_ins_total[1h]))", decimals=0,
               description="Candidate interruptions of interviewer speech in the last hour."), 4, 4)  # fmt: skip

    d.row("Voice loop latency")
    d.add(
        quantiles(
            "Turn latency: candidate end-of-turn to first interviewer audio",
            "interview_turn_latency_seconds",
            threshold=3,
            description="Dashed line: 3 s SLO (alert TurnLatencyP95High).",
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        quantiles(
            "Time to first audio: utterance start to first TTS chunk",
            "interview_time_to_first_audio_seconds",
            threshold=2,
            description="Dashed line: 2 s SLO (alert TimeToFirstAudioP95High).",
        ),
        12, 8,
    )  # fmt: skip

    d.row("Sessions and speech")
    d.add(
        timeseries(
            "Live sessions and WebSocket connections",
            [("sum(interview_active_sessions)", "sessions"), ("sum(interview_ws_connections)", "websockets")],
            colors={"sessions": SLOT[0], "websockets": SLOT[1]},
            decimals=0,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Barge-ins per minute",
            [("sum(rate(interview_barge_ins_total[$__rate_interval])) * 60", "barge-ins")],
            description="Candidate speech that interrupted the interviewer (handled by the runtime).",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Integrity events per minute by source",
            [("sum by (source) (rate(interview_integrity_events_total[$__rate_interval])) * 60", "{{source}}")],
            colors=SOURCE_COLORS,
            description="Signals for human review only (never automatic decisions).",
        ),
        8, 8,
    )  # fmt: skip

    d.row("API and errors")
    d.add(
        timeseries(
            "API requests by status class",
            [(status_class("interview_http_requests_total", 'job="interview-api"', "status"), "{{code_class}}")],
            unit="reqps", colors=STATUS_CLASS_COLORS,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Slowest API routes (p95, last 5m)",
            "sort_desc(topk(10, histogram_quantile(0.95, sum by (le, route) "
            "(rate(interview_http_request_seconds_bucket[5m])))))",
            "{{route}}", unit="s",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Unexpected errors by component (selected range)",
            "sort_desc(sum by (component) (increase(interview_errors_total[$__range])) > 0)",
            "{{component}}", decimals=0,
            description="interview_errors_total: unexpected exceptions caught per component.",
        ),
        8, 8,
    )  # fmt: skip

    d.row("Edge and access")
    d.add(
        timeseries(
            "Edge requests by status class",
            [(
                status_class("caddy_http_request_duration_seconds_count",
                             'job="interview-edge", handler="headers"', "code"),
                "{{code_class}}",
            )],
            unit="reqps", colors=STATUS_CLASS_COLORS,
            description="Every request through Caddy (SPA, API, WebSocket upgrades). 502 = API unreachable.",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Staff sign-in outcomes per minute",
            [(
                'sum by (event) (rate(interview_auth_events_total{event=~"login_succeeded|login_failed|account_locked"}'
                "[$__rate_interval])) * 60",
                "{{event}}",
            )],
            colors={"login_succeeded": GOOD, "login_failed": WARNING, "account_locked": CRITICAL},
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Rate-limited requests by scope (selected range)",
            "sort_desc(sum by (scope) (increase(interview_rate_limited_total[$__range])) > 0)",
            "{{scope}}", decimals=0,
        ),
        8, 8,
    )  # fmt: skip
    return d


def ai_models() -> Dashboard:
    d = Dashboard(
        "interview-ai-models",
        "AI Models",
        "LLM latency and TTFT by task/endpoint, tokens, failures, speech/embedding/rerank/OCR latency, "
        "shared vLLM servers and the DGX GPU.",
    )
    d.row("At a glance")
    d.add(
        stat(
            "LLM requests/s", "sum(rate(interview_llm_request_seconds_count[5m]))", unit="reqps", decimals=2
        ),
        4,
        4,
    )
    d.add(
        stat(
            "LLM failures (5m)",
            '(sum(rate(interview_llm_request_seconds_count{status=~"error|timeout|circuit_open"}[5m])) or vector(0))'
            ' / sum(rate(interview_llm_request_seconds_count{status!="cancelled"}[5m]))',
            unit="percentunit", decimals=2, thresholds=status_thresholds(0.01, 0.05),
            description="error + timeout + circuit_open over all non-cancelled LLM requests.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        stat(
            "Turn TTFT p95",
            "histogram_quantile(0.95, sum by (le) (rate(interview_llm_ttft_seconds_bucket"
            f'{{endpoint="main", task=~"{TURN_TASK_RE}"}}[5m])))',
            unit="s", thresholds=status_thresholds(0.5, 1.0),
            description="Last 5 minutes: time to first token for turn-critical tasks on the main endpoint.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(stat("Main LLM running", 'sum(vllm:num_requests_running{job="vllm", endpoint="main"})', decimals=0,
               description="Requests executing on the shared main vLLM server (all clients)."), 4, 4)  # fmt: skip
    d.add(
        stat(
            "Main LLM waiting",
            'sum(vllm:num_requests_waiting{job="vllm", endpoint="main"})',
            decimals=0, thresholds=status_thresholds(1, 5),
            description="Queued requests on the shared main vLLM server; any queueing adds turn latency.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        meter(
            "Main LLM KV cache",
            'max(vllm:kv_cache_usage_perc{job="vllm", endpoint="main"})',
            "KV cache", unit="percentunit", decimals=1, meter=(0.8, 0.95), max_value=1,
        ),
        4, 4,
    )  # fmt: skip

    d.row("LLM latency (platform side)")
    d.add(
        timeseries(
            "LLM request latency p95 by endpoint",
            [(
                "histogram_quantile(0.95, sum by (le, endpoint) "
                '(rate(interview_llm_request_seconds_bucket{status!="cancelled"}[$__rate_interval])))',
                "{{endpoint}}",
            )],
            unit="s", colors=ENDPOINT_COLORS,
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Time to first token p95: turn-critical tasks (main)",
            [(
                "histogram_quantile(0.95, sum by (le, task) (rate(interview_llm_ttft_seconds_bucket"
                f'{{endpoint="main", task=~"{TURN_TASK_RE}"}}[$__rate_interval])))',
                "{{task}}",
            )],
            unit="s", colors=TASK_COLORS,
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Request latency p95 by task (last 15m)",
            "sort_desc(histogram_quantile(0.95, sum by (le, task) "
            '(rate(interview_llm_request_seconds_bucket{status!="cancelled"}[15m]))))',
            "{{task}}", unit="s",
        ),
        12, 9,
    )  # fmt: skip
    d.add(
        ranking(
            "Time to first token p95 by task (last 15m)",
            "sort_desc(histogram_quantile(0.95, sum by (le, task) (rate(interview_llm_ttft_seconds_bucket[15m]))))",
            "{{task}}", unit="s",
        ),
        12, 9,
    )  # fmt: skip

    d.row("Throughput and failures")
    d.add(
        timeseries(
            "Completion tokens/s by endpoint (this platform)",
            [('sum by (endpoint) (rate(interview_llm_tokens_total{kind="completion"}[$__rate_interval]))', "{{endpoint}}")],
            unit="suffix: tok/s", colors=ENDPOINT_COLORS,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Generation tokens/s by vLLM server (all clients)",
            [(
                'sum by (endpoint) (rate(vllm:generation_tokens_total{job="vllm", endpoint=~"main|vision"}'
                "[$__rate_interval]))",
                "{{endpoint}}",
            )],
            unit="suffix: tok/s", colors=ENDPOINT_COLORS,
            description="Server-side throughput of the shared models, including the other stack's load.",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Model call failures per minute by endpoint",
            [(
                'sum by (endpoint) (rate(interview_model_call_failures_total{reason!="cancelled"}[$__rate_interval])) * 60',
                "{{endpoint}}",
            )],
            colors=ENDPOINT_COLORS,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Failures by endpoint and reason (selected range)",
            "sort_desc(sum by (endpoint, reason) (increase(interview_model_call_failures_total[$__range])) > 0)",
            "{{endpoint}} / {{reason}}", decimals=0,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Completion tokens by task (selected range)",
            'sort_desc(sum by (task) (increase(interview_llm_tokens_total{kind="completion"}[$__range])) > 0)',
            "{{task}}", decimals=0,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Prompt tokens by task (selected range)",
            'sort_desc(sum by (task) (increase(interview_llm_tokens_total{kind="prompt"}[$__range])) > 0)',
            "{{task}}", decimals=0,
        ),
        8, 8,
    )  # fmt: skip

    d.row("Speech, embeddings, rerank, OCR")
    d.add(
        timeseries(
            "Whisper STT latency p95 by kind",
            [(
                "histogram_quantile(0.95, sum by (le, kind) (rate(interview_stt_latency_seconds_bucket[$__rate_interval])))",
                "{{kind}}",
            )],
            unit="s", colors={"final": SLOT[4], "partial": SLOT[0]},
            description="final = end-of-turn transcription (on the turn path); partial = live captions.",
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "TTS chunk latency p95 by provider",
            [(
                "histogram_quantile(0.95, sum by (le, provider) (rate(interview_tts_latency_seconds_bucket[$__rate_interval])))",
                "{{provider}}",
            )],
            unit="s", colors=TTS_PROVIDER_COLORS,
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        quantiles("Embedding latency", "interview_embedding_seconds", color=ENDPOINT_COLORS["embedding"]),
        8,
        7,
    )
    d.add(quantiles("Rerank latency", "interview_rerank_seconds", color=ENDPOINT_COLORS["reranker"]), 8, 7)
    d.add(quantiles("OCR latency per page", "interview_ocr_seconds", color=ENDPOINT_COLORS["ocr"]), 8, 7)

    d.row("Shared vLLM servers (read-only)")
    d.add(
        timeseries("Running requests by server", [('sum by (endpoint) (vllm:num_requests_running{job="vllm"})', "{{endpoint}}")],
                   colors=ENDPOINT_COLORS, decimals=0),
        6, 8,
    )  # fmt: skip
    d.add(
        timeseries("Waiting requests by server", [('sum by (endpoint) (vllm:num_requests_waiting{job="vllm"})', "{{endpoint}}")],
                   colors=ENDPOINT_COLORS, decimals=0),
        6, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "KV cache usage by server",
            [('max by (endpoint) (vllm:kv_cache_usage_perc{job="vllm"})', "{{endpoint}}")],
            unit="percentunit", colors=ENDPOINT_COLORS, max_value=1, threshold=0.95,
        ),
        6, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Server-side TTFT p95 by server",
            [(
                "histogram_quantile(0.95, sum by (le, endpoint) (rate(vllm:time_to_first_token_seconds_bucket"
                '{job="vllm", endpoint=~"main|vision"}[$__rate_interval])))',
                "{{endpoint}}",
            )],
            unit="s", colors=ENDPOINT_COLORS,
        ),
        6, 8,
    )  # fmt: skip

    d.row("GPU (DGX exporter, read-only)")
    d.add(timeseries("GPU utilisation", [("max(dgx_gpu_utilization_percent)", "utilisation")],
                     unit="percent", max_value=100), 6, 7)  # fmt: skip
    d.add(
        timeseries(
            "GPU memory allocated", [("sum(dgx_gpu_memory_allocated_bytes)", "allocated")], unit="bytes"
        ),
        6,
        7,
    )
    d.add(
        meter(
            "GPU memory share",
            'sum(dgx_gpu_memory_allocated_bytes) / max(node_memory_MemTotal_bytes{job="host"})',
            "allocated", unit="percentunit", decimals=1, meter=(0.85, 0.95), max_value=1,
            description="GB10 GPU memory is unified with host RAM: GPU allocations over total host memory.",
        ),
        4, 7,
    )  # fmt: skip
    d.add(timeseries("GPU temperature", [("max(dgx_gpu_temperature_celsius)", "temperature")], unit="celsius",
                     min_value=None), 4, 7)  # fmt: skip
    d.add(timeseries("GPU power", [("max(dgx_gpu_power_watts)", "power")], unit="watt"), 4, 7)
    d.row("Endpoint reachability (deep readiness probe)")
    d.add(
        timeseries(
            "AI endpoints up (1 up, 0 down, -1 unknown or disabled)",
            [("min by (endpoint) (interview_ai_endpoint_up)", "{{endpoint}}")],
            decimals=0,
            description="From the 15 s cached health probe behind /readyz?deep=1 and the staff system-health page. "
            "main, whisper and tts are required for interviews; vision, embedding, reranker and ocr only degrade features.",
        ),
        16, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "VAD engine in use",
            [("max by (engine) (interview_vad_engine_info)", "{{engine}}")],
            decimals=0,
            description="silero is expected; energy means the Silero model file was missing or failed to load (turn-taking degrades).",
        ),
        8, 8,
    )  # fmt: skip

    d.row("Circuit breakers and call ledger")
    d.add(
        timeseries(
            "Circuit breaker state by endpoint",
            [("max by (endpoint) (interview_circuit_breaker_state)", "{{endpoint}}")],
            decimals=0,
            description="0 closed (healthy), 1 half-open (probing), 2 open (calls fail fast and fall back).",
        ),
        16, 8,
    )  # fmt: skip
    d.add(
        stat(
            "Ledger records dropped (range)",
            "sum(increase(interview_model_call_ledger_dropped_total[$__range])) or vector(0)",
            decimals=0, thresholds=status_thresholds(1, 100),
            description="model_calls ledger records lost because the write queue was full or closed; should stay 0.",
        ),
        8, 8,
    )  # fmt: skip

    return d


def workers_infra() -> Dashboard:
    d = Dashboard(
        "interview-workers-infra",
        "Workers & Infrastructure",
        "Job queues and outcomes, coding sandbox, database, Redis, process and host resources.",
    )
    d.row("Queues and jobs")
    d.add(
        stat("Pending jobs", 'sum(interview_queue_depth{queue!~"(jobs:)?dead"})', decimals=0,
             thresholds=status_thresholds(25, 200), description="Messages waiting in the job streams (dead letters excluded)."),
        4, 4,
    )  # fmt: skip
    d.add(stat("Jobs/min (5m)", "sum(rate(interview_jobs_total[5m])) * 60", decimals=1), 4, 4)
    d.add(
        stat(
            "Job failures (15m)",
            '(sum(rate(interview_jobs_total{status=~"failed|dead"}[15m])) or vector(0)) / sum(rate(interview_jobs_total[15m]))',
            unit="percentunit", decimals=1, thresholds=status_thresholds(0.05, 0.2),
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        stat(
            "Dead letters",
            # 0 when the exporter is up but the stream does not exist yet.
            'max(redis_stream_length{job="redis", stream="jobs:dead"}) or on () (max(redis_up{job="redis"}) * 0)',
            decimals=0, thresholds=status_thresholds(1, 25),
            description="Length of the jobs:dead stream: jobs that exhausted their retries.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(stat("Jobs in flight", "sum(interview_jobs_in_flight)", decimals=0), 4, 4)
    d.add(stat("Coding runs/min", "sum(rate(interview_coding_executions_total[5m])) * 60", decimals=1), 4, 4)

    d.add(timeseries("Pending jobs (all queues)", [('sum(interview_queue_depth{queue!~"(jobs:)?dead"})', "pending")],
                     decimals=0), 8, 8)  # fmt: skip
    d.add(
        ranking("Pending jobs by queue (now)", "sort_desc(sum by (queue) (interview_queue_depth))", "{{queue}}", decimals=0),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Jobs per minute by outcome",
            [("sum by (status) (rate(interview_jobs_total[$__rate_interval])) * 60", "{{status}}")],
            colors=JOB_STATUS_COLORS,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Job duration p95 by type (last 15m)",
            "sort_desc(histogram_quantile(0.95, sum by (le, type) (rate(interview_job_seconds_bucket[15m]))))",
            "{{type}}", unit="s",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking("Jobs by type (selected range)", "sort_desc(sum by (type) (increase(interview_jobs_total[$__range])) > 0)",
                 "{{type}}", decimals=0),
        8, 8,
    )  # fmt: skip
    d.add(timeseries("Jobs in flight", [("sum(interview_jobs_in_flight)", "in flight")], decimals=0), 8, 8)

    d.row("Coding sandbox")
    d.add(
        timeseries(
            "Executions per minute by outcome",
            [
                ('sum(rate(interview_coding_executions_total{status!~"sandbox_error|rejected"}[$__rate_interval])) * 60', "completed (any verdict)"),
                ('sum(rate(interview_coding_executions_total{status="sandbox_error"}[$__rate_interval])) * 60', "sandbox error"),
                ('sum(rate(interview_coding_executions_total{status="rejected"}[$__rate_interval])) * 60', "rejected"),
            ],
            colors={"completed (any verdict)": MUTED, "sandbox error": CRITICAL, "rejected": SERIOUS},
            description="Candidate verdicts (passed, failed, compile_error, timeout, memory_exceeded) are grey; "
            "platform-side failures are status-coloured.",
        ),
        8, 9,
    )  # fmt: skip
    d.add(
        timeseries(
            "Executions per minute by language",
            [("sum by (language) (rate(interview_coding_executions_total[$__rate_interval])) * 60", "{{language}}")],
            colors=LANGUAGE_COLORS,
        ),
        8, 9,
    )  # fmt: skip
    d.add(
        ranking(
            "Execution round trip p95 by language (last 15m)",
            "sort_desc(histogram_quantile(0.95, sum by (le, language) (rate(interview_coding_execution_seconds_bucket[15m]))))",
            "{{language}}", unit="s",
        ),
        8, 9,
    )  # fmt: skip

    d.row("Database and Redis")
    d.add(
        quantiles("Database operation latency (all operations)", "interview_db_query_seconds",
                  description="interview_db_query_seconds across operations."),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Slowest database operations (p95, last 15m)",
            "sort_desc(topk(10, histogram_quantile(0.95, sum by (le, operation) (rate(interview_db_query_seconds_bucket[15m])))))",
            "{{operation}}", unit="s",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Postgres connections by state",
            [('sum by (state) (pg_stat_activity_count{job="postgres"})', "{{state}}")],
            colors=PG_STATE_COLORS, decimals=0,
            description="Alert PostgresConnectionsHigh fires above 80% of max_connections.",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Postgres transactions per second",
            [
                ('sum(rate(pg_stat_database_xact_commit{job="postgres", datname!~"template.*|postgres"}[$__rate_interval]))', "commits"),
                ('sum(rate(pg_stat_database_xact_rollback{job="postgres", datname!~"template.*|postgres"}[$__rate_interval]))', "rollbacks"),
            ],
            unit="ops", colors={"commits": SLOT[0], "rollbacks": SLOT[1]},
        ),
        6, 8,
    )  # fmt: skip
    d.add(
        timeseries("Database size", [('sum(pg_database_size_bytes{job="postgres", datname!~"template.*|postgres"})', "size")],
                   unit="bytes"),
        6, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Redis memory used", [('max(redis_memory_used_bytes{job="redis"})', "used")], unit="bytes"
        ),
        6,
        8,
    )
    d.add(
        ranking("Redis stream lengths (now)", 'sort_desc(max by (stream) (redis_stream_length{job="redis"}))', "{{stream}}",
                 decimals=0, description="Job streams (jobs:<type>) and the dead-letter stream."),
        6, 8,
    )  # fmt: skip

    d.row("Processes and host")
    d.add(
        timeseries(
            "Process CPU by service",
            [(f"sum by (service) (rate(process_cpu_seconds_total{{{APP_JOBS}}}[$__rate_interval]))", "{{service}}")],
            unit="short", colors=SERVICE_COLORS, axis_label="cores",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Process memory (RSS) by service",
            [(f"sum by (service) (process_resident_memory_bytes{{{APP_JOBS}}})", "{{service}}")],
            unit="bytes", colors=SERVICE_COLORS,
        ),
        8, 8,
    )  # fmt: skip
    d.add_stacked(
        [
            meter(
                "Root filesystem used",
                '1 - max(node_filesystem_avail_bytes{job="host", mountpoint="/"})'
                ' / max(node_filesystem_size_bytes{job="host", mountpoint="/"})',
                "/", unit="percentunit", decimals=1, meter=(0.8, 0.9), max_value=1,
                description="Docker volumes (pgdata, appdata, promdata) live on this filesystem.",
            ),
            meter(
                "Host memory used",
                '1 - max(node_memory_MemAvailable_bytes{job="host"}) / max(node_memory_MemTotal_bytes{job="host"})',
                "RAM", unit="percentunit", decimals=1, meter=(0.85, 0.95), max_value=1,
                description="Unified memory: shared with GPU allocations of the local models.",
            ),
        ],
        8, 4,
    )  # fmt: skip
    d.add(
        timeseries(
            "Host CPU busy",
            [('1 - avg(rate(node_cpu_seconds_total{job="host", mode="idle"}[$__rate_interval]))', "busy")],
            unit="percentunit", max_value=1,
            description="All 20 cores; the host is shared with another production AI stack.",
        ),
        12, 7,
    )  # fmt: skip
    d.add(timeseries("Host memory available", [('max(node_memory_MemAvailable_bytes{job="host"})', "available")],
                     unit="bytes"), 12, 7)  # fmt: skip
    d.row("Session lifecycle (abandoned sessions)")
    d.add(
        stat(
            "Abandoned sessions pending",
            "max(interview_sessions_abandoned_pending) or vector(0)",
            decimals=0, thresholds=status_thresholds(1, 10),
            description="Started sessions idle past SESSION_ABANDON_AFTER_SECONDS that the last sweep could not end "
            "(lock held, Redis unreachable or SESSION_SWEEP_DRY_RUN).",
        ),
        6, 6,
    )  # fmt: skip
    d.add(
        timeseries(
            "Sessions ended offline per hour by reason",
            [("sum by (reason, actor) (increase(interview_sessions_ended_offline_total[1h]))", "{{reason}} ({{actor}})")],
            decimals=0,
            description="disconnect_timeout = the abandoned-session sweep; cancelled = staff cancel; candidate_ended = REST end.",
        ),
        18, 6,
    )  # fmt: skip

    d.row("Intake interviews and open links")
    d.add(
        timeseries(
            "Open-link registrations per minute by outcome",
            [("sum by (outcome) (rate(interview_open_link_registrations_total[$__rate_interval])) * 60", "{{outcome}}")],
            decimals=1,
            description="registered / resumed are normal; blocked, rejected and unavailable spikes suggest abuse or a full link.",
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        timeseries(
            "Intake activities ended per minute by end reason",
            [("sum by (end_reason) (rate(interview_intake_activities_total[$__rate_interval])) * 60", "{{end_reason}}")],
            decimals=1,
        ),
        12, 8,
    )  # fmt: skip
    d.add(
        stat(
            "Intake invitations (range)",
            "sum(increase(interview_intake_invitations_total[$__range])) or vector(0)",
            decimals=0, description="Single, batch and draft invitations sent in the selected range.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        stat(
            "Intake evaluation p95",
            "histogram_quantile(0.95, sum by (le) (rate(interview_intake_evaluation_seconds_bucket[$__range])))",
            unit="s", decimals=0, thresholds=status_thresholds(180, 600),
            description="All evaluators plus scoring; the candidate waits for nothing, reviewers do.",
        ),
        4, 4,
    )  # fmt: skip
    d.add(
        ranking(
            "Intake evaluations by status (range)",
            "sort_desc(sum by (status) (increase(interview_intake_evaluations_total[$__range])) > 0)",
            "{{status}}", decimals=0,
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Intake stage duration p50 (range)",
            "sort_desc(histogram_quantile(0.5, sum by (le, stage) (rate(interview_intake_stage_seconds_bucket[$__range]))))",
            "{{stage}}", unit="s",
        ),
        8, 8,
    )  # fmt: skip
    d.add(
        ranking(
            "Human intake decisions (range)",
            "sort_desc(sum by (outcome, overrides_ai) (increase(interview_intake_decisions_total[$__range])) > 0)",
            "{{outcome}} (overrides AI: {{overrides_ai}})", decimals=0,
        ),
        8, 8,
    )  # fmt: skip

    return d


DASHBOARDS = {
    "interview-overview.json": overview,
    "interview-ai-models.json": ai_models,
    "interview-workers-infra.json": workers_infra,
}


def render_all() -> dict[str, str]:
    return {
        name: json.dumps(build().render(), indent=2, ensure_ascii=False) + "\n"
        for name, build in DASHBOARDS.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed JSON differs from the generator"
    )
    args = parser.parse_args()
    rendered = render_all()
    stale = [
        name
        for name, text in rendered.items()
        if not (OUT_DIR / name).exists() or (OUT_DIR / name).read_text() != text
    ]
    if args.check:
        for name in stale:
            print(
                f"stale: infra/grafana/dashboards/{name} (run: python3 infra/grafana/generate_dashboards.py)"
            )
        return 1 if stale else 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (OUT_DIR / name).write_text(text)
        print(f"wrote infra/grafana/dashboards/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
