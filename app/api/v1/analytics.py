"""Analytics (Figma screen 11).

One call returns everything the screen needs: goal progress with a weight
series, BMI with its category, the logging streak, and this week's calorie
bars.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.v1.profile import compute_streak
from app.core.deps import CurrentUser, Db, LangDep
from app.core.i18n import Lang
from app.db.models import Meal, WeightEntry
from app.schemas.food import (
    AnalyticsOut,
    BmiOut,
    DayCaloriesOut,
    GoalProgressOut,
    StreakOut,
    WeightPointOut,
)
from app.services.plan import bmi, bmi_category_key

router = APIRouter(tags=["analytics"])

_RANGE_DAYS = {"90d": 90, "6m": 182, "1y": 365, "all": None}

_BMI_LABELS: dict[str, dict[Lang, str]] = {
    "underweight": {Lang.EN: "Underweight", Lang.AR: "نحافة"},
    "healthy": {Lang.EN: "Healthy", Lang.AR: "صحي"},
    "overweight": {Lang.EN: "Overweight", Lang.AR: "زيادة وزن"},
    "obese": {Lang.EN: "Obese", Lang.AR: "سمنة"},
}


@router.get("/analytics", response_model=AnalyticsOut)
async def analytics(
    user: CurrentUser,
    db: Db,
    lang: LangDep,
    # Named `time_range` internally: a parameter called `range` would shadow
    # the builtin for the whole function body.
    time_range: str = Query(
        default="90d", alias="range", pattern="^(90d|6m|1y|all)$"
    ),
) -> AnalyticsOut:
    today = datetime.now(UTC).date()

    # --- weight series ---
    window = _RANGE_DAYS[time_range]
    query = select(WeightEntry).where(WeightEntry.user_id == user.id)
    if window is not None:
        query = query.where(WeightEntry.recorded_on >= today - timedelta(days=window))
    entries = (
        await db.scalars(query.order_by(WeightEntry.recorded_on))
    ).all()

    series = [
        WeightPointOut(
            label=e.recorded_on.strftime("%b %d"), kg=e.kg, date=e.recorded_on
        )
        for e in entries
    ]

    current = entries[-1].kg if entries else user.starting_weight_kg
    first = entries[0].kg if entries else user.starting_weight_kg
    # Negative means weight lost over the window — the design renders it as
    # a green "▾ 2.0 kg".
    delta = round((current - first), 1) if (current and first) else 0.0

    # --- BMI ---
    bmi_out = None
    if current and user.height_cm:
        value = bmi(current, user.height_cm)
        if value is not None:
            key = bmi_category_key(value)
            bmi_out = BmiOut(
                value=value,
                category=_BMI_LABELS[key].get(lang, _BMI_LABELS[key][Lang.EN]),
                category_key=key,
            )

    # --- calories this week (Mon..Sun, matching the chart) ---
    week_start = today - timedelta(days=today.weekday())
    rows = (
        await db.execute(
            select(Meal.eaten_on, func.coalesce(func.sum(Meal.calories), 0))
            .where(
                Meal.user_id == user.id,
                Meal.eaten_on >= week_start,
                Meal.eaten_on <= week_start + timedelta(days=6),
            )
            .group_by(Meal.eaten_on)
        )
    ).all()
    by_day = {row[0]: float(row[1]) for row in rows}

    calories_week = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        logged = day in by_day
        calories_week.append(
            DayCaloriesOut(
                day_label=day.strftime("%a")[0],
                calories=by_day.get(day, 0.0),
                # A faded bar means "no data yet", not "zero calories" —
                # future days in the current week are inactive.
                is_active=logged,
            )
        )

    return AnalyticsOut(
        goal_progress=GoalProgressOut(
            current_weight_kg=current,
            starting_weight_kg=user.starting_weight_kg,
            goal_weight_kg=user.goal_weight_kg,
            delta_kg=delta,
            series=series,
        ),
        current_bmi=bmi_out,
        streak=StreakOut(days=await compute_streak(db, user.id, user.timezone)),
        calories_this_week=calories_week,
    )
