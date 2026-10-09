"""HTTP client for integration tests against a running sandbox service.

SANDBOX_TEST_URL     default http://127.0.0.1:8220 (the compose service)
SANDBOX_TEST_SECRET  defaults to SANDBOX_SHARED_SECRET from the repository .env
                     (then from the environment); the value is never printed
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ENV = Path(__file__).resolve().parents[4] / ".env"


def _secret() -> str:
    if os.environ.get("SANDBOX_TEST_SECRET"):
        return os.environ["SANDBOX_TEST_SECRET"]
    if REPO_ENV.exists():
        for line in REPO_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("SANDBOX_SHARED_SECRET="):
                return line.split("=", 1)[1].strip().strip('"')
    if os.environ.get("SANDBOX_SHARED_SECRET"):
        return os.environ["SANDBOX_SHARED_SECRET"]
    pytest.skip("no sandbox secret available")


class SandboxClient:
    def __init__(self, base_url: str, secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._secret = secret.encode()
        self._http = httpx.Client(timeout=120)

    def signed_headers(self, body: bytes, timestamp: int | None = None) -> dict[str, str]:
        ts = str(int(time.time()) if timestamp is None else timestamp)
        sig = hmac.new(self._secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        return {"X-Sandbox-Timestamp": ts, "X-Sandbox-Signature": sig, "Content-Type": "application/json"}

    def raw(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
        return self._http.request(method, self.base_url + path, content=body, headers=headers or {})

    def execute(self, **payload: Any) -> dict[str, Any]:
        payload.setdefault("request_id", f"it-{uuid.uuid4()}")
        body = json.dumps(payload).encode()
        response = self.raw("POST", "/v1/execute", body, self.signed_headers(body))
        assert response.status_code == 200, response.text
        return response.json()

    def get(self, path: str, signed: bool = False) -> httpx.Response:
        return self.raw("GET", path, b"", self.signed_headers(b"") if signed else None)


@pytest.fixture(scope="session")
def sandbox() -> SandboxClient:
    url = os.environ.get("SANDBOX_TEST_URL", "http://127.0.0.1:8220")
    client = SandboxClient(url, _secret())
    try:
        health = client.get("/health")
    except httpx.HTTPError:
        pytest.skip(f"sandbox service not reachable at {url}")
    if health.status_code != 200:
        pytest.skip(f"sandbox service unhealthy: {health.text[:200]}")
    return client
