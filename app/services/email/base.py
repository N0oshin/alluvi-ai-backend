"""Email contract: templates build a message, a sender delivers it.

Same shape as `services/storage` and `services/vision` — the callers in
`api/v1/auth.py` know nothing about which provider is configured.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailDeliveryError(RuntimeError):
    """Provider refused or was unreachable. Callers turn this into an AppError."""


class EmailSender(abc.ABC):
    name: str

    @abc.abstractmethod
    async def send(self, message: EmailMessage) -> None:
        """Deliver one message, or raise EmailDeliveryError."""


__all__ = ["EmailDeliveryError", "EmailMessage", "EmailSender"]
