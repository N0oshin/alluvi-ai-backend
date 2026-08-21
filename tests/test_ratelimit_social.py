"""Rate limiting and social sign-in verification.

The social tests sign real RS256 tokens with a throwaway RSA key and feed the
matching public JWK in through a patched fetch — so signature, expiry,
issuer, and audience checks all run for real; only the network call is faked.
"""

from __future__ import annotations

import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from app.core.config import settings
from app.core.errors import AppError
from app.core import ratelimit
from app.db.models import AuthProvider
from app.services import social

# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_sliding_window_allows_then_blocks():
    limiter = ratelimit.SlidingWindowLimiter(2, 60)
    assert limiter.allow("k")
    assert limiter.allow("k")
    assert not limiter.allow("k")
    # A different key has its own window.
    assert limiter.allow("other")


@pytest.fixture
def limits_on(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    yield
    for limiter in (
        ratelimit.login_by_email,
        ratelimit.login_by_ip,
        ratelimit.signup_by_ip,
        ratelimit.forgot_by_email,
        ratelimit.forgot_by_ip,
    ):
        limiter.reset()


async def test_login_locks_after_ten_attempts_per_email(client, limits_on):
    body = {"email": f"victim-{uuid.uuid4().hex[:8]}@example.com", "password": "Wrong1!"}
    for _ in range(10):
        resp = await client.post("/api/Auth/login", json=body)
        assert resp.status_code == 401
    resp = await client.post("/api/Auth/login", json=body)
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"

    # A different email from the same IP is still allowed (IP cap is 30).
    other = {"email": "someone-else@example.com", "password": "Wrong1!"}
    assert (await client.post("/api/Auth/login", json=other)).status_code == 401


async def test_forgot_password_locks_after_three_per_email(client, limits_on):
    body = {"email": "unknown@example.com"}
    for _ in range(3):
        resp = await client.post("/api/Auth/forgotPassword", json=body)
        assert resp.status_code == 200
    resp = await client.post("/api/Auth/forgotPassword", json=body)
    assert resp.status_code == 429


async def test_signup_locks_after_ten_per_ip(client, limits_on):
    for i in range(10):
        resp = await client.post(
            "/api/Auth/SignUp",
            json={
                "name": "Flood Test",
                "email": f"flood-{i}-{uuid.uuid4().hex[:6]}@example.com",
                "password": "Passw0rd!",
            },
        )
        assert resp.status_code == 201
    resp = await client.post(
        "/api/Auth/SignUp",
        json={
            "name": "Flood Test",
            "email": "flood-last@example.com",
            "password": "Passw0rd!",
        },
    )
    assert resp.status_code == 429


# --------------------------------------------------------------------------
# Social sign-in
# --------------------------------------------------------------------------

_KID = "test-kid"
_CLIENT_ID = "test-client-id.apps.googleusercontent.com"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUBLIC_PEM = _private_key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
_PUBLIC_JWK = {**jwk.construct(_PUBLIC_PEM, "RS256").to_dict(), "kid": _KID}


def _google_token(**overrides) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "sub": "google-sub-123",
        "email": f"social-{uuid.uuid4().hex[:8]}@example.com",
        "email_verified": True,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256", headers={"kid": _KID})


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_IDS", _CLIENT_ID)

    async def fake_fetch(url: str, *, force: bool = False):
        return {"keys": [_PUBLIC_JWK]}

    monkeypatch.setattr(social, "_fetch_jwks", fake_fetch)


async def test_social_unconfigured_answers_501(client):
    resp = await client.post(
        "/api/Auth/google", json={"identityToken": "whatever", "name": "X"}
    )
    assert resp.status_code == 501
    assert resp.json()["code"] == "SOCIAL_AUTH_NOT_CONFIGURED"


async def test_valid_google_token_verifies(google_configured):
    token = _google_token()
    claims = await social.verify_identity_token(token, AuthProvider.google)
    assert claims["sub"] == "google-sub-123"
    assert "@example.com" in claims["email"]


async def test_wrong_audience_rejected(google_configured):
    token = _google_token(aud="someone-elses-app")
    with pytest.raises(AppError) as err:
        await social.verify_identity_token(token, AuthProvider.google)
    assert err.value.code == "SOCIAL_AUTH_INVALID"


async def test_wrong_issuer_rejected(google_configured):
    token = _google_token(iss="https://evil.example.com")
    with pytest.raises(AppError):
        await social.verify_identity_token(token, AuthProvider.google)


async def test_expired_token_rejected(google_configured):
    token = _google_token(exp=int(time.time()) - 10)
    with pytest.raises(AppError):
        await social.verify_identity_token(token, AuthProvider.google)


async def test_unverified_email_rejected(google_configured):
    token = _google_token(email_verified=False)
    with pytest.raises(AppError):
        await social.verify_identity_token(token, AuthProvider.google)


async def test_garbage_token_rejected(google_configured):
    with pytest.raises(AppError):
        await social.verify_identity_token("not-a-jwt", AuthProvider.google)


async def test_google_endpoint_creates_user_and_session(client, google_configured):
    token = _google_token()
    resp = await client.post(
        "/api/Auth/google", json={"identityToken": token, "name": "Social User"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]
    assert body["refreshToken"]
    assert body["isNewUser"] is True

    # Same subject again links to the same account rather than duplicating.
    resp = await client.post(
        "/api/Auth/google", json={"identityToken": token, "name": "Social User"}
    )
    assert resp.status_code == 200
    assert resp.json()["isNewUser"] is False
