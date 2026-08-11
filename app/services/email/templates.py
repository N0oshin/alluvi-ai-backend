"""Email copy. English only for now.

Unlike `core/i18n.py`, these are not localised yet — see GUIDE.md. When Arabic
copy arrives, give each builder a `lang: Lang` argument and switch here; no
caller or provider changes.

Every template supplies plain text as well as HTML. Text is not optional:
spam filters score HTML-only mail worse, and some corporate clients strip HTML
entirely.
"""

from __future__ import annotations

import html as _html

from app.services.email.base import EmailMessage

_APP = "Alluvi"


def _shell(heading: str, body_html: str) -> str:
    """A deliberately plain HTML shell.

    No external CSS or images: mail clients block remote content by default,
    and inline styles are the only thing that renders consistently.
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


def password_reset_email(to: str, *, url: str, ttl_hours: int) -> EmailMessage:
    text = (
        f"Reset your {_APP} password using this link:\n\n{url}\n\n"
        f"The link expires in {ttl_hours} hour(s). "
        "If you didn't ask to reset your password, you can ignore this email — "
        "your password stays unchanged."
    )
    safe = _html.escape(url, quote=True)
    body = f"""\
      <p style="margin:0 0 24px;font-size:15px;line-height:1.5;">
        Tap the button below to choose a new password.
      </p>
      <p style="margin:0 0 24px;">
        <a href="{safe}"
           style="display:inline-block;padding:12px 24px;border-radius:8px;
                  background:#1a1c1e;color:#ffffff;text-decoration:none;
                  font-size:15px;font-weight:600;">Reset password</a>
      </p>
      <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">
        Or paste this link into your browser:
      </p>
      <p style="margin:0 0 24px;font-size:13px;word-break:break-all;">
        <a href="{safe}" style="color:#2563eb;">{safe}</a>
      </p>
      <p style="margin:0;font-size:14px;color:#6b7280;">
        This link expires in {ttl_hours} hour(s). If you didn't request it,
        your password stays unchanged.
      </p>"""
    return EmailMessage(
        to=to,
        subject=f"Reset your {_APP} password",
        text=text,
        html=_shell("Reset your password", body),
    )


__all__ = ["password_reset_email", "verification_email"]
