#!/usr/bin/env python3
"""Write Prometheus file-discovery targets for the shared AI endpoints from .env.

The model servers live at different addresses on each node (the head node serves the main LLM on
localhost:8000; a worker node reaches it over the interconnect and the other endpoints through a
tunnel), so the `vllm` scrape job reads infra/prometheus/targets/ai-endpoints.json, generated from the
same *_BASE_URL settings the API uses. Endpoints listed in AI_DISABLED_ENDPOINTS are left out.

    python3 scripts/prom_targets.py            # writes infra/prometheus/targets/ai-endpoints.json
    python3 scripts/prom_targets.py --check    # exit 1 when the file is missing or stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "infra" / "prometheus" / "targets" / "ai-endpoints.json"
# endpoint label -> .env key (the labels match the API's own `endpoint` label values)
ENDPOINTS = (
    ("main", "MAIN_LLM_BASE_URL", "http://localhost:8000/v1"),
    ("vision", "VISION_LLM_BASE_URL", "http://localhost:8002/v1"),
    ("embedding", "EMBEDDING_BASE_URL", "http://localhost:8003/v1"),
    ("reranker", "RERANKER_BASE_URL", "http://localhost:8005"),
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def host_port(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname  # the tunnel binds IPv4 only
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{host}:{port}"


def build(env: dict[str, str]) -> list[dict[str, object]]:
    disabled = {item.strip() for item in env.get("AI_DISABLED_ENDPOINTS", "").split(",") if item.strip()}
    groups: list[dict[str, object]] = []
    for label, key, default in ENDPOINTS:
        if label in disabled:
            continue
        target = host_port(env.get(key) or default)
        if target:
            groups.append({"targets": [target], "labels": {"endpoint": label}})
    return groups


def main(argv: list[str]) -> int:
    rendered = json.dumps(build(read_env(ROOT / ".env")), indent=2) + "\n"
    if "--check" in argv:
        current = TARGETS.read_text(encoding="utf-8") if TARGETS.exists() else ""
        if current != rendered:
            print(f"{TARGETS.relative_to(ROOT)} is missing or stale: run make prom-targets", file=sys.stderr)
            return 1
        return 0
    TARGETS.parent.mkdir(parents=True, exist_ok=True)
    TARGETS.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGETS.relative_to(ROOT)}: {', '.join(g['labels']['endpoint'] + '=' + g['targets'][0] for g in build(read_env(ROOT / '.env')))}")  # type: ignore[index]
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
