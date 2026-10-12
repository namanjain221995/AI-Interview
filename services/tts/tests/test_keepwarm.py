"""Idle keep-warm: a session left idle was paged out and the first question after a pause took ~4 s to synthesize."""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.conftest import FakeEngine
from tts_service.config import Settings
from tts_service.service import TtsService


class CountingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.warmups: list[str] = []

    def warm_up(self, text: str, voice: str) -> float:
        self.warmups.append(text)
        return 0.01


async def _ready(settings: Settings, engine: CountingEngine) -> TtsService:
    service = TtsService(settings, engine)
    await service.start()
    engine.warmups.clear()  # the startup warm-up is not a keep-warm
    return service


async def test_idle_sessions_are_kept_warm() -> None:
    engine = CountingEngine()
    service = await _ready(Settings(keepwarm_seconds=0.05), engine)
    service.last_used = time.monotonic() - 10
    task = asyncio.create_task(service.keep_warm())
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.warmups and set(engine.warmups) == {Settings().keepwarm_text}


async def test_no_keep_warm_while_in_use_or_when_disabled() -> None:
    engine = CountingEngine()
    service = await _ready(Settings(keepwarm_seconds=0.05), engine)

    async def busy() -> None:
        while True:
            service.last_used = time.monotonic()  # a request every few milliseconds
            await asyncio.sleep(0.005)

    traffic = asyncio.create_task(busy())
    task = asyncio.create_task(service.keep_warm())
    await asyncio.sleep(0.2)
    for t in (task, traffic):
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    assert engine.warmups == []
    disabled = await _ready(Settings(keepwarm_seconds=0), CountingEngine())
    await asyncio.wait_for(disabled.keep_warm(), timeout=1)  # returns at once
