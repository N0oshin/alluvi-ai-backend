"""Onboarding questionnaire and the daily plan.

Plan computation lives server-side and is persisted with both its inputs and
its outputs, so a stored plan can always be explained after the fact and
recalculated when the inputs change.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update

from fastapi import APIRouter

from app.core.deps import CurrentUser, Db
from app.core.errors import AppError
from app.db.models import (
    ActivityLevel,
    Gender,
    NutritionPlan,
    User,
    WeightEntry,
    WeightGoal,
    WeightSource,
)
from app.schemas.profile import (
    NutritionGoalsOut,
    NutritionGoalsUpdate,
    PlanOut,
    PlanRequest,
)
from app.services.plan import PLAN_VERSION, compute_plan

router = APIRouter(tags=["plan"])


def _enum_or_none[T](enum_cls: type[T], raw: str | None) -> T | None:
    if not raw:
        return None
    try:
        return enum_cls(raw.lower())
    except ValueError:
        return None


async def _deactivate_plans(db: Db, user_id) -> None:
    await db.execute(
        update(NutritionPlan)
        .where(NutritionPlan.user_id == user_id, NutritionPlan.is_active.is_(True))
        .values(is_active=False)
    )


async def active_plan(db: Db, user_id) -> NutritionPlan | None:
    return await db.scalar(
        select(NutritionPlan)
        .where(NutritionPlan.user_id == user_id, NutritionPlan.is_active.is_(True))
        .order_by(NutritionPlan.created_at.desc())
        .limit(1)
    )


async def recalculate_plan(db: Db, user: User) -> NutritionPlan | None:
    """Recompute from the user's stored inputs.

    Called when weight, goal, or personal details change. A plan the user has
    overridden on Adjust Goals is left alone — their edit wins until they ask
    for a fresh calculation.
    """
    current = await active_plan(db, user.id)
    if current is not None and current.is_override:
        return current

    if user.height_cm is None or user.starting_weight_kg is None:
        return current

    latest = await db.scalar(
        select(WeightEntry)
        .where(WeightEntry.user_id == user.id)
        .order_by(WeightEntry.recorded_on.desc(), WeightEntry.created_at.desc())
        .limit(1)
    )
    weight = latest.kg if latest else user.starting_weight_kg

    result = compute_plan(
        gender=user.gender,
        activity_level=user.activity_level,
        height_cm=user.height_cm,
        weight_kg=weight,
        goal=user.goal,
        desired_weight_kg=user.goal_weight_kg or weight,
        birthday=user.birthday,
    )

    await _deactivate_plans(db, user.id)
    plan = NutritionPlan(
        user_id=user.id,
        daily_calories=result.daily_calories,
        protein_g=result.protein_g,
        carbs_g=result.carbs_g,
        fats_g=result.fats_g,
        weight_delta_kg=result.weight_delta_kg,
        target_date=result.target_date,
        input_gender=user.gender,
        input_activity=user.activity_level,
        input_height_cm=user.height_cm,
        input_weight_kg=weight,
        input_desired_weight_kg=user.goal_weight_kg,
        input_goal=user.goal,
        plan_version=PLAN_VERSION,
        is_active=True,
    )
    db.add(plan)
    await db.flush()
    return plan


@router.post("/userinfo/plan", response_model=PlanOut)
async def create_plan(payload: PlanRequest, user: CurrentUser, db: Db) -> PlanOut:
    """Finish onboarding: store the answers, compute the plan, persist both.

    Returns in one call — the calculation is arithmetic, so the "Building your
    plan…" progress screen stays client-side theatre rather than a poll loop.
    """
    gender = _enum_or_none(Gender, payload.gender)
    activity = _enum_or_none(ActivityLevel, payload.activity_level)
    goal = _enum_or_none(WeightGoal, payload.goal)

    user.gender = gender or user.gender
    user.activity_level = activity or user.activity_level
    user.goal = goal or user.goal
    user.height_cm = payload.height_cm
    user.starting_weight_kg = payload.weight_kg
    user.goal_weight_kg = payload.desired_weight_kg
    if payload.birthday:
        user.birthday = payload.birthday

    result = compute_plan(
        gender=user.gender,
        activity_level=user.activity_level,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        goal=user.goal,
        desired_weight_kg=payload.desired_weight_kg,
        birthday=user.birthday,
    )

    await _deactivate_plans(db, user.id)
    plan = NutritionPlan(
        user_id=user.id,
        daily_calories=result.daily_calories,
        protein_g=result.protein_g,
        carbs_g=result.carbs_g,
        fats_g=result.fats_g,
        weight_delta_kg=result.weight_delta_kg,
        target_date=result.target_date,
        input_gender=user.gender,
        input_activity=user.activity_level,
        input_height_cm=payload.height_cm,
        input_weight_kg=payload.weight_kg,
        input_desired_weight_kg=payload.desired_weight_kg,
        input_age=None,
        input_goal=user.goal,
        plan_version=PLAN_VERSION,
        is_active=True,
    )
    db.add(plan)
    await db.flush()

    # Seed the weight series so goal progress has a starting point.
    today = datetime.now(UTC).date()
    exists = await db.scalar(
        select(WeightEntry).where(
            WeightEntry.user_id == user.id, WeightEntry.recorded_on == today
        )
    )
    if exists is None:
        db.add(
            WeightEntry(
                user_id=user.id,
                kg=payload.weight_kg,
                recorded_on=today,
                source=WeightSource.onboarding,
            )
        )
        await db.flush()

    return _plan_out(plan)


def _plan_out(plan: NutritionPlan) -> PlanOut:
    return PlanOut(
        daily_calories=plan.daily_calories,
        protein_g=plan.protein_g,
        carbs_g=plan.carbs_g,
        fats_g=plan.fats_g,
        weight_delta_kg=plan.weight_delta_kg,
        target_date=plan.target_date,
        plan_version=plan.plan_version,
        is_override=plan.is_override,
        computed_at=plan.created_at,
    )


@router.get("/userinfo/plan", response_model=PlanOut)
async def get_plan(user: CurrentUser, db: Db) -> PlanOut:
    plan = await active_plan(db, user.id)
    if plan is None:
        raise AppError("profile.plan_missing", status_code=404, code="NO_PLAN")
    return _plan_out(plan)


@router.get("/profile/nutritionGoals", response_model=NutritionGoalsOut)
async def get_goals(user: CurrentUser, db: Db) -> NutritionGoalsOut:
    plan = await active_plan(db, user.id)
    if plan is None:
        raise AppError("profile.plan_missing", status_code=404, code="NO_PLAN")
    return NutritionGoalsOut(
        calories=float(plan.daily_calories),
        protein=float(plan.protein_g),
        carbs=float(plan.carbs_g),
        fats=float(plan.fats_g),
        is_override=plan.is_override,
    )


@router.put("/profile/nutritionGoals", response_model=NutritionGoalsOut)
async def update_goals(
    payload: NutritionGoalsUpdate, user: CurrentUser, db: Db
) -> NutritionGoalsOut:
    """Adjust Goals.

    The new plan is flagged `is_override`, which stops a later automatic
    recalculation from silently discarding the user's edit.
    """
    previous = await active_plan(db, user.id)
    await _deactivate_plans(db, user.id)

    plan = NutritionPlan(
        user_id=user.id,
        daily_calories=round(payload.calories),
        protein_g=round(payload.protein),
        carbs_g=round(payload.carbs),
        fats_g=round(payload.fats),
        weight_delta_kg=previous.weight_delta_kg if previous else 0.0,
        target_date=previous.target_date if previous else None,
        input_gender=user.gender,
        input_activity=user.activity_level,
        input_height_cm=user.height_cm,
        input_weight_kg=previous.input_weight_kg if previous else None,
        input_desired_weight_kg=user.goal_weight_kg,
        input_goal=user.goal,
        plan_version=PLAN_VERSION,
        is_override=True,
        is_active=True,
    )
    db.add(plan)
    await db.flush()

    return NutritionGoalsOut(
        calories=float(plan.daily_calories),
        protein=float(plan.protein_g),
        carbs=float(plan.carbs_g),
        fats=float(plan.fats_g),
        is_override=True,
    )
