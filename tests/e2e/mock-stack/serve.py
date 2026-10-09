"""Entry point of one mock-AI stack process: ``serve.py api --port N`` (uvicorn) or ``serve.py worker``.

Started by ``stack.py`` with the isolated environment (throwaway database, leased Redis DB, temporary storage)
already in ``os.environ``. The mock AI services are installed before the app is imported, so the app's start-up
(``init_ai``) keeps them instead of building clients for the real model endpoints.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parents[2] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(HERE))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "worker"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8480)
    args = parser.parse_args()

    import mock_ai

    mock_ai.install()
    if args.role == "worker":
        runpy.run_module("app.worker", run_name="__main__", alter_sys=True)
        return
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        server_header=False,
        log_level="warning",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
