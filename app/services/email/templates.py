"""Email copy. 
"""

from __future__ import annotations

import html as _html

from app.services.email.base import EmailMessage

_APP = "Alluvi"


def _shell(heading: str, body_html: str) -> str:
    """A deliberately plain HTML shell.
    """
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#f6f7f9;
               font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
               color:#1a1c1e;">
    <div style="max-width:480px;margin:0 auto;background:#ffffff;
                border-radius:12px;padding:32px;">
      <h1 style="margin:0 0 16px;font-size:20px;font-weight:600;">{heading}</h1>
      {body_html}
      <p style="margin:32px 0 0;font-size:12px;color:#6b7280;">
        {_APP} &middot; If you weren't expecting this email, you can ignore it.
      </p>
    </div>
  </body>
</html>"""


def verification_email(to: str, *, code: str, ttl_minutes: int) -> EmailMessage:
    text = (
        f"Your {_APP} verification code is {code}\n\n"
        f"Enter it in the app to finish creating your account. "
        f"The code expires in {ttl_minutes} minutes.\n\n"
        "If you didn't create an account, you can ignore this email."
    )
    body = f"""\
      <p style="margin:0 0 24px;font-size:15px;line-height:1.5;">
        Enter this code in the app to finish creating your account.
      </p>
      <p style="margin:0 0 24px;font-size:32px;font-weight:700;
                letter-spacing:6px;font-family:ui-monospace,monospace;">
        {_html.escape(code)}
      </p>
      <p style="margin:0;font-size:14px;color:#6b7280;">
        This code expires in {ttl_minutes} minutes.
      </p>"""
    return EmailMessage(
        to=to,
        subject=f"{code} is your {_APP} verification code",
        text=text,
        html=_shell("Verify your email", body),
    )


def password_reset_email(to: str, *, code: str, ttl_minutes: int) -> EmailMessage:
    """Same shape as the verification mail: a 6-digit code, not a link.

    The app never leaves the foreground — no deep link, no web page — so the
    copy has to make it obvious this code is for a *password reset* and is
    harmless to ignore if the user did not ask for it.
    """
    text = (
        f"Your {_APP} password reset code is {code}\n\n"
        f"Enter it in the app to choose a new password. "
        f"The code expires in {ttl_minutes} minutes.\n\n"
        "If you didn't ask to reset your password, you can ignore this email — "
        "your password stays unchanged."
    )
    body = f"""\
      <p style="margin:0 0 24px;font-size:15px;line-height:1.5;">
        Enter this code in the app to choose a new password.
      </p>
      <p style="margin:0 0 24px;font-size:32px;font-weight:700;
                letter-spacing:6px;font-family:ui-monospace,monospace;">
        {_html.escape(code)}
      </p>
      <p style="margin:0;font-size:14px;color:#6b7280;">
        This code expires in {ttl_minutes} minutes. If you didn't request it,
        your password stays unchanged.
      </p>"""
    return EmailMessage(
        to=to,
        subject=f"{code} is your {_APP} password reset code",
        text=text,
        html=_shell("Reset your password", body),
    )


__all__ = ["password_reset_email", "verification_email"]
