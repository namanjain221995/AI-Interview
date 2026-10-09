"""Guards the deployment architecture encoded in docker-compose.yml.

The resolved compose model (``docker compose config``) must keep the
networking model from DEPLOYMENT.md, publish stateful services on loopback
only, rotate logs everywhere, and contain no object storage service: storage is
the local `appdata` volume (no S3/MinIO).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
HOST_NETWORK = {"migrate", "api", "worker", "edge", "prometheus", "grafana"}
BACKEND_NETWORK = {"postgres", "redis", "tts", "sandbox", "postgres-exporter", "redis-exporter"}
OTHER_SYSTEMS_PORTS = {3000, 4000, 5050, 5432, 8000, 8002, 8003, 8005, 8080, 9000, 9090, 3300, 9835, 9838}


@pytest.fixture(scope="module")
def model() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    if not (REPO / ".env").exists():
        pytest.skip(".env missing (make setup)")
    result = subprocess.run(  # noqa: S603
        ["docker", "compose", "--project-directory", str(REPO), "--profile", "runtimes", "--profile", "security",  # noqa: S607
         "config", "--format", "json"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(result.stdout)


def test_project_name(model: dict[str, Any]) -> None:
    assert model["name"] == "interview"


def test_no_object_storage_anywhere(model: dict[str, Any]) -> None:
    text = json.dumps(model).lower()
    for forbidden in ("minio", "s3://", "aws_access_key", "boto"):
        assert forbidden not in text, forbidden
    for name, service in model["services"].items():
        image = service.get("image", "")
        assert "minio" not in name and "minio" not in image


def test_networking_model(model: dict[str, Any]) -> None:
    services = model["services"]
    for name in HOST_NETWORK:
        assert services[name].get("network_mode") == "host", name
    for name in BACKEND_NETWORK:
        assert "backend" in services[name].get("networks", {}), name
        assert services[name].get("network_mode") is None, name


def test_published_ports_are_loopback_only_and_free(model: dict[str, Any]) -> None:
    for name, service in model["services"].items():
        for port in service.get("ports", []):
            assert port.get("host_ip") == "127.0.0.1", (
                f"{name} publishes on {port.get('host_ip') or 'all interfaces'}"
            )
            assert int(port["published"]) not in OTHER_SYSTEMS_PORTS, (
                f"{name} uses a port owned by another system"
            )


def test_every_service_rotates_logs(model: dict[str, Any]) -> None:
    for name, service in model["services"].items():
        logging = service.get("logging", {})
        assert logging.get("driver") == "json-file", name
        assert logging["options"]["max-size"] and logging["options"]["max-file"], name


def test_shared_storage_volume_for_api_and_worker(model: dict[str, Any]) -> None:
    for name in ("api", "worker"):
        service = model["services"][name]
        assert service["environment"]["STORAGE_LOCAL_ROOT"] == "/srv/interview-data/storage"
        assert any(
            v["source"] == "appdata" and v["target"] == "/srv/interview-data" for v in service["volumes"]
        )


def test_cpu_heavy_and_privileged_services_have_limits(model: dict[str, Any]) -> None:
    for name in ("tts", "sandbox", "edge", "prometheus", "grafana"):
        limits = model["services"][name].get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("cpus") and limits.get("memory"), name


def test_edge_is_hardened(model: dict[str, Any]) -> None:
    edge = model["services"]["edge"]
    assert edge.get("read_only") is True
    assert "ALL" in edge.get("cap_drop", [])
    assert "no-new-privileges:true" in edge.get("security_opt", [])
    assert edge["environment"]["API_UPSTREAM"].startswith("127.0.0.1:")
    assert any(v["target"] == "/etc/caddy/Caddyfile" and v.get("read_only") for v in edge["volumes"])
