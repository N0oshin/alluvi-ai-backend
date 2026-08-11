"""Authentication.

Covers Figma screens 13-16 (Sign In / Email Sign In / Create Account / Forgot
Password), 20 (Verify Code) and 35 (Delete Account).

Two distinct recovery mechanisms, per the design:
  * Sign-up verification uses a **6-digit code** emailed to the user.
  * Password reset uses an emailed **link** carrying an opaque token.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, status
from sqlalchemy import select, update

from app.core.config import settings
from app.core.deps import CurrentUser, Db
from app.core.errors import AppError
from app.core.timeutil import ensure_utc
from app.core.security import (
    create_access_token,
    generate_otp,
    generate_refresh_token,
    generate_reset_token,
    hash_otp,
    hash_password,
    hash_refresh_token,
    password_is_valid,
    refresh_expiry,
    verify_password,
)
from app.db.models import (
    AuthProvider,
    NotificationSettings,
    OtpCode,
    OtpPurpose,
    PasswordResetToken,
    RefreshToken,
    User,
)
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    ResendCodeRequest,
    ResetPasswordRequest,
    SignUpRequest,
    SignUpResponse,
    SocialAuthRequest,
    TokenResponse,
    VerifyCodeRequest,
)
from app.schemas.common import MessageResponse
from app.services.email import (
    EmailDeliveryError,
    EmailMessage,
    get_email_sender,
    password_reset_email,
    verification_email,
)
from app.services.storage.local import get_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/Auth", tags=["auth"])


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _issue_session(
    db: Db, user: User, *, family_id: uuid.UUID | None = None
) -> TokenResponse:
    """Mint an access/refresh pair. `family_id` continues an existing chain."""
    access, expires_in = create_access_token(user.id)
    raw_refresh = generate_refresh_token()

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            family_id=family_id or uuid.uuid4(),
            expires_at=refresh_expiry(),
        )
    )
    await db.flush()

    return TokenResponse(
        token=access,
        refresh_token=raw_refresh,
        expires_in=expires_in,
        first_name=(user.name or "").split(" ")[0],
        last_mobile_digit="",
        email_verified=user.email_verified,
    )


async def _revoke_family(db: Db, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def _revoke_all_for_user(db: Db, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def _send_email(message: EmailMessage) -> None:
    """Deliver, or fail the request.

    Failing loudly is deliberate. The alternative — log the error and return
    success — leaves the user staring at a code-entry screen for a code that
    will never arrive, with no way to tell that anything went wrong. Raising
    here also makes the DB middleware roll back, so we never keep an OTP row
    whose email was never delivered.
    """
    try:
        await get_email_sender().send(message)
    except EmailDeliveryError as exc:
        logger.error("Email delivery failed for %s: %s", message.to, exc)
        raise AppError(
            "auth.email_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="EMAIL_FAILED",
        ) from exc


async def _issue_otp(db: Db, user: User) -> str:
    """Invalidate outstanding codes and mint a fresh one."""
    await db.execute(
        update(OtpCode)
        .where(OtpCode.user_id == user.id, OtpCode.consumed_at.is_(None))
        .values(consumed_at=datetime.now(UTC))
    )
    code = generate_otp()
    db.add(
        OtpCode(
            user_id=user.id,
            purpose=OtpPurpose.verify_email,
            code_hash=hash_otp(code),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=settings.OTP_TTL_MINUTES),
        )
    )
    await db.flush()
    await _send_email(
        verification_email(
            user.email, code=code, ttl_minutes=settings.OTP_TTL_MINUTES
        )
    )
    return code


def _ensure_password_policy(password: str) -> None:
    """The rule shown on Create Account: 8+ chars, a number and a symbol."""
    if not password_is_valid(password):
        raise AppError(
            "auth.weak_password",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="WEAK_PASSWORD",
        )


async def _bootstrap_user_rows(db: Db, user: User) -> None:
    db.add(NotificationSettings(user_id=user.id))
    await db.flush()


# --------------------------------------------------------------------------
# Email + password
# --------------------------------------------------------------------------


@router.post("/SignUp", response_model=SignUpResponse, status_code=201)
async def sign_up(payload: SignUpRequest, db: Db) -> SignUpResponse:
    _ensure_password_policy(payload.password)

    email = payload.email.lower()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise AppError(
            "auth.email_taken",
            status_code=status.HTTP_409_CONFLICT,
            code="EMAIL_TAKEN",
        )

    user = User(
        email=email,
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        provider=AuthProvider.email,
        username=email.split("@")[0][:60],
        invite_code=uuid.uuid4().hex[:8].upper(),
    )
    db.add(user)
    await db.flush()
    await _bootstrap_user_rows(db, user)
    await _issue_otp(db, user)

    return SignUpResponse(email=user.email, verification_required=True)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: Db) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))

    # Same error for "no such user" and "wrong password" — never confirm
    # whether an address is registered.
    if (
        user is None
        or user.deleted_at is not None
        or not user.password_hash
        or not verify_password(payload.password, user.password_hash)
    ):
        raise AppError(
            "auth.invalid_credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_CREDENTIALS",
        )

    if not user.email_verified:
        await _issue_otp(db, user)
        # Commit before raising. This endpoint answers 409, and the DB
        # middleware rolls back on any status >= 400 — which would discard the
        # OTP row we just created while the email had already gone out, so the
        # user would receive a code the database has no record of.
        await db.commit()
        # 409, not 403: a 403 would make the client wipe the session.
        raise AppError(
            "auth.not_verified",
            status_code=status.HTTP_409_CONFLICT,
            code="EMAIL_NOT_VERIFIED",
        )

    return await _issue_session(db, user)


@router.post("/verifyCode", response_model=TokenResponse)
async def verify_code(payload: VerifyCodeRequest, db: Db) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        raise AppError(
            "auth.otp_invalid",
            status_code=status.HTTP_400_BAD_REQUEST,
            code="OTP_INVALID",
        )

    otp = await db.scalar(
        select(OtpCode)
        .where(OtpCode.user_id == user.id, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if otp is None:
        raise AppError("auth.otp_invalid", code="OTP_INVALID")

    if ensure_utc(otp.expires_at) < datetime.now(UTC):
        raise AppError("auth.otp_expired", code="OTP_EXPIRED")

    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        otp.consumed_at = datetime.now(UTC)
        raise AppError("auth.otp_expired", code="OTP_EXPIRED")

    if otp.code_hash != hash_otp(payload.code):
        otp.attempts += 1
        raise AppError("auth.otp_invalid", code="OTP_INVALID")

    otp.consumed_at = datetime.now(UTC)
    user.email_verified = True
    await db.flush()

    return await _issue_session(db, user)


@router.post("/resendCode", response_model=MessageResponse)
async def resend_code(payload: ResendCodeRequest, db: Db) -> MessageResponse:
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    # Always the same reply — never reveal whether the address exists.
    generic = MessageResponse(message="If that email is registered, a code is on its way.")
    if user is None:
        return generic

    recent = await db.scalar(
        select(OtpCode)
        .where(OtpCode.user_id == user.id)
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if recent is not None:
        age = (datetime.now(UTC) - ensure_utc(recent.created_at)).total_seconds()
        if age < settings.OTP_RESEND_COOLDOWN_SECONDS:
            raise AppError(
                "auth.otp_cooldown",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                code="OTP_COOLDOWN",
            )

    await _issue_otp(db, user)
    return generic


@router.post("/forgotPassword", response_model=MessageResponse)
async def forgot_password(payload: ForgotPasswordRequest, db: Db) -> MessageResponse:
    """Emails a reset **link**, not a code (Figma screen 16)."""
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    generic = MessageResponse(
        message="If that email is registered, a reset link is on its way."
    )
    if user is None or user.deleted_at is not None:
        return generic

    raw = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw),
            expires_at=datetime.now(UTC)
            + timedelta(hours=settings.PASSWORD_RESET_TTL_HOURS),
        )
    )
    await db.flush()
    await _send_email(
        password_reset_email(
            user.email,
            url=f"{settings.PASSWORD_RESET_URL}?token={raw}",
            ttl_hours=settings.PASSWORD_RESET_TTL_HOURS,
        )
    )
    return generic


@router.post("/resetPassword", response_model=MessageResponse)
async def reset_password(payload: ResetPasswordRequest, db: Db) -> MessageResponse:
    _ensure_password_policy(payload.password)

    record = await db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_refresh_token(payload.token),
            PasswordResetToken.consumed_at.is_(None),
        )
    )
    if record is None or ensure_utc(record.expires_at) < datetime.now(UTC):
        raise AppError("auth.reset_invalid", code="RESET_INVALID")

    user = await db.scalar(select(User).where(User.id == record.user_id))
    if user is None:
        raise AppError("auth.reset_invalid", code="RESET_INVALID")

    user.password_hash = hash_password(payload.password)
    user.email_verified = True
    record.consumed_at = datetime.now(UTC)
    # A password reset invalidates every existing session.
    await _revoke_all_for_user(db, user.id)
    await db.flush()

    return MessageResponse(message="Your password has been reset.")


# --------------------------------------------------------------------------
# Social
# --------------------------------------------------------------------------


async def _social_login(
    db: Db, *, provider: AuthProvider, subject: str, email: str, name: str | None
) -> TokenResponse:
    user = await db.scalar(
        select(User).where(
            User.provider == provider, User.provider_subject == subject
        )
    )
    is_new = False

    if user is None:
        # Link to an existing email account rather than creating a duplicate.
        user = await db.scalar(select(User).where(User.email == email.lower()))

    if user is None:
        is_new = True
        user = User(
            email=email.lower(),
            name=(name or "").strip(),
            provider=provider,
            provider_subject=subject,
            email_verified=True,  # the provider already verified it
            username=email.split("@")[0][:60],
            invite_code=uuid.uuid4().hex[:8].upper(),
        )
        db.add(user)
        await db.flush()
        await _bootstrap_user_rows(db, user)
    else:
        user.provider = provider
        user.provider_subject = subject
        user.email_verified = True
        if name and not user.name:
            # Apple returns the name exactly once, on first sign-in.
            user.name = name.strip()
        await db.flush()

    response = await _issue_session(db, user)
    response.is_new_user = is_new
    return response


@router.post("/apple", response_model=TokenResponse)
async def apple_sign_in(payload: SocialAuthRequest, db: Db) -> TokenResponse:
    claims = await _verify_social_token(payload.identity_token, AuthProvider.apple)
    return await _social_login(
        db,
        provider=AuthProvider.apple,
        subject=claims["sub"],
        email=claims["email"],
        name=payload.name,
    )


@router.post("/google", response_model=TokenResponse)
async def google_sign_in(payload: SocialAuthRequest, db: Db) -> TokenResponse:
    claims = await _verify_social_token(payload.identity_token, AuthProvider.google)
    return await _social_login(
        db,
        provider=AuthProvider.google,
        subject=claims["sub"],
        email=claims["email"],
        name=payload.name,
    )


async def _verify_social_token(token: str, provider: AuthProvider) -> dict[str, str]:
    """Validate an Apple/Google identity token.

    NOT IMPLEMENTED — this must verify the JWT signature against the
    provider's published JWKS and check `iss`, `aud`, and `exp` before any
    session is issued. Until then social sign-in is refused rather than
    trusted, because accepting an unverified token would let anyone sign in
    as anyone.
    """
    raise AppError(
        "error.generic",
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="SOCIAL_AUTH_NOT_CONFIGURED",
    )


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: Db) -> TokenResponse:
    """Rotate the refresh token.

    Replaying an already-consumed token means it leaked, so the entire family
    is revoked and the user must sign in again.
    """
    record = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    if record is None:
        raise AppError(
            "auth.invalid_token",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_REFRESH",
        )

    if record.consumed_at is not None:
        logger.warning(
            "Refresh token reuse detected for user %s; revoking family %s",
            record.user_id,
            record.family_id,
        )
        await _revoke_family(db, record.family_id)
        # Commit before raising. This request ends as a 401, and the session
        # middleware rolls back on any 4xx — without an explicit commit the
        # revocation would be undone and the stolen token's successor would
        # keep working. Detection with no enforcement is worse than neither.
        await db.commit()
        raise AppError(
            "auth.invalid_token",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="REFRESH_REUSED",
        )

    now = datetime.now(UTC)
    if record.revoked_at is not None or ensure_utc(record.expires_at) < now:
        raise AppError(
            "auth.invalid_token",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="REFRESH_EXPIRED",
        )

    user = await db.scalar(select(User).where(User.id == record.user_id))
    if user is None or user.deleted_at is not None:
        raise AppError(
            "auth.invalid_token",
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_REFRESH",
        )

    record.consumed_at = now
    return await _issue_session(db, user, family_id=record.family_id)


@router.post("/logout", response_model=MessageResponse)
async def logout(payload: LogoutRequest, db: Db) -> MessageResponse:
    record = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    # Idempotent: an unknown or already-dead token is still a successful logout.
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await db.flush()
    return MessageResponse(message="Signed out.")


@router.delete("/account", response_model=MessageResponse)
async def delete_account(user: CurrentUser, db: Db) -> MessageResponse:
    """Figma screen 35.

    The copy promises permanence — "This permanently erases your data and
    history. This action cannot be undone." So this revokes every session,
    purges the user's photo prefix from storage, and hard-deletes the row;
    every child table cascades.
    """
    await _revoke_all_for_user(db, user.id)
    await get_storage().delete_prefix(f"meals/{user.id}")
    await db.delete(user)
    await db.flush()
    return MessageResponse(message="Your account has been deleted.")
