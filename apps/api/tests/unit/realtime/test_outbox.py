"""Outbox sequencing and replay (in-memory path; the Redis path is covered in
tests/integration/realtime/test_outbox_lock.py)."""

from __future__ import annotations

import json

from app.realtime.outbox import Outbox
from app.schemas.realtime import HeartbeatAckMsg, NoticeMsg, TtsChunkMsg


def record(outbox: Outbox, message) -> int:
    seq = outbox.next_seq()
    message.seq = seq
    outbox.record(seq, message.model_dump_json(), msg_type=message.type)
    return seq


async def test_sequence_and_replay_after_last_seq() -> None:
    outbox = Outbox("s1", None, buffer_size=50)
    assert await outbox.init() == 0
    seqs = [record(outbox, NoticeMsg(code=f"n{i}", message="m")) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    replay = await outbox.replay_after(2)
    assert [json.loads(r)["seq"] for r in replay] == [3, 4, 5]
    assert await outbox.replay_after(5) == []
    assert await outbox.replay_after(99) == []
    await outbox.aclose()


async def test_audio_announcements_and_heartbeats_are_not_replayed() -> None:
    outbox = Outbox("s1", None)
    await outbox.init()
    record(outbox, NoticeMsg(code="a", message="m"))
    record(
        outbox,
        TtsChunkMsg(utterance_id="u", chunk_id=1, index=0, text="x", sample_rate=24000, duration_ms=10),
    )
    record(outbox, HeartbeatAckMsg(t=1.0, server_time=2.0))
    record(outbox, NoticeMsg(code="b", message="m"))
    replay = [json.loads(r) for r in await outbox.replay_after(0)]
    assert [(r["seq"], r["type"]) for r in replay] == [(1, "notice"), (4, "notice")]
    await outbox.aclose()


async def test_buffer_is_bounded() -> None:
    outbox = Outbox("s1", None, buffer_size=10)
    await outbox.init()
    for i in range(25):
        record(outbox, NoticeMsg(code=str(i), message="m"))
    replay = [json.loads(r)["seq"] for r in await outbox.replay_after(0)]
    assert replay == list(range(16, 26))  # oldest dropped; client gets what is retained
    await outbox.aclose()
