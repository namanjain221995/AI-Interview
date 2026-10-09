"""Root pytest configuration.

Fixtures live in plugin modules under ``tests/fixtures/`` (owned by the
module that provides them) and are loaded when present, so a module that is
still being built never breaks the test run of another module.
"""

from __future__ import annotations

import importlib.util
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("ENABLE_TEST_HOOKS", "true")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ["TEST_HOOKS_TOKEN"] = ""  # pytest calls hooks without the dev shared secret

_PLUGIN_CANDIDATES = (
    "tests.fixtures.core",  # platform: db, redis, app/client, users, auth clients, storage
    "tests.fixtures.ai",  # ai-layer: mock AI services
    "tests.fixtures.domain",  # domain factories (jobs, candidates, sessions)
)

pytest_plugins: list[str] = []
for _name in _PLUGIN_CANDIDATES:
    # find_spec (not import) keeps pytest's assertion rewriting for the plugins.
    if importlib.util.find_spec(_name) is not None:
        pytest_plugins.append(_name)
