"""Fixtures that run the real edge Caddyfile in a throwaway Caddy container.

The container uses host networking (exactly like the compose `edge` service)
on free high ports, in front of a fake upstream API (``fake_upstream.py``).
By default the stock ``caddy`` image is used so the Caddyfile can be tested
without building the SPA; set ``EDGE_TEST_IMAGE=interview-edge:local`` to run
the same tests against the built edge image (non-root user).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fake_upstream import UpstreamServer

REPO_ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = REPO_ROOT / "infra" / "caddy" / "Caddyfile"
STOCK_IMAGE = "caddy:2.11.7-alpine"
EDGE_IMAGE = os.environ.get("EDGE_TEST_IMAGE", STOCK_IMAGE)
EXTRA_TLS_HOSTNAMES = "interview.test edge.example"

INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><title>Swayam by TechSara</title>
<script type="module" src="/assets/app-3f9a1c7e.js"></script></head>
<body><div id="root"></div></body></html>
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))  # noqa: S104 - probing for a free port on every interface
        return int(sock.getsockname()[1])


def non_loopback_ipv4() -> str | None:
    """An address of this host that is not loopback (the source IP of the default route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: nothing is sent for UDP connect
            address = str(sock.getsockname()[0])
        except OSError:
            return None
    return None if address.startswith("127.") else address


@dataclass
class Edge:
    container: str
    http_port: int
    https_port: int
    tunnel_port: int
    upstream_port: int

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def tunnel(self) -> str:
        return f"http://127.0.0.1:{self.tunnel_port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.http_port}"

    def logs(self) -> str:
        result = subprocess.run(  # noqa: S603
            ["docker", "logs", self.container],
            capture_output=True,
            text=True,
            check=False,  # noqa: S607
        )
        return result.stdout + result.stderr


def _write_site(root: Path) -> None:
    (root / "assets").mkdir(parents=True)
    (root / "avatars").mkdir()
    (root / "index.html").write_text(INDEX_HTML)
    script = (
        "export const lines = [\n"
        + "".join(f'  "question {i}: tell me about a project",\n' for i in range(2000))
        + "];\n"
    )
    (root / "assets" / "app-3f9a1c7e.js").write_text(script)
    # Build-time precompressed sibling (what apps/web/docker/precompress.mjs emits).
    gz = zlib.compressobj(9, zlib.DEFLATED, 31)
    (root / "assets" / "app-3f9a1c7e.js.gz").write_bytes(gz.compress(script.encode()) + gz.flush())
    (root / "assets" / "style-1a2b3c4d.css").write_text(
        "".join(f".c{i}{{color:#{i % 999:03d}}}\n" for i in range(3000))
    )
    (root / "avatars" / "interviewer.glb").write_bytes(b"glTF" + b"\x02\x00\x00\x00" + bytes(4096))
    (root / "vad.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00" + bytes(2048))
    (root / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    # Readable by the non-root user of the edge image.
    for path in [root, *root.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def _start_edge(site_root: Path, upstream_port: int) -> Edge:
    http_port, https_port, tunnel_port = free_port(), free_port(), free_port()
    name = f"interview-test-edge-{uuid.uuid4().hex[:8]}"
    cmd = [
        "docker", "run", "-d", "--name", name, "--label", "interview.test=edge", "--network", "host",
        "-e", f"EDGE_HTTP_PORT={http_port}", "-e", f"EDGE_HTTPS_PORT={https_port}",
        "-e", f"EDGE_TUNNEL_PORT={tunnel_port}",
        "-e", f"API_UPSTREAM=127.0.0.1:{upstream_port}", "-e", "EDGE_SITE_ROOT=/srv/www",
        "-e", f"EDGE_TLS_HOSTNAMES={EXTRA_TLS_HOSTNAMES}", "-e", "EDGE_HSTS=max-age=31536000",
        "-v", f"{CADDYFILE}:/etc/caddy/Caddyfile:ro", "-v", f"{site_root}:/srv/www:ro",
    ]  # fmt: skip
    if EDGE_IMAGE == STOCK_IMAGE:
        cmd += ["--tmpfs", "/run/caddy"]  # the edge image creates this directory itself
    cmd.append(EDGE_IMAGE)
    container = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603
    edge = Edge(
        container=container,
        http_port=http_port,
        https_port=https_port,
        tunnel_port=tunnel_port,
        upstream_port=upstream_port,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{edge.http}/__edge/health", timeout=1).status_code == 200:
                return edge
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    logs = edge.logs()
    _stop(edge)
    raise RuntimeError(f"edge container did not become healthy:\n{logs[-4000:]}")


def _stop(edge: Edge) -> None:
    subprocess.run(["docker", "rm", "-f", edge.container], capture_output=True, check=False)  # noqa: S603,S607


@pytest.fixture(scope="session", autouse=True)
def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    probe = subprocess.run(["docker", "image", "inspect", EDGE_IMAGE], capture_output=True, check=False)  # noqa: S603,S607
    if probe.returncode != 0:
        pytest.skip(f"image {EDGE_IMAGE} not present locally (docker pull {EDGE_IMAGE})")


@pytest.fixture(scope="session")
def site_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("edge-site")
    _write_site(root)
    return root


@pytest.fixture(scope="session")
def upstream() -> Iterator[UpstreamServer]:
    server = UpstreamServer(free_port())
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def edge(site_root: Path, upstream: UpstreamServer) -> Iterator[Edge]:
    instance = _start_edge(site_root, upstream.port)
    yield instance
    _stop(instance)


@pytest.fixture(scope="session")
def edge_without_api(site_root: Path) -> Iterator[Edge]:
    """An edge whose API upstream is a closed port (API down / restarting)."""
    instance = _start_edge(site_root, free_port())
    yield instance
    _stop(instance)


@pytest.fixture(scope="session")
def lan_ip() -> str:
    address = non_loopback_ipv4()
    if address is None:
        pytest.skip("no non-loopback IPv4 address on this host")
    return address
