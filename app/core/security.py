"""Password hashing and the access/refresh token pair.

  * Access token  — JWT, 15 minutes, stateless, sent as `Authorization: Bearer`.
  * Refresh token — opaque random string, 30 days, stored **hashed**.
  * Rotation      — every refresh mints a new token and consumes the old one.
                    Replaying a consumed token means theft: the whole family
                    is revoked and the user must sign in again.

Nothing mutable goes in the JWT claims. `isPremium` in particular must not be
a claim, or the client would show a stale entitlement for up to 15 minutes.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from jose import JWTError, jwt

from app.core.config import settings

_hasher = PasswordHasher()

# "At least 8 characters, with a number and a symbol" — the rule shown on the
# Create Account screen. Enforced here so the client and server agree.
_PASSWORD_RE = re.compile(
    r"^(?=.*\d)(?=.*[^A-Za-z0-9\s]).{8,}$"
)


def password_is_valid(password: str) -> bool:
    return bool(_PASSWORD_RE.match(password))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        _hasher.verify(hashed, password)
        return True
    except (VerifyMismatchError, VerificationError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


# --------------------------------------------------------------------------
# Access token (JWT)
# --------------------------------------------------------------------------


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(16),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload


# --------------------------------------------------------------------------
# Refresh token (opaque, hashed at rest)
# --------------------------------------------------------------------------


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 is right here: the token is already 48 bytes of entropy, so
    there is nothing to brute-force and we need a deterministic lookup key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)


# --------------------------------------------------------------------------
# One-time codes
# --------------------------------------------------------------------------


def generate_otp() -> str:
    """Six digits, matching the Verify Code screen's six boxes."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_reset_token() -> str:
    """Password reset is a link, not a code — this rides in the deep link."""
    return secrets.token_urlsafe(32)
