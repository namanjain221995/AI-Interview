from __future__ import annotations

import base64
import time
import uuid

import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.services.storage.signing import (
    SIGNED_URL_PREFIX,
    InvalidMediaToken,
    decode_media_token,
    make_media_token,
    signed_url,
    signed_url_with_expiry,
    verify_media_token,
)


def test_roundtrip_and_url_safety():
    media_id = uuid.uuid4()
    token = make_media_token(media_id, 60)
    assert verify_media_token(token) == media_id
    assert verify_media_token(token.encode().decode()) == media_id
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    _, expires = decode_media_token(token)
    assert 55 <= expires.timestamp() - time.time() <= 61


def test_default_ttl_from_settings():
    token = make_media_token(str(uuid.uuid4()))
    _, expires = decode_media_token(token)
    assert abs(expires.timestamp() - time.time() - get_settings().media_url_ttl_seconds) < 2


def test_expired_token():
    token = make_media_token(uuid.uuid4(), 10, now=time.time() - 100)
    with pytest.raises(InvalidMediaToken):
        verify_media_token(token)


def test_tampering_is_detected():
    media_id = uuid.uuid4()
    raw = bytearray(base64.urlsafe_b64decode(make_media_token(media_id, 60) + "=="))
    raw[5] ^= 0x01  # flip a bit of the media id
    forged = base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
    with pytest.raises(InvalidMediaToken):
        verify_media_token(forged)
    raw2 = bytearray(base64.urlsafe_b64decode(make_media_token(media_id, 60) + "=="))
    raw2[20] = (raw2[20] + 1) % 256  # extend the expiry
    with pytest.raises(InvalidMediaToken):
        verify_media_token(base64.urlsafe_b64encode(bytes(raw2)).rstrip(b"=").decode())


@pytest.mark.parametrize("token", ["", "abc", "!!!!", "A" * 76, "A" * 500])
def test_malformed_tokens(token):
    with pytest.raises(InvalidMediaToken):
        verify_media_token(token)


def test_key_rotation_invalidates_tokens():
    settings = get_settings()
    original = settings.media_url_secret
    token = make_media_token(uuid.uuid4(), 60)
    try:
        settings.media_url_secret = SecretStr("another-media-secret-another-media-secret")
        with pytest.raises(InvalidMediaToken):
            verify_media_token(token)
    finally:
        settings.media_url_secret = original


def test_ttl_bounds():
    with pytest.raises(ValueError):
        make_media_token(uuid.uuid4(), 0)
    with pytest.raises(ValueError):
        make_media_token(uuid.uuid4(), 10 * 24 * 3600)


def test_signed_url_shapes():
    media_id = uuid.uuid4()
    url = signed_url(media_id, 30)
    assert url.startswith(SIGNED_URL_PREFIX)
    assert verify_media_token(url.removeprefix(SIGNED_URL_PREFIX)) == media_id
    url2, expires = signed_url_with_expiry(media_id)
    assert url2.startswith("/api/v1/media/signed/") and expires.tzinfo is not None
