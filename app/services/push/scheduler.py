"""Meal-reminder scheduler: the first code that *initiates* a push.

A single asyncio task, started from the app lifespan, that ticks once a
minute and asks: whose local wall-clock just hit a reminder slot? "Local" is
the whole problem — reminder times and quiet hours are wall-clock concepts,
so every comparison happens in the user's own zone (`User.timezone`, kept
fresh by the addUserToken heartbeat).

Deliberately in-process rather than Celery/cron: this backend has no worker
infrastructure, and a minute-granularity loop over a mobile-app user base is
cheap. The known cost: if the process is down at 13:00 sharp, that lunch
reminder is skipped, not queued. Acceptable for reminders.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import DeviceToken, Meal, NotificationSettings, User
from app.db.session import SessionLocal
from app.services.push.base import PushMessage
from app.services.push.dispatch import push_to_user

logger = logging.getLogger(__name__)

# Local wall-clock times. Fixed for every user for now; per-user schedules
# would be a column on NotificationSettings, not a code change here.
MEAL_SLOTS: dict[str, PushMessage] = {
    "08:30": PushMessage(
        title="Breakfast time 🍳",
        body="Start your streak for today — log your breakfast.",
    ),
    "13:00": PushMessage(
        title="Lunch time 🥗",
        body="Don't forget to log your lunch.",
    ),
    "19:30": PushMessage(
        title="Dinner time 🍽️",
        body="Log your dinner to close out the day.",
    ),
}


STREAK_SLOT = "21:00"

async def _streak_message(db, user: User, local_date) -> PushMessage | None:
    """The data-driven part: only speak when the streak is genuinely at risk.

    Returns None when there is nothing to say — already logged today, or no
    streak to lose.
    """
    logged_today = await db.scalar(
        select(Meal.id)
        .where(Meal.user_id == user.id, Meal.eaten_on == local_date)
        .limit(1)
    )
    if logged_today is not None:
        return None

    # Lazy import, same reason as services/achievements.py: profile.py is an
    # API module and importing it at module load would risk a cycle.
    from app.api.v1.profile import compute_streak

    streak = await compute_streak(db, user.id, user.timezone)
    if streak == 0:
        return None

    return PushMessage(
        title=f"🔥 Your {streak}-day streak is at risk",
        body="Log one meal before midnight to keep it alive.",
    )


def _is_quiet(local_hhmm: str, start: str, end: str) -> bool:
    """True if `local_hhmm` falls inside the quiet window.

    "HH:mm" strings compare correctly as strings (zero-padded, lexicographic
    == chronological). The window usually spans midnight (22:00 → 07:00):
    start > end means "after start OR before end".
    """
    if start <= end:
        return start <= local_hhmm < end
    return local_hhmm >= start or local_hhmm < end


async def _tick(already_sent: set[str]) -> None:
    """One pass: push to every user whose local time matches a slot."""
    async with SessionLocal() as db:
        users = (
            await db.scalars(
                select(User).where(
                    User.deleted_at.is_(None),
                    User.id.in_(
                        select(DeviceToken.user_id).where(
                            DeviceToken.user_id.is_not(None)
                        )
                    ),
                )
            )
        ).all()
        if not users:
            return

        # One query for all settings rows, not one per user.
        prefs = {
            s.user_id: s
            for s in await db.scalars(
                select(NotificationSettings).where(
                    NotificationSettings.user_id.in_([u.id for u in users])
                )
            )
        }

        now_utc = datetime.now(timezone.utc)
        for user in users:
            try:
                local = now_utc.astimezone(ZoneInfo(user.timezone))
            except (KeyError, ValueError):
                # UTC is the least-bad fallback, not a real answer (see the
                # note on User.timezone).
                local = now_utc

            hhmm = local.strftime("%H:%M")
            if hhmm not in MEAL_SLOTS and hhmm != STREAK_SLOT:
                continue

            p = prefs.get(user.id)
            # No settings row means the user never touched the toggles; the
            # model defaults (reminders on, quiet 22:00-07:00) apply.
            quiet_start = p.quiet_start if p else "22:00"
            quiet_end = p.quiet_end if p else "07:00"
            if _is_quiet(hhmm, quiet_start, quiet_end):
                continue

            # Guard against double-fire if a tick ever runs twice inside the
            # same minute (slow previous tick, clock adjustment). Marked
            # *before* the streak queries so a re-run can't double-send.
            key = f"{user.id}:{local.date().isoformat()}:{hhmm}"
            if key in already_sent:
                continue
            already_sent.add(key)

            message: PushMessage | None = None
            if hhmm in MEAL_SLOTS:
                if p is None or p.meal_reminders:
                    message = MEAL_SLOTS[hhmm]
            elif p is None or p.streak_reminder:
                message = await _streak_message(db, user, local.date())
            if message is None:
                continue

            delivered = await push_to_user(db, user.id, message)
            logger.info(
                "Reminder %s -> user %s (%d device(s))",
                hhmm,
                user.id,
                delivered,
            )

        # push_to_user may have deleted dead tokens; this session is ours to
        # settle (no request middleware here).
        await db.commit()


async def reminder_loop() -> None:
    already_sent: set[str] = set()
    last_prune = datetime.now(timezone.utc).date()

    while True:
        # Sleep to the next minute boundary so each local HH:mm is seen
        # exactly once per tick.
        await asyncio.sleep(60 - datetime.now(timezone.utc).second or 60)
        try:
            today = datetime.now(timezone.utc).date()
            if today != last_prune:
                already_sent.clear()
                last_prune = today
            await _tick(already_sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One bad tick (DB hiccup, provider outage) must not kill the
            # loop for the rest of the process's life.
            logger.exception("Reminder tick failed; continuing")


__all__ = ["reminder_loop", "MEAL_SLOTS"]
