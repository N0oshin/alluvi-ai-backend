"""Real delivery via Firebase Cloud Messaging.

FCM is the one gateway that reaches both platforms: Android natively, iOS by
forwarding to APNs (Firebase holds the APNs key uploaded in the console).
That matches the storage design — `device_tokens` keeps both kinds in one
column without interpretation.

The firebase-admin SDK is synchronous, so the actual network calls run in a
worker thread via `asyncio.to_thread` to keep the event loop free.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.services.push.base import PushMessage, PushSender

logger = logging.getLogger(__name__)


class FcmPushSender(PushSender):
    name = "fcm"

    def __init__(self) -> None:
        if not settings.FIREBASE_CREDENTIALS:
            raise RuntimeError(
                "PUSH_PROVIDER=fcm requires FIREBASE_CREDENTIALS (path to a "
                "service-account JSON). Set it in .env, or use "
                "PUSH_PROVIDER=console for local work."
            )
        # Imported here so the dependency only matters when fcm is selected.
        import firebase_admin
        from firebase_admin import credentials

        # initialize_app is process-global and raises on a second call; guard
        # so constructing the sender twice (tests, reload) stays safe.
        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.Certificate(settings.FIREBASE_CREDENTIALS)
            )

    async def send(self, tokens: list[str], message: PushMessage) -> set[str]:
        from firebase_admin import messaging

        def _send_sync() -> set[str]:
            batch = [
                messaging.Message(
                    token=token,
                    notification=messaging.Notification(
                        title=message.title, body=message.body
                    ),
                    data=message.data or None,
                )
                for token in tokens
            ]
            responses = messaging.send_each(batch).responses

            dead: set[str] = set()
            for token, result in zip(tokens, responses):
                if result.exception is None:
                    continue
                if isinstance(result.exception, messaging.UnregisteredError):
                    # App uninstalled or token rotated — prune, don't retry.
                    dead.add(token)
                else:
                    # Transient (quota, unavailable) or config errors: log and
                    # move on; push is best-effort.
                    logger.warning(
                        "FCM send failed for one device: %s", result.exception
                    )
            return dead

        return await asyncio.to_thread(_send_sync)


__all__ = ["FcmPushSender"]
