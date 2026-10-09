"""Test hooks need the shared secret outside the pytest environment."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

pytestmark = pytest.mark.integration


async def test_hooks_reject_missing_or_wrong_token(client, settings):
    settings.app_env = "development"
    settings.test_hooks_token = SecretStr("s3cret-hooks-token")
    missing = await client.post("/api/v1/test/ping")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "test_hooks_forbidden"
    wrong = await client.post("/api/v1/test/ping", headers={"X-Test-Hooks-Token": "nope"})
    assert wrong.status_code == 403
    ok = await client.post("/api/v1/test/ping", headers={"X-Test-Hooks-Token": "s3cret-hooks-token"})
    assert ok.status_code == 200 and ok.json()["pong"] is True


async def test_hooks_without_configured_token_only_work_under_pytest(client, settings):
    settings.app_env = "development"
    settings.test_hooks_token = None
    assert (await client.post("/api/v1/test/ping")).status_code == 403
    settings.app_env = "test"
    assert (await client.post("/api/v1/test/ping")).status_code == 200
