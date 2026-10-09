"""The sandbox's wire models must match the API's authoritative contract.

apps/api/app/services/coding/contracts.py is loaded by path with its two
imports stubbed (only enums from apps/api/app/domain/enums.py are real), so
the comparison does not depend on the rest of the API package.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from pydantic import BaseModel

from sandbox_service import models

API_ROOT = Path(__file__).resolve().parents[4] / "apps" / "api" / "app"
CONTRACTS = API_ROOT / "services" / "coding" / "contracts.py"
ENUMS = API_ROOT / "domain" / "enums.py"


@pytest.fixture(scope="module")
def api_contracts():
    if not CONTRACTS.exists() or not ENUMS.exists():
        pytest.skip("API contract files not available (sandbox built standalone)")
    saved = {
        k: sys.modules.get(k)
        for k in ("app", "app.domain", "app.domain.enums", "app.schemas", "app.schemas.realtime", "api_coding_contracts")
    }
    try:
        enums_spec = importlib.util.spec_from_file_location("app.domain.enums", ENUMS)
        enums = importlib.util.module_from_spec(enums_spec)
        assert enums_spec.loader is not None
        sys.modules["app"] = types.ModuleType("app")
        sys.modules["app.domain"] = types.ModuleType("app.domain")
        sys.modules["app.domain.enums"] = enums
        enums_spec.loader.exec_module(enums)
        realtime = types.ModuleType("app.schemas.realtime")

        class _Stub(BaseModel):
            pass

        realtime.CodingRunResultMsg = _Stub  # type: ignore[attr-defined]
        realtime.CodingStateMsg = _Stub  # type: ignore[attr-defined]
        sys.modules["app.schemas"] = types.ModuleType("app.schemas")
        sys.modules["app.schemas.realtime"] = realtime
        spec = importlib.util.spec_from_file_location("api_coding_contracts", CONTRACTS)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules["api_coding_contracts"] = module  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(module)
        yield module, enums
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


@pytest.mark.parametrize("name", ["SandboxTestCase", "ExecuteRequest", "SandboxTestResult", "ExecuteResponse"])
def test_models_match_api_contract(api_contracts, name):
    module, _ = api_contracts
    ours = getattr(models, name).model_json_schema()
    theirs = getattr(module, name).model_json_schema()
    assert ours == theirs


@pytest.mark.parametrize("name", ["CodeLanguage", "RunStatus"])
def test_enums_match_api(api_contracts, name):
    _, enums = api_contracts
    assert {e.name: e.value for e in getattr(models, name)} == {e.name: e.value for e in getattr(enums, name)}
