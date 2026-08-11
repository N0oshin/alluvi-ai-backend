"""Timezone helpers.

SQLite has no native timezone-aware type, so a `DateTime(timezone=True)`
column round-trips as *naive* there while Postgres returns it aware. Comparing
one against `datetime.now(UTC)` raises `TypeError: can't compare offset-naive
and offset-aware datetimes`. Every read of a stored timestamp goes through
`ensure_utc` so the same code works on both backends.
"""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to a naive datetime; convert an aware one to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)
