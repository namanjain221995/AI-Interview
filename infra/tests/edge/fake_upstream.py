"""Minimal stand-in for the FastAPI app, used to exercise the edge Caddyfile.

It mirrors only the surface the edge cares about: JSON routes, uploads, the
SSE live feed, the WebSocket gateway, /metrics and the test hooks.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

SSE_EVENTS = 3
SSE_INTERVAL_S = 0.8


def _headers(request: Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


async def echo(request: Request) -> Response:
    response = JSONResponse({"path": request.url.path, "headers": _headers(request)})
    # The edge must replace these (deferred security headers), not duplicate them.
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Powered-By"] = "fake-upstream"
    return response


async def big(_: Request) -> Response:
    items = [{"id": i, "text": "interview transcript line " * 4} for i in range(600)]
    return JSONResponse({"items": items, "total": len(items)})


async def upload(request: Request) -> Response:
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
    except ClientDisconnect:  # the edge aborted an oversized body (413)
        return Response(status_code=499)
    return JSONResponse({"bytes": size})


async def live(_: Request) -> Response:
    async def events() -> AsyncIterator[bytes]:
        for i in range(SSE_EVENTS):
            payload = json.dumps({"n": i, "t": time.time()})
            yield f"event: tick\ndata: {payload}\n\n".encode()
            await asyncio.sleep(SSE_INTERVAL_S)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def metrics(_: Request) -> Response:
    return PlainTextResponse("# HELP fake_up fake\n# TYPE fake_up gauge\nfake_up 1\n")


async def healthz(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def test_hook(request: Request) -> Response:
    return JSONResponse({"hook": request.path_params["rest"]})


async def ws_session(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "welcome",
            "session_id": websocket.path_params["session_id"],
            "ticket_seen": bool(websocket.query_params.get("ticket")),
            "forwarded_for": websocket.headers.get("x-forwarded-for"),
        }
    )
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await websocket.send_bytes(message["bytes"])
            elif message.get("text") is not None:
                await websocket.send_text(f"echo:{message['text']}")
    except WebSocketDisconnect:
        return


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/metrics", metrics),
            Route("/api/v1/echo", echo),
            Route("/api/v1/big", big),
            Route("/api/v1/sessions/{session_id}/live", live),
            Route("/api/v1/test/{rest:path}", test_hook, methods=["GET", "POST"]),
            Route("/api/v1/candidates/{cid}/resumes", upload, methods=["POST"]),
            Route("/api/v1/portal/interviews/{iid}/resume", upload, methods=["POST"]),
            Route("/api/v1/portal/sessions/{sid}/recording", upload, methods=["POST"]),
            Route("/api/v1/portal/sessions/{sid}/frames", upload, methods=["POST"]),
            Route("/api/v1/jobs", upload, methods=["POST"]),
            WebSocketRoute("/ws/sessions/{session_id}", ws_session),
        ]
    )


class UpstreamServer:
    """Runs the fake app with uvicorn in a background thread."""

    def __init__(self, port: int) -> None:
        self.port = port
        config = uvicorn.Config(
            build_app(), host="127.0.0.1", port=port, log_level="warning", ws="websockets-sansio"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="fake-upstream", daemon=True)

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("fake upstream did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
