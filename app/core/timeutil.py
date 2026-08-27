"""Timezone helpers.

SQLite has no native timezone-aware type, so a `DateTime(timezone=True)`
column round-trips as *naive* there while Postgres returns it aware. Comparing
one against `datetime.now(UTC)` raises `TypeError: can't compare offset-naive
and offset-aware datetimes`. Every read of a stored timestamp goes through
`ensure_utc` so the same code works on both backends.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to a naive datetime; convert an aware one to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def user_tz(tz_name: str) -> tzinfo:
    """The user's zone, or UTC when the stored name is unknown — the same
    least-bad fallback policy as User.timezone documents."""
    try:
        return ZoneInfo(tz_name)
    except (KeyError, ValueError):
        return UTC


def local_date(value: datetime, tz_name: str) -> date:
    """The calendar day `value` falls on for a user in `tz_name`.

    Day labels (Meal.eaten_on, streaks) are local-calendar concepts: a dinner
    at 20:00 in Los Angeles is 03:00 *tomorrow* in UTC, but it belongs to the
    user's today. Every eaten_on write and every "today" comparison must go
    through here (or local_today) so all of them agree on whose calendar is
    in use.
    """
    return ensure_utc(value).astimezone(user_tz(tz_name)).date()


def local_today(tz_name: str) -> date:
    return local_date(utcnow(), tz_name)
