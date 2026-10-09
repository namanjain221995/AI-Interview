"""Barge-in over a real socket: closing an LLM stream must make the server
see the disconnect at once (vLLM aborts the generation on disconnect)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from app.ai.base import CallContext
from app.ai.breaker import CircuitBreaker
from app.ai.clients.llm import OpenAICompatibleLLM
from app.ai.http import EndpointClient
from app.ai.ledger import MemoryLedger
from app.ai.router import ModelRouter
from app.domain.enums import ModelTask

STATE: dict[str, float | int | None] = {"closed_at": None, "sent": 0}


async def chat_completions(request: Request) -> StreamingResponse:
    await request.json()

    async def events() -> AsyncIterator[bytes]:
        try:
            yield b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n'
            for i in range(2000):
                payload = {"choices": [{"index": 0, "delta": {"content": f"tok{i} "}}]}
                yield f"data: {json.dumps(payload)}\n\n".encode()
                STATE["sent"] = int(STATE["sent"] or 0) + 1
                await asyncio.sleep(0.01)
            yield b"data: [DONE]\n\n"
        finally:
            STATE["closed_at"] = time.perf_counter()

    return StreamingResponse(events(), media_type="text/event-stream")


@pytest.fixture(scope="module")
def sse_server() -> Iterator[str]:
    app = Starlette(routes=[Route("/v1/chat/completions", chat_completions, methods=["POST"])])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


async def _llm(base_url: str) -> tuple[OpenAICompatibleLLM, list[EndpointClient], MemoryLedger]:
    clients = [
        EndpointClient(name, base_url, timeout=10, max_concurrency=2, breaker=CircuitBreaker(name))
        for name in ("main", "vision")
    ]
    ledger = MemoryLedger()
    llm = OpenAICompatibleLLM(
        endpoints={"main": clients[0], "vision": clients[1]},
        models={"main": "m", "vision": "v"},
        router=ModelRouter(),
        ledger=ledger,
    )
    return llm, clients, ledger


@pytest.mark.parametrize("mode", ["aclose_same_task", "aclose_other_task", "cancel_task"])
async def test_closing_stream_disconnects_server_promptly(sse_server: str, mode: str) -> None:
    STATE["closed_at"] = None
    STATE["sent"] = 0
    llm, clients, ledger = await _llm(sse_server)
    try:
        stream = llm.stream(
            [{"role": "user", "content": "go"}], context=CallContext(task=ModelTask.TURN_DECISION)
        )
        received: list[str] = []

        three = asyncio.Event()

        async def consume() -> None:
            async for delta in stream:
                received.append(delta)
                if len(received) == 3:
                    three.set()

        if mode == "aclose_same_task":
            async for delta in stream:
                received.append(delta)
                if len(received) == 3:
                    break
            closed = time.perf_counter()
            await stream.aclose()
        else:
            task = asyncio.create_task(consume())
            await asyncio.wait_for(three.wait(), 5)
            closed = time.perf_counter()
            if mode == "aclose_other_task":
                await stream.aclose()
                await asyncio.wait_for(task, 2)
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        for _ in range(200):
            if STATE["closed_at"] is not None:
                break
            await asyncio.sleep(0.005)
        assert STATE["closed_at"] is not None, "server never saw the disconnect"
        disconnect_ms = (float(STATE["closed_at"]) - closed) * 1000
        print(f"\n[barge-in:{mode}] server saw disconnect {disconnect_ms:.1f} ms after close")
        assert disconnect_ms < 250, f"server saw the disconnect after {disconnect_ms:.0f} ms"
        sent_at_close = int(STATE["sent"] or 0)
        await asyncio.sleep(0.1)
        assert int(STATE["sent"] or 0) == sent_at_close  # generation stopped
        assert received[:3] == ["tok0 ", "tok1 ", "tok2 "]
        assert ledger.records[-1].status == "cancelled"
        assert clients[0].inflight == 0
    finally:
        for client in clients:
            await client.aclose()
