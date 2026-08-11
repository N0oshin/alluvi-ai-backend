"""Daily nutrition plan calculation.

Ported from the Flutter client's `UserInfoService._calculatePlan()` so existing
users see the same numbers, then corrected in one place: the client hardcodes
an age of 30 because onboarding never asks for a birthday. Here the real age is
used whenever it is known, and `PLAN_VERSION` records which formula produced a
stored plan.

Bump PLAN_VERSION whenever the formula changes, so an old row is never
reinterpreted under new rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.db.models import ActivityLevel, Gender, WeightGoal

PLAN_VERSION = 1

# Fallback when the birthday is unknown. Same value the client used, kept so
# results don't shift for users who onboarded before we collected a birthday.
ASSUMED_AGE = 30

_ACTIVITY_MULTIPLIER: dict[ActivityLevel, float] = {
    ActivityLevel.low: 1.2,  # 0-2 workouts/week
    ActivityLevel.moderate: 1.375,  # 3-5
    ActivityLevel.high: 1.55,  # 6+
}

_CALORIE_FLOOR = 1200
_CALORIE_CEILING = 4000
_KG_PER_WEEK = 0.5  # the rate the goal date assumes
_MAX_WEEKS = 104


@dataclass(slots=True)
class PlanResult:
    daily_calories: int
    protein_g: int
    carbs_g: int
    fats_g: int
    weight_delta_kg: float
    target_date: date | None
    plan_version: int = PLAN_VERSION


def age_from_birthday(birthday: date | None, *, today: date | None = None) -> int:
    if birthday is None:
        return ASSUMED_AGE
    today = today or date.today()
    years = today.year - birthday.year
    if (today.month, today.day) < (birthday.month, birthday.day):
        years -= 1
    return max(13, min(100, years))


def compute_plan(
    *,
    gender: Gender | None,
    activity_level: ActivityLevel | None,
    height_cm: float,
    weight_kg: float,
    goal: WeightGoal | None,
    desired_weight_kg: float,
    birthday: date | None = None,
    today: date | None = None,
) -> PlanResult:
    today = today or date.today()
    age = age_from_birthday(birthday, today=today)

    # Mifflin-St Jeor basal metabolic rate.
    is_female = gender == Gender.female
    bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + (-161 if is_female else 5)

    multiplier = _ACTIVITY_MULTIPLIER.get(
        activity_level or ActivityLevel.moderate, 1.375
    )
    calories = bmr * multiplier

    if goal == WeightGoal.lose:
        calories -= 500
    elif goal == WeightGoal.gain:
        calories += 300

    calories = max(_CALORIE_FLOOR, min(_CALORIE_CEILING, calories))

    protein_g = round(weight_kg * 2)
    fats_g = round(calories * 0.25 / 9)
    remaining = calories - (protein_g * 4) - (fats_g * 9)
    carbs_g = max(0, round(remaining / 4))

    delta_kg = abs(weight_kg - desired_weight_kg)
    if goal == WeightGoal.maintain or delta_kg < 0.1:
        target_date = None
    else:
        weeks = max(1, min(_MAX_WEEKS, -(-delta_kg // _KG_PER_WEEK)))
        target_date = today + timedelta(weeks=int(weeks))

    return PlanResult(
        daily_calories=round(calories),
        protein_g=protein_g,
        carbs_g=carbs_g,
        fats_g=fats_g,
        weight_delta_kg=round(delta_kg, 1),
        target_date=target_date,
    )


def bmi(weight_kg: float, height_cm: float) -> float | None:
    if height_cm <= 0:
        return None
    metres = height_cm / 100.0
    return round(weight_kg / (metres * metres), 1)


def bmi_category_key(value: float) -> str:
    """Stable key, not a display string — the client owns the pill colour."""
    if value < 18.5:
        return "underweight"
    if value < 25:
        return "healthy"
    if value < 30:
        return "overweight"
    return "obese"
