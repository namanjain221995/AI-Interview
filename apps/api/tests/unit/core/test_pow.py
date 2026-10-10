"""Proof-of-work (S15): valid, expired, replayed, tampered, wrong number, wrong scope; IPv6 /64 keys."""

from __future__ import annotations

import base64
import json

import pytest

from app.core import pow as pow_mod
from app.core.ratelimit import ip_key

NOW = 1_800_000_000.0


def _payload(solution: str, **changes: object) -> str:
    body = json.loads(base64.b64decode(solution))
    body.update(changes)
    return base64.b64encode(json.dumps(body).encode()).decode()


def test_a_solved_challenge_verifies_for_its_scope_until_it_expires() -> None:
    challenge = pow_mod.create_challenge("link:a", max_number=200, ttl_s=600, now=NOW)
    assert challenge.algorithm == "SHA-256" and challenge.maxnumber == 200
    solution = pow_mod.solve(challenge)
    assert pow_mod.verify(solution, "link:a", now=NOW + 1) == (challenge.challenge, int(NOW) + 600)
    with pytest.raises(pow_mod.PowError) as expired:
        pow_mod.verify(solution, "link:a", now=NOW + 601)
    assert expired.value.code == "pow_invalid" and expired.value.reason == "expired"
    with pytest.raises(pow_mod.PowError) as scope:
        pow_mod.verify(solution, "link:b", now=NOW + 1)
    assert scope.value.reason == "scope"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"number": -1}, "number"),
        ({"signature": "0" * 64}, "signature"),
        ({"algorithm": "SHA-1"}, "algorithm"),
        ({"salt": "tampered?scope=link:a&expires=9999999999"}, "number"),
    ],
)
def test_tampered_solutions_are_refused(change: dict[str, object], reason: str) -> None:
    challenge = pow_mod.create_challenge("link:a", max_number=200, ttl_s=600, now=NOW)
    good = pow_mod.solve(challenge)
    body = json.loads(base64.b64decode(good))
    if "number" in change and change["number"] == -1:
        change = {"number": body["number"] + 1}  # a wrong number
    with pytest.raises(pow_mod.PowError) as error:
        pow_mod.verify(_payload(good, **change), "link:a", now=NOW + 1)
    assert error.value.code == "pow_invalid" and error.value.reason == reason


def test_digits_moved_from_the_number_into_the_salt_never_stretch_the_expiry(monkeypatch) -> None:
    # Review of S15: sha256(salt + number) is unchanged when leading digits move into the salt.
    monkeypatch.setattr(pow_mod.secrets, "randbelow", lambda _n: 92503)
    challenge = pow_mod.create_challenge("link:a", max_number=100_000, ttl_s=600, now=NOW)
    body = json.loads(base64.b64decode(pow_mod.solve(challenge)))
    assert body["number"] == 92503
    shifted = _payload(pow_mod.solve(challenge), salt=body["salt"] + "9", number=2503)
    with pytest.raises(pow_mod.PowError) as error:
        pow_mod.verify(shifted, "link:a", now=NOW + 10 * 365 * 86400)
    assert error.value.reason == "salt"
    monkeypatch.undo()
    far = pow_mod.create_challenge("link:a", max_number=200, ttl_s=10 * 86400, now=NOW)
    with pytest.raises(pow_mod.PowError) as stretched:
        pow_mod.verify(pow_mod.solve(far), "link:a", now=NOW + 1)
    assert stretched.value.reason == "expires"  # never issued that far ahead


@pytest.mark.parametrize("payload", [None, "", "not base64!", base64.b64encode(b"{}").decode()])
def test_missing_or_malformed_solutions(payload: str | None) -> None:
    with pytest.raises(pow_mod.PowError) as error:
        pow_mod.verify(payload, "link:a", now=NOW)
    assert error.value.code == ("pow_required" if not payload else "pow_invalid")


class _FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, tuple[str, int]] = {}

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.keys:
            return False
        self.keys[key] = (value, ex)
        return True


async def test_a_solution_is_single_use() -> None:
    redis = _FakeRedis()
    await pow_mod.consume(redis, "abc", int(NOW) + 300, now=NOW)  # type: ignore[arg-type]
    assert redis.keys["pow:used:abc"][1] == 300
    with pytest.raises(pow_mod.PowError) as replay:
        await pow_mod.consume(redis, "abc", int(NOW) + 300, now=NOW)  # type: ignore[arg-type]
    assert replay.value.reason == "replayed"
    await pow_mod.consume(redis, "far", int(NOW) + 10**9, now=NOW)  # type: ignore[arg-type]
    assert redis.keys["pow:used:far"][1] <= 600 + pow_mod.CLOCK_SKEW_S  # used keys always expire


def test_ipv6_addresses_are_keyed_by_their_64() -> None:
    assert ip_key("2001:db8:1:2:aaaa::1") == ip_key("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert ip_key("2001:db8:1:3::1") != ip_key("2001:db8:1:2::1")
    assert ip_key("203.0.113.9") == "203.0.113.9"
    assert ip_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert ip_key("unknown") == "unknown"
