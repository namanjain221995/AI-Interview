"""Logging redaction, audit detail sanitizing, organization settings loading,
lifecycle hooks and queue registration rules."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import pytest

from app.core import lifecycle, queue
from app.core.audit import sanitize_details
from app.core.logging import redact_sensitive
from app.domain.enums import CandidateReportMode
from app.schemas.policies import OrganizationSettings
from app.services.identity.organizations import changed_sections, load_organization_settings, normalize_slug


def test_log_redaction():
    event = redact_sensitive(
        None,
        "info",
        {
            "event": "x",
            "password": "p",
            "jwt_secret": "s",
            "csrf_token": "t",
            "token": "t",
            "authorization": "Bearer x",
            "cookies": "c",
            "access_code": "ABCD",
            "prompt_tokens": 12,
            "max_tokens": 20,
            "token_type": "bearer",
            "user_id": "u",
        },
    )
    for key in ("password", "jwt_secret", "csrf_token", "token", "authorization", "cookies", "access_code"):
        assert event[key] == "[redacted]", key
    assert event["prompt_tokens"] == 12 and event["max_tokens"] == 20 and event["user_id"] == "u"
    assert event["token_type"] == "bearer"


class _Color(Enum):
    RED = "red"


def test_audit_details_are_json_safe_and_redacted():
    uid = uuid.uuid4()
    details = sanitize_details(
        {
            "id": uid,
            "at": datetime(2026, 1, 1, tzinfo=UTC),
            "color": _Color.RED,
            "raw": b"\x00\x01",
            "nested": {"refresh_token": "abc", "password_hash": "x", "ok": [1, 2, {3}]},
            "settings": OrganizationSettings(),
            "long": "x" * 5000,
            "items": list(range(500)),
        }
    )
    assert details["id"] == str(uid) and details["at"].startswith("2026-01-01")
    assert details["color"] == "red" and details["raw"] == "<2 bytes>"
    assert details["nested"]["refresh_token"] == "[redacted]" and details["nested"]["password_hash"] == "[redacted]"
    assert details["nested"]["ok"] == [1, 2, [3]]
    assert details["settings"]["candidate_report_mode"] == "after_review"
    assert len(details["long"]) < 2100 and len(details["items"]) == 100
    assert sanitize_details(None) == {}


def test_organization_settings_tolerant_load():
    assert load_organization_settings(None) == OrganizationSettings()
    stored = OrganizationSettings(candidate_report_mode=CandidateReportMode.IMMEDIATE).model_dump(mode="json")
    stored["obsolete_flag"] = True
    stored["integrity"]["removed_option"] = 3
    stored["integrity"]["camera_required"] = False
    stored["recording"] = {"record_video": "not-a-bool"}
    loaded = load_organization_settings(stored)
    assert loaded.candidate_report_mode is CandidateReportMode.IMMEDIATE
    assert loaded.integrity.camera_required is False  # customization kept despite unknown key
    assert loaded.recording == OrganizationSettings().recording  # invalid section -> defaults
    assert changed_sections(OrganizationSettings(), loaded) == ["candidate_report_mode", "integrity"]


def test_slug_normalization():
    assert normalize_slug(" Acme-Corp ") == "acme-corp"
    for bad in ("-acme", "acme-", "ac me", "a" * 101, ""):
        with pytest.raises(Exception):
            normalize_slug(bad)


async def test_lifecycle_hooks_run_in_order_and_supervise_background_tasks():
    calls: list[str] = []
    crashes = {"n": 0}

    @lifecycle.on_startup("t.first", processes=("api",))
    async def first() -> None:
        calls.append("start1")

    @lifecycle.on_startup("t.second", processes=("api", "worker"))
    async def second() -> None:
        calls.append("start2")

    @lifecycle.on_startup("t.worker_only", processes=("worker",))
    async def worker_only() -> None:
        calls.append("worker")

    @lifecycle.on_shutdown("t.stop1")
    async def stop1() -> None:
        calls.append("stop1")

    @lifecycle.on_shutdown("t.stop2")
    async def stop2() -> None:
        calls.append("stop2")

    @lifecycle.background_task("t.bg")
    async def bg(stop: asyncio.Event) -> None:
        if crashes["n"] == 0:
            crashes["n"] += 1
            raise RuntimeError("boom")
        calls.append("bg")
        await stop.wait()

    try:
        runner = lifecycle.LifecycleRunner("api")
        await runner.start()
        await asyncio.sleep(1.2)  # first run crashes, supervisor restarts after 1 s
        await runner.stop()
        assert calls[:2] == ["start1", "start2"]
        assert "worker" not in calls and "bg" in calls
        assert calls[-2:] == ["stop2", "stop1"]
    finally:
        for name in ("t.first", "t.second", "t.worker_only", "t.stop1", "t.stop2", "t.bg"):
            lifecycle.unregister(name)


async def test_strict_lifecycle_reraises_startup_failure():
    @lifecycle.on_startup("t.fails")
    async def fails() -> None:
        raise RuntimeError("cannot start")

    try:
        with pytest.raises(RuntimeError):
            await lifecycle.LifecycleRunner("api", strict=True).start()
        await lifecycle.LifecycleRunner("api", strict=False).start()  # logged, not raised
    finally:
        lifecycle.unregister("t.fails")
    with pytest.raises(ValueError):
        lifecycle.on_startup("bad", processes=("browser",))  # type: ignore[arg-type]


def test_optional_import():
    assert lifecycle.optional_import("app.does_not_exist_module", strict=True) is None
    assert lifecycle.optional_import("app.core.rbac", strict=True) is not None


# ------------------------------------------------------------------ queue


def test_job_type_validation():
    for good in ("resume.process", "system.ping", "a", "retention.sweep", "x_y.z_1"):
        assert queue.validate_job_type(good) == good
    for bad in ("", "Resume.Process", "a..b", ".a", "a.", "jobs:x", "dead", "delayed", "a b", "x" * 65):
        with pytest.raises(ValueError):
            queue.validate_job_type(bad)


def test_handler_call_styles():
    try:

        @queue.job_handler("t.payload")
        async def h1(payload: dict[str, Any]) -> None: ...

        @queue.job_handler("t.payload_ctx")
        async def h2(payload: dict[str, Any], ctx: queue.JobContext) -> None: ...

        @queue.job_handler("t.ctx")
        async def h3(ctx: queue.JobContext) -> None: ...

        @queue.job_handler("t.kwargs")
        async def h4(*, resume_id: str, job_id: str | None = None) -> None: ...

        styles = {t: queue.get_handler(t).call_style for t in ("t.payload", "t.payload_ctx", "t.ctx", "t.kwargs")}
        assert styles == {"t.payload": "payload", "t.payload_ctx": "payload_ctx", "t.ctx": "ctx", "t.kwargs": "kwargs"}
        assert queue.get_handler("t.kwargs").kwarg_names == frozenset({"resume_id", "job_id"})

        @queue.job_handler("t.payload")  # same function re-registered (module reload): allowed
        async def h1(payload: dict[str, Any]) -> None: ...  # noqa: F811

        with pytest.raises(ValueError):

            @queue.job_handler("t.payload_ctx")
            async def other(payload: dict[str, Any]) -> None: ...

        with pytest.raises(TypeError):

            @queue.job_handler("t.sync")
            def sync_handler(payload: dict[str, Any]) -> None: ...

        with pytest.raises(ValueError):
            queue.job_handler("t.zero", concurrency=0)
    finally:
        for t in ("t.payload", "t.payload_ctx", "t.ctx", "t.kwargs", "t.sync"):
            queue.unregister_handler(t)


async def test_invoke_kwargs_filters_unknown_payload_keys():
    seen: dict[str, Any] = {}
    try:

        @queue.job_handler("t.kw")
        async def handler(*, resume_id: str) -> None:
            seen["resume_id"] = resume_id

        ctx = queue.JobContext(job_id="j", job_type="t.kw", attempt=1, max_attempts=3, payload={"resume_id": "r", "extra": 1})
        await queue._invoke(queue.get_handler("t.kw"), ctx)
        assert seen == {"resume_id": "r"}
    finally:
        queue.unregister_handler("t.kw")


def test_payload_normalization_and_retryability():
    uid = uuid.uuid4()
    assert queue._normalize_payload({"id": uid, "when": datetime(2026, 1, 1, tzinfo=UTC), "tags": {"a"}}) == {
        "id": str(uid), "when": "2026-01-01T00:00:00+00:00", "tags": ["a"],
    }
    with pytest.raises(TypeError):
        queue._normalize_payload(["not", "a", "mapping"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        queue._normalize_payload({"x": object()})
    assert not queue._is_retryable(queue.PermanentJobError("x"))

    class NotRetryable(Exception):
        retryable = False

    assert not queue._is_retryable(NotRetryable())
    assert queue._is_retryable(TimeoutError())


def test_backoff_growth_and_cap():
    worker = queue.Worker.__new__(queue.Worker)
    worker.backoff_base, worker.backoff_cap = 2.0, 300.0
    delays = [worker._backoff(n) for n in (1, 2, 3, 10)]
    assert 1.6 <= delays[0] <= 2.4 and 3.2 <= delays[1] <= 4.8 and 6.4 <= delays[2] <= 9.6
    assert delays[3] <= 360


def test_periodic_job_registration():
    with pytest.raises(ValueError):
        queue.register_periodic_job("t.too_often", 10)
    queue.register_periodic_job("t.hourly", 3600, {"a": 1})
    try:
        assert any(j.job_type == "t.hourly" and j.payload == {"a": 1} for j in queue.periodic_jobs())
    finally:
        queue._PERIODIC.pop("t.hourly", None)
