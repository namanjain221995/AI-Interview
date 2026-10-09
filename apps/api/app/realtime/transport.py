"""Transport abstraction for the session runtime.

The runtime only needs ``send_text`` / ``send_bytes`` / ``close``; keeping it
behind this protocol lets a WebRTC transport (aiortc / LiveKit data channel +
audio track) be added later without touching the pipeline.
"""

from __future__ import annotations

import contextlib
import secrets
from typing import Protocol

from starlette.websockets import WebSocket, WebSocketState

from app.core.logging import get_logger
from app.realtime.close_codes import reason_for

log = get_logger(__name__)


class Transport(Protocol):
    id: str

    @property
    def closed(self) -> bool: ...

    async def send_text(self, data: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, code: int, reason: str | None = None) -> None: ...


class WebSocketTransport:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.id = secrets.token_hex(6)
        self._closed = False
        self.close_code: int | None = None

    @property
    def closed(self) -> bool:
        return (
            self._closed
            or self.websocket.application_state != WebSocketState.CONNECTED
            or self.websocket.client_state == WebSocketState.DISCONNECTED
        )

    async def send_text(self, data: str) -> None:
        if self.closed:
            raise ConnectionError("transport closed")
        await self.websocket.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError("transport closed")
        await self.websocket.send_bytes(data)

    async def close(self, code: int, reason: str | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self.close_code = code
        if (
            self.websocket.application_state == WebSocketState.CONNECTED
            and self.websocket.client_state != WebSocketState.DISCONNECTED
        ):
            with contextlib.suppress(Exception):
                await self.websocket.close(code=code, reason=(reason or reason_for(code))[:120])
