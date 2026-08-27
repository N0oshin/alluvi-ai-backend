"""Achievement awarding.

Definitions pending product sign-off (the seed only ships labels):
  * early_bird     — a meal eaten before 09:00 (UTC; the server has no user
                     timezone, and every other time in the API is UTC).
  * protein_pro    — the day's protein total meets the active plan's goal.
  * perfect_week   — 7 consecutive days each with at least one meal and a
                     calorie total within the daily goal.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Achievement,
    Meal,
    NutritionPlan,
    User,
    UserAchievement,
    WeightEntry,
    WeightGoal,
)

logger = logging.getLogger(__name__)

EARLY_BIRD_CUTOFF_HOUR = 9  # UTC


async def award(db: AsyncSession, user_id: uuid.UUID, key: str) -> Achievement | None:
    """Unlock one badge. Returns the Achievement only when newly awarded.

    Idempotent twice over: a lookup skips the common repeat, and the
    `uq_user_achievement` constraint settles the race between two concurrent
    requests — inside a savepoint, so a duplicate never poisons the session.
    """
    achievement = await db.scalar(select(Achievement).where(Achievement.key == key))
    if achievement is None:
        # Catalogue not seeded (or an unknown key) — nothing to award.
        return None

    already = await db.scalar(
        select(UserAchievement.id).where(
            UserAchievement.user_id == user_id,
            UserAchievement.achievement_id == achievement.id,
        )
    )
    if already is not None:
        return None

    try:
        async with db.begin_nested():
            db.add(
                UserAchievement(
                    user_id=user_id,
                    achievement_id=achievement.id,
                    unlocked_at=datetime.now(UTC),
                )
            )
    except IntegrityError:
        return None
    return achievement


async def on_meal_saved(db: AsyncSession, user: User, meal: Meal) -> list[Achievement]:
    """Every rule a saved meal can flip. Non-fatal by design."""
    try:
        newly = await _evaluate_meal(db, user, meal)
        newly += await _maybe_award_legend(db, user.id, any_new=bool(newly))
        return newly
    except Exception:
        logger.exception("Achievement evaluation failed for user %s", user.id)
        return []


async def on_weight_logged(db: AsyncSession, user: User) -> list[Achievement]:
    """Every rule a weight entry can flip. Non-fatal by design."""
    try:
        newly = await _evaluate_weight(db, user)
        newly += await _maybe_award_legend(db, user.id, any_new=bool(newly))
        return newly
    except Exception:
        logger.exception("Achievement evaluation failed for user %s", user.id)
        return []


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


async def _unlocked_keys(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await db.scalars(
        select(Achievement.key)
        .join(UserAchievement, UserAchievement.achievement_id == Achievement.id)
        .where(UserAchievement.user_id == user_id)
    )
    return set(rows.all())


async def _active_plan(db: AsyncSession, user_id: uuid.UUID) -> NutritionPlan | None:
    return await db.scalar(
        select(NutritionPlan)
        .where(NutritionPlan.user_id == user_id, NutritionPlan.is_active.is_(True))
        .order_by(NutritionPlan.created_at.desc())
        .limit(1)
    )


async def _evaluate_meal(db: AsyncSession, user: User, meal: Meal) -> list[Achievement]:
    unlocked = await _unlocked_keys(db, user.id)
    newly: list[Achievement] = []

    async def try_award(key: str, condition: bool) -> None:
        if condition and key not in unlocked:
            got = await award(db, user.id, key)
            if got is not None:
                newly.append(got)

    meal_count = None
    if {"first_scan", "meals_25", "meals_100"} - unlocked:
        meal_count = await db.scalar(
            select(func.count(Meal.id)).where(Meal.user_id == user.id)
        )
    if meal_count is not None:
        await try_award("first_scan", meal_count >= 1)
        await try_award("meals_25", meal_count >= 25)
        await try_award("meals_100", meal_count >= 100)

    # SQLite hands naive datetimes back; every naive value in this app is UTC.
    eaten = meal.eaten_at
    if eaten.tzinfo is None:
        eaten = eaten.replace(tzinfo=UTC)
    await try_award("early_bird", eaten.astimezone(UTC).hour < EARLY_BIRD_CUTOFF_HOUR)

    plan = None
    if {"protein_pro", "perfect_week"} - unlocked:
        plan = await _active_plan(db, user.id)

    if plan is not None and "protein_pro" not in unlocked and plan.protein_g > 0:
        day_protein = await db.scalar(
            select(func.coalesce(func.sum(Meal.protein_g), 0)).where(
                Meal.user_id == user.id, Meal.eaten_on == meal.eaten_on
            )
        )
        await try_award("protein_pro", int(day_protein or 0) >= plan.protein_g)

    if plan is not None and "perfect_week" not in unlocked and plan.daily_calories > 0:
        await try_award(
            "perfect_week", await _perfect_week(db, user.id, meal, plan)
        )

    if {"streak_7", "streak_30"} - unlocked:
        # Lazy import: profile.py calls into this module, so a top-level
        # import here would be circular.
        from app.api.v1.profile import compute_streak

        streak = await compute_streak(db, user.id, user.timezone)
        await try_award("streak_7", streak >= 7)
        await try_award("streak_30", streak >= 30)

    return newly


async def _perfect_week(
    db: AsyncSession, user_id: uuid.UUID, meal: Meal, plan: NutritionPlan
) -> bool:
    """7 consecutive days ending on the meal's day, each logged and within
    the calorie goal. Judged against the *current* goal — re-deriving each
    day's historical goal is not worth the precision."""
    start = meal.eaten_on - timedelta(days=6)
    rows = (
        await db.execute(
            select(Meal.eaten_on, func.sum(Meal.calories))
            .where(
                Meal.user_id == user_id,
                Meal.eaten_on >= start,
                Meal.eaten_on <= meal.eaten_on,
            )
            .group_by(Meal.eaten_on)
        )
    ).all()
    if len(rows) < 7:
        return False
    return all(0 < int(total) <= plan.daily_calories for _, total in rows)


async def _evaluate_weight(db: AsyncSession, user: User) -> list[Achievement]:
    unlocked = await _unlocked_keys(db, user.id)
    if not ({"first_kilo", "halfway", "goal_reached"} - unlocked):
        return []

    starting = user.starting_weight_kg
    goal = user.goal_weight_kg
    if starting is None:
        return []

    latest = await db.scalar(
        select(WeightEntry)
        .where(WeightEntry.user_id == user.id)
        .order_by(WeightEntry.recorded_on.desc(), WeightEntry.created_at.desc())
        .limit(1)
    )
    if latest is None:
        return []

    # Direction-aware: for a gain goal, "progress" is weight *added*. The
    # goal weight decides the direction; the enum is only the tiebreaker so a
    # maintain user with a goal weight still earns the weight badges.
    if goal is not None and goal != starting:
        losing = goal < starting
    elif user.goal in (WeightGoal.lose, WeightGoal.gain):
        losing = user.goal is WeightGoal.lose
    else:
        return []

    progress = (starting - latest.kg) if losing else (latest.kg - starting)
    total = abs(goal - starting) if goal is not None else None

    newly: list[Achievement] = []

    async def try_award(key: str, condition: bool) -> None:
        if condition and key not in unlocked:
            got = await award(db, user.id, key)
            if got is not None:
                newly.append(got)

    await try_award("first_kilo", progress >= 1.0)
    if total:
        await try_award("halfway", progress >= total / 2)
        await try_award("goal_reached", progress >= total)
    return newly


async def _maybe_award_legend(
    db: AsyncSession, user_id: uuid.UUID, *, any_new: bool
) -> list[Achievement]:
    """Legend = every other badge in the catalogue. Only worth checking on a
    request that just awarded something."""
    if not any_new:
        return []
    others = set(
        (await db.scalars(select(Achievement.key).where(Achievement.key != "legend"))).all()
    )
    if not others or not others <= await _unlocked_keys(db, user_id):
        return []
    got = await award(db, user_id, "legend")
    return [got] if got is not None else []
