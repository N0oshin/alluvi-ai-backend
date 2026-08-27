"""Development sender: writes the notification to the log instead of sending.

The default, mirroring `email/console.py` — local work and the test suite
stay free of Firebase credentials and network calls.
"""

from __future__ import annotations

import logging

from app.services.push.base import PushMessage, PushSender

logger = logging.getLogger(__name__)


class ConsolePushSender(PushSender):
    name = "console"

    async def send(self, tokens: list[str], message: PushMessage) -> set[str]:
        logger.info(
            "PUSH (not sent — PUSH_PROVIDER=console)\n"
            "  tokens: %d device(s)\n"
            "  title:  %s\n"
            "  body:   %s\n"
            "  data:   %s",
            len(tokens),
            message.title,
            message.body,
            message.data,
        )
        return set()


__all__ = ["ConsolePushSender"]
