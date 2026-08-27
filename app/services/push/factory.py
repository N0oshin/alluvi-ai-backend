"""Provider selection. Swapping providers is one env var."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.push.base import PushSender
from app.services.push.console import ConsolePushSender


@lru_cache
def get_push_sender() -> PushSender:
    if settings.PUSH_PROVIDER == "fcm":
        # Imported lazily so missing credentials only matter when selected.
        from app.services.push.fcm import FcmPushSender

        return FcmPushSender()
    return ConsolePushSender()


__all__ = ["get_push_sender"]
