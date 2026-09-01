"""Authentication.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.deps import CurrentUser, Db
from app.core.errors import AppError
from app.core.ratelimit import (
    client_ip,
    enforce,
    forgot_by_email,
    forgot_by_ip,
    login_by_email,
    login_by_ip,
    signup_by_ip,
)
from app.core.timeutil import ensure_utc
from app.core.security import (
    create_access_token,
    generate_otp,
    generate_refresh_token,
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
from app.services.social import verify_identity_token
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


async def _issue_otp(
    db: Db, user: User, *, purpose: OtpPurpose = OtpPurpose.verify_email
) -> str:
    await db.execute(
        update(OtpCode)
        .where(
            OtpCode.user_id == user.id,
            OtpCode.purpose == purpose,
            OtpCode.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(UTC))
    )
    code = generate_otp()
    db.add(
        OtpCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=hash_otp(code),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=settings.OTP_TTL_MINUTES),
        )
    )
    await db.flush()
    template = (
        password_reset_email
        if purpose is OtpPurpose.reset_password
        else verification_email
    )
    await _send_email(
        template(user.email, code=code, ttl_minutes=settings.OTP_TTL_MINUTES)
    )
    return code


async def _check_otp(db: Db, user: User, code: str, purpose: OtpPurpose) -> OtpCode:
    """
    Find the user's newest unused code for this purpose.
    No code? → OTP_INVALID
    Expired? → OTP_EXPIRED
    Too many wrong tries already? → lock it, OTP_EXPIRED
    Wrong code? → count the attempt, OTP_INVALID
    Otherwise return the code row so the caller can mark it used.
    """
    otp = await db.scalar(
        select(OtpCode)
        .where(
            OtpCode.user_id == user.id,
            OtpCode.purpose == purpose,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if otp is None:
        raise AppError("auth.otp_invalid", code="OTP_INVALID")

    if ensure_utc(otp.expires_at) < datetime.now(UTC):
        raise AppError("auth.otp_expired", code="OTP_EXPIRED")

    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        otp.consumed_at = datetime.now(UTC)
        await db.commit()
        raise AppError("auth.otp_expired", code="OTP_EXPIRED")

    if otp.code_hash != hash_otp(code):
        otp.attempts += 1
        await db.commit()
        raise AppError("auth.otp_invalid", code="OTP_INVALID")

    return otp


_USERNAME_UNSAFE = re.compile(r"[^a-z0-9._-]")


async def _unique_username(db: Db, email: str) -> str:
    """Derive a free username from the email's local part.

    Local parts are not unique across providers — alice@gmail.com and
    alice@outlook.com both want "alice" — and an address change leaves the
    old username behind, so the derived name is often already taken. Append
    the first free numeric suffix rather than letting the insert die on
    uq_users_username.
    """
    base = _USERNAME_UNSAFE.sub("", email.split("@")[0].lower())[:50] or "user"
    taken = set(
        (
            await db.scalars(
                select(User.username).where(
                    User.username.startswith(base, autoescape=True)
                )
            )
        ).all()
    )
    if base not in taken:
        return base
    suffix = 1
    while f"{base}{suffix}" in taken:
        suffix += 1
    return f"{base}{suffix}"


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
async def sign_up(
    payload: SignUpRequest, db: Db, request: Request
) -> SignUpResponse:
    # Rate-limited per IP: every successful sign-up sends an email, and a
    # loop over throwaway addresses burns sending reputation, not just rows.
    enforce(signup_by_ip, client_ip(request))
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
        username=await _unique_username(db, email),
        invite_code=uuid.uuid4().hex[:8].upper(),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Two signups racing on the same address (or the same derived
        # username) both pass the checks above and one loses at the insert.
        # That is a conflict, not a server fault.
        logger.warning("Signup conflict for %s: %s", email, exc.orig)
        raise AppError(
            "auth.email_taken",
            status_code=status.HTTP_409_CONFLICT,
            code="EMAIL_TAKEN",
        ) from exc
    await _bootstrap_user_rows(db, user)
    await _issue_otp(db, user)

    return SignUpResponse(email=user.email, verification_required=True)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: Db, request: Request) -> TokenResponse:
    # Both keys, before touching the database: per-email caps a targeted
    # attack on one account, per-IP caps one machine spraying many accounts.
    enforce(login_by_ip, client_ip(request))
    enforce(login_by_email, payload.email.lower())

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

    otp = await _check_otp(db, user, payload.code, OtpPurpose.verify_email)
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
async def forgot_password(
    payload: ForgotPasswordRequest, db: Db, request: Request
) -> MessageResponse:
    """Emails a 6-digit reset code."""
    # Limited even for unknown addresses — the response never reveals whether
    # the email exists, and neither should the rate limiter's behaviour.
    enforce(forgot_by_ip, client_ip(request))
    enforce(forgot_by_email, payload.email.lower())

    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    generic = MessageResponse(
        message="If that email is registered, a reset code is on its way."
    )
    if user is None or user.deleted_at is not None:
        return generic

    await _issue_otp(db, user, purpose=OtpPurpose.reset_password)
    return generic


@router.post("/verifyResetCode", response_model=MessageResponse)
async def verify_reset_code(payload: VerifyCodeRequest, db: Db) -> MessageResponse:
    """Check a reset code *without* consuming it.
    """
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or user.deleted_at is not None:
        raise AppError("auth.otp_invalid", code="OTP_INVALID")

    await _check_otp(db, user, payload.code, OtpPurpose.reset_password)
    return MessageResponse(message="Code verified.")


@router.post("/resetPassword", response_model=MessageResponse)
async def reset_password(payload: ResetPasswordRequest, db: Db) -> MessageResponse:
    _ensure_password_policy(payload.password)

    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    # Unknown address answers exactly like a wrong code: this endpoint must
    # not become the account-enumeration oracle that forgotPassword avoids.
    if user is None or user.deleted_at is not None:
        raise AppError("auth.otp_invalid", code="OTP_INVALID")

    otp = await _check_otp(db, user, payload.code, OtpPurpose.reset_password)
    otp.consumed_at = datetime.now(UTC)
    user.password_hash = hash_password(payload.password)
    # Proving control of the inbox verifies the address as a side effect.
    user.email_verified = True
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
            username=await _unique_username(db, email.lower()),
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
    """Validate an Apple/Google identity token against the provider's JWKS.

    Signature, expiry, issuer, and audience are all checked in
    `app.services.social`. Answers 501 while the provider's audience
    (client ID / bundle ID) is not configured — refusal stays the default
    rather than trust.
    """
    return await verify_identity_token(token, provider)


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
    storage = get_storage()
    await storage.delete_prefix(f"meals/{user.id}")
    await storage.delete_prefix(f"avatars/{user.id}")
    await db.delete(user)
    await db.flush()
    return MessageResponse(message="Your account has been deleted.")
