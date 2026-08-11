"""Development sender: writes the message to the log instead of sending it.

This is the default, and it is what keeps local work and the test suite free of
network calls, API keys, and accidental mail to real addresses. It preserves
the behaviour the project had before a real provider existed.
"""

from __future__ import annotations

import logging

from app.services.email.base import EmailMessage, EmailSender

logger = logging.getLogger(__name__)


class ConsoleEmailSender(EmailSender):
    name = "console"

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "EMAIL (not sent — EMAIL_PROVIDER=console)\n"
            "  to:      %s\n"
            "  subject: %s\n"
            "  ----\n%s\n  ----",
            message.to,
            message.subject,
            message.text,
        )


__all__ = ["ConsoleEmailSender"]
