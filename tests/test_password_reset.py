"""Forgot / reset password via an emailed 6-digit code.

Pattern: `forgotPassword` mints a `reset_password` OTP through the same table
the sign-up flow uses; `resetPassword` takes `{email, code, password}`.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import OtpCode, OtpPurpose, User

OLD = "Passw0rd!"
NEW = "N3wPassw0rd!"


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing mail instead of printing it."""
    from app.api.v1 import auth as auth_module
    from app.services.email import base as email_base

    messages: list[email_base.EmailMessage] = []

    class _Capture(email_base.EmailSender):
        name = "capture"

        async def send(self, message):
            messages.append(message)

    monkeypatch.setattr(auth_module, "get_email_sender", lambda: _Capture())
    return messages


def _code_from(message) -> str:
    match = re.search(r"\b(\d{6})\b", message.text)
    assert match is not None, message.text
    return match.group(1)


async def _verified_user(client, session_factory, sent) -> str:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "Test User", "email": email, "password": OLD},
    )
    assert resp.status_code == 201, resp.text
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == email))
        user.email_verified = True
        await db.commit()
    sent.clear()
    return email


async def test_forgot_then_reset_changes_password_and_revokes_sessions(
    client, session_factory, sent
):
    email = await _verified_user(client, session_factory, sent)

    # An existing session that must die once the password is reset.
    login = await client.post("/api/Auth/login", json={"email": email, "password": OLD})
    assert login.status_code == 200, login.text
    old_refresh = login.json()["refreshToken"]

    resp = await client.post("/api/Auth/forgotPassword", json={"email": email})
    assert resp.status_code == 200, resp.text
    assert len(sent) == 1
    assert sent[0].to == email
    assert "reset" in sent[0].subject.lower()
    code = _code_from(sent[0])

    resp = await client.post(
        "/api/Auth/resetPassword",
        json={"email": email, "code": code, "password": NEW},
    )
    assert resp.status_code == 200, resp.text

    # New password works, old one does not.
    assert (
        await client.post("/api/Auth/login", json={"email": email, "password": NEW})
    ).status_code == 200
    assert (
        await client.post("/api/Auth/login", json={"email": email, "password": OLD})
    ).status_code == 401

    # Every pre-reset session is revoked.
    refresh = await client.post("/api/Auth/refresh", json={"refreshToken": old_refresh})
    assert refresh.status_code == 401, refresh.text

    # The code is single-use.
    again = await client.post(
        "/api/Auth/resetPassword",
        json={"email": email, "code": code, "password": NEW},
    )
    assert again.status_code == 400
    assert again.json()["code"] == "OTP_INVALID"


async def test_forgot_password_reply_is_generic_for_unknown_email(client, sent):
    resp = await client.post(
        "/api/Auth/forgotPassword", json={"email": "nobody@example.com"}
    )
    assert resp.status_code == 200
    assert sent == []


async def test_reset_with_unknown_email_looks_like_a_wrong_code(client):
    resp = await client.post(
        "/api/Auth/resetPassword",
        json={"email": "nobody@example.com", "code": "123456", "password": NEW},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "OTP_INVALID"


async def test_reset_enforces_password_policy(client, session_factory, sent):
    email = await _verified_user(client, session_factory, sent)
    resp = await client.post(
        "/api/Auth/resetPassword",
        json={"email": email, "code": "123456", "password": "short"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "WEAK_PASSWORD"


async def test_signup_code_cannot_reset_a_password_and_vice_versa(
    client, session_factory, sent
):
    """Codes are scoped by purpose: one table, two flows, no crossover."""
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "Test User", "email": email, "password": OLD},
    )
    assert resp.status_code == 201, resp.text
    signup_code = _code_from(sent[0])

    resp = await client.post("/api/Auth/forgotPassword", json={"email": email})
    assert resp.status_code == 200
    reset_code = _code_from(sent[1])

    # The sign-up code is not a reset code.
    resp = await client.post(
        "/api/Auth/resetPassword",
        json={"email": email, "code": signup_code, "password": NEW},
    )
    if signup_code != reset_code:
        assert resp.status_code == 400, resp.text

    # Requesting a reset did not cancel the pending sign-up verification.
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == email))
        outstanding = (
            await db.scalars(
                select(OtpCode).where(
                    OtpCode.user_id == user.id, OtpCode.consumed_at.is_(None)
                )
            )
        ).all()
    assert {o.purpose for o in outstanding} == {
        OtpPurpose.verify_email,
        OtpPurpose.reset_password,
    }

    # And the reset code is not a sign-up code.
    resp = await client.post(
        "/api/Auth/verifyCode", json={"email": email, "code": reset_code}
    )
    if signup_code != reset_code:
        assert resp.status_code == 400, resp.text

    # Sign-up verification still works with its own code afterwards.
    resp = await client.post(
        "/api/Auth/verifyCode", json={"email": email, "code": signup_code}
    )
    assert resp.status_code == 200, resp.text


async def test_wrong_attempts_are_counted_and_lock_the_code(
    client, session_factory, sent
):
    """The attempts counter must survive the 4xx rollback, or the cap is dead."""
    email = await _verified_user(client, session_factory, sent)
    await client.post("/api/Auth/forgotPassword", json={"email": email})
    code = _code_from(sent[0])
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(settings.OTP_MAX_ATTEMPTS):
        resp = await client.post(
            "/api/Auth/resetPassword",
            json={"email": email, "code": wrong, "password": NEW},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "OTP_INVALID"

    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == email))
        otp = await db.scalar(
            select(OtpCode).where(
                OtpCode.user_id == user.id,
                OtpCode.purpose == OtpPurpose.reset_password,
            )
        )
    assert otp.attempts == settings.OTP_MAX_ATTEMPTS

    # Even the right code is refused now — the user must request a new one.
    resp = await client.post(
        "/api/Auth/resetPassword",
        json={"email": email, "code": code, "password": NEW},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "OTP_EXPIRED"


async def test_verify_code_attempts_are_persisted_too(client, sent):
    """Regression: verifyCode used to increment attempts and then lose it."""
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    await client.post(
        "/api/Auth/SignUp",
        json={"name": "Test User", "email": email, "password": OLD},
    )
    code = _code_from(sent[0])
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(settings.OTP_MAX_ATTEMPTS):
        await client.post("/api/Auth/verifyCode", json={"email": email, "code": wrong})

    resp = await client.post("/api/Auth/verifyCode", json={"email": email, "code": code})
    assert resp.status_code == 400
    assert resp.json()["code"] == "OTP_EXPIRED"
