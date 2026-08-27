"""Push pipeline: quiet hours, local-calendar dates, dispatch pruning, and
the streak reminder's decision logic.

The scheduler's *loop* (minute alignment, lifespan wiring) is deliberately
untested — it's clock-driven glue. What regresses silently is the logic:
the midnight-spanning quiet window, the LA-dinner day-label case, and
"stay silent when the streak is safe".
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.timeutil import local_date, local_today
from app.db.models import DeviceToken, Meal, User
from app.services.push import dispatch
from app.services.push.base import PushMessage, PushSender
from app.services.push.scheduler import _is_quiet, _streak_message


# --------------------------------------------------------------------------
# _is_quiet — pure function, so plain (non-async) tests
# --------------------------------------------------------------------------


class TestIsQuiet:
    def test_spanning_midnight_late_evening(self):
        assert _is_quiet("23:00", "22:00", "07:00")

    def test_spanning_midnight_early_morning(self):
        assert _is_quiet("06:59", "22:00", "07:00")

    def test_spanning_midnight_daytime_is_not_quiet(self):
        assert not _is_quiet("13:00", "22:00", "07:00")

    def test_start_boundary_is_quiet_end_boundary_is_not(self):
        # Half-open window: quiet begins exactly at start, ends exactly at end.
        assert _is_quiet("22:00", "22:00", "07:00")
        assert not _is_quiet("07:00", "22:00", "07:00")

    def test_non_spanning_window(self):
        assert _is_quiet("13:30", "13:00", "14:00")
        assert not _is_quiet("14:00", "13:00", "14:00")
        assert not _is_quiet("12:59", "13:00", "14:00")

    def test_equal_start_and_end_is_never_quiet(self):
        assert not _is_quiet("13:00", "13:00", "13:00")


# --------------------------------------------------------------------------
# local_date / local_today — the LA-dinner case
# --------------------------------------------------------------------------


class TestLocalDate:
    def test_la_dinner_belongs_to_previous_local_day(self):
        # 03:00 UTC on the 28th is 20:00 on the 27th in Los Angeles (UTC-7).
        instant = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
        assert local_date(instant, "America/Los_Angeles") == date(2026, 8, 27)

    def test_ahead_of_utc_rolls_forward(self):
        # 21:00 UTC is already 00:30 next day in Tehran (UTC+3:30).
        instant = datetime(2026, 8, 27, 21, 0, tzinfo=UTC)
        assert local_date(instant, "Asia/Tehran") == date(2026, 8, 28)

    def test_utc_zone_is_identity(self):
        instant = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
        assert local_date(instant, "UTC") == date(2026, 8, 28)

    def test_naive_datetime_is_treated_as_utc(self):
        assert local_date(datetime(2026, 8, 28, 3, 0), "America/Los_Angeles") == date(
            2026, 8, 27
        )

    def test_unknown_zone_falls_back_to_utc(self):
        instant = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
        assert local_date(instant, "Not/AZone") == date(2026, 8, 28)

    def test_local_today_matches_manual_conversion(self):
        assert local_today("Asia/Riyadh") == datetime.now(UTC).astimezone(
            __import__("zoneinfo").ZoneInfo("Asia/Riyadh")
        ).date()


# --------------------------------------------------------------------------
# push_to_user — token lookup and dead-token pruning
# --------------------------------------------------------------------------


class FakeSender(PushSender):
    """Records what it was asked to send; reports `dead` tokens back."""

    name = "fake"

    def __init__(self, dead: set[str] | None = None) -> None:
        self.dead = dead or set()
        self.calls: list[tuple[list[str], PushMessage]] = []

    async def send(self, tokens: list[str], message: PushMessage) -> set[str]:
        self.calls.append((tokens, message))
        return self.dead & set(tokens)


async def _make_user(db, timezone: str = "UTC") -> User:
    user = User(email=f"push-{uuid.uuid4().hex[:8]}@example.com", timezone=timezone)
    db.add(user)
    await db.flush()
    return user


class TestPushToUser:
    async def test_no_tokens_sends_nothing(self, session_factory, monkeypatch):
        sender = FakeSender()
        monkeypatch.setattr(dispatch, "get_push_sender", lambda: sender)
        async with session_factory() as db:
            user = await _make_user(db)
            delivered = await dispatch.push_to_user(
                db, user.id, PushMessage(title="t", body="b")
            )
        assert delivered == 0
        assert sender.calls == []

    async def test_delivers_to_every_device_and_prunes_dead(
        self, session_factory, monkeypatch
    ):
        sender = FakeSender(dead={"dead-token"})
        monkeypatch.setattr(dispatch, "get_push_sender", lambda: sender)
        async with session_factory() as db:
            user = await _make_user(db)
            db.add(DeviceToken(token="live-token", user_id=user.id))
            db.add(DeviceToken(token="dead-token", user_id=user.id))
            await db.flush()

            delivered = await dispatch.push_to_user(
                db, user.id, PushMessage(title="t", body="b")
            )
            await db.commit()

            assert delivered == 1
            [(sent_tokens, _)] = sender.calls
            assert sorted(sent_tokens) == ["dead-token", "live-token"]

            remaining = (await db.scalars(select(DeviceToken.token))).all()
            assert remaining == ["live-token"]

    async def test_other_users_tokens_are_not_sent_to(
        self, session_factory, monkeypatch
    ):
        sender = FakeSender()
        monkeypatch.setattr(dispatch, "get_push_sender", lambda: sender)
        async with session_factory() as db:
            user = await _make_user(db)
            other = await _make_user(db)
            db.add(DeviceToken(token="mine", user_id=user.id))
            db.add(DeviceToken(token="theirs", user_id=other.id))
            # An unclaimed anonymous token must never be pushed to either.
            db.add(DeviceToken(token="anonymous"))
            await db.flush()

            delivered = await dispatch.push_to_user(
                db, user.id, PushMessage(title="t", body="b")
            )
        assert delivered == 1
        [(sent_tokens, _)] = sender.calls
        assert sent_tokens == ["mine"]


# --------------------------------------------------------------------------
# _streak_message — speak only when the streak is genuinely at risk
# --------------------------------------------------------------------------


def _meal(user_id, day: date) -> Meal:
    return Meal(
        user_id=user_id,
        title="Test meal",
        calories=500,
        protein_g=30,
        carbs_g=50,
        fat_g=20,
        eaten_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        eaten_on=day,
    )


class TestStreakMessage:
    async def test_at_risk_streak_produces_message_with_count(self, session_factory):
        async with session_factory() as db:
            user = await _make_user(db)
            today = local_today(user.timezone)
            for offset in (1, 2, 3):  # three-day run ending yesterday
                db.add(_meal(user.id, today - timedelta(days=offset)))
            await db.flush()

            message = await _streak_message(db, user, today)
        assert message is not None
        # The count must be computed from data; the wording around it is
        # cosmetic and free to change.
        assert "3" in message.title

    async def test_logged_today_stays_silent(self, session_factory):
        async with session_factory() as db:
            user = await _make_user(db)
            today = local_today(user.timezone)
            db.add(_meal(user.id, today - timedelta(days=1)))
            db.add(_meal(user.id, today))
            await db.flush()

            assert await _streak_message(db, user, today) is None

    async def test_no_streak_stays_silent(self, session_factory):
        async with session_factory() as db:
            user = await _make_user(db)
            today = local_today(user.timezone)
            # Last meal two days ago: the streak is already broken, nothing
            # to save — a reminder would be nagging.
            db.add(_meal(user.id, today - timedelta(days=2)))
            await db.flush()

            assert await _streak_message(db, user, today) is None

    async def test_no_meals_at_all_stays_silent(self, session_factory):
        async with session_factory() as db:
            user = await _make_user(db)
            assert await _streak_message(db, user, local_today(user.timezone)) is None
