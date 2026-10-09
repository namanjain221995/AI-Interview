"""Entrypoint: drop root privileges, then serve the API with uvicorn.

The container starts as root only to learn the group that owns the mounted
Docker socket; the server itself runs as the unprivileged ``sandbox`` user
with that group as its only supplementary group. (Access to the Docker socket
is still host-root equivalent: candidate code never reaches this process.)
"""

from __future__ import annotations

import contextlib
import os
import sys

import uvicorn

from .config import get_settings

SERVICE_UID = 10002
SERVICE_GID = 10002


def drop_privileges(docker_url: str) -> None:
    if os.geteuid() != 0:
        return
    groups: list[int] = []
    if docker_url.startswith("unix://"):
        path = docker_url[len("unix://") :]
        with contextlib.suppress(OSError):
            groups.append(os.stat(path).st_gid)
    os.setgroups([g for g in groups if g != 0] or [])
    os.setgid(SERVICE_GID)
    os.setuid(SERVICE_UID)
    if os.geteuid() == 0 or os.getuid() == 0:  # pragma: no cover - defensive
        sys.exit("refusing to run as root")
    os.environ["HOME"] = "/tmp"


def main() -> None:
    settings = get_settings()
    drop_privileges(settings.docker_url)
    uvicorn.run(
        "sandbox_service.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,
        loop="uvloop",
        http="httptools",
        access_log=False,
        server_header=False,
        date_header=False,
        proxy_headers=False,
        timeout_keep_alive=15,
        log_config=None,
    )


if __name__ == "__main__":
    main()
