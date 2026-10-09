"""OCR client for ``baidu/Unlimited-OCR`` (OpenAI-compatible chat endpoint).

The model answers the prompt ``OCR`` (``settings.ocr_prompt``; the prompt
"document parsing" loops) with one line per layout block, e.g.::

    title [33, 46, 275, 102]JOHN SMITH
    text [34, 116, 495, 157]john.smith@example.com

Layout tags are stripped, untagged chatter before the first block (the model
sometimes prefixes a sentence or the word "result") is dropped, and
degenerate repetition loops are detected and collapsed.

Errors: transport failures (endpoint down, timeout, circuit open) raise
``AIUnavailable`` / ``AITimeout`` so the calling job can retry later; a
request the endpoint rejects for this particular image (4xx) returns
``OcrResult(status="error")`` so the remaining pages can still be processed.
"""

from __future__ import annotations

import base64
import re
import time
from collections import Counter
from typing import Any

from app.ai.base import AIError, AIRejected, AIUnavailable, CallContext, OcrResult
from app.ai.http import EndpointClient, call_status, failure_reason
from app.ai.ledger import LedgerSink, ModelCallRecord, NullLedger, text_payload
from app.core import metrics
from app.core.logging import get_logger
from app.domain.enums import ModelCallStatus, ModelTask

log = get_logger(__name__)

SUPPORTED_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"}
)
DEFAULT_MAX_TOKENS = 3072
MAX_IMAGE_BYTES = 12 * 1024 * 1024

_LAYOUT_TAG = re.compile(
    r"^\s*([A-Za-z][A-Za-z_\-]{0,30})\s*\[\s*\d+(?:\.\d+)?\s*(?:,\s*\d+(?:\.\d+)?\s*){3}\]\s?(.*)$"
)
_EMPTY_MARKERS = frozenset({"[empty string]", "[empty]", "empty string", "<empty>"})
_ALNUM = re.compile(r"[A-Za-z0-9]")


_CONTEXT_LIMIT = re.compile(
    r"maximum context length is (\d+) tokens.*?prompt contains at least (\d+) input tokens", re.DOTALL
)
MIN_OUTPUT_TOKENS = 256


def _fitting_max_tokens(message: str) -> int | None:
    """Output budget that fits the context, from vLLM's context-length error."""
    match = _CONTEXT_LIMIT.search(message)
    if not match:
        return None
    budget = int(match.group(1)) - int(match.group(2)) - 16
    return budget if budget >= MIN_OUTPUT_TOKENS else None


def strip_layout_tags(raw: str) -> str:
    """Remove ``kind [x1, y1, x2, y2]`` prefixes. When the output contains
    tagged blocks, untagged lines before the first block are model chatter
    and are dropped; untagged lines after it are continuation lines."""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tagged = [bool(_LAYOUT_TAG.match(line)) for line in lines]
    out: list[str] = []
    seen_tag = False
    for line, is_tagged in zip(lines, tagged, strict=True):
        if is_tagged:
            seen_tag = True
            match = _LAYOUT_TAG.match(line)
            content = match.group(2) if match else line
        else:
            if any(tagged) and not seen_tag:
                continue
            content = line
        content = content.rstrip()
        if content.strip().lower() in _EMPTY_MARKERS:
            continue
        out.append(content)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_degenerate(text: str, *, finish_reason: str | None = None) -> bool:
    """Detect repetition loops: one line dominating the output, a long run of
    identical lines, or a periodic tail."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 8:
        top, count = Counter(lines).most_common(1)[0]
        if _ALNUM.search(top) and count >= 6 and count / len(lines) >= 0.5:
            return True
        run = best = 1
        for prev, cur in zip(lines, lines[1:], strict=False):
            run = run + 1 if cur == prev and _ALNUM.search(cur) else 1
            best = max(best, run)
        if best >= 6:
            return True
    min_repeats = 4 if finish_reason == "length" else 6
    return (
        _periodic_tail(text, min_repeats=min_repeats, min_span=120 if finish_reason == "length" else 200)
        is not None
    )


def _periodic_tail(text: str, *, min_repeats: int, min_span: int) -> int | None:
    """Period of a repeated unit at the end of ``text`` (or None)."""
    tail = text[-2000:].rstrip()
    for period in range(1, 201):
        if period * min_repeats > len(tail):
            break
        unit = tail[-period:]
        if not _ALNUM.search(unit):
            continue
        repeats = 1
        pos = len(tail) - period
        while pos - period >= 0 and tail[pos - period : pos] == unit:
            repeats += 1
            pos -= period
        if repeats >= min_repeats and repeats * period >= min_span:
            return period
    return None


def collapse_repetition(text: str) -> str:
    """Best-effort cleanup of a looping output: collapse consecutive
    duplicate lines and cut a periodic tail down to one occurrence."""
    lines = text.splitlines()
    collapsed: list[str] = []
    for line in lines:
        if collapsed and line.strip() and line.strip() == collapsed[-1].strip():
            continue
        collapsed.append(line)
    result = "\n".join(collapsed)
    period = _periodic_tail(result, min_repeats=3, min_span=60)
    if period:
        unit = result[-period:]
        while result.endswith(unit + unit):
            result = result[:-period]
    return result.strip()


class UnlimitedOcrClient:
    """``OcrClient`` for the OCR chat endpoint."""

    def __init__(
        self,
        http: EndpointClient,
        *,
        model: str,
        prompt: str = "OCR",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        ledger: LedgerSink | None = None,
    ) -> None:
        self._http = http
        self.model = model
        self.prompt = prompt
        self.max_tokens = max_tokens
        self._ledger: LedgerSink = ledger or NullLedger()

    async def ocr_image(
        self,
        image: bytes,
        *,
        mime_type: str = "image/png",
        prompt: str | None = None,
        context: CallContext | None = None,
    ) -> OcrResult:
        task = (context.task if context else ModelTask.OCR).value
        mime = mime_type.split(";")[0].strip().lower()
        if mime == "image/jpg":
            mime = "image/jpeg"
        if not image:
            return OcrResult(text="", status="empty")
        if mime not in SUPPORTED_MIME_TYPES:
            return OcrResult(text="", status="error", raw=f"unsupported image type {mime}")
        if len(image) > MAX_IMAGE_BYTES:
            return OcrResult(text="", status="error", raw=f"image too large ({len(image)} bytes)")
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt or self.prompt},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        started = time.perf_counter()
        try:
            # idempotent=False: a timed-out page is not re-run (it costs a long generation).
            try:
                response = await self._http.request(
                    "POST", "/chat/completions", json=body, idempotent=False, task=task
                )
            except AIRejected as exc:
                # A large page can leave less room than max_tokens in the
                # model context: retry once with the output budget that fits.
                budget = _fitting_max_tokens(str(exc))
                if budget is None or budget >= body["max_tokens"]:
                    raise
                log.info("ai.ocr.reduced_max_tokens", max_tokens=budget)
                body["max_tokens"] = budget
                response = await self._http.request(
                    "POST", "/chat/completions", json=body, idempotent=False, task=task
                )
            data = response.json()
            choice = (data.get("choices") or [None])[0] if isinstance(data, dict) else None
            if not isinstance(choice, dict):
                raise AIUnavailable("ocr: malformed completion response", endpoint="ocr")
        except AIRejected as exc:
            metrics.MODEL_CALL_FAILURES.labels(endpoint="ocr", task=task, reason=failure_reason(exc)).inc()
            self._record(context, ModelCallStatus.ERROR, started, 1, None, None, exc, None)
            log.warning("ai.ocr.rejected", error=str(exc))
            return OcrResult(
                text="", status="error", raw=str(exc), latency_ms=(time.perf_counter() - started) * 1000
            )
        except AIError as exc:
            metrics.MODEL_CALL_FAILURES.labels(endpoint="ocr", task=task, reason=failure_reason(exc)).inc()
            self._record(context, call_status(exc), started, 1, None, None, exc, None)
            raise
        elapsed = time.perf_counter() - started
        metrics.OCR_SECONDS.observe(elapsed)
        raw = str((choice.get("message") or {}).get("content") or "")
        finish_reason = choice.get("finish_reason")
        text = strip_layout_tags(raw)
        if not text:
            status = "empty"
        elif is_degenerate(text, finish_reason=finish_reason):
            status = "degenerate"
            text = collapse_repetition(text)
            log.warning("ai.ocr.degenerate_output", finish_reason=finish_reason, raw_chars=len(raw))
        else:
            status = "ok"
        usage = data.get("usage") if isinstance(data, dict) else None
        self._record(
            context,
            ModelCallStatus.OK,
            started,
            response.attempts,
            usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            usage.get("completion_tokens") if isinstance(usage, dict) else None,
            None,
            raw,
        )
        return OcrResult(text=text, status=status, raw=raw, latency_ms=elapsed * 1000)  # type: ignore[arg-type]

    def _record(
        self,
        context: CallContext | None,
        status: ModelCallStatus,
        started: float,
        attempts: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        error: BaseException | None,
        raw: str | None,
    ) -> None:
        try:
            self._ledger.record(
                ModelCallRecord(
                    task=(context.task if context else ModelTask.OCR).value,
                    endpoint="ocr",
                    model=self.model,
                    status=status.value,
                    attempts=attempts,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
                    completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
                    error=None if error is None else f"{type(error).__name__}: {error}",
                    response_payload=text_payload(raw)
                    if raw is not None and self._ledger.log_payloads
                    else None,
                    session_id=context.session_id if context else None,
                    organization_id=context.organization_id if context else None,
                    prompt_version=context.prompt_version if context else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ai.ledger.record_failed", error=str(exc))
