"""Embedding cache against the running Redis (the platform plugin leases a
per-process logical database and points REDIS_URL at it)."""

from __future__ import annotations

import json
import uuid

import httpx
import numpy as np
import pytest
import respx
from redis.asyncio import Redis

from app.ai.breaker import CircuitBreaker
from app.ai.clients.embeddings import CACHE_TTL_SECONDS, EmbeddingCache, OpenAIEmbeddingsClient, cache_key
from app.ai.http import EndpointClient
from app.core.config import get_settings

pytestmark = pytest.mark.integration

BASE = "http://emb.test/v1"


async def test_cache_round_trip_with_real_redis() -> None:
    redis = Redis.from_url(get_settings().redis_url, socket_timeout=2)
    try:
        await redis.ping()
    except Exception as exc:  # noqa: BLE001
        await redis.aclose()
        pytest.skip(f"redis not reachable: {type(exc).__name__}")
    model = f"test-model-{uuid.uuid4().hex}"
    http = EndpointClient(
        "embedding", BASE, timeout=5, max_concurrency=2, breaker=CircuitBreaker("embedding")
    )
    client = OpenAIEmbeddingsClient(
        http, model=model, dimensions=4, batch_size=8, cache=EmbeddingCache(redis, dimensions=4)
    )
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, 2.0, 2.0, float(len(t))]} for i, t in enumerate(inputs)
                ]
            },
        )

    keys = [cache_key(model, "document", t) for t in ("alpha", "beta")] + [
        cache_key(model, "query", "Instruct: i\nQuery: alpha")
    ]
    try:
        with respx.mock(base_url=BASE) as mock:
            mock.post("/embeddings").mock(side_effect=handler)
            first = await client.embed(["alpha", "beta"])
            second = await client.embed(["beta", "alpha"])
            query = await client.embed_query("alpha", "i")
        assert calls == [["alpha", "beta"], ["Instruct: i\nQuery: alpha"]]
        assert np.allclose(first[0], second[1], atol=1e-7)
        assert np.isclose(np.linalg.norm(query), 1.0, atol=1e-6)
        raw = await redis.get(keys[0])
        assert raw is not None and len(raw) == 16
        assert np.allclose(np.frombuffer(raw, dtype="<f4"), first[0], atol=1e-7)
        ttl = await redis.ttl(keys[0])
        assert CACHE_TTL_SECONDS - 60 < ttl <= CACHE_TTL_SECONDS
    finally:
        await redis.delete(*keys)
        await redis.aclose()
        await http.aclose()
