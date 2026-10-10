"""core.ai_health: cached probe, required/optional classification, startup log (S16)."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.registry import set_ai
from app.core import ai_health


class _Stub:
    def __init__(
        self, report: dict[str, dict[str, Any]] | None = None, fail: Exception | None = None
    ) -> None:
        self.report = report or {}
        self.fail = fail
        self.calls = 0

    async def health(self) -> dict[str, dict[str, Any]]:
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.report


def _ok(*names: str, **overrides: str) -> dict[str, dict[str, Any]]:
    report = {name: {"status": "ok", "latency_ms": 1.0} for name in names}
    for name, status in overrides.items():
        report[name] = {"status": status, "latency_ms": None, "detail": status}
    return report


@pytest.fixture(autouse=True)
def _fresh_cache():
    ai_health.invalidate()
    yield
    ai_health.invalidate()


def test_classify_required_and_optional() -> None:
    report = _ok(*ai_health.ALL_ENDPOINTS, whisper="down", reranker="disabled", ocr="degraded")
    required_down, optional_down = ai_health.classify(report)
    assert required_down == ["whisper"]
    assert optional_down == ["reranker"]  # degraded = reachable; disabled counts as unavailable


def test_classify_missing_endpoint_counts_as_down() -> None:
    required_down, optional_down = ai_health.classify(_ok("main", "tts"))
    assert required_down == ["whisper"]
    assert optional_down == list(ai_health.OPTIONAL_ENDPOINTS)


async def test_probe_is_cached_per_services_object() -> None:
    stub = _Stub(_ok(*ai_health.ALL_ENDPOINTS))
    previous = set_ai(stub)  # type: ignore[arg-type]
    try:
        first = await ai_health.probe()
        second = await ai_health.probe()
        assert first == second and stub.calls == 1
        other = _Stub(_ok(*ai_health.ALL_ENDPOINTS, main="down"))
        set_ai(other)  # type: ignore[arg-type]
        third = await ai_health.probe()  # a new services object bypasses the cache
        assert third["main"]["status"] == "down" and other.calls == 1
        await ai_health.probe(force=True)
        assert other.calls == 2
    finally:
        set_ai(previous)


async def test_probe_reports_a_failing_registry_as_down_without_raising() -> None:
    previous = set_ai(_Stub(fail=RuntimeError("boom")))  # type: ignore[arg-type]
    try:
        report = await ai_health.probe()
    finally:
        set_ai(previous)
    assert set(report) == set(ai_health.ALL_ENDPOINTS)
    assert all(data["status"] == "down" and "RuntimeError" in data["detail"] for data in report.values())


async def test_startup_probe_logs_per_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []

    class _Log:
        def __getattr__(self, level: str):
            def emit(event: str, **kw: Any) -> None:
                events.append((level, event, kw))

            return emit

    monkeypatch.setattr(ai_health, "log", _Log())
    previous = set_ai(_Stub(_ok(*ai_health.ALL_ENDPOINTS, whisper="down", vision="down")))  # type: ignore[arg-type]
    try:
        await ai_health.log_startup_probe("api")  # never raises
    finally:
        set_ai(previous)
    unreachable = {
        (level, kw["endpoint"], kw["required"])
        for level, event, kw in events
        if event == "ai.endpoint_unreachable"
    }
    assert unreachable == {("error", "whisper", True), ("warning", "vision", False)}
    assert all(kw.get("process") == "api" for _, event, kw in events if event == "ai.endpoint_unreachable")
