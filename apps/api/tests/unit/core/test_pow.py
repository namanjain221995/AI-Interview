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


def test_ipv6_addresses_are_keyed_by_their_64() -> None:
    assert ip_key("2001:db8:1:2:aaaa::1") == ip_key("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert ip_key("2001:db8:1:3::1") != ip_key("2001:db8:1:2::1")
    assert ip_key("203.0.113.9") == "203.0.113.9"
    assert ip_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert ip_key("unknown") == "unknown"
