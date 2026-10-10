"""Self-hosted proof-of-work for public forms (S15), in the ALTCHA challenge format.

The server picks a random ``number`` and publishes ``challenge = sha256(salt + number)`` with an HMAC signature;
the browser finds the number by trying 0..``maxnumber`` (about a second on a mid-range phone at the default
difficulty) and sends it back. A solution is bound to a scope (the open link), expires, and is single use
(Redis), so scripted mass registration costs real CPU per attempt while people never see a puzzle. No
third-party CAPTCHA, no cookies, no personal data.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

ALGORITHM = "SHA-256"
_USED_PREFIX = "pow:used:"


class PowError(Exception):
    """A missing or unusable solution; ``code`` is the API error code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Challenge:
    algorithm: str
    challenge: str
    salt: str
    signature: str
    maxnumber: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_SALT_END = "&"
CLOCK_SKEW_S = 60


def _key() -> bytes:
    settings = get_settings()
    base = (
        settings.open_link_signal_secret.get_secret_value()
        if settings.open_link_signal_secret is not None
        and settings.open_link_signal_secret.get_secret_value()
        else settings.media_url_secret.get_secret_value()
    )
    return hmac.new(base.encode("utf-8"), b"proof-of-work-v1", hashlib.sha256).digest()


def _digest(salt: str, number: int) -> str:
    return hashlib.sha256(f"{salt}{number}".encode()).hexdigest()


def _sign(challenge: str) -> str:
    return hmac.new(_key(), challenge.encode("ascii"), hashlib.sha256).hexdigest()


def create_challenge(
    scope: str, *, max_number: int | None = None, ttl_s: int | None = None, now: float | None = None
) -> Challenge:
    settings = get_settings()
    maxnumber = max_number if max_number is not None else settings.open_link_pow_max_number
    expires = int(
        (now if now is not None else time.time())
        + (ttl_s if ttl_s is not None else settings.open_link_pow_ttl_seconds)
    )
    # The trailing "&" ends the signed part: the hash covers salt + number, so without a fixed end a client could
    # move leading digits of the number into the salt and stretch "expires" (verify() requires it).
    salt = f"{secrets.token_hex(12)}?scope={scope}&expires={expires}{_SALT_END}"
    challenge = _digest(salt, secrets.randbelow(maxnumber + 1))
    return Challenge(ALGORITHM, challenge, salt, _sign(challenge), maxnumber)


def solve(challenge: Challenge) -> str:
    """The payload a browser would send (tests and tooling); brute force like the web solver."""
    for number in range(challenge.maxnumber + 1):
        if _digest(challenge.salt, number) == challenge.challenge:
            body = {"algorithm": challenge.algorithm, "challenge": challenge.challenge, "number": number,
                    "salt": challenge.salt, "signature": challenge.signature}  # fmt: skip
            return base64.b64encode(json.dumps(body).encode()).decode()
    raise ValueError("unsolvable challenge")


def verify(payload: str | None, scope: str, *, now: float | None = None) -> tuple[str, int]:
    """``(challenge, expires)`` of a valid solution for ``scope``; raises ``PowError`` otherwise."""
    if not payload:
        raise PowError("pow_required", "missing")
    try:
        body = json.loads(base64.b64decode(payload, validate=True))
        challenge, salt, signature = str(body["challenge"]), str(body["salt"]), str(body["signature"])
        number = int(body["number"])
        algorithm = str(body.get("algorithm", ALGORITHM))
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise PowError("pow_invalid", "malformed") from None
    if algorithm != ALGORITHM:
        raise PowError("pow_invalid", "algorithm")
    if not hmac.compare_digest(_sign(challenge), signature):
        raise PowError("pow_invalid", "signature")
    if number < 0 or not hmac.compare_digest(_digest(salt, number), challenge):
        raise PowError("pow_invalid", "number")
    if not salt.endswith(_SALT_END):
        raise PowError("pow_invalid", "salt")
    params = parse_qs(salt.partition("?")[2])
    if params.get("scope", [""])[0] != scope:
        raise PowError("pow_invalid", "scope")
    try:
        expires = int(params.get("expires", ["0"])[0])
    except ValueError:
        raise PowError("pow_invalid", "expires") from None
    current = now if now is not None else time.time()
    if expires <= current:
        raise PowError("pow_invalid", "expired")
    if expires > current + get_settings().open_link_pow_ttl_seconds + CLOCK_SKEW_S:
        raise PowError("pow_invalid", "expires")  # never issued that far ahead
    return challenge, expires


async def consume(redis: Redis, challenge: str, expires: int, *, now: float | None = None) -> None:
    """Single use: a replayed solution raises ``pow_invalid``. A Redis failure lets the request through (the
    rate limits still apply) rather than locking real candidates out."""
    ttl = max(
        1,
        min(
            expires - int(now if now is not None else time.time()),
            get_settings().open_link_pow_ttl_seconds + CLOCK_SKEW_S,
        ),
    )
    try:
        fresh = await redis.set(f"{_USED_PREFIX}{challenge}", "1", nx=True, ex=ttl)
    except (RedisError, OSError) as exc:
        log.warning("pow.redis_unavailable", error=type(exc).__name__)
        return
    if not fresh:
        raise PowError("pow_invalid", "replayed")


__all__ = ["ALGORITHM", "Challenge", "PowError", "consume", "create_challenge", "solve", "verify"]
