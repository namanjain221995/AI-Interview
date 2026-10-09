"""WebSocket Origin validation and ``ws_url`` construction.

The edge (Caddy) does not check Origin on ``/ws/*``, so the API must, to stop
cross-site WebSocket hijacking. Allowed origins:

* ``PUBLIC_BASE_URL``'s origin and every ``CORS_ALLOWED_ORIGINS`` entry;
* same-host origins: the Origin's host:port equals the request's ``Host``
  header (default ports normalised by the Origin scheme), so
  ``https://<lan-ip>:3443`` and ``http://localhost:3200`` both work without
  configuration.

A missing Origin (non-browser client) is accepted outside production only;
``null`` origins are always rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(?::\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](?::\d{1,5})?$")


def _normalize_origin(origin: str) -> tuple[str, str, int] | None:
    """Origin -> (scheme, host, port) or None when malformed."""
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        return None
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        return None
    return scheme, parts.hostname.lower(), port


def _normalize_host(host: str, scheme: str) -> tuple[str, int] | None:
    """Host header ("example.com:3443", "[::1]:3200") -> (host, port)."""
    try:
        parts = urlsplit(f"//{host.strip()}")
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    return hostname.lower(), port or DEFAULT_PORTS.get(scheme, 80)


def allowed_origins(public_base_url: str, cors_allowed_origins: Iterable[str]) -> set[tuple[str, str, int]]:
    out: set[tuple[str, str, int]] = set()
    for value in [public_base_url, *cors_allowed_origins]:
        if not value or value.strip() == "*":
            continue
        norm = _normalize_origin(value.rstrip("/"))
        if norm is not None:
            out.add(norm)
    return out


def origin_allowed(
    origin: str | None,
    *,
    host: str | None,
    public_base_url: str,
    cors_allowed_origins: Iterable[str],
    allow_missing: bool,
) -> bool:
    if origin is None or not origin.strip():
        return allow_missing
    if origin.strip().lower() == "null":
        return False
    norm = _normalize_origin(origin)
    if norm is None:
        return False
    if norm in allowed_origins(public_base_url, cors_allowed_origins):
        return True
    if host:
        scheme, o_host, o_port = norm
        request_host = _normalize_host(host, scheme)
        if request_host is not None and request_host == (o_host, o_port):
            return True
    return False


def header(headers: Mapping[str, str] | Iterable[tuple[bytes, bytes]], name: str) -> str | None:
    if isinstance(headers, Mapping):
        return headers.get(name)
    target = name.lower().encode("latin-1")
    for key, value in headers:
        if key.lower() == target:
            return value.decode("latin-1")
    return None


def build_ws_url(*, scheme: str, host: str | None, session_id: str, public_base_url: str) -> str:
    """Absolute ``ws(s)://host/ws/sessions/{id}`` for the host the browser used.

    ``scheme`` is the request scheme after proxy headers (uvicorn
    ``--proxy-headers`` maps ``X-Forwarded-Proto: https`` to ``https``). The
    ticket is NOT part of the URL: the client appends ``?ticket=...``."""
    path = ws_path(session_id)
    secure = scheme in ("https", "wss")
    if host and (not _HOST_RE.match(host) or _normalize_host(host, scheme) is None):
        host = None
    if not host:
        base = urlsplit(public_base_url)
        secure = base.scheme == "https"
        host = base.netloc
    return f"{'wss' if secure else 'ws'}://{host}{path}"


def ws_path(session_id: str) -> str:
    return f"/ws/sessions/{session_id}"
