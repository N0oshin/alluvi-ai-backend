from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.services.email.base import EmailDeliveryError, EmailMessage, EmailSender

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.resend.com/emails"


class ResendEmailSender(EmailSender):
    name = "resend"

    def __init__(self) -> None:
        if not settings.RESEND_API_KEY:
            raise RuntimeError(
                "EMAIL_PROVIDER=resend requires RESEND_API_KEY. "
                "Set it in .env, or use EMAIL_PROVIDER=console for local work."
            )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.EMAIL_TIMEOUT_SECONDS),
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
        )

    async def send(self, message: EmailMessage) -> None:
        payload: dict[str, object] = {
            "from": settings.EMAIL_FROM,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html

        try:
            response = await self._client.post(_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            logger.error("Resend unreachable: %s", exc)
            raise EmailDeliveryError("resend unreachable") from exc

        if response.status_code >= 400:
            # The body carries the actual reason (unverified domain, bad key,
            # rate limit). Never log the API key, which lives in the headers.
            logger.error(
                "Resend rejected the message (%s): %s",
                response.status_code,
                response.text[:500],
            )
            raise EmailDeliveryError(f"resend returned {response.status_code}")

        logger.info(
            "Sent %r to %s (resend id=%s)",
            message.subject,
            message.to,
            response.json().get("id"),
        )


__all__ = ["ResendEmailSender"]
