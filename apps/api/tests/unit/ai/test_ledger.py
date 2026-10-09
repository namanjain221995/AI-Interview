from __future__ import annotations

import asyncio
import uuid

import pytest

from app.ai.ledger import (
    MAX_ERROR_CHARS,
    MAX_MESSAGE_CHARS,
    ModelCallLedger,
    ModelCallRecord,
    messages_payload,
    text_payload,
)
from app.core.metrics import REGISTRY


def rec(i: int = 0, **kwargs) -> ModelCallRecord:
    return ModelCallRecord(
        task="turn_decision", endpoint="main", model="m", status="ok", latency_ms=float(i), **kwargs
    )


class Writer:
    def __init__(self, fail_times: int = 0, delay: float = 0.0) -> None:
        self.batches: list[list[dict]] = []
        self.fail_times = fail_times
        self.delay = delay

    async def __call__(self, rows: list[dict]) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("db down")
        self.batches.append(rows)


async def test_records_are_batched_and_flushed() -> None:
    writer = Writer()
    ledger = ModelCallLedger(writer=writer, batch_size=3, flush_interval=10)
    for i in range(7):
        ledger.record(rec(i))
    await ledger.flush()
    assert [len(b) for b in writer.batches] == [3, 3, 1]
    assert ledger.written == 7 and ledger.pending == 0
    await ledger.aclose()


async def test_background_task_flushes_periodically() -> None:
    writer = Writer()
    ledger = ModelCallLedger(writer=writer, flush_interval=0.05)
    await ledger.start()
    ledger.record(rec())
    for _ in range(50):
        if writer.batches:
            break
        await asyncio.sleep(0.01)
    assert len(writer.batches) == 1
    await ledger.aclose()


async def test_record_never_blocks_and_drops_when_full() -> None:
    writer = Writer()
    ledger = ModelCallLedger(writer=writer, max_queue=2, flush_interval=10)
    before = REGISTRY.get_sample_value("interview_model_call_ledger_dropped_total") or 0
    for i in range(5):
        ledger.record(rec(i))
    assert ledger.pending == 2
    assert ledger.dropped == 3
    assert REGISTRY.get_sample_value("interview_model_call_ledger_dropped_total") == before + 3
    await ledger.aclose()
    assert sum(len(b) for b in writer.batches) == 2


async def test_writer_failures_are_contained() -> None:
    writer = Writer(fail_times=1)
    ledger = ModelCallLedger(writer=writer, batch_size=10, flush_interval=10)
    ledger.record(rec(1))
    await ledger.flush()
    assert ledger.failed == 1 and writer.batches == []
    ledger.record(rec(2))
    await ledger.flush()
    assert ledger.written == 1
    await ledger.aclose()


async def test_malformed_record_is_dropped_not_fatal() -> None:
    writer = Writer()
    ledger = ModelCallLedger(writer=writer, flush_interval=10)
    bad = rec(1)
    bad.attempts = "many"  # type: ignore[assignment]
    ledger.record(bad)
    ledger.record(rec(2))
    await ledger.flush()
    assert ledger.failed == 1 and ledger.written == 1
    await ledger.aclose()


async def test_aclose_flushes_and_rejects_new_records() -> None:
    writer = Writer(delay=0.01)
    ledger = ModelCallLedger(writer=writer, flush_interval=10)
    await ledger.start()
    for i in range(4):
        ledger.record(rec(i))
    await ledger.aclose()
    assert sum(len(b) for b in writer.batches) == 4
    ledger.record(rec(9))
    assert ledger.dropped == 1


async def test_record_outside_loop_is_queued_until_start() -> None:
    writer = Writer()
    ledger = ModelCallLedger(writer=writer, flush_interval=0.02)

    def sync_record() -> None:
        ledger.record(rec())

    await asyncio.get_running_loop().run_in_executor(None, sync_record)
    assert ledger.pending == 1
    await ledger.start()
    await asyncio.sleep(0.1)
    assert ledger.written == 1
    await ledger.aclose()


def test_to_row_conversions() -> None:
    session = uuid.uuid4()
    row = rec(
        session_id=str(session),
        organization_id="not-a-uuid",
        error="x" * (MAX_ERROR_CHARS + 50),
        prompt_version="p" * 60,
        attempts=0,
    ).to_row()
    assert row["session_id"] == session
    assert row["organization_id"] is None
    assert len(row["error"]) == MAX_ERROR_CHARS
    assert len(row["prompt_version"]) == 40
    assert row["attempts"] == 1
    assert isinstance(row["id"], uuid.UUID)
    assert row["created_at"].tzinfo is not None


def test_payload_helpers_remove_images_and_truncate() -> None:
    messages = [
        {"role": "system", "content": "s" * (MAX_MESSAGE_CHARS + 100)},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "text", "text": "describe"},
            ],
        },
    ]
    stored = messages_payload(messages)
    assert stored[0]["content"].startswith("s" * 100) and "truncated" in stored[0]["content"]
    assert stored[1]["content"] == [
        {"type": "image_url", "omitted": True},
        {"type": "text", "text": "describe"},
    ]
    assert "base64" not in str(stored)
    assert text_payload(None) == {"text": ""}


@pytest.mark.parametrize("value", [None, "x"])
async def test_null_ledger(value) -> None:
    from app.ai.ledger import NullLedger

    NullLedger().record(rec())
