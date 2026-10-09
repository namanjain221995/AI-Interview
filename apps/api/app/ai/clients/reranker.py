"""Qwen3-Reranker client (vLLM ``POST /score`` with the model's prompt
template).

``/v1/rerank`` exists on the server but sends untemplated text and
discriminates poorly; ``/score`` with the template below yields the
probability that the document satisfies the query. Every failure surfaces
as ``AIUnavailable`` so retrieval callers can keep their own ordering.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Sequence
from typing import Any

from app.ai.base import AIError, AIUnavailable, CallContext, RerankScore
from app.ai.http import EndpointClient, call_status, failure_reason, gather_or_cancel
from app.ai.ledger import LedgerSink, ModelCallRecord, NullLedger
from app.core import metrics
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus, ModelTask

log = get_logger(__name__)

PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct "
    'provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
DEFAULT_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"

# The model context is 4096 tokens per (query, document) pair; documents are
# cut client side so the template suffix (which carries the yes/no decision
# point) is never truncated away.
MAX_DOCUMENT_CHARS = 6000
MAX_QUERY_CHARS = 2000
DOCS_PER_REQUEST = 32
DEGENERATE_SPREAD = 1e-4


def format_query(query: str, instruction: str | None = None) -> str:
    return f"{PREFIX}<Instruct>: {instruction or DEFAULT_INSTRUCTION}\n<Query>: {query[:MAX_QUERY_CHARS]}\n"


def format_document(document: str) -> str:
    return f"<Document>: {document[:MAX_DOCUMENT_CHARS]}{SUFFIX}"


class QwenRerankerClient:
    """``RerankerClient`` with degenerate-score checks. Concurrency is capped
    by the endpoint client (``RERANKER_MAX_CONCURRENCY`` slots; waiting for a
    slot counts against the call's time budget)."""

    def __init__(
        self,
        http: EndpointClient,
        *,
        model: str,
        ledger: LedgerSink | None = None,
        docs_per_request: int = DOCS_PER_REQUEST,
    ) -> None:
        self._http = http
        self.model = model
        self._ledger: LedgerSink = ledger or NullLedger()
        self._docs_per_request = max(1, docs_per_request)

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        instruction: str | None = None,
        top_k: int | None = None,
        context: CallContext | None = None,
    ) -> list[RerankScore]:
        if not documents:
            return []
        task = (context.task if context else ModelTask.RERANK).value
        started = time.perf_counter()
        text_1 = format_query(query, instruction)
        chunks = [
            (offset, list(documents[offset : offset + self._docs_per_request]))
            for offset in range(0, len(documents), self._docs_per_request)
        ]
        status = ModelCallStatus.OK
        error: BaseException | None = None
        attempts = 0
        try:
            results = await gather_or_cancel(
                *(self._score(text_1, docs, offset, task) for offset, docs in chunks)
            )
        except asyncio.CancelledError:
            status = ModelCallStatus.CANCELLED
            self._record(context, status, started, 1, None)
            raise
        except AIError as exc:
            status, error = call_status(exc), exc
            metrics.MODEL_CALL_FAILURES.labels(
                endpoint="reranker", task=task, reason=failure_reason(exc)
            ).inc()
            self._record(context, status, started, 1, exc)
            if isinstance(exc, AIUnavailable):
                raise
            raise AIUnavailable(f"reranker: {exc}", endpoint="reranker") from exc
        scores: list[RerankScore] = []
        for chunk_scores, chunk_attempts in results:
            scores.extend(chunk_scores)
            attempts += chunk_attempts
        self._check_degenerate(scores, len(documents))
        scores.sort(key=lambda s: (-s.score, s.index))
        self._record(context, status, started, max(1, attempts), error)
        return scores[:top_k] if top_k is not None else scores

    async def _score(
        self, text_1: str, docs: list[str], offset: int, task: str
    ) -> tuple[list[RerankScore], int]:
        body: dict[str, Any] = {
            "model": self.model,
            "text_1": text_1,
            "text_2": [format_document(d) for d in docs],
        }
        started = time.perf_counter()
        result = await self._http.request("POST", "/score", json=body, task=task)
        metrics.RERANK_SECONDS.observe(time.perf_counter() - started)
        data = result.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != len(docs):
            raise AIUnavailable("reranker: malformed /score response", endpoint="reranker")
        scores: list[RerankScore] = []
        seen: set[int] = set()
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                raise AIUnavailable("reranker: malformed score item", endpoint="reranker")
            index = item.get("index", position)
            raw = item.get("score")
            if not isinstance(index, int) or not 0 <= index < len(docs) or index in seen:
                raise AIUnavailable("reranker: invalid document index in response", endpoint="reranker")
            seen.add(index)
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value):
                log.warning("ai.reranker.non_finite_score", index=offset + index)
                value = 0.0
            scores.append(RerankScore(index=offset + index, score=min(1.0, max(0.0, value))))
        return scores, result.attempts

    @staticmethod
    def _check_degenerate(scores: Sequence[RerankScore], count: int) -> None:
        if count < 3 or not scores:
            return
        values = [s.score for s in scores]
        spread = max(values) - min(values)
        if spread < DEGENERATE_SPREAD:
            log.warning(
                "ai.reranker.degenerate_scores",
                documents=count,
                value=round(values[0], 6),
                hint="all documents scored the same; check the prompt template / endpoint",
            )

    def _record(
        self,
        context: CallContext | None,
        status: ModelCallStatus,
        started: float,
        attempts: int,
        error: BaseException | None,
    ) -> None:
        try:
            self._ledger.record(
                ModelCallRecord(
                    task=(context.task if context else ModelTask.RERANK).value,
                    endpoint="reranker",
                    model=self.model,
                    status=status.value,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=None if error is None else f"{type(error).__name__}: {error}",
                    session_id=context.session_id if context else None,
                    organization_id=context.organization_id if context else None,
                    prompt_version=context.prompt_version if context else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai.ledger.record_failed", error=str(exc))
