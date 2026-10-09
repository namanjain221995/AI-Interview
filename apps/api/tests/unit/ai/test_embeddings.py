from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from app.ai.base import AIRejected, AIUnavailable, CallContext
from app.ai.clients.embeddings import (
    EmbeddingCache,
    OpenAIEmbeddingsClient,
    cache_key,
    format_query,
    l2_normalize,
)
from app.domain.enums import ModelTask

BASE = "http://emb.test/v1"
DIMS = 8


class FakePipeline:
    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.ops: list[tuple[str, bytes, int | None]] = []

    def set(self, key: str, value: bytes, ex: int | None = None) -> FakePipeline:
        self.ops.append((key, value, ex))
        return self

    async def execute(self) -> list[bool]:
        if self.store.fail:
            raise ConnectionError("redis down")
        for key, value, ex in self.ops:
            self.store.data[key] = value
            self.store.ttls[key] = ex
        return [True] * len(self.ops)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False
        self.hang = False
        self.mget_calls = 0

    async def mget(self, keys: list[str]) -> list[Any]:
        self.mget_calls += 1
        if self.hang:
            await asyncio.sleep(10)
        if self.fail:
            raise ConnectionError("redis down")
        return [self.data.get(k) for k in keys]

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


def vec(seed: int, scale: float = 3.0) -> list[float]:
    rng = np.random.default_rng(seed)
    return (rng.normal(size=DIMS) * scale).tolist()


def handler_factory(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        data = [
            {"object": "embedding", "index": i, "embedding": vec(len(text))}
            for i, text in enumerate(body["input"])
        ]
        data.reverse()  # servers may return items out of order
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 3 * len(data)}})

    return handler


@pytest.fixture
def client_factory(endpoint_factory, ledger):
    def make(cache: EmbeddingCache | None = None, batch_size: int = 2) -> OpenAIEmbeddingsClient:
        http = endpoint_factory("embedding", BASE, retries=1)
        return OpenAIEmbeddingsClient(
            http, model="emb-model", dimensions=DIMS, batch_size=batch_size, cache=cache, ledger=ledger
        )

    return make


async def test_batches_normalises_and_preserves_order(client_factory, ledger) -> None:
    client = client_factory(batch_size=2)
    calls: list[dict] = []
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").mock(side_effect=handler_factory(calls))
        vectors = await client.embed(texts)
    assert [len(c["input"]) for c in calls] == [2, 2, 1]
    assert all(c["model"] == "emb-model" and c["truncate_prompt_tokens"] == 4000 for c in calls)
    for text, vector in zip(texts, vectors, strict=True):
        assert len(vector) == DIMS
        assert math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=1e-5)
        expected = np.asarray(vec(len(text)))
        assert np.allclose(vector, expected / np.linalg.norm(expected), atol=1e-6)
    record = ledger.records[-1]
    assert (record.endpoint, record.task, record.status, record.attempts) == (
        "embedding",
        "embedding",
        "ok",
        3,
    )


async def test_duplicates_embedded_once(client_factory) -> None:
    client = client_factory(batch_size=10)
    calls: list[dict] = []
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").mock(side_effect=handler_factory(calls))
        vectors = await client.embed(["same", "other", "same"])
    assert calls[0]["input"] == ["same", "other"]
    assert vectors[0] == vectors[2]


async def test_query_format_and_empty_text(client_factory) -> None:
    client = client_factory()
    calls: list[dict] = []
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").mock(side_effect=handler_factory(calls))
        await client.embed_query("kubernetes autoscaling", "Find resume evidence")
        await client.embed_one("")
    assert calls[0]["input"] == ["Instruct: Find resume evidence\nQuery: kubernetes autoscaling"]
    assert calls[1]["input"] == [" "]
    assert (
        format_query("q")
        == "Instruct: Given a query, retrieve relevant passages that answer the query\nQuery: q"
    )


async def test_cache_hits_skip_endpoint_and_store_float32(client_factory, ledger) -> None:
    redis = FakeRedis()
    client = client_factory(cache=EmbeddingCache(redis, dimensions=DIMS))
    calls: list[dict] = []
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/embeddings").mock(side_effect=handler_factory(calls))
        first = await client.embed(["alpha", "beta"])
        records_after_first = len(ledger.records)
        second = await client.embed(["beta", "alpha"])
        assert route.call_count == 1
    assert np.allclose(first[0], second[1], atol=1e-7)
    assert len(ledger.records) == records_after_first  # cache-only hits are not model calls
    key = cache_key("emb-model", "document", "alpha")
    assert key.startswith("cache:emb:") and len(key) == len("cache:emb:") + 64
    assert len(redis.data[key]) == DIMS * 4
    assert redis.ttls[key] == 7 * 24 * 3600
    # query and document modes never share entries
    assert cache_key("emb-model", "query", "alpha") != key


async def test_cache_failures_never_break_embedding(client_factory) -> None:
    redis = FakeRedis()
    redis.fail = True
    client = client_factory(cache=EmbeddingCache(redis, dimensions=DIMS))
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").mock(side_effect=handler_factory([]))
        assert len(await client.embed(["x"])) == 1
    redis.fail, redis.hang = False, True
    cache = EmbeddingCache(redis, dimensions=DIMS, op_timeout=0.05)
    client = client_factory(cache=cache)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").mock(side_effect=handler_factory([]))
        loop = asyncio.get_running_loop()
        started = loop.time()
        assert len(await client.embed(["y"])) == 1
        assert loop.time() - started < 1.0


async def test_corrupt_cache_entries_are_ignored(client_factory) -> None:
    redis = FakeRedis()
    redis.data[cache_key("emb-model", "document", "x")] = b"\x00\x01"
    client = client_factory(cache=EmbeddingCache(redis, dimensions=DIMS))
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/embeddings").mock(side_effect=handler_factory([]))
        await client.embed(["x"])
    assert route.call_count == 1


async def test_dimension_mismatch_and_errors(client_factory, ledger) -> None:
    client = client_factory()
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").respond(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
        with pytest.raises(AIUnavailable, match="dimensions"):
            await client.embed(["x"])
    with respx.mock(base_url=BASE) as mock:
        mock.post("/embeddings").respond(400, json={"error": {"message": "too long"}})
        with pytest.raises(AIRejected):
            await client.embed(["x"], context=CallContext(task=ModelTask.QUESTION_GENERATION))
    assert ledger.records[-1].status == "error"
    assert ledger.records[-1].task == "question_generation"


async def test_failed_batch_cancels_the_rest(client_factory) -> None:
    client = client_factory(batch_size=1)
    started: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["input"][0]
        started.append(text)
        if text == "bad":
            return httpx.Response(400, json={"error": {"message": "nope"}})
        await asyncio.sleep(5)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": vec(1)}]})

    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/embeddings").mock(side_effect=handler)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        with pytest.raises(AIRejected):
            await client.embed(["slow1", "bad", "slow2"])
        assert loop.time() - t0 < 2


def test_l2_normalize_handles_zero() -> None:
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]
    assert math.isclose(sum(x * x for x in l2_normalize([3.0, 4.0])), 1.0, rel_tol=1e-6)


async def test_empty_input_returns_empty(client_factory) -> None:
    assert await client_factory().embed([]) == []
