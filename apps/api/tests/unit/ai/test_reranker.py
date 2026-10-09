from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from app.ai.base import AIUnavailable
from app.ai.clients.reranker import PREFIX, SUFFIX, QwenRerankerClient, format_document, format_query

BASE = "http://rerank.test"


@pytest.fixture
def make_client(endpoint_factory, ledger):
    def make(concurrency: int = 3, docs_per_request: int = 32, retries: int = 1) -> QwenRerankerClient:
        http = endpoint_factory("reranker", BASE, concurrency=concurrency, retries=retries)
        return QwenRerankerClient(http, model="rr", ledger=ledger, docs_per_request=docs_per_request)

    return make


def scoring(scores_by_doc: dict[str, float]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        data = []
        for i, doc in enumerate(body["text_2"]):
            text = doc[len("<Document>: ") : -len(SUFFIX)]
            data.append({"index": i, "object": "score", "score": scores_by_doc.get(text, 0.01)})
        return httpx.Response(200, json={"data": data})

    return handler


async def test_template_body_sorting_and_top_k(make_client, ledger) -> None:
    client = make_client()
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/score").mock(side_effect=scoring({"paris": 0.99, "banana": 0.001, "france": 0.6}))
        result = await client.rerank(
            "capital of France?", ["banana", "paris", "france"], instruction="Find answers", top_k=2
        )
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "rr"
    assert body["text_1"] == PREFIX + "<Instruct>: Find answers\n<Query>: capital of France?\n"
    assert body["text_2"][1] == "<Document>: paris" + SUFFIX
    assert [(s.index, s.score) for s in result] == [(1, 0.99), (2, 0.6)]
    assert ledger.records[-1].endpoint == "reranker" and ledger.records[-1].status == "ok"


async def test_default_instruction_and_formatting_helpers() -> None:
    assert "<Instruct>: Given a web search query" in format_query("q")
    assert format_document("x" * 10000).startswith("<Document>: " + "x" * 10)
    assert format_document("x" * 10000).endswith(SUFFIX)
    assert len(format_document("x" * 10000)) < 6100


async def test_large_lists_are_chunked_with_global_indexes(make_client) -> None:
    client = make_client(docs_per_request=4)
    docs = [f"d{i}" for i in range(10)]
    scores = {f"d{i}": i / 10 for i in range(10)}
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/score").mock(side_effect=scoring(scores))
        result = await client.rerank("q", docs)
    assert route.call_count == 3
    assert [s.index for s in result] == list(range(9, -1, -1))


async def test_concurrency_is_capped(make_client) -> None:
    client = make_client(concurrency=2, docs_per_request=1)
    active = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, json={"data": [{"index": 0, "score": 0.5}]})

    with respx.mock(base_url=BASE) as mock:
        mock.post("/score").mock(side_effect=handler)
        await client.rerank("q", [f"d{i}" for i in range(6)])
    assert peak == 2


async def test_errors_raise_ai_unavailable(make_client, ledger) -> None:
    client = make_client(retries=0)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/score").respond(400, json={"error": {"message": "too long"}})
        with pytest.raises(AIUnavailable):
            await client.rerank("q", ["a", "b"])
    assert ledger.records[-1].status == "error"
    with respx.mock(base_url=BASE) as mock:
        mock.post("/score").respond(200, json={"data": [{"index": 7, "score": 0.5}]})
        with pytest.raises(AIUnavailable, match="malformed|invalid"):
            await client.rerank("q", ["a"])
    with respx.mock(base_url=BASE) as mock:
        mock.post("/score").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(AIUnavailable):
            await client.rerank("q", ["a"])


async def test_degenerate_scores_warn(make_client) -> None:
    client = make_client()
    with respx.mock(base_url=BASE) as mock:
        mock.post("/score").mock(side_effect=scoring({}))  # everything 0.01
        with capture_logs() as logs:
            result = await client.rerank("q", ["a", "b", "c", "d"])
    assert len(result) == 4
    assert any(
        entry["event"] == "ai.reranker.degenerate_scores" and entry["log_level"] == "warning"
        for entry in logs
    )


async def test_non_finite_scores_are_zeroed(make_client) -> None:
    client = make_client()
    with respx.mock(base_url=BASE) as mock:
        mock.post("/score").respond(
            200, content=b'{"data": [{"index": 0, "score": NaN}, {"index": 1, "score": 0.4}]}'
        )
        result = await client.rerank("q", ["a", "b"])
    assert [(s.index, s.score) for s in result] == [(1, 0.4), (0, 0.0)]


async def test_empty_documents(make_client) -> None:
    assert await make_client().rerank("q", []) == []
