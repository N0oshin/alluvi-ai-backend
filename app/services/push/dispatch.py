"""The one entry point the rest of the backend calls to push to a user.

Owns the two things every caller would otherwise have to repeat: looking up
the user's device tokens, and pruning the ones the provider reports dead.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceToken
from app.services.push.base import PushMessage
from app.services.push.factory import get_push_sender


async def push_to_user(
    db: AsyncSession, user_id: uuid.UUID, message: PushMessage
) -> int:
    """Send `message` to every device the user has registered.

    Returns the number of devices that accepted delivery. 0 is normal — the
    user may have no registered token yet, or every token may have died.
    """
    tokens = list(
        await db.scalars(
            select(DeviceToken.token).where(DeviceToken.user_id == user_id)
        )
    )
    if not tokens:
        return 0

    dead = await get_push_sender().send(tokens, message)
    if dead:
        await db.execute(delete(DeviceToken).where(DeviceToken.token.in_(dead)))

    return len(tokens) - len(dead)


__all__ = ["push_to_user"]
