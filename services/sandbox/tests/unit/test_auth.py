from __future__ import annotations

import hashlib
import hmac

import pytest

from sandbox_service.auth import Authenticator, AuthError, ReplayGuard, sign

SECRET = b"unit-test-secret-0123456789abcdef"


def test_sign_matches_contract_formula():
    body = b'{"a":1}'
    assert sign(SECRET, "1700000000", body) == hmac.new(SECRET, b"1700000000." + body, hashlib.sha256).hexdigest()


def test_valid_request_accepted_once():
    auth = Authenticator(SECRET, 60)
    body = b"{}"
    sig = sign(SECRET, "1000", body)
    auth.verify("1000", sig, body, now=1010)
    with pytest.raises(AuthError) as exc:
        auth.verify("1000", sig, body, now=1011)
    assert exc.value.reason == "replay"


def test_uppercase_signature_accepted():
    auth = Authenticator(SECRET, 60)
    auth.verify("1000", sign(SECRET, "1000", b"x").upper(), b"x", now=1000)


@pytest.mark.parametrize(
    ("timestamp", "signature", "body", "now", "reason"),
    [
        (None, "ab", b"", 1000, "missing"),
        ("1000", None, b"", 1000, "missing"),
        ("abc", "0" * 64, b"", 1000, "bad_timestamp"),
        ("-5", "0" * 64, b"", 1000, "bad_timestamp"),
        ("1000", "0" * 64, b"", 1061, "expired"),
        ("1100", "0" * 64, b"", 1000, "expired"),
        ("1000", "0" * 64, b"", 1000, "bad_signature"),
        ("1000", "zz", b"", 1000, "bad_signature"),
    ],
)
def test_rejections(timestamp, signature, body, now, reason):
    auth = Authenticator(SECRET, 60)
    with pytest.raises(AuthError) as exc:
        auth.verify(timestamp, signature, body, now=now)
    assert exc.value.reason == reason


def test_tampered_body_rejected():
    auth = Authenticator(SECRET, 60)
    sig = sign(SECRET, "1000", b'{"code":"print(1)"}')
    with pytest.raises(AuthError):
        auth.verify("1000", sig, b'{"code":"print(2)"}', now=1000)


def test_short_secret_refused():
    with pytest.raises(ValueError):
        Authenticator(b"short", 60)


def test_replay_guard_forgets_after_window_and_is_bounded():
    guard = ReplayGuard(window_seconds=10, max_entries=3)
    assert guard.check_and_remember("a", 0)
    assert not guard.check_and_remember("a", 5)
    assert guard.check_and_remember("a", 20)
    for i in range(10):
        assert guard.check_and_remember(f"s{i}", 21)
    assert len(guard._seen) <= 3
