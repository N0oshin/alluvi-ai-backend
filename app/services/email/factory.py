"""Provider selection. Swapping providers is one env var."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.email.base import EmailSender
from app.services.email.console import ConsoleEmailSender


@lru_cache
def get_email_sender() -> EmailSender:
    if settings.EMAIL_PROVIDER == "resend":
        # Imported lazily so a missing key only matters when it is selected.
        from app.services.email.resend import ResendEmailSender

        return ResendEmailSender()
    return ConsoleEmailSender()


__all__ = ["get_email_sender"]
