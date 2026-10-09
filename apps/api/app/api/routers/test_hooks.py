"""Test-only control endpoints.

Included by ``app.main`` only when ``ENABLE_TEST_HOOKS=true`` (refused in
production by the settings validator). Other modules add their own hook
routers in modules named ``test_hooks_<domain>.py`` / ``<domain>_test_hooks.py``
(or set ``TEST_HOOKS = True``) and get the same gating automatically.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header

from app.core.config import get_settings
from app.core.errors import Forbidden

TEST_HOOKS = True


async def require_test_hooks_token(
    x_test_hooks_token: Annotated[str | None, Header(alias="X-Test-Hooks-Token")] = None,
) -> None:
    """Defence in depth on top of ENABLE_TEST_HOOKS and the edge's loopback-only rule:
    every hook call must present the shared secret TEST_HOOKS_TOKEN. Only the pytest
    environment (APP_ENV=test) may run without a configured token."""
    settings = get_settings()
    expected = settings.test_hooks_token.get_secret_value() if settings.test_hooks_token else None
    if expected is None:
        if settings.is_test:
            return
        raise Forbidden("Test hooks require TEST_HOOKS_TOKEN.", code="test_hooks_forbidden")
    if not x_test_hooks_token or not hmac.compare_digest(x_test_hooks_token, expected):
        raise Forbidden("Invalid test hooks token.", code="test_hooks_forbidden")


router = APIRouter(prefix="/test", tags=["test-hooks"], dependencies=[Depends(require_test_hooks_token)])


@router.post("/ping", summary="Test hook liveness")
async def ping() -> dict[str, Any]:
    settings = get_settings()
    return {"status": "ok", "pong": True, "environment": settings.app_env, "time": datetime.now(UTC).isoformat()}
