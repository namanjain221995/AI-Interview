"""Behavioural tests for infra/caddy/Caddyfile (the edge proxy).

Each test talks to a real Caddy process (see conftest.py) and checks one
promise the platform relies on: security headers, SPA routing and caching,
API/WebSocket/SSE proxying, body limits, loopback-only surfaces, on-demand
internal TLS for LAN testing and credential redaction in access logs.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import time
import uuid

import httpx
import pytest
from conftest import Edge
from websockets.asyncio.client import connect

pytestmark = pytest.mark.docker

CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' ws: wss:; worker-src 'self' blob:; "
    "font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
PERMISSIONS_POLICY = (
    "camera=(self), microphone=(self), display-capture=(self), fullscreen=(self), geolocation=()"
)
MIB = 1024 * 1024


def _assert_security_headers(response: httpx.Response) -> None:
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers
    assert "via" not in response.headers


# ----------------------------------------------------------------------------- SPA


def test_index_has_security_headers_and_no_cache(edge: Edge) -> None:
    response = httpx.get(f"{edge.http}/")
    assert response.status_code == 200
    assert '<div id="root">' in response.text
    _assert_security_headers(response)
    assert response.headers["cache-control"] == "no-cache"
    # HSTS is only ever sent by the HTTPS listener.
    assert "strict-transport-security" not in response.headers


@pytest.mark.parametrize(
    "path", ["/candidate/room", "/app/sessions/7b6a/report", "/i/Zm9vYmFyLWludml0ZS10b2tlbg", "/login"]
)
def test_client_side_routes_fall_back_to_index(edge: Edge, path: str) -> None:
    response = httpx.get(f"{edge.http}{path}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in response.text
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/assets/app-deadbeef.js", "/favicon.ico", "/avatars/missing.glb"])
def test_missing_file_like_paths_are_404_not_the_spa_shell(edge: Edge, path: str) -> None:
    response = httpx.get(f"{edge.http}{path}")
    assert response.status_code == 404
    assert '<div id="root">' not in response.text
    assert "immutable" not in response.headers.get("cache-control", "")


def test_hashed_assets_are_immutable_and_served_precompressed(edge: Edge) -> None:
    response = httpx.get(f"{edge.http}/assets/app-3f9a1c7e.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers.get("vary", "").lower()
    assert response.text.startswith("export const lines")


def test_assets_without_precompressed_sibling_are_compressed_on_the_fly(edge: Edge) -> None:
    response = httpx.get(f"{edge.http}/assets/style-1a2b3c4d.css", headers={"Accept-Encoding": "zstd"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["content-encoding"] == "zstd"


@pytest.mark.parametrize(
    ("path", "mime"), [("/avatars/interviewer.glb", "model/gltf-binary"), ("/vad.wasm", "application/wasm")]
)
def test_model_and_wasm_assets_have_correct_mime_and_long_cache(edge: Edge, path: str, mime: str) -> None:
    response = httpx.get(f"{edge.http}{path}")
    assert response.status_code == 200
    assert response.headers["content-type"] == mime
    assert response.headers["cache-control"] == "public, max-age=604800"
    assert response.headers.get("etag")


# ----------------------------------------------------------------------------- API


def test_api_proxy_sets_trusted_forwarding_headers(edge: Edge) -> None:
    response = httpx.get(
        f"{edge.http}/api/v1/echo",
        headers={"X-Forwarded-For": "6.6.6.6", "X-Request-ID": "spoofed"},
    )
    assert response.status_code == 200
    upstream_headers = response.json()["headers"]
    # Spoofed client values are replaced by the edge (it is the first hop).
    assert upstream_headers["x-forwarded-for"] == "127.0.0.1"
    assert upstream_headers["x-forwarded-proto"] == "http"
    assert str(uuid.UUID(upstream_headers["x-request-id"])) == upstream_headers["x-request-id"]
    assert upstream_headers["host"] == f"127.0.0.1:{edge.http_port}"
    # Upstream X-Frame-Options/X-Powered-By/Server are replaced or removed.
    _assert_security_headers(response)


def test_api_json_is_compressed(edge: Edge) -> None:
    response = httpx.get(f"{edge.http}/api/v1/big", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["total"] == 600


def test_sse_is_streamed_without_buffering_or_compression(edge: Edge) -> None:
    arrivals: list[float] = []
    with httpx.stream(
        "GET", f"{edge.http}/api/v1/sessions/0b8f/live", headers={"Accept-Encoding": "gzip, zstd"}, timeout=10
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in response.headers
        start = time.monotonic()
        for line in response.iter_lines():
            if line.startswith("data:"):
                arrivals.append(time.monotonic() - start)
    assert len(arrivals) == 3
    assert arrivals[0] < 0.5, arrivals  # the first event is not held back
    assert arrivals[2] - arrivals[0] > 1.2, arrivals  # events arrive as produced (0.8 s apart)


@pytest.mark.parametrize(
    ("path", "size", "status"),
    [
        ("/api/v1/jobs", 1 * MIB, 200),
        ("/api/v1/jobs", 3 * MIB, 413),
        ("/api/v1/portal/sessions/5f1e/frames", 3 * MIB, 200),
        ("/api/v1/portal/sessions/5f1e/frames", 5 * MIB, 413),
        ("/api/v1/candidates/2c4d/resumes", 20 * MIB, 200),
        ("/api/v1/portal/interviews/9a8b/resume", 20 * MIB, 200),
        ("/api/v1/portal/sessions/5f1e/recording", 20 * MIB, 200),
        ("/api/v1/candidates/2c4d/resumes", 26 * MIB, 413),
    ],
)
def test_request_body_limits(edge: Edge, path: str, size: int, status: int) -> None:
    try:
        response = httpx.post(f"{edge.http}{path}", content=b"x" * size, timeout=30)
    except httpx.TransportError:
        # Caddy may close the connection while an oversized body is still being sent.
        assert status == 413
        return
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"bytes": size}
    else:
        assert response.json()["error"]["code"] == "payload_too_large"


# ----------------------------------------------------------------------------- WebSocket


def test_websocket_is_proxied_and_survives_idle_periods(edge: Edge) -> None:
    async def scenario() -> None:
        uri = f"{edge.ws}/ws/sessions/0b8f?ticket=WS-TICKET-SECRET-123"
        async with connect(uri, ping_interval=None, open_timeout=5, max_size=None) as ws:
            welcome = json.loads(await ws.recv())
            assert welcome == {
                "type": "welcome",
                "session_id": "0b8f",
                "ticket_seen": True,
                "forwarded_for": "127.0.0.1",
            }
            await ws.send("hello")
            assert await ws.recv() == "echo:hello"
            frame = bytes([0x01, 0, 0, 0, 7, 0, 0, 0]) + bytes(640)  # mic PCM frame layout
            await ws.send(frame)
            assert await ws.recv() == frame
            # Longer than the edge's read_header timeout with zero traffic.
            await asyncio.sleep(17)
            await ws.send("still-here")
            assert await ws.recv() == "echo:still-here"

    asyncio.run(scenario())


def test_websocket_over_the_https_listener(edge: Edge, lan_ip: str) -> None:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    async def scenario() -> None:
        uri = f"wss://{lan_ip}:{edge.https_port}/ws/sessions/0b8f?ticket=T"
        async with connect(uri, ssl=context, ping_interval=None, open_timeout=5) as ws:
            welcome = json.loads(await ws.recv())
            assert welcome["forwarded_for"] == lan_ip
            await ws.send("over-tls")
            assert await ws.recv() == "echo:over-tls"

    asyncio.run(scenario())


# ----------------------------------------------------------------------------- internal surfaces


@pytest.mark.parametrize("path", ["/metrics", "/api/v1/test/sessions/1/advance-clock", "/__edge/metrics"])
def test_internal_endpoints_answer_loopback_clients(edge: Edge, path: str) -> None:
    response = httpx.get(f"{edge.http}{path}")
    assert response.status_code == 200


def test_edge_metrics_expose_caddy_series(edge: Edge) -> None:
    httpx.get(f"{edge.http}/")
    body = httpx.get(f"{edge.http}/__edge/metrics").text
    assert "caddy_http_requests_total" in body or "caddy_http_request_duration_seconds" in body


@pytest.mark.parametrize(
    "path",
    [
        "/metrics",
        "/api/v1/test/sessions/1/advance-clock",
        "/__edge/metrics",
        "/__edge/tls/ask?domain=localhost",
    ],
)
def test_internal_endpoints_are_blocked_for_lan_clients(edge: Edge, lan_ip: str, path: str) -> None:
    response = httpx.get(f"http://{lan_ip}:{edge.http_port}{path}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_lan_clients_reach_the_api_with_their_real_address(edge: Edge, lan_ip: str) -> None:
    response = httpx.get(f"http://{lan_ip}:{edge.http_port}/api/v1/echo")
    assert response.status_code == 200
    assert response.json()["headers"]["x-forwarded-for"] == lan_ip


# ----------------------------------------------------------------------------- TLS (LAN testing)


@pytest.mark.parametrize(
    ("domain", "allowed"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("10.20.30.40", True),
        ("192.168.9.54", True),
        ("172.16.5.4", True),
        ("172.32.0.1", False),
        ("100.94.16.2", True),
        ("100.128.0.1", False),
        ("8.8.8.8", False),
        ("dgx-spark.local", True),
        ("interview.lan", True),
        ("box.tail1234.ts.net", True),
        ("interview.test", True),  # EDGE_TLS_HOSTNAMES
        ("edge.example", True),  # EDGE_TLS_HOSTNAMES
        ("evil.example.com", False),
        ("interview.test.evil.com", False),
        ("local", False),
    ],
)
def test_on_demand_tls_permission_policy(edge: Edge, domain: str, allowed: bool) -> None:
    response = httpx.get(f"{edge.http}/__edge/tls/ask", params={"domain": domain})
    assert response.status_code == (200 if allowed else 403)


def _handshake(host: str, port: int, server_name: str | None) -> dict:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=server_name) as tls:
            der = tls.getpeercert(binary_form=True)
            assert der
            return {"version": tls.version(), "der": der}


def test_https_issues_internal_certificate_for_lan_ip(edge: Edge, lan_ip: str) -> None:
    # Browsers send no SNI for IP literals; the certificate is chosen by the local address.
    info = _handshake(lan_ip, edge.https_port, None)
    assert info["version"] in {"TLSv1.2", "TLSv1.3"}
    response = httpx.get(f"https://{lan_ip}:{edge.https_port}/", verify=False)
    assert response.status_code == 200
    _assert_security_headers(response)
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    echo = httpx.get(f"https://{lan_ip}:{edge.https_port}/api/v1/echo", verify=False).json()
    assert echo["headers"]["x-forwarded-proto"] == "https"


@pytest.mark.parametrize("server_name", ["localhost", "dgx-spark.local", "interview.test"])
def test_https_serves_allowed_names(edge: Edge, server_name: str) -> None:
    _handshake("127.0.0.1", edge.https_port, server_name)


def test_https_refuses_names_outside_the_policy(edge: Edge) -> None:
    with pytest.raises((ssl.SSLError, ConnectionResetError)):
        _handshake("127.0.0.1", edge.https_port, "evil.example.com")


def test_hsts_is_not_sent_for_localhost(edge: Edge) -> None:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    transport = httpx.HTTPTransport(verify=context)
    with httpx.Client(transport=transport) as client:
        response = client.get(f"https://localhost:{edge.https_port}/")
    assert response.status_code == 200
    assert "strict-transport-security" not in response.headers


# ----------------------------------------------------------------------------- logging


def test_access_log_is_json_and_redacts_credentials(edge: Edge) -> None:
    httpx.get(f"{edge.http}/i/INVITE-SECRET-AAA")
    httpx.get(f"{edge.http}/api/v1/candidate-access/INVITE-SECRET-BBB/verify")
    httpx.get(f"{edge.http}/api/v1/media/signed/MEDIA-SECRET-CCC")
    httpx.get(f"{edge.http}/api/v1/echo?ticket=TICKET-SECRET-DDD&x=1")
    httpx.get(
        f"{edge.http}/api/v1/echo",
        headers={"Referer": f"{edge.http}/i/REFERER-SECRET-EEE", "X-CSRF-Token": "CSRF-SECRET-FFF"},
    )
    time.sleep(0.5)
    logs = edge.logs()
    for secret in ("INVITE-SECRET", "MEDIA-SECRET", "TICKET-SECRET", "REFERER-SECRET", "CSRF-SECRET"):
        assert secret not in logs, secret
    access = [json.loads(line) for line in logs.splitlines() if '"http.log.access' in line]
    uris = {entry["request"]["uri"] for entry in access}
    assert "/i/REDACTED" in uris
    assert "/api/v1/candidate-access/REDACTED/verify" in uris
    assert "/api/v1/media/signed/REDACTED" in uris
    assert "/api/v1/echo?ticket=REDACTED&x=1" in uris
    assert all("status" in entry and "duration" in entry for entry in access)


# ----------------------------------------------------------------------------- resilience


def test_api_outage_returns_json_502_after_retry_window(edge_without_api: Edge) -> None:
    start = time.monotonic()
    response = httpx.get(f"{edge_without_api.http}/api/v1/echo", timeout=30)
    elapsed = time.monotonic() - start
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "upstream_unavailable"
    assert body["error"]["request_id"]
    assert elapsed >= 4.5  # lb_try_duration rides out short restarts
    _assert_security_headers(response)
    # The SPA keeps loading while the API restarts.
    assert httpx.get(f"{edge_without_api.http}/candidate/room").status_code == 200
