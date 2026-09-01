"""Auth request/response bodies."""

from __future__ import annotations

from pydantic import EmailStr, Field

from app.schemas.common import CamelModel


class SignUpRequest(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str


class LoginRequest(CamelModel):
    email: EmailStr
    password: str


class VerifyCodeRequest(CamelModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendCodeRequest(CamelModel):
    email: EmailStr


class ForgotPasswordRequest(CamelModel):
    email: EmailStr


class ResetPasswordRequest(CamelModel):
    """Completes a reset with the 6-digit code from `forgotPassword`."""

    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
    password: str


class SocialAuthRequest(CamelModel):
    """Apple / Google identity token exchange.

    `identityToken` is verified against the provider before a session is
    issued; `name` is only used on first sign-in, because Apple returns it
    exactly once.
    """

    identity_token: str
    name: str | None = None


class RefreshRequest(CamelModel):
    refresh_token: str


class LogoutRequest(CamelModel):
    refresh_token: str


class TokenResponse(CamelModel):
    """Shaped for `UserCache.logUserIn()`, which needs token + firstName +
    lastMobileDigit, plus the refresh token the client must start storing.

    `lastMobileDigit` is template debris — the design has no phone number
    anywhere, so it is always empty. Kept only so the existing client call
    site compiles unchanged.
    """

    token: str
    refresh_token: str
    expires_in: int
    first_name: str
    last_mobile_digit: str = ""
    is_new_user: bool = False
    email_verified: bool = True


class SignUpResponse(CamelModel):
    """Sign-up does not return a session: the email must be verified first,
    which is what the 6-digit code on screen 20 is for."""

    email: str
    verification_required: bool = True
