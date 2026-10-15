"""Health endpoints, request transaction semantics, router discovery, rate
limiting, WebSocket tickets and the candidate principal."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from fastapi import APIRouter, Depends
from sqlalchemy import select, text

from app.core import ratelimit
from app.core.deps import CandidatePrincipal, DbSession, get_candidate_principal, require_permission
from app.core.errors import NotFound
from app.core.lifecycle import run_after_commit
from app.core.metrics import RATE_LIMITED
from app.core.rbac import Principal
from app.core.security import consume_ws_ticket, issue_ws_ticket
from app.db.models import SystemNote
from app.domain.enums import Role

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------- health


async def test_liveness_readiness_metrics(client):
    live = await client.get("/healthz")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    ready = await client.get("/readyz")
    assert ready.status_code == 200, ready.text
    assert {k: v["status"] for k, v in ready.json()["checks"].items()} == {"database": "ok", "redis": "ok", "storage": "ok"}
    api = await client.get("/api/v1/health")
    assert api.status_code == 200 and api.json()["environment"] == "test"
    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "interview_http_requests_total" in metrics.text
    assert 'route="/healthz"' in metrics.text
    assert metrics.headers["content-type"].startswith("text/plain")


async def test_readiness_reports_failures(client, monkeypatch):
    from app.api.routers import health

    async def broken() -> float:
        raise ConnectionError("down")

    monkeypatch.setattr(health, "check_redis", broken)
    response = await client.get("/readyz")
    assert response.status_code == 503 and response.json()["checks"]["redis"]["status"] == "down"


class _StubAI:
    async def health(self) -> dict[str, dict[str, Any]]:
        return {"main": {"status": "ok", "latency_ms": 12.5, "circuit": "closed"},
                "whisper": {"status": "down", "latency_ms": None, "detail": "ConnectError"}}

    async def aclose(self) -> None:
        return None


async def test_system_health_aggregates_components(auth_client, settings, redis):
    from app.ai.registry import set_ai

    previous = set_ai(_StubAI())  # type: ignore[arg-type]
    await redis.set("worker:heartbeat:test-worker", '{"id": "test-worker", "inflight": 0}', ex=30)
    await redis.sadd("worker:ids", "test-worker", "gone-worker")  # gone-worker has no heartbeat
    await redis.sadd("jobs:types", "system.ping")
    await redis.xadd("jobs:system.ping", {"job_id": str(uuid.uuid4()), "type": "system.ping"})
    try:
        staff = await auth_client(Role.ADMIN)  # settings:manage
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{settings.tts_base_url}/health").respond(200, json={"status": "ok"})
            mock.get(f"{settings.sandbox_base_url}/health").respond(503)
            response = await staff.get("/api/v1/system/health")
    finally:
        set_ai(previous)
    assert response.status_code == 200, response.text
    body = response.json()
    components = {c["name"]: c for c in body["components"]}
    assert components["database"]["status"] == "ok" and components["redis"]["status"] == "ok"
    assert components["storage"]["info"]["backend"] == "local"
    assert components["queue"]["info"]["pending"] == {"system.ping": 1}
    assert components["workers"]["info"]["count"] == 1
    assert await redis.smembers("worker:ids") == {"test-worker"}  # stale id pruned
    assert components["tts"]["status"] == "ok"
    assert components["sandbox"]["status"] == "down" and components["sandbox"]["detail"] == "HTTP 503"
    assert components["ai.main"]["status"] == "ok" and components["ai.whisper"]["status"] == "down"
    assert body["status"] == "degraded"
    assert settings.tts_base_url not in response.text  # no internal URLs leak


async def test_system_health_names_missing_sandbox_runtimes(auth_client, settings):
    from app.ai.registry import set_ai

    previous = set_ai(_StubAI())  # type: ignore[arg-type]
    try:
        staff = await auth_client(Role.ADMIN)
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{settings.tts_base_url}/health").respond(200, json={"status": "ok"})
            mock.get(f"{settings.sandbox_base_url}/health").respond(200, json={"status": "ok"})
            mock.get(f"{settings.sandbox_base_url}/ready").mock(
                side_effect=[
                    httpx.Response(503, json={"ready": False, "docker": True, "missing_languages": ["go", "java"]}),
                    httpx.Response(404),  # an older sandbox without /ready
                ]
            )
            missing = await staff.get("/api/v1/system/health")
            older = await staff.get("/api/v1/system/health")
    finally:
        set_ai(previous)
    sandbox = {c["name"]: c for c in missing.json()["components"]}["sandbox"]
    assert sandbox["status"] == "degraded"
    assert sandbox["detail"] == "missing runtimes: go, java (make sandbox-images)"
    assert {c["name"]: c for c in older.json()["components"]}["sandbox"]["status"] == "ok"


async def test_system_health_requires_settings_manage(client, candidate_auth_client, auth_client):
    assert (await client.get("/api/v1/system/health")).status_code == 401
    candidate = await candidate_auth_client()
    assert (await candidate.get("/api/v1/system/health")).status_code == 403
    interviewer = await auth_client(Role.INTERVIEWER)  # operational detail is for administrators only
    assert (await interviewer.get("/api/v1/system/health")).status_code == 403


# ------------------------------------------------------------- deep readiness


class _HealthStub:
    def __init__(self, **statuses: str) -> None:
        names = ("main", "vision", "embedding", "reranker", "whisper", "ocr", "tts")
        self.report = {n: {"status": statuses.get(n, "ok"), "latency_ms": 1.0, "detail": None} for n in names}

    async def health(self):
        return self.report


async def test_deep_readiness_classifies_required_and_optional_endpoints(client):
    from app.ai.registry import set_ai
    from app.core import ai_health

    ai_health.invalidate()
    previous = set_ai(_HealthStub())  # type: ignore[arg-type]
    try:
        ready = await client.get("/readyz?deep=1")
        assert ready.status_code == 200, ready.text
        body = ready.json()
        assert body["status"] == "ready" and body["required_down"] == [] and body["degraded"] == []
        assert set(body["ai"]) == set(ai_health.ALL_ENDPOINTS)

        set_ai(_HealthStub(vision="down", reranker="disabled"))  # type: ignore[arg-type]
        degraded = await client.get("/readyz?deep=1")
        assert degraded.status_code == 200, degraded.text  # optional endpoints only degrade features
        assert degraded.json()["degraded"] == ["vision", "reranker"]

        set_ai(_HealthStub(whisper="down"))  # type: ignore[arg-type]
        not_ready = await client.get("/readyz?deep=1")
        assert not_ready.status_code == 503
        assert not_ready.json()["status"] == "not_ready" and not_ready.json()["required_down"] == ["whisper"]

        shallow = await client.get("/readyz")  # the shallow probe never touches the AI endpoints
        assert shallow.status_code == 200 and "ai" not in shallow.json()
    finally:
        set_ai(previous)
        ai_health.invalidate()


async def test_deep_readiness_is_loopback_only(app, client):
    transport = httpx.ASGITransport(app=app, client=("192.168.9.5", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as lan:
        response = await lan.get("/readyz?deep=1")
    assert response.status_code == 403
    assert response.json()["status"] == "forbidden"
    # Through the edge the peer is loopback but X-Forwarded-For names the real (LAN) client.
    via_edge = await client.get("/readyz?deep=1", headers={"X-Forwarded-For": "192.168.9.5, 127.0.0.1"})
    assert via_edge.status_code == 403
    local_via_edge = await client.get("/readyz?deep=1", headers={"X-Forwarded-For": "127.0.0.1"})
    assert local_via_edge.status_code in (200, 503)  # loopback client through the edge is allowed


# ------------------------------------------------------ request transactions


def _tx_router() -> APIRouter:
    router = APIRouter(prefix="/tx-test")

    @router.post("/ok")
    async def ok(db: DbSession) -> dict[str, str]:
        note = SystemNote(kind="tx", body="committed")
        db.add(note)
        events: list[str] = []

        async def after() -> None:
            await asyncio.sleep(0.05)
            events.append("after-commit")

        run_after_commit(db, after)
        await db.flush()
        return {"id": str(note.id)}

    @router.post("/fail")
    async def fail(db: DbSession) -> None:
        db.add(SystemNote(kind="tx", body="rolled back"))
        await db.flush()
        raise NotFound("nope")

    @router.post("/conflict")
    async def conflict(db: DbSession) -> None:
        await db.execute(text("CREATE TEMP TABLE IF NOT EXISTS t_unique (v int UNIQUE) ON COMMIT DROP"))
        await db.execute(text("INSERT INTO t_unique VALUES (1)"))
        await db.execute(text("INSERT INTO t_unique VALUES (1)"))

    return router


async def test_get_db_commits_before_response_and_rolls_back_on_error(app, db_session):
    app.include_router(_tx_router(), prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
        created = await http.post("/api/v1/tx-test/ok")
        assert created.status_code == 200
        # Visible from another connection the moment the response arrived.
        row = await db_session.get(SystemNote, uuid.UUID(created.json()["id"]))
        assert row is not None and row.body == "committed"
        failed = await http.post("/api/v1/tx-test/fail")
        assert failed.status_code == 404
        conflict = await http.post("/api/v1/tx-test/conflict")
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "conflict"
    bodies = (await db_session.scalars(select(SystemNote.body))).all()
    assert bodies == ["committed"]


async def test_dependency_helpers_on_custom_routes(app, auth_client, candidate_auth_client):
    router = APIRouter(prefix="/deps-test")

    @router.get("/candidate")
    async def candidate_only(principal: CandidatePrincipal) -> dict[str, str]:
        return {"interview_id": str(principal.interview_id)}

    @router.post("/jobs-write")
    async def jobs_write(principal: Annotated[Principal, Depends(require_permission("jobs:write"))]) -> dict[str, str]:
        return {"role": principal.role.value}

    app.include_router(router, prefix="/api/v1")
    candidate = await candidate_auth_client()
    ok = await candidate.get("/api/v1/deps-test/candidate")
    assert ok.status_code == 200 and ok.json()["interview_id"] == str(candidate.candidate.interview_id)
    assert (await candidate.post("/api/v1/deps-test/jobs-write")).status_code == 403
    recruiter = await auth_client(Role.RECRUITER)
    assert (await recruiter.get("/api/v1/deps-test/candidate")).status_code == 403
    assert (await recruiter.post("/api/v1/deps-test/jobs-write")).json() == {"role": "RECRUITER"}
    interviewer = await auth_client(Role.INTERVIEWER)
    denied = await interviewer.post("/api/v1/deps-test/jobs-write")
    assert denied.status_code == 403 and denied.json()["error"]["details"]["missing_permissions"] == ["jobs:write"]
    assert get_candidate_principal is not None


# ----------------------------------------------------------- app factory


async def test_router_discovery_and_test_hook_gating(settings, redis, storage, _db_cleanup):
    from app.main import create_app

    settings.enable_test_hooks = True
    with_hooks = create_app(settings)
    async with LifespanManager(with_hooks):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=with_hooks), base_url="http://t") as http:
            assert (await http.post("/api/v1/test/ping")).json()["pong"] is True
            spec = (await http.get("/api/openapi.json")).json()
            assert "/api/v1/auth/login" in spec["paths"] and "/api/v1/users/{user_id}" in spec["paths"]
            assert (await http.get("/api/docs")).status_code == 200
            assert (await http.get("/healthz")).status_code == 200

    settings.enable_test_hooks = False
    settings.enable_api_docs = False
    without = create_app(settings)
    async with LifespanManager(without):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=without), base_url="http://t") as http:
            assert (await http.post("/api/v1/test/ping")).status_code == 404
            assert (await http.get("/api/openapi.json")).status_code == 404
            assert (await http.get("/api/docs")).status_code == 404


async def test_lifecycle_hooks_run_with_the_app(settings, redis, storage, _db_cleanup):
    from app.core import lifecycle
    from app.main import create_app

    calls: list[str] = []

    @lifecycle.on_startup("test.api_hook")
    async def started() -> None:
        calls.append("up")

    @lifecycle.on_shutdown("test.api_hook")
    async def stopped() -> None:
        calls.append("down")

    try:
        application = create_app(settings)
        async with LifespanManager(application):
            assert calls == ["up"]
        assert calls == ["up", "down"]
    finally:
        lifecycle.unregister("test.api_hook")


# ------------------------------------------------------------- rate limits


async def test_sliding_window_limiter(redis):
    results = [await ratelimit.hit("unit", "k1", 3, 1.0, redis=redis) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert 0 < results[3].retry_after <= 1.0
    await asyncio.sleep(1.05)
    assert (await ratelimit.hit("unit", "k1", 3, 1.0, redis=redis)).allowed
    assert (await ratelimit.hit("unit", "other", 3, 1.0, redis=redis)).allowed
    ttl = await redis.pttl(ratelimit.rate_limit_key("unit", "k1"))
    assert 0 < ttl <= 1000
    await ratelimit.reset("unit", "k1", redis=redis)
    assert await redis.exists(ratelimit.rate_limit_key("unit", "k1")) == 0


async def test_limiter_is_atomic_under_concurrency(redis):
    outcomes = await asyncio.gather(*(ratelimit.hit("burst", "ip", 10, 5.0, redis=redis) for _ in range(40)))
    assert sum(r.allowed for r in outcomes) == 10


async def test_enforce_raises_and_counts(redis):
    from app.core.errors import RateLimited

    before = RATE_LIMITED.labels(scope="enforce")._value.get()
    await ratelimit.enforce_rate_limit("enforce", "x", limit=1, window_s=10, redis=redis)
    with pytest.raises(RateLimited) as info:
        await ratelimit.enforce_rate_limit("enforce", "x", limit=1, window_s=10, redis=redis)
    assert info.value.headers["Retry-After"] and RATE_LIMITED.labels(scope="enforce")._value.get() == before + 1


async def test_limiter_fails_open_without_redis():
    from redis.exceptions import ConnectionError as RedisConnectionError

    class Broken:
        async def eval(self, *args: Any) -> Any:
            raise RedisConnectionError("down")

    result = await ratelimit.hit("x", "y", 1, 1.0, redis=Broken())  # type: ignore[arg-type]
    assert result.allowed


def test_rate_limit_keys():
    assert ratelimit.rate_limit_key("login", "10.0.0.1") == "rl:login:10.0.0.1"
    assert ratelimit.rate_limit_key("s", "a b/c") == "rl:s:a_b_c"
    assert len(ratelimit.rate_limit_key("s", "x" * 500)) < 100
    assert ratelimit.hashed_key(" A@B.com ") == ratelimit.hashed_key("a@b.com")


# --------------------------------------------------------------- WS tickets


async def test_ws_tickets_are_single_use(redis):
    sid, cid, iid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ticket = await issue_ws_ticket(sid, cid, iid)
    assert await redis.ttl(f"ws:ticket:{ticket}") > 0
    consumed = await consume_ws_ticket(ticket)
    assert consumed is not None
    assert (consumed.session_id, consumed.candidate_id, consumed.interview_id) == (str(sid), str(cid), str(iid))
    assert await consume_ws_ticket(ticket) is None
    assert await consume_ws_ticket("bad ticket!") is None
    assert await consume_ws_ticket(None) is None


async def test_ws_ticket_expires(redis):
    ticket = await issue_ws_ticket(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), ttl_seconds=1)
    await asyncio.sleep(1.1)
    assert await consume_ws_ticket(ticket) is None


async def test_concurrent_consumption_has_one_winner(redis):
    ticket = await issue_ws_ticket(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    results = await asyncio.gather(*(consume_ws_ticket(ticket) for _ in range(10)))
    assert sum(r is not None for r in results) == 1


# -------------------------------------------------------- candidate access


async def test_candidate_principal(candidate_auth_client, db_session):
    candidate = await candidate_auth_client()
    me = await candidate.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["subject_type"] == "candidate" and body["role"] == "CANDIDATE"
    assert body["interview_id"] == str(candidate.candidate.interview_id)
    assert body["permissions"] == ["portal:self"]
    assert (await candidate.get("/api/v1/users")).status_code == 403
    assert (await candidate.get("/api/v1/settings")).status_code == 403
    rotated = await candidate.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200 and rotated.json()["principal"]["subject_type"] == "candidate"


async def test_cancelled_interview_ends_candidate_access(candidate_auth_client, db_session):
    from sqlalchemy import update

    from app.db.models import Interview
    from app.services.identity.auth_cache import invalidate_auth_cache

    candidate = await candidate_auth_client()
    assert (await candidate.get("/api/v1/auth/me")).status_code == 200
    await db_session.execute(update(Interview).where(Interview.id == candidate.candidate.interview_id).values(status="cancelled"))
    await db_session.commit()
    await invalidate_auth_cache([candidate.candidate.auth_session_id])
    response = await candidate.get("/api/v1/auth/me")
    assert response.status_code == 401 and response.json()["error"]["code"] == "interview_unavailable"
