from __future__ import annotations

import time
import uuid

import jwt
import pytest
from pydantic import SecretStr

from app.core import security
from app.core.config import get_settings
from app.core.errors import Unauthorized, ValidationFailed
from app.domain.enums import Role, SubjectType


@pytest.fixture
def restore_settings():
    settings = get_settings()
    snapshot = dict(settings.__dict__)
    yield settings
    settings.__dict__.clear()
    settings.__dict__.update(snapshot)


# ------------------------------------------------------------------ passwords


def test_hash_and_verify_roundtrip():
    hashed = security.hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert security.verify_password("correct horse battery staple", hashed)
    assert not security.verify_password("wrong horse battery staple", hashed)


def test_verify_handles_garbage_hash():
    assert not security.verify_password("anything", "not-a-hash")
    assert not security.verify_password("anything", "")


def test_production_profile_parameters():
    hasher = security._hasher_for(False)
    assert (hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (3, 65536, 4)
    assert hasher.type.name == "ID"


def test_needs_rehash_detects_weaker_profile(restore_settings):
    weak = security._hasher_for(True).hash("some long passphrase value")
    restore_settings.app_env = "production"
    assert security.password_needs_rehash(weak)
    restore_settings.app_env = "test"
    assert not security.password_needs_rehash(weak)
    assert security.password_needs_rehash("garbage")


async def test_async_hash_and_verify():
    hashed = await security.hash_password_async("async passphrase works fine")
    assert await security.verify_password_async("async passphrase works fine", hashed)
    assert not await security.verify_password_async("nope nope nope nope", hashed)
    await security.burn_password_check_async("whatever")


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "password123!",
        "Password2026!",
        "aaaaaaaaaaaaaaaa",
        "abcabcabcabcabc",
        "123456789012",
        "qwertyuiopasdf",
        "abcdefghijklmn",
        "ababababababab",
    ],
)
def test_password_policy_rejects_trivial(password):
    assert security.password_problems(password, min_length=12)


def test_password_policy_accepts_strong_and_reports_context():
    assert security.password_problems("violet-Harbor-73-quiet", min_length=12) == []
    problems = security.password_problems("janedoe-Harbor-73", email="janedoe@example.com", min_length=12)
    assert any("name or email" in p for p in problems)
    problems = security.password_problems("Margaret-river-2026", full_name="Margaret Hill", min_length=12)
    assert any("name or email" in p for p in problems)
    assert security.password_problems("x" * 300, min_length=12)


def test_validate_password_raises_with_details():
    with pytest.raises(ValidationFailed) as info:
        security.validate_password("short")
    assert info.value.code == "weak_password"
    assert info.value.details["problems"]


# ---------------------------------------------------------------------- JWT


def _token(**overrides):
    params = dict(
        subject_id=uuid.uuid4(),
        subject_type=SubjectType.USER,
        role=Role.ADMIN,
        organization_id=uuid.uuid4(),
        auth_session_id=uuid.uuid4(),
    )
    params.update(overrides)
    return security.create_access_token(**params), params


def test_access_token_claims_roundtrip():
    (token, expires), params = _token(interview_id=None)
    claims = security.decode_access_token(token)
    assert claims.subject_id == params["subject_id"]
    assert claims.typ is SubjectType.USER
    assert claims.role is Role.ADMIN
    assert claims.organization_id == params["organization_id"]
    assert claims.auth_session_id == params["auth_session_id"]
    assert claims.iid is None
    assert claims.exp - claims.iat == get_settings().access_token_ttl_seconds
    assert abs(expires.timestamp() - claims.exp) < 1
    raw = jwt.decode(token, options={"verify_signature": False})
    assert set(raw) == {"sub", "typ", "org", "role", "sid", "iid", "exp", "iat", "jti"}


def test_candidate_token_carries_interview():
    interview = uuid.uuid4()
    (token, _), _ = _token(subject_type=SubjectType.CANDIDATE, role=Role.CANDIDATE, interview_id=interview)
    claims = security.decode_access_token(token)
    assert claims.interview_id == interview and claims.typ is SubjectType.CANDIDATE


def test_expired_token_rejected():
    (token, _), _ = _token(ttl_seconds=1, now=time.time() - 120)
    with pytest.raises(Unauthorized) as info:
        security.decode_access_token(token)
    assert info.value.code == "token_expired"


def test_tampered_and_foreign_tokens_rejected():
    (token, _), _ = _token()
    head, payload, sig = token.split(".")
    with pytest.raises(Unauthorized):
        security.decode_access_token(f"{head}.{payload}.{sig[:-2]}AA")
    forged = jwt.encode({"sub": str(uuid.uuid4())}, "another-secret-value-that-is-long-enough", algorithm="HS256")
    with pytest.raises(Unauthorized):
        security.decode_access_token(forged)
    unsigned = jwt.encode({"sub": "x"}, None, algorithm="none")
    with pytest.raises(Unauthorized):
        security.decode_access_token(unsigned)
    with pytest.raises(Unauthorized):
        security.decode_access_token("")


def test_role_and_subject_type_must_agree():
    payload = {
        "sub": str(uuid.uuid4()), "typ": "candidate", "org": None, "role": "ADMIN", "sid": str(uuid.uuid4()),
        "iid": None, "iat": int(time.time()), "exp": int(time.time()) + 60, "jti": "x",
    }
    token = jwt.encode(payload, get_settings().jwt_secret.get_secret_value(), algorithm="HS256")
    with pytest.raises(Unauthorized):
        security.decode_access_token(token)


def test_previous_secret_accepted_during_rotation(restore_settings):
    old_secret = restore_settings.jwt_secret
    (token, _), _ = _token()
    restore_settings.jwt_secret = SecretStr("a-brand-new-signing-secret-for-rotation-0123456789")
    with pytest.raises(Unauthorized):
        security.decode_access_token(token)
    restore_settings.jwt_previous_secret = old_secret
    assert security.decode_access_token(token).role is Role.ADMIN


def test_unsupported_algorithm_refused(restore_settings):
    restore_settings.jwt_algorithm = "none"
    with pytest.raises(RuntimeError):
        _token()


# ------------------------------------------------------- opaque tokens, CSRF


def test_refresh_and_invite_tokens():
    token = security.generate_refresh_token()
    assert len(token) >= 43 and token != security.generate_refresh_token()
    assert security.hash_token(token) == security.hash_token(token)
    assert len(security.hash_token(token)) == 64
    invite, digest = security.generate_invite_token()
    assert digest == security.hash_token(invite) and invite not in digest


def test_csrf_token_is_bound_to_session(restore_settings):
    sid = uuid.uuid4()
    token = security.create_csrf_token(sid)
    assert security.verify_csrf_token(sid, token)
    assert security.verify_csrf_token(str(sid), token)
    assert not security.verify_csrf_token(uuid.uuid4(), token)
    assert not security.verify_csrf_token(sid, token[:-1] + ("A" if token[-1] != "A" else "B"))
    assert not security.verify_csrf_token(sid, None)
    assert not security.verify_csrf_token(sid, "x" * 500)
    old = restore_settings.jwt_secret
    restore_settings.jwt_secret = SecretStr("rotated-secret-rotated-secret-rotated-secret")
    restore_settings.jwt_previous_secret = old
    assert security.verify_csrf_token(sid, token)


def test_constant_time_equals():
    assert security.constant_time_equals("abc", "abc")
    assert not security.constant_time_equals("abc", "abd")
    assert not security.constant_time_equals(None, "abc")


# ------------------------------------------------------------- access codes


def test_access_codes():
    code = security.generate_access_code()
    assert len(code) == 8
    assert set(code) <= set(security.ACCESS_CODE_ALPHABET)
    assert not set("0O1IL") & set(security.ACCESS_CODE_ALPHABET)
    hashed = security.hash_access_code(code)
    assert hashed.startswith("$argon2id$")
    spaced = f"{code[:4].lower()} - {code[4:]}"
    assert security.normalize_access_code(spaced) == code
    assert security.verify_access_code(spaced, hashed)
    assert not security.verify_access_code("ABCDEFGH" if code != "ABCDEFGH" else "HGFEDCBA", hashed)
    assert not security.verify_access_code("", hashed)


async def test_access_codes_async():
    code = security.generate_access_code()
    hashed = await security.hash_access_code_async(code)
    assert await security.verify_access_code_async(code.lower(), hashed)
    assert not await security.verify_access_code_async("ZZZZZZZZ", hashed)
