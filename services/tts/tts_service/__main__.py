"""``python -m tts_service``: serve the app with uvicorn (one process; concurrency is the worker pool)."""

from __future__ import annotations

import importlib.util

import uvicorn

from tts_service.config import get_settings
from tts_service.logging_setup import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    uvicorn.run(
        "tts_service.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,
        loop="uvloop" if importlib.util.find_spec("uvloop") else "asyncio",
        http="httptools" if importlib.util.find_spec("httptools") else "h11",
        log_config=None,
        access_log=False,
        server_header=False,
        timeout_keep_alive=30,
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
