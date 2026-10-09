"""Production refuses unsafe configuration."""

from __future__ import annotations

import pytest

from app.core.config import Settings

SAFE = {
    "app_env": "production",
    "jwt_secret": "j" * 40,
    "media_url_secret": "m" * 40,
    "sandbox_shared_secret": "s" * 40,
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "enable_test_hooks": False,
    "cookie_secure": True,
    "csrf_enabled": True,
    "enable_api_docs": False,
    "malware_scan_mode": "optional",
    "model_call_log_payloads": False,
}


def test_safe_production_config_loads() -> None:
    assert Settings(**SAFE).is_production  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("jwt_secret", "short", "JWT_SECRET"),
        ("media_url_secret", "short", "MEDIA_URL_SECRET"),
        ("sandbox_shared_secret", "short", "SANDBOX_SHARED_SECRET"),
        ("enable_test_hooks", True, "ENABLE_TEST_HOOKS"),
        ("cookie_secure", False, "COOKIE_SECURE"),
        ("csrf_enabled", False, "CSRF_ENABLED"),
        ("enable_api_docs", True, "ENABLE_API_DOCS"),
        ("malware_scan_mode", "off", "MALWARE_SCAN_MODE"),
        ("model_call_log_payloads", True, "MODEL_CALL_LOG_PAYLOADS"),
    ],
)
def test_unsafe_production_config_is_refused(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**{**SAFE, field: value})  # type: ignore[arg-type]
