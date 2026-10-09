"""Error envelope, request context, security headers, CORS and HTTP metrics,
exercised on a minimal app (no database/redis)."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    RateLimited,
    ServiceUnavailable,
    Unauthorized,
    ValidationFailed,
    register_exception_handlers,
)
from app.core.metrics import ERRORS, HTTP_REQUESTS
from app.core.middleware import install_middleware


class _Body(BaseModel):
    password: str
    count: int


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("db error with internals")
        self.sqlstate = sqlstate


def _app(cors: list[str] | None = None) -> FastAPI:
    settings = get_settings().model_copy(update={"cors_allowed_origins": cors or []})
    app = FastAPI()
    register_exception_handlers(app)
    install_middleware(app, settings)

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        if item_id == 404:
            raise NotFound("Item not found.", details={"item_id": item_id})
        if item_id == 403:
            raise Forbidden()
        if item_id == 401:
            raise Unauthorized(code="token_expired")
        if item_id == 409:
            raise Conflict("dup")
        if item_id == 422:
            raise ValidationFailed("bad", details={"field": "x"})
        if item_id == 429:
            raise RateLimited(retry_after=2.2)
        if item_id == 503:
            raise ServiceUnavailable(retry_after=7)
        if item_id == 500:
            raise RuntimeError("secret internal detail")
        if item_id == 418:
            raise HTTPException(status_code=418, detail="teapot")
        if item_id == 23505:
            raise IntegrityError("INSERT", {}, _Orig("23505"))
        if item_id == 23502:
            raise IntegrityError("INSERT", {}, _Orig("23502"))
        return {"id": item_id}

    @app.post("/echo")
    async def echo(body: _Body) -> dict[str, int]:
        return {"count": body.count}

    return app


@pytest.fixture
async def http():
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.parametrize(
    ("item", "status", "code"),
    [
        (404, 404, "not_found"),
        (403, 403, "forbidden"),
        (401, 401, "token_expired"),
        (409, 409, "conflict"),
        (422, 422, "validation_failed"),
        (429, 429, "rate_limited"),
        (503, 503, "service_unavailable"),
        (418, 418, "http_418"),
        (23505, 409, "conflict"),
    ],
)
async def test_error_envelope(http, item, status, code):
    response = await http.get(f"/items/{item}")
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["x-request-id"]


async def test_error_specific_headers(http):
    assert (await http.get("/items/429")).headers["retry-after"] == "3"
    assert (await http.get("/items/429")).json()["error"]["details"]["retry_after_seconds"] == 3
    assert (await http.get("/items/503")).headers["retry-after"] == "7"
    assert (await http.get("/items/401")).headers["www-authenticate"] == "Bearer"
    assert (await http.get("/items/404")).json()["error"]["details"] == {"item_id": 404}


async def test_unhandled_exception_is_opaque_500(http, caplog):
    before = ERRORS.labels(component="api")._value.get()
    with caplog.at_level(logging.ERROR):
        response = await http.get("/items/500")
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in response.text
    assert body["request_id"] == response.headers["x-request-id"]
    assert ERRORS.labels(component="api")._value.get() == before + 1
    assert response.headers["x-content-type-options"] == "nosniff"
    # Integrity errors that are not conflicts are server bugs -> 500 as well.
    assert (await http.get("/items/23502")).status_code == 500


async def test_validation_errors_do_not_echo_input(http):
    response = await http.post("/echo", json={"password": "hunter2-super-secret", "count": "not-a-number"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_failed"
    assert "hunter2-super-secret" not in response.text
    assert "not-a-number" not in response.text
    assert body["details"]["errors"][0]["loc"] == ["body", "count"]
    malformed = await http.post("/echo", content=b"{not json", headers={"content-type": "application/json"})
    assert malformed.status_code == 422


async def test_unknown_route_and_method(http):
    missing = await http.get("/does-not-exist")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    wrong = await http.delete("/items/1")
    assert wrong.status_code == 405 and wrong.json()["error"]["code"] == "method_not_allowed"


async def test_request_id_and_security_headers(http):
    response = await http.get("/items/1", headers={"X-Request-ID": "client-supplied-id-123"})
    assert response.headers["x-request-id"] == "client-supplied-id-123"
    generated = await http.get("/items/1", headers={"X-Request-ID": "bad id with spaces <script>"})
    assert generated.headers["x-request-id"] != "bad id with spaces <script>"
    assert len(generated.headers["x-request-id"]) == 32
    for name, value in {
        "x-content-type-options": "nosniff",
        "cache-control": "no-store",
        "referrer-policy": "no-referrer",
        "x-frame-options": "DENY",
    }.items():
        assert response.headers[name] == value
    assert "default-src 'none'" in response.headers["content-security-policy"]


async def test_metrics_use_route_templates(http):
    route = "/items/{item_id}"
    before = HTTP_REQUESTS.labels(method="GET", route=route, status="200")._value.get()
    await http.get("/items/7")
    await http.get("/items/8?token=abc")
    assert HTTP_REQUESTS.labels(method="GET", route=route, status="200")._value.get() == before + 2
    unmatched = HTTP_REQUESTS.labels(method="GET", route="<unmatched>", status="404")._value.get()
    await http.get("/random/path/123")
    assert HTTP_REQUESTS.labels(method="GET", route="<unmatched>", status="404")._value.get() == unmatched + 1


async def test_access_log_never_contains_query_or_path_secrets(http, capsys):
    await http.get("/items/9?token=super-secret-token-value", cookies={"access_token": "cookie-secret"})
    out = capsys.readouterr().out
    assert "super-secret-token-value" not in out and "cookie-secret" not in out


async def test_cors_allows_only_configured_origins():
    transport = httpx.ASGITransport(app=_app(cors=["http://localhost:5173", "*"]))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ok = await client.options(
            "/items/1",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "x-csrf-token"},
        )
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert ok.headers["access-control-allow-credentials"] == "true"
        denied = await client.get("/items/1", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in denied.headers
        error = await client.get("/items/404", headers={"Origin": "http://localhost:5173"})
        assert error.headers["access-control-allow-origin"] == "http://localhost:5173"
