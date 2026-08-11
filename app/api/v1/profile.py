"""Profile, personal details, weight, Apple Health, and settings."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.deps import CurrentUser, Db, LangDep, not_found
from app.core.i18n import Lang
from app.db.models import (
    Achievement,
    Feedback,
    Gender,
    LegalDocument,
    Meal,
    NotificationSettings,
    UserAchievement,
    WeightEntry,
    WeightSource,
)
from app.api.v1.userinfo import active_plan, recalculate_plan
from app.schemas.common import MessageResponse
from app.schemas.profile import (
    AchievementOut,
    AchievementsOut,
    CurrentWeightOut,
    FeedbackRequest,
    HealthSyncOut,
    HealthSyncRequest,
    InviteInfoOut,
    LegalDocumentOut,
    LegalSectionOut,
    NotificationSettingsOut,
    NotificationSettingsUpdate,
    PersonalDetailsOut,
    PersonalDetailsUpdate,
    ProfileOut,
    ProfileSummaryOut,
    RingColorOut,
    WeightEntryOut,
    WeightUpdateRequest,
)

router = APIRouter(prefix="/profile", tags=["profile"])


async def _latest_weight(db: Db, user_id) -> WeightEntry | None:
    return await db.scalar(
        select(WeightEntry)
        .where(WeightEntry.user_id == user_id)
        .order_by(WeightEntry.recorded_on.desc(), WeightEntry.created_at.desc())
        .limit(1)
    )


async def compute_streak(db: Db, user_id) -> int:
    """Consecutive days ending today (or yesterday) with at least one meal."""
    rows = (
        await db.scalars(
            select(Meal.eaten_on)
            .where(Meal.user_id == user_id)
            .group_by(Meal.eaten_on)
            .order_by(Meal.eaten_on.desc())
            .limit(400)
        )
    ).all()
    if not rows:
        return 0

    logged = sorted(set(rows), reverse=True)
    today = datetime.now(UTC).date()

    # Today not logged yet is not a broken streak — start from yesterday.
    if logged[0] == today:
        cursor = today
    elif logged[0] == today - timedelta(days=1):
        cursor = today - timedelta(days=1)
    else:
        return 0

    streak = 0
    for day in logged:
        if day == cursor:
            streak += 1
            cursor -= timedelta(days=1)
        elif day < cursor:
            break
    return streak


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


@router.get("", response_model=ProfileOut)
async def get_profile(user: CurrentUser, db: Db) -> ProfileOut:
    plan = await active_plan(db, user.id)
    goal = plan.daily_calories if plan else 0

    today = datetime.now(UTC).date()
    consumed = int(
        await db.scalar(
            select(func.coalesce(func.sum(Meal.calories), 0)).where(
                Meal.user_id == user.id, Meal.eaten_on == today
            )
        )
        or 0
    )

    return ProfileOut(
        display_name=user.name or (user.username or "User"),
        username=user.username or "",
        is_premium=user.is_premium,
        calories_left=max(0, goal - consumed),
        calorie_goal=goal,
        streak_days=await compute_streak(db, user.id),
        apple_health_connected=user.apple_health_connected,
        last_synced_at=user.last_synced_at,
    )


@router.get("/summary", response_model=ProfileSummaryOut)
async def get_summary(user: CurrentUser, db: Db) -> ProfileSummaryOut:
    """Settings (screen 12) header in one call."""
    plan = await active_plan(db, user.id)
    latest = await _latest_weight(db, user.id)

    goal_label = ""
    if user.goal and plan:
        verb = {"lose": "Lose", "gain": "Gain", "maintain": "Maintain"}[user.goal.value]
        goal_label = (
            f"{verb} {plan.weight_delta_kg:g} kg"
            if user.goal.value != "maintain"
            else "Maintain weight"
        )

    return ProfileSummaryOut(
        display_name=user.name or "User",
        goal=user.goal.value if user.goal else None,
        goal_label=goal_label,
        current_weight_kg=latest.kg if latest else user.starting_weight_kg,
        goal_weight_kg=user.goal_weight_kg,
        daily_calories=plan.daily_calories if plan else None,
    )


@router.get("/personalDetails", response_model=PersonalDetailsOut)
async def get_personal_details(user: CurrentUser) -> PersonalDetailsOut:
    return PersonalDetailsOut(
        name=user.name or "",
        email=user.email,
        gender=(user.gender.value if user.gender else "other"),
        height_cm=user.height_cm or 170.0,
        birthday=user.birthday,
    )


@router.put("/personalDetails", response_model=PersonalDetailsOut)
async def update_personal_details(
    payload: PersonalDetailsUpdate, user: CurrentUser, db: Db
) -> PersonalDetailsOut:
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.email is not None:
        user.email = payload.email.lower()
    if payload.gender is not None:
        try:
            user.gender = Gender(payload.gender.lower())
        except ValueError:
            pass
    if payload.height_cm is not None:
        user.height_cm = payload.height_cm
    if payload.birthday is not None:
        user.birthday = payload.birthday

    await db.flush()
    # Height, birthday, and gender all feed BMR — the plan must follow.
    await recalculate_plan(db, user)

    return PersonalDetailsOut(
        name=user.name or "",
        email=user.email,
        gender=(user.gender.value if user.gender else "other"),
        height_cm=user.height_cm or 170.0,
        birthday=user.birthday,
    )


# --------------------------------------------------------------------------
# Weight
# --------------------------------------------------------------------------


@router.get("/weight", response_model=CurrentWeightOut)
async def get_weight(user: CurrentUser, db: Db) -> CurrentWeightOut:
    latest = await _latest_weight(db, user.id)
    plan = await active_plan(db, user.id)
    current = latest.kg if latest else (user.starting_weight_kg or 0.0)

    target_date = plan.target_date if plan else None
    return CurrentWeightOut(
        starting_weight_kg=user.starting_weight_kg or current,
        current_weight_kg=current,
        goal_weight_kg=user.goal_weight_kg or current,
        # Pre-formatted for the current Dart model; `targetDate` is the ISO
        # value the client should move to.
        estimated_goal_date=(
            f"Est. {target_date.strftime('%b %d')}" if target_date else ""
        ),
        target_date=target_date,
    )


@router.put("/weight", response_model=CurrentWeightOut)
async def update_weight(
    payload: WeightUpdateRequest, user: CurrentUser, db: Db
) -> CurrentWeightOut:
    """Logs a weight. Also reachable from the Analytics "Update" button."""
    if payload.goal_weight_kg is not None:
        user.goal_weight_kg = payload.goal_weight_kg

    if payload.current_weight_kg is not None:
        today = datetime.now(UTC).date()
        existing = await db.scalar(
            select(WeightEntry).where(
                WeightEntry.user_id == user.id,
                WeightEntry.recorded_on == today,
                WeightEntry.source == WeightSource.manual,
            )
        )
        if existing is not None:
            existing.kg = payload.current_weight_kg
        else:
            db.add(
                WeightEntry(
                    user_id=user.id,
                    kg=payload.current_weight_kg,
                    recorded_on=today,
                    source=WeightSource.manual,
                )
            )
        if user.starting_weight_kg is None:
            user.starting_weight_kg = payload.current_weight_kg

    await db.flush()
    await recalculate_plan(db, user)
    return await get_weight(user, db)


@router.get("/weightHistory", response_model=list[WeightEntryOut])
async def weight_history(
    user: CurrentUser,
    db: Db,
    limit: int = Query(default=365, ge=1, le=2000),
) -> list[WeightEntryOut]:
    entries = (
        await db.scalars(
            select(WeightEntry)
            .where(WeightEntry.user_id == user.id)
            .order_by(WeightEntry.recorded_on.desc())
            .limit(limit)
        )
    ).all()
    return [
        WeightEntryOut(date=e.recorded_on, kg=e.kg, source=e.source.value)
        for e in reversed(entries)
    ]


@router.post("/healthSync", response_model=HealthSyncOut)
async def health_sync(
    payload: HealthSyncRequest, user: CurrentUser, db: Db
) -> HealthSyncOut:
    """Apple Health ingest — read-only, one direction.

    The client reads HealthKit on-device and posts samples up; the backend
    never talks to Apple. Idempotent on the HealthKit sample UUID, because
    without that a re-sync would double-count every entry.
    """
    imported = 0
    skipped = 0

    for sample in payload.samples:
        existing = await db.scalar(
            select(WeightEntry).where(
                WeightEntry.user_id == user.id,
                WeightEntry.external_id == sample.external_id,
            )
        )
        if existing is not None:
            skipped += 1
            continue
        db.add(
            WeightEntry(
                user_id=user.id,
                kg=sample.kg,
                recorded_on=sample.recorded_on,
                source=WeightSource.apple_health,
                external_id=sample.external_id,
            )
        )
        imported += 1

    user.apple_health_connected = True
    user.last_synced_at = datetime.now(UTC)
    await db.flush()

    if imported:
        await recalculate_plan(db, user)

    return HealthSyncOut(
        imported=imported, skipped=skipped, last_synced_at=user.last_synced_at
    )


# --------------------------------------------------------------------------
# Settings & content
# --------------------------------------------------------------------------


@router.get("/notificationSettings", response_model=NotificationSettingsOut)
async def get_notification_settings(
    user: CurrentUser, db: Db
) -> NotificationSettingsOut:
    row = await db.scalar(
        select(NotificationSettings).where(NotificationSettings.user_id == user.id)
    )
    if row is None:
        row = NotificationSettings(user_id=user.id)
        db.add(row)
        await db.flush()
    return NotificationSettingsOut.model_validate(row)


@router.put("/notificationSettings", response_model=NotificationSettingsOut)
async def update_notification_settings(
    payload: NotificationSettingsUpdate, user: CurrentUser, db: Db
) -> NotificationSettingsOut:
    row = await db.scalar(
        select(NotificationSettings).where(NotificationSettings.user_id == user.id)
    )
    if row is None:
        row = NotificationSettings(user_id=user.id)
        db.add(row)
        await db.flush()

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    await db.flush()
    return NotificationSettingsOut.model_validate(row)


@router.get("/achievements", response_model=AchievementsOut)
async def get_achievements(user: CurrentUser, db: Db) -> AchievementsOut:
    """The header reads "4 of 12 unlocked", so counts ship alongside the list."""
    catalogue = (
        await db.scalars(select(Achievement).order_by(Achievement.sort_order))
    ).all()
    unlocked_ids = set(
        (
            await db.scalars(
                select(UserAchievement.achievement_id).where(
                    UserAchievement.user_id == user.id
                )
            )
        ).all()
    )

    items = [
        AchievementOut(
            id=a.id,
            key=a.key,
            label=a.label,
            icon_key=a.icon_key,
            unlocked=a.id in unlocked_ids,
        )
        for a in catalogue
    ]
    return AchievementsOut(
        unlocked=sum(1 for i in items if i.unlocked), total=len(items), items=items
    )


@router.get("/ringColors", response_model=list[RingColorOut])
async def ring_colors(user: CurrentUser, db: Db) -> list[RingColorOut]:
    """Ring Colors Explained (screen 34).

    `colorToken` names a theme colour rather than a hex value, so the rings
    stay theme-aware in dark mode.
    """
    plan = await active_plan(db, user.id)
    today = datetime.now(UTC).date()

    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(Meal.calories), 0),
                func.coalesce(func.sum(Meal.protein_g), 0),
                func.coalesce(func.sum(Meal.carbs_g), 0),
                func.coalesce(func.sum(Meal.fat_g), 0),
                func.coalesce(func.avg(Meal.health_score), 0),
            ).where(Meal.user_id == user.id, Meal.eaten_on == today)
        )
    ).one()
    cal, protein, carbs, fat, health = totals

    def ratio(value: float, goal: float | None) -> float:
        return round(min(1.0, value / goal), 3) if goal else 0.0

    return [
        RingColorOut(
            title="Calories",
            description="Energy remaining from your daily budget",
            value=ratio(float(cal), plan.daily_calories if plan else None),
            color_token="primary",
        ),
        RingColorOut(
            title="Protein",
            description="Builds muscle — aim to close it every day",
            value=ratio(float(protein), plan.protein_g if plan else None),
            color_token="protein",
        ),
        RingColorOut(
            title="Carbs",
            description="Fuels your workouts and your brain",
            value=ratio(float(carbs), plan.carbs_g if plan else None),
            color_token="carbs",
        ),
        RingColorOut(
            title="Fats",
            description="Healthy fats support hormones",
            value=ratio(float(fat), plan.fats_g if plan else None),
            color_token="fats",
        ),
        RingColorOut(
            title="Health score",
            description="Overall quality of what you logged",
            value=round(float(health) / 10.0, 3),
            color_token="success",
        ),
    ]


@router.get("/inviteInfo", response_model=InviteInfoOut)
async def invite_info(user: CurrentUser, db: Db) -> InviteInfoOut:
    """Static copy only.

    No referral reward is granted, tracked, or paid out — the feature is
    deferred, and the design contradicts itself on whether the reward is $10
    cash or one month of Premium.
    """
    if not user.invite_code:
        import uuid as _uuid

        user.invite_code = _uuid.uuid4().hex[:8].upper()
        await db.flush()

    return InviteInfoOut(
        invite_code=user.invite_code,
        reward_title="Give 1 month, get 1 month",
        reward_subtitle="You both get Premium free when they join",
        steps=[
            "Share your invite link",
            "Your friend joins Alluvi AI",
            "You both get 1 month Premium",
        ],
    )


@router.post("/feedback", response_model=MessageResponse)
async def submit_feedback(
    payload: FeedbackRequest, user: CurrentUser, db: Db
) -> MessageResponse:
    db.add(Feedback(user_id=user.id, message=payload.message))
    await db.flush()
    return MessageResponse(message="Thanks — we've logged your request.")


# --------------------------------------------------------------------------
# Legal content (unauthenticated is fine, but kept behind auth for symmetry)
# --------------------------------------------------------------------------

legal_router = APIRouter(prefix="/legal", tags=["legal"])


async def _legal(db: Db, slug: str, lang: Lang) -> LegalDocumentOut:
    doc = await db.scalar(
        select(LegalDocument).where(
            LegalDocument.slug == slug, LegalDocument.lang == lang.value
        )
    )
    if doc is None:
        doc = await db.scalar(
            select(LegalDocument).where(
                LegalDocument.slug == slug, LegalDocument.lang == "en"
            )
        )
    if doc is None:
        raise not_found()

    sections = [LegalSectionOut(**s) for s in json.loads(doc.body)]
    return LegalDocumentOut(
        last_updated=doc.last_updated.strftime("%B %d, %Y"),
        intro=doc.intro,
        sections=sections,
        footer_note=doc.footer_note,
    )


@legal_router.get("/terms", response_model=LegalDocumentOut)
async def terms(db: Db, lang: LangDep) -> LegalDocumentOut:
    return await _legal(db, "terms", lang)


@legal_router.get("/privacy", response_model=LegalDocumentOut)
async def privacy(db: Db, lang: LangDep) -> LegalDocumentOut:
    return await _legal(db, "privacy", lang)
