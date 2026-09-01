"""Push token registration.

the client only records
success when the status is 200 **and the response body is the literal JSON
`true`**. Anything else and it re-sends on the next launch. So both endpoints
return a bare boolean, not an envelope.

The client also sends an APNs token on iOS and an FCM token on Android, so
both are stored in the same column without interpretation.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentUser, Db
from app.core.timeutil import is_valid_tz
from app.db.models import DeviceToken
from app.schemas.profile import DeviceTokenRequest

router = APIRouter(prefix="/UserNotification", tags=["notifications"])


@router.post("/addAnonymousToken", response_model=bool)
async def add_anonymous_token(payload: DeviceTokenRequest, db: Db) -> bool:
    """it registers a push token before the user has signed in."""
    existing = await db.scalar(
        select(DeviceToken).where(DeviceToken.token == payload.token)
    )
    if existing is None:
        db.add(DeviceToken(token=payload.token))
        await db.flush()
    return True


@router.post("/addUserToken", response_model=bool)
async def add_user_token(
    payload: DeviceTokenRequest, user: CurrentUser, db: Db
) -> bool:
    existing = await db.scalar(
        select(DeviceToken).where(DeviceToken.token == payload.token)
    )
    if existing is None:
        db.add(DeviceToken(token=payload.token, user_id=user.id))
    else:
        # Claim a token that was registered anonymously before sign-in.
        existing.user_id = user.id

    # Legacy timezone path (only reached when the user granted push
    # permission). The X-Timezone request header is the primary mechanism —
    # see get_current_user. An unknown zone name is dropped rather than
    # failing the registration.
    if payload.timezone and payload.timezone != user.timezone:
        if is_valid_tz(payload.timezone):
            user.timezone = payload.timezone

    await db.flush()
    return True
