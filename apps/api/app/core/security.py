"""Credentials and tokens.

* Passwords and access codes: argon2id (RFC 9106 low-memory profile: t=3,
  m=64 MiB, p=4; ~75 ms per verify on the DGX host). The ``test`` environment
  uses a cheap profile so suites stay fast; :func:`password_needs_rehash`
  upgrades any weaker hash on the next successful login. Hashing runs on a
  small dedicated thread pool (argon2 releases the GIL) so it never blocks the
  event loop that also carries the realtime voice loop.
* Access tokens: HS256 JWTs with the claims of CONTRACTS.md section 3. Tokens
  signed with ``JWT_PREVIOUS_SECRET`` still verify during key rotation.
* Refresh / invite tokens: 32 random bytes (url-safe); only SHA-256 is stored.
* CSRF tokens: HMAC of the auth session id (double-submit, session bound).
* WebSocket tickets: single-use random tickets in Redis (GETDEL), 60 s TTL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import Unauthorized, ValidationFailed
from app.domain.enums import Role, SubjectType

if TYPE_CHECKING:
    from redis.asyncio import Redis

# ------------------------------------------------------------ argon2 hashing

_PRODUCTION_PROFILE = {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 4}
_TEST_PROFILE = {"time_cost": 1, "memory_cost": 1024, "parallelism": 1}
_HASH_EXECUTOR: ThreadPoolExecutor | None = None


@lru_cache(maxsize=2)
def _hasher_for(test_profile: bool) -> PasswordHasher:
    params = _TEST_PROFILE if test_profile else _PRODUCTION_PROFILE
    return PasswordHasher(
        time_cost=params["time_cost"],
        memory_cost=params["memory_cost"],
        parallelism=params["parallelism"],
        hash_len=32,
        salt_len=16,
    )


def password_hasher() -> PasswordHasher:
    return _hasher_for(get_settings().is_test)


def _executor() -> ThreadPoolExecutor:
    global _HASH_EXECUTOR
    if _HASH_EXECUTOR is None:
        _HASH_EXECUTOR = ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2), thread_name_prefix="argon2")
    return _HASH_EXECUTOR


def hash_password(password: str) -> str:
    return password_hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification; ``False`` for a mismatch or a malformed hash."""
    try:
        return password_hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return password_hasher().check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


@lru_cache(maxsize=2)
def _dummy_hash(test_profile: bool) -> str:
    return _hasher_for(test_profile).hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    """Spend the same time as a real verification (unknown account path)."""
    verify_password(password, _dummy_hash(get_settings().is_test))


async def hash_password_async(password: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(_executor(), hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.get_running_loop().run_in_executor(_executor(), verify_password, password, password_hash)


async def burn_password_check_async(password: str) -> None:
    await asyncio.get_running_loop().run_in_executor(_executor(), burn_password_check, password)


# ----------------------------------------------------------- password policy

MAX_PASSWORD_LENGTH = 256

# Most common passwords that satisfy a 12+ character rule, plus base words
# that are trivially guessed with digit/symbol affixes ("Password2026!").
_COMMON_PASSWORDS = frozenset(
    {
        "password", "passw0rd", "p@ssword", "p@ssw0rd", "password1", "password12", "password123",
        "password1234", "passwordpassword", "123456789012", "1234567890123", "12345678901234",
        "123456123456", "111111111111", "000000000000", "123123123123", "qwertyuiop12", "qwertyuiopas",
        "qwerty123456", "1q2w3e4r5t6y", "1qaz2wsx3edc", "zaq12wsxcde3", "asdfghjkl123", "iloveyou1234",
        "letmein12345", "welcome12345", "welcome", "letmein", "iloveyou", "qwerty", "admin", "administrator",
        "changeme", "changemenow", "secret", "monkey", "dragon", "football", "baseball", "superman",
        "batman", "trustno1", "sunshine", "princess", "shadow", "master", "whatever", "freedom",
        "interview", "interviewer", "recruiter", "candidate", "default", "temporary", "summer", "winter",
        "spring", "autumn", "abcdefghijkl", "abcd1234abcd", "aaaaaaaaaaaa", "correcthorse",
    }
)
_KEYBOARD_ROWS = ("qwertyuiopasdfghjklzxcvbnm", "1234567890", "abcdefghijklmnopqrstuvwxyz")
_AFFIX = re.compile(r"^[\W\d_]+|[\W\d_]+$")


def _is_keyboard_or_alpha_sequence(lowered: str) -> bool:
    for row in _KEYBOARD_ROWS:
        doubled = row + row
        if lowered in doubled or lowered[::-1] in doubled:
            return True
    return False


def _mostly_monotone(value: str) -> bool:
    if len(value) < 4:
        return False
    steps = [ord(b) - ord(a) for a, b in zip(value, value[1:], strict=False)]
    near = sum(1 for s in steps if s in (-1, 0, 1))
    return near / len(steps) >= 0.9


def _repeated_pattern(value: str) -> bool:
    n = len(value)
    for period in range(1, n // 3 + 1):
        unit = value[:period]
        if (unit * (n // period + 1))[:n] == value:
            return True
    return False


def password_problems(
    password: str,
    *,
    email: str | None = None,
    full_name: str | None = None,
    min_length: int | None = None,
) -> list[str]:
    """Human-readable reasons why ``password`` is not acceptable (empty = OK).

    Follows NIST SP 800-63B: length + blocklist + context words, no composition
    rules.
    """
    minimum = min_length if min_length is not None else get_settings().password_min_length
    problems: list[str] = []
    if len(password) < minimum:
        problems.append(f"Password must be at least {minimum} characters long.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters long.")
    lowered = password.lower()
    stripped = _AFFIX.sub("", lowered)
    letters_only = re.sub(r"[^a-z]", "", lowered)
    if lowered in _COMMON_PASSWORDS or stripped in _COMMON_PASSWORDS or letters_only in _COMMON_PASSWORDS:
        problems.append("Password is too common.")
    if len(set(password)) < 5 or _repeated_pattern(password):
        problems.append("Password is too repetitive.")
    if _is_keyboard_or_alpha_sequence(lowered) or _mostly_monotone(lowered):
        problems.append("Password must not be a simple sequence.")
    context_words: set[str] = set()
    if email and "@" in email:
        local = email.split("@", 1)[0].lower()
        context_words.update(part for part in re.split(r"[^a-z0-9]+", local) if len(part) >= 4)
        if len(local) >= 4:
            context_words.add(local)
    if full_name:
        context_words.update(part for part in re.split(r"[^a-z0-9]+", full_name.lower()) if len(part) >= 4)
    if any(word in lowered for word in context_words):
        problems.append("Password must not contain your name or email address.")
    return problems


def validate_password(password: str, *, email: str | None = None, full_name: str | None = None) -> None:
    problems = password_problems(password, email=email, full_name=full_name)
    if problems:
        raise ValidationFailed(
            "Password does not meet the password policy.",
            code="weak_password",
            details={"field": "password", "problems": problems},
        )


# ------------------------------------------------------------- access tokens

_ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "typ", "sid", "jti", "role"]
_JWT_LEEWAY_SECONDS = 5


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    sub: str
    typ: SubjectType
    org: str | None
    role: Role
    sid: str
    iid: str | None
    exp: int
    iat: int
    jti: str

    @property
    def subject_id(self) -> uuid.UUID:
        return uuid.UUID(self.sub)

    @property
    def auth_session_id(self) -> uuid.UUID:
        return uuid.UUID(self.sid)

    @property
    def organization_id(self) -> uuid.UUID | None:
        return uuid.UUID(self.org) if self.org else None

    @property
    def interview_id(self) -> uuid.UUID | None:
        return uuid.UUID(self.iid) if self.iid else None


def _jwt_algorithm() -> str:
    algorithm = get_settings().jwt_algorithm.upper()
    if algorithm not in _ALLOWED_JWT_ALGORITHMS:
        raise RuntimeError(f"Unsupported JWT_ALGORITHM {algorithm!r}; use one of {sorted(_ALLOWED_JWT_ALGORITHMS)}")
    return algorithm


def _jwt_keys() -> list[str]:
    settings = get_settings()
    keys = [settings.jwt_secret.get_secret_value()]
    if settings.jwt_previous_secret is not None and settings.jwt_previous_secret.get_secret_value():
        keys.append(settings.jwt_previous_secret.get_secret_value())
    return keys


def create_access_token(
    *,
    subject_id: uuid.UUID | str,
    subject_type: SubjectType,
    role: Role,
    organization_id: uuid.UUID | str | None,
    auth_session_id: uuid.UUID | str,
    interview_id: uuid.UUID | str | None = None,
    ttl_seconds: int | None = None,
    now: float | None = None,
) -> tuple[str, datetime]:
    """Return ``(jwt, expires_at)``."""
    settings = get_settings()
    issued = int(now if now is not None else time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.access_token_ttl_seconds)
    expires = issued + ttl
    payload: dict[str, Any] = {
        "sub": str(subject_id),
        "typ": SubjectType(subject_type).value,
        "org": str(organization_id) if organization_id else None,
        "role": Role(role).value,
        "sid": str(auth_session_id),
        "iid": str(interview_id) if interview_id else None,
        "iat": issued,
        "exp": expires,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, _jwt_keys()[0], algorithm=_jwt_algorithm())
    return token, datetime.fromtimestamp(expires, tz=UTC)


def decode_access_token(token: str) -> AccessTokenClaims:
    """Verify signature and expiry; raise :class:`Unauthorized` otherwise."""
    if not token or len(token) > 4096:
        raise Unauthorized("Invalid access token.", code="invalid_token")
    algorithm = _jwt_algorithm()
    payload: dict[str, Any] | None = None
    for key in _jwt_keys():
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                options={"require": _REQUIRED_CLAIMS},
                leeway=_JWT_LEEWAY_SECONDS,
            )
            break
        except jwt.InvalidSignatureError:
            continue
        except jwt.ExpiredSignatureError:
            raise Unauthorized("Access token expired.", code="token_expired") from None
        except jwt.InvalidTokenError:
            raise Unauthorized("Invalid access token.", code="invalid_token") from None
    if payload is None:
        raise Unauthorized("Invalid access token.", code="invalid_token")
    try:
        claims = AccessTokenClaims(
            sub=str(uuid.UUID(str(payload["sub"]))),
            typ=SubjectType(payload["typ"]),
            org=str(uuid.UUID(str(payload["org"]))) if payload.get("org") else None,
            role=Role(payload["role"]),
            sid=str(uuid.UUID(str(payload["sid"]))),
            iid=str(uuid.UUID(str(payload["iid"]))) if payload.get("iid") else None,
            exp=int(payload["exp"]),
            iat=int(payload["iat"]),
            jti=str(payload["jti"]),
        )
    except (KeyError, TypeError, ValueError):
        raise Unauthorized("Invalid access token.", code="invalid_token") from None
    if (claims.typ is SubjectType.CANDIDATE) != (claims.role is Role.CANDIDATE):
        raise Unauthorized("Invalid access token.", code="invalid_token")
    return claims


# -------------------------------------------------- opaque / invite tokens

def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest used to store refresh and invite tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_invite_token() -> tuple[str, str]:
    """Return ``(token, sha256_hex)``; only the hash may be persisted."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


# ---------------------------------------------------------------------- CSRF

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _csrf_mac(secret: str, auth_session_id: str) -> str:
    key = hmac.new(secret.encode("utf-8"), b"interview/csrf/v1", hashlib.sha256).digest()
    return _b64url(hmac.new(key, f"csrf:{auth_session_id}".encode(), hashlib.sha256).digest())


def create_csrf_token(auth_session_id: uuid.UUID | str) -> str:
    return _csrf_mac(_jwt_keys()[0], str(auth_session_id))


def verify_csrf_token(auth_session_id: uuid.UUID | str, token: str | None) -> bool:
    if not token or len(token) > 128:
        return False
    sid = str(auth_session_id)
    valid = False
    for secret in _jwt_keys():
        # Evaluate every candidate so timing does not reveal which key matched.
        valid |= hmac.compare_digest(_csrf_mac(secret, sid), token)
    return valid


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --------------------------------------------------------------- access codes

ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
ACCESS_CODE_LENGTH = 8


def generate_access_code() -> str:
    return "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH))


def normalize_access_code(code: str) -> str:
    return re.sub(r"[\s-]+", "", code or "").upper()


def hash_access_code(code: str) -> str:
    return hash_password(normalize_access_code(code))


def verify_access_code(code: str, code_hash: str) -> bool:
    normalized = normalize_access_code(code)
    if not normalized or len(normalized) > 64:
        return False
    return verify_password(normalized, code_hash)


async def hash_access_code_async(code: str) -> str:
    return await hash_password_async(normalize_access_code(code))


async def verify_access_code_async(code: str, code_hash: str) -> bool:
    normalized = normalize_access_code(code)
    if not normalized or len(normalized) > 64:
        return False
    return await verify_password_async(normalized, code_hash)


# --------------------------------------------------------- WebSocket tickets

WS_TICKET_PREFIX = "ws:ticket:"
_TICKET_FORMAT = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


@dataclass(frozen=True, slots=True)
class WsTicket:
    session_id: str
    candidate_id: str
    interview_id: str
    issued_at: float


async def issue_ws_ticket(
    session_id: uuid.UUID | str,
    candidate_id: uuid.UUID | str,
    interview_id: uuid.UUID | str,
    *,
    redis: Redis | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Create a single-use ticket bound to the session and the candidate."""
    if redis is None:
        from app.core.redis import get_redis

        redis = get_redis()
    ticket = secrets.token_urlsafe(32)
    value = json.dumps(
        {
            "session_id": str(session_id),
            "candidate_id": str(candidate_id),
            "interview_id": str(interview_id),
            "issued_at": time.time(),
        }
    )
    ttl = int(ttl_seconds if ttl_seconds is not None else get_settings().ws_ticket_ttl_seconds)
    await redis.set(f"{WS_TICKET_PREFIX}{ticket}", value, ex=max(1, ttl))
    return ticket


async def consume_ws_ticket(ticket: str | None, *, redis: Redis | None = None) -> WsTicket | None:
    """Atomically fetch-and-delete a ticket; ``None`` if unknown, used or expired."""
    if not ticket or not _TICKET_FORMAT.match(ticket):
        return None
    if redis is None:
        from app.core.redis import get_redis

        redis = get_redis()
    raw = await redis.getdel(f"{WS_TICKET_PREFIX}{ticket}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return WsTicket(
            session_id=str(data["session_id"]),
            candidate_id=str(data["candidate_id"]),
            interview_id=str(data["interview_id"]),
            issued_at=float(data.get("issued_at", 0.0)),
        )
    except (ValueError, KeyError, TypeError):
        return None
