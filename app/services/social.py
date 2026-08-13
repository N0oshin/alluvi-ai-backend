"""Apple / Google identity-token verification.

Both providers sign identity tokens with rotating RSA keys published as a
JWKS document. Verifying one therefore means: fetch the provider's JWKS,
pick the key whose `kid` matches the token header, check the signature and
expiry, and pin the issuer and audience.

The audience is *our* app's identifier (Google OAuth client ID / Apple
bundle ID). Skipping that check would accept a perfectly genuine token that
was minted for someone else's app — the classic token-substitution attack.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import status
from jose import JWTError, jwt

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import AuthProvider


@dataclass(frozen=True)
class _ProviderMeta:
    jwks_url: str
    # Google historically issues both forms of `iss`.
    issuers: tuple[str, ...]


_META: dict[AuthProvider, _ProviderMeta] = {
    AuthProvider.google: _ProviderMeta(
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        issuers=("https://accounts.google.com", "accounts.google.com"),
    ),
    AuthProvider.apple: _ProviderMeta(
        jwks_url="https://appleid.apple.com/auth/keys",
        issuers=("https://appleid.apple.com",),
    ),
}

# JWKS documents change rarely (key rotation), so they are cached in memory
# and refreshed on expiry — or immediately when a token arrives with a `kid`
# the cache doesn't know, which is exactly what rotation looks like.
_JWKS_TTL_SECONDS = 6 * 60 * 60
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _not_configured() -> AppError:
    return AppError(
        "error.generic",
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="SOCIAL_AUTH_NOT_CONFIGURED",
    )


def _invalid() -> AppError:
    return AppError(
        "auth.social_invalid",
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="SOCIAL_AUTH_INVALID",
    )


def _audiences(provider: AuthProvider) -> list[str]:
    raw = (
        settings.GOOGLE_CLIENT_IDS
        if provider is AuthProvider.google
        else settings.APPLE_BUNDLE_IDS
    )
    audiences = [a.strip() for a in (raw or "").split(",") if a.strip()]
    if not audiences:
        raise _not_configured()
    return audiences


async def _fetch_jwks(url: str, *, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    cached = _jwks_cache.get(url)
    if cached and not force and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            jwks = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if cached:
            # A stale key set still verifies signatures correctly; only a
            # rotation during the outage would reject, and that fails closed.
            return cached[1]
        raise AppError(
            "auth.social_unavailable",
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="SOCIAL_AUTH_UNAVAILABLE",
        ) from exc
    _jwks_cache[url] = (now, jwks)
    return jwks


async def _signing_key(meta: _ProviderMeta, kid: str) -> dict[str, Any]:
    jwks = await _fetch_jwks(meta.jwks_url)
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        # Unknown kid usually means the provider rotated keys since our
        # cache was filled — refetch once before giving up.
        jwks = await _fetch_jwks(meta.jwks_url, force=True)
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise _invalid()
    return key


async def verify_identity_token(
    token: str, provider: AuthProvider
) -> dict[str, str]:
    """Returns `{"sub": ..., "email": ...}` for a genuine token, else raises.

    Raises 501 when the provider's audience is not configured, 401 for any
    defect in the token itself, 502 when the provider's JWKS is unreachable.
    """
    audiences = _audiences(provider)
    meta = _META[provider]

    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise _invalid() from None
    kid = header.get("kid")
    if not kid:
        raise _invalid()

    key = await _signing_key(meta, kid)

    try:
        # Signature and `exp` are enforced here. `aud`/`iss` are checked
        # manually below because jose accepts only a single expected value
        # and we allow several (per-platform client IDs, Google's two iss
        # forms).
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except JWTError:
        raise _invalid() from None

    if claims.get("iss") not in meta.issuers:
        raise _invalid()

    aud = claims.get("aud")
    aud_values = aud if isinstance(aud, list) else [aud]
    if not any(a in audiences for a in aud_values):
        raise _invalid()

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        # Without an email we can't create or link the account. Apple only
        # omits it when the app requested no email scope — a client bug.
        raise _invalid()
    if provider is AuthProvider.google and claims.get("email_verified") in (
        False,
        "false",
    ):
        raise _invalid()

    return {"sub": str(sub), "email": str(email)}
