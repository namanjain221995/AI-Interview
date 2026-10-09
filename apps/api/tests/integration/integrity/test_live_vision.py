"""Live check of frame analysis against the real vision endpoint (opt-in).

Sends two synthetic 640 px frames through the production prompt + schema
(``DefaultIntegrityService._observe``): an empty room and a room with two
drawn human figures. Run with ``-s`` to see latency and the observations::

    uv run pytest tests/integration/integrity/test_live_vision.py -m live -s
"""

from __future__ import annotations

import io
import time
import uuid

import pytest
from PIL import Image, ImageDraw

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.schemas.ai import FrameObservation
from app.services.integrity.frames import prepare_frame
from app.services.integrity.policy import resolve_policy
from app.services.integrity.service import DefaultIntegrityService, SessionContext

pytestmark = pytest.mark.live


def _room(figures: int) -> bytes:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), (196, 186, 170))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, int(height * 0.62)], fill=(214, 206, 192))  # wall
    draw.rectangle([0, int(height * 0.62), width, height], fill=(126, 98, 72))  # floor
    draw.rectangle(
        [int(width * 0.68), int(height * 0.12), int(width * 0.92), int(height * 0.42)], fill=(150, 185, 215)
    )  # window
    draw.line(
        [int(width * 0.8), int(height * 0.12), int(width * 0.8), int(height * 0.42)],
        fill=(240, 240, 240),
        width=6,
    )
    draw.rectangle(
        [int(width * 0.05), int(height * 0.25), int(width * 0.2), int(height * 0.62)], fill=(110, 80, 60)
    )  # shelf
    draw.rectangle(
        [int(width * 0.1), int(height * 0.7), int(width * 0.9), int(height * 0.76)], fill=(90, 64, 44)
    )  # desk
    for index in range(figures):
        cx = int(width * (0.35 + 0.3 * index))
        top = int(height * 0.16)
        skin, shirt, hair = (222, 178, 146), ((52, 84, 140) if index == 0 else (150, 60, 60)), (60, 40, 30)
        draw.rectangle([cx - 18, top + 118, cx + 18, top + 150], fill=skin)  # neck
        draw.polygon(
            [
                (cx - 120, top + 150),
                (cx + 120, top + 150),
                (cx + 150, int(height * 0.72)),
                (cx - 150, int(height * 0.72)),
            ],
            fill=shirt,
        )
        draw.ellipse([cx - 62, top, cx + 62, top + 135], fill=skin)  # head
        draw.chord([cx - 66, top - 8, cx + 66, top + 80], 180, 360, fill=hair)
        for dx in (-24, 24):  # eyes
            draw.ellipse([cx + dx - 8, top + 55, cx + dx + 8, top + 67], fill=(40, 30, 30))
        draw.arc([cx - 26, top + 80, cx + 26, top + 110], 20, 160, fill=(150, 70, 70), width=4)  # mouth
        draw.line([cx - 120, top + 160, cx - 175, int(height * 0.7)], fill=shirt, width=40)  # arms
        draw.line([cx + 120, top + 160, cx + 175, int(height * 0.7)], fill=shirt, width=40)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


async def test_live_vision_two_synthetic_frames() -> None:
    settings = get_settings()
    services = RealAIServices(settings, redis_client=None, ledger=MemoryLedger())
    previous = set_ai(services)
    service = DefaultIntegrityService()
    ctx = SessionContext(
        session_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        interview_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        status="live",
        started_at=None,
        ended_at=None,
        reconnect_count=0,
        resolved=resolve_policy({}),
    )
    results: dict[str, tuple[float, FrameObservation, int]] = {}
    try:
        for name, figures in (("empty_room", 0), ("two_figures", 2)):
            prepared = prepare_frame(_room(figures), "image/jpeg", max_bytes=settings.max_frame_bytes)
            started = time.perf_counter()
            observation = await service._observe(ctx, prepared.data, FrameObservation)
            elapsed = time.perf_counter() - started
            results[name] = (elapsed, observation, len(prepared.data))
            print(
                f"\n[live-vision] {name}: {elapsed * 1000:.0f} ms, {len(prepared.data)} bytes "
                f"{prepared.width}x{prepared.height} -> {observation.model_dump_json()}"
            )
    finally:
        set_ai(previous)
        await services.aclose()
    empty = results["empty_room"][1]
    assert isinstance(empty, FrameObservation) and isinstance(results["two_figures"][1], FrameObservation)
    assert empty.person_count == 0  # nobody in an empty room
    assert all(0 <= obs.confidence <= 1 for _, obs, _ in results.values())
