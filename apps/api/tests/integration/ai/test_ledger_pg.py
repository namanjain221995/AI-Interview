"""The model-call ledger writes into the migrated ``model_calls`` table through
``app.db.session.session_scope`` (default writer)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ledger import ModelCallLedger, ModelCallRecord, messages_payload, text_payload
from app.db.models.system import ModelCall

pytestmark = pytest.mark.integration


async def test_ledger_batches_into_model_calls(db_session: AsyncSession) -> None:
    session_id = uuid.uuid4()
    ledger = ModelCallLedger(batch_size=10, flush_interval=0.05, log_payloads=True)
    await ledger.start()
    ids = []
    for i in range(23):
        record = ModelCallRecord(
            task="turn_decision",
            endpoint="main",
            model="Qwen/Qwen3.6-35B-A3B-NVFP4",
            status="ok" if i % 5 else "repaired",
            attempts=1 + (i % 2),
            latency_ms=100.0 + i,
            ttft_ms=12.5,
            prompt_tokens=120,
            completion_tokens=40,
            repaired=i % 5 == 0,
            prompt_version="turn_decision@0123456789",
            request_hash="ab" * 32,
            session_id=str(session_id),
            organization_id="not-a-uuid",
            request_payload={"messages": messages_payload([{"role": "user", "content": "hi"}])},
            response_payload=text_payload('{"speech": "Hello"}'),
        )
        ids.append(record.id)
        ledger.record(record)
    await ledger.aclose()
    assert ledger.written == 23 and ledger.failed == 0 and ledger.dropped == 0

    count = await db_session.scalar(
        select(func.count()).select_from(ModelCall).where(ModelCall.session_id == session_id)
    )
    assert count == 23
    row = await db_session.get(ModelCall, ids[0])
    assert row is not None
    assert row.status == "repaired" and row.repaired is True
    assert row.organization_id is None
    assert row.response_payload == {"text": '{"speech": "Hello"}'}
    assert row.request_payload == {"messages": [{"role": "user", "content": "hi"}]}
    assert row.created_at.tzinfo is not None


async def test_ledger_survives_database_errors(db_session: AsyncSession) -> None:
    ledger = ModelCallLedger(batch_size=5, flush_interval=10)
    first = ModelCallRecord(task="memory_summary", endpoint="vision", model="m", status="ok")
    ledger.record(first)
    await ledger.flush()
    assert ledger.written == 1
    duplicate = ModelCallRecord(id=first.id, task="memory_summary", endpoint="vision", model="m", status="ok")
    ledger.record(duplicate)  # primary-key violation: the batch fails, the ledger keeps going
    await ledger.flush()
    assert ledger.failed == 1
    second = ModelCallRecord(task="memory_summary", endpoint="vision", model="m", status="ok")
    ledger.record(second)
    await ledger.flush()
    assert ledger.written == 2
    assert await db_session.get(ModelCall, second.id) is not None
    await ledger.aclose()
