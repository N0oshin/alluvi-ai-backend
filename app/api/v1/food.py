"""Food scanning and the meal log.

Two separate calls by design (Figma screen 10):
  * `POST food/analyze` runs the AI and returns a throwaway result.
  * `POST meals` is the "Done" button — it commits the meal to the log.

"Fix Results" is a plain recapture on the client: it discards the analysis and
returns to the camera. There is deliberately no correction endpoint here.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentUser, Db, not_found
from app.core.errors import AppError
from app.db.models import (
    DetectedItem,
    FoodAnalysis,
    Meal,
    MealPhoto,
    MealType,
    NutritionPlan,
    OffProduct,
    PortionConfidence,
)
from app.services.nutrition.nutri_cal import (
    health_score_from_macros,
    log_scan,
    price_scan,
)
from app.services.nutrition.off_client import fetch_product
from app.services.nutrition.sanity import reconcile_kcal
from app.services.vision.gemini import PROMPT_VERSION, GeminiScanClient
from app.schemas.common import MessageResponse
from app.schemas.food import (
    DaySummaryOut,
    DetectedItemOut,
    FavoriteOut,
    FoodAnalysisOut,
    MealOut,
    SaveMealRequest,
    TextAnalyzeRequest,
    UpdateMealRequest,
    WeekDayOut,
)
from app.schemas.profile import AchievementOut
from app.services.achievements import on_meal_saved
from app.services.storage.local import build_key, get_storage, process_photo
from app.services.vision.base import (
    CONTAINERS,
    DetectedItemResult,
    VisionAnalysisError,
)
from app.services.vision.factory import get_vision_provider

router = APIRouter(tags=["food"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _meal_type_for(moment: datetime) -> MealType:
    hour = moment.astimezone(UTC).hour
    if hour < 11:
        return MealType.breakfast
    if hour < 16:
        return MealType.lunch
    if hour < 22:
        return MealType.dinner
    return MealType.snack


async def _active_plan(db: Db, user_id: uuid.UUID) -> NutritionPlan | None:
    return await db.scalar(
        select(NutritionPlan)
        .where(NutritionPlan.user_id == user_id, NutritionPlan.is_active.is_(True))
        .order_by(NutritionPlan.created_at.desc())
        .limit(1)
    )


def _progress(value: float, goal: float | None) -> float:
    """Share of the daily target, clamped to 0..1 for the progress bars."""
    if not goal:
        return 0.0
    return round(min(1.0, max(0.0, value / goal)), 3)


def _format_time(moment: datetime) -> str:
    """ "12:07" — 24h avoids the %-I directive, which is not portable to Windows."""
    return moment.astimezone(UTC).strftime("%H:%M")


# --------------------------------------------------------------------------
# Analyze
# --------------------------------------------------------------------------


@router.post("/food/analyze", response_model=FoodAnalysisOut)
async def analyze_food(
    user: CurrentUser,
    db: Db,
    image: UploadFile = File(...),
    container: str | None = Form(default=None),
) -> FoodAnalysisOut:
    raw = await image.read()
    if not raw:
        raise AppError("food.no_image", code="NO_IMAGE")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise AppError(
            "food.image_too_large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code="IMAGE_TOO_LARGE",
        )

    # Downscale + re-encode (which strips EXIF/GPS) before anything else sees it.
    try:
        processed, width, height = process_photo(raw)
    except ValueError:
        raise AppError("food.bad_image", code="BAD_IMAGE") from None

    photo = MealPhoto(
        user_id=user.id,
        storage_key="",  # set below, once the id exists
        mime_type="image/jpeg",
        size_bytes=len(processed),
        width=width,
        height=height,
    )
    db.add(photo)
    await db.flush()

    photo.storage_key = build_key(user.id, photo.id)
    storage = get_storage()
    await storage.save(processed, key=photo.storage_key, mime_type="image/jpeg")

    # An unrecognised value is dropped rather than rejected: the hint only
    # improves the estimate, so a client sending a label this build does not
    # know should still get an analysis back.
    hint = container.strip().lower() if container else None
    if hint not in CONTAINERS:
        hint = None

    provider = get_vision_provider()
    try:
        result = await provider.analyze(processed, "image/jpeg", container=hint)
    except VisionAnalysisError as exc:
        # Orphan the photo rather than leaving a dangling analysis row.
        await storage.delete(photo.storage_key)
        await db.delete(photo)
        await db.flush()
        raise AppError(exc.message_key, code="ANALYSIS_FAILED") from exc

    analysis = FoodAnalysis(
        user_id=user.id,
        photo_id=photo.id,
        name=result.name,
        calories_per_serving=result.calories_per_serving,
        protein_g_per_serving=result.protein_g_per_serving,
        carbs_g_per_serving=result.carbs_g_per_serving,
        fat_g_per_serving=result.fat_g_per_serving,
        health_score=result.health_score,
        health_score_max=result.health_score_max,
        estimated_portion_grams=result.estimated_portion_grams,
        portion_confidence=PortionConfidence(result.portion_confidence),
        scale_reference=result.scale_reference,
        container_hint=hint,
        provider=provider.name,
        model=result.model,
    )
    db.add(analysis)
    await db.flush()

    for item in result.detected_items:
        db.add(
            DetectedItem(
                analysis_id=analysis.id, label=item.label, cx=item.cx, cy=item.cy
            )
        )
    await db.flush()

    plan = await _active_plan(db, user.id)
    now = datetime.now(UTC)

    return FoodAnalysisOut(
        analysis_id=analysis.id,
        name=result.name,
        time_label=_format_time(now),
        meal_type_label=_meal_type_for(now).value.upper(),
        calories_per_serving=result.calories_per_serving,
        protein_grams_per_serving=result.protein_g_per_serving,
        carbs_grams_per_serving=result.carbs_g_per_serving,
        fat_grams_per_serving=result.fat_g_per_serving,
        protein_progress=_progress(
            result.protein_g_per_serving, plan.protein_g if plan else None
        ),
        carbs_progress=_progress(
            result.carbs_g_per_serving, plan.carbs_g if plan else None
        ),
        fat_progress=_progress(result.fat_g_per_serving, plan.fats_g if plan else None),
        health_score=result.health_score,
        health_score_max=result.health_score_max,
        estimated_portion_grams=result.estimated_portion_grams,
        portion_confidence=result.portion_confidence,
        scale_reference=result.scale_reference,
        image_url=storage.url_for(photo.storage_key),
        detected_items=[
            DetectedItemOut(
                label=i.label,
                cx=i.cx,
                cy=i.cy,
                grams=i.grams,
                confidence=i.confidence,
            )
            for i in result.detected_items
        ],
    )


# --------------------------------------------------------------------------
# Analyze: text + barcode routes (no photo)
# --------------------------------------------------------------------------


async def _respond_for_analysis(
    db: Db,
    user_id: uuid.UUID,
    analysis: FoodAnalysis,
    result_items: list,
    image_url: str | None,
) -> FoodAnalysisOut:
    """FoodAnalysisOut for a freshly persisted analysis (non-photo routes)."""
    plan = await _active_plan(db, user_id)
    now = datetime.now(UTC)
    return FoodAnalysisOut(
        analysis_id=analysis.id,
        name=analysis.name,
        time_label=_format_time(now),
        meal_type_label=_meal_type_for(now).value.upper(),
        calories_per_serving=analysis.calories_per_serving,
        protein_grams_per_serving=analysis.protein_g_per_serving,
        carbs_grams_per_serving=analysis.carbs_g_per_serving,
        fat_grams_per_serving=analysis.fat_g_per_serving,
        protein_progress=_progress(
            analysis.protein_g_per_serving, plan.protein_g if plan else None
        ),
        carbs_progress=_progress(
            analysis.carbs_g_per_serving, plan.carbs_g if plan else None
        ),
        fat_progress=_progress(
            analysis.fat_g_per_serving, plan.fats_g if plan else None
        ),
        health_score=analysis.health_score,
        health_score_max=analysis.health_score_max,
        estimated_portion_grams=analysis.estimated_portion_grams,
        portion_confidence=analysis.portion_confidence.value,
        scale_reference=analysis.scale_reference,
        image_url=image_url,
        detected_items=[
            DetectedItemOut(
                label=i.label,
                cx=i.cx,
                cy=i.cy,
                grams=i.grams,
                confidence=i.confidence,
            )
            for i in result_items
        ],
    )


@router.post("/food/analyze-text", response_model=FoodAnalysisOut)
async def analyze_food_text(
    payload: TextAnalyzeRequest, user: CurrentUser, db: Db
) -> FoodAnalysisOut:
    """Same pipeline as the photo route, minus the camera: the model reads
    the description, the database prices it."""
    hint = payload.container.strip().lower() if payload.container else None
    if hint not in CONTAINERS:
        hint = None

    started = time.perf_counter()
    try:
        client = GeminiScanClient()
        scan, meta = await client.scan(
            text_description=payload.description, container=hint
        )
    except VisionAnalysisError as exc:
        await log_scan(
            route="text",
            status="error",
            user_id=user.id,
            latency_ms=int((time.perf_counter() - started) * 1000),
            model_used=settings.GEMINI_MODEL,
            prompt_version=PROMPT_VERSION,
        )
        raise AppError(exc.message_key, code="ANALYSIS_FAILED") from exc

    if scan.not_food or not scan.items:
        await log_scan(route="text", status="not_food", user_id=user.id, meta=meta)
        raise AppError("food.not_food", code="ANALYSIS_FAILED")

    result, matched = await price_scan(scan, meta)
    await log_scan(
        route="text",
        status="ok",
        user_id=user.id,
        meta=meta,
        matched_foods=matched,
    )

    analysis = FoodAnalysis(
        user_id=user.id,
        photo_id=None,
        name=result.name,
        calories_per_serving=result.calories_per_serving,
        protein_g_per_serving=result.protein_g_per_serving,
        carbs_g_per_serving=result.carbs_g_per_serving,
        fat_g_per_serving=result.fat_g_per_serving,
        health_score=result.health_score,
        health_score_max=result.health_score_max,
        estimated_portion_grams=result.estimated_portion_grams,
        portion_confidence=PortionConfidence(result.portion_confidence),
        scale_reference=result.scale_reference,
        container_hint=hint,
        provider="pipeline",
        model=result.model,
    )
    db.add(analysis)
    await db.flush()
    for item in result.detected_items:
        db.add(
            DetectedItem(
                analysis_id=analysis.id, label=item.label, cx=item.cx, cy=item.cy
            )
        )
    await db.flush()

    return await _respond_for_analysis(
        db, user.id, analysis, result.detected_items, image_url=None
    )


@router.get("/food/barcode/{code}", response_model=FoodAnalysisOut)
async def analyze_barcode(code: str, user: CurrentUser, db: Db) -> FoodAnalysisOut:
    """No model call, ever. Cache-first: the off_products table starts
    empty; a first-ever scan fetches the product live from Open Food Facts
    and caches it, so every later scan is a pure local lookup."""
    code = code.strip()
    product = await db.scalar(select(OffProduct).where(OffProduct.barcode == code))
    if product is None:
        fetched = await fetch_product(code)
        if fetched is not None:
            product = OffProduct(**fetched)
            db.add(product)
            await db.flush()
    if product is None or product.kcal_100g is None:
        await log_scan(route="barcode", status="miss", user_id=user.id)
        raise AppError(
            "food.barcode_unknown",
            status_code=status.HTTP_404_NOT_FOUND,
            code="BARCODE_UNKNOWN",
        )

    grams = product.serving_grams or 100.0
    p100_p = product.protein_100g or 0.0
    p100_c = product.carbs_100g or 0.0
    p100_f = product.fat_100g or 0.0
    kcal_100 = reconcile_kcal(product.kcal_100g, p100_p, p100_c, p100_f)
    factor = grams / 100.0

    # Brand prefixes the name unless it's already in it ("Nutella Nutella").
    name = (
        f"{product.brand} {product.name}"
        if product.brand and product.brand.lower() not in product.name.lower()
        else product.name
    )
    analysis = FoodAnalysis(
        user_id=user.id,
        photo_id=None,
        name=name[:200],
        calories_per_serving=round(kcal_100 * factor),
        protein_g_per_serving=round(p100_p * factor),
        carbs_g_per_serving=round(p100_c * factor),
        fat_g_per_serving=round(p100_f * factor),
        health_score=health_score_from_macros(kcal_100, p100_p, p100_c, p100_f),
        health_score_max=10,
        estimated_portion_grams=round(grams),
        # A printed label beats any estimate; "high" only when the pack told
        # us the serving size, since 100 g is our guess, not theirs.
        portion_confidence=PortionConfidence.high
        if product.serving_grams
        else PortionConfidence.medium,
        scale_reference="label",
        provider="pipeline",
        model=None,
    )
    db.add(analysis)
    await db.flush()
    item = DetectedItem(analysis_id=analysis.id, label=product.name[:120])
    db.add(item)
    await db.flush()

    await log_scan(
        route="barcode",
        status="ok",
        user_id=user.id,
        matched_foods=[
            {
                "query": code,
                "source": "off",
                "matched": name,
                "score": 1.0,
                "grams": grams,
                "kcal": round(kcal_100 * factor, 1),
            }
        ],
    )

    return await _respond_for_analysis(
        db,
        user.id,
        analysis,
        [
            DetectedItemResult(
                label=product.name[:120],
                grams=round(grams),
                confidence=1.0 if product.serving_grams else 0.5,
            )
        ],
        image_url=None,
    )


# --------------------------------------------------------------------------
# Meal log
# --------------------------------------------------------------------------


def _macro_scale(
    analysis: FoodAnalysis, quantity: float, calories: int, *, edited: bool
) -> float:
    """How far to scale the per-serving macros for this meal.

    Without an edit that is just the quantity. With one it is derived from the
    calorie figure instead, because a user editing calories is correcting the
    portion, not the energy alone: protein and carbs are 4 kcal per gram and
    fat is 9, so halving the calories while leaving the macros untouched
    stores a row that contradicts itself — and every analytics total built on
    that row inherits the error.
    """
    if not edited or analysis.calories_per_serving <= 0:
        return quantity
    return calories / analysis.calories_per_serving


def _scaled_macros(analysis: FoodAnalysis, scale: float) -> tuple[int, int, int]:
    """Per-serving macros scaled and rounded to the whole grams the UI shows."""
    return (
        round(analysis.protein_g_per_serving * scale),
        round(analysis.carbs_g_per_serving * scale),
        round(analysis.fat_g_per_serving * scale),
    )


@router.post("/meals", response_model=MealOut, status_code=201)
async def save_meal(payload: SaveMealRequest, user: CurrentUser, db: Db) -> MealOut:
    """The "Done" button — commits an analysis to the log."""
    analysis = await db.scalar(
        select(FoodAnalysis).where(
            FoodAnalysis.id == payload.analysis_id, FoodAnalysis.user_id == user.id
        )
    )
    if analysis is None:
        raise not_found()

    # Snapped to 2dp so a float like 0.30000000000000004 never reaches the DB
    # or the client; the UI only ever sends quarter steps.
    quantity = round(payload.quantity, 2)
    calories = round(analysis.calories_per_serving * quantity)
    edited = False
    if payload.calories_override is not None:
        # The pencil icon on the calories card. Recorded as user-supplied so
        # it is never mistaken for an AI estimate.
        calories = payload.calories_override
        edited = True

    protein_g, carbs_g, fat_g = _scaled_macros(
        analysis, _macro_scale(analysis, quantity, calories, edited=edited)
    )

    eaten_at = payload.eaten_at or datetime.now(UTC)
    if eaten_at.tzinfo is None:
        eaten_at = eaten_at.replace(tzinfo=UTC)

    meal_type = _meal_type_for(eaten_at)
    if payload.meal_type:
        try:
            meal_type = MealType(payload.meal_type.lower())
        except ValueError:
            pass  # unknown label falls back to the time-of-day guess

    meal = Meal(
        user_id=user.id,
        photo_id=analysis.photo_id,
        analysis_id=analysis.id,
        title=analysis.name,
        meal_type=meal_type,
        quantity=quantity,
        calories=calories,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        health_score=analysis.health_score,
        calories_edited=edited,
        eaten_at=eaten_at,
        eaten_on=eaten_at.date(),
        is_favorite=payload.is_favorite,
    )
    db.add(meal)
    await db.flush()

    analysis.saved_meal_id = meal.id
    await db.flush()

    # Best-effort: a badge bug must never break saving (see the service).
    newly = await on_meal_saved(db, user, meal)

    response = await _meal_response(db, meal)
    response.newly_unlocked = [
        AchievementOut(
            id=a.id, key=a.key, label=a.label, icon_key=a.icon_key, unlocked=True
        )
        for a in newly
    ]
    return response


async def _meal_response(db: Db, meal: Meal) -> MealOut:
    image_url = ""
    if meal.photo_id:
        photo = await db.scalar(select(MealPhoto).where(MealPhoto.id == meal.photo_id))
        if photo:
            image_url = get_storage().url_for(photo.storage_key)

    return MealOut(
        id=meal.id,
        title=meal.title,
        image_url=image_url,
        calories=meal.calories,
        protein_grams=meal.protein_g,
        carbs_grams=meal.carbs_g,
        fat_grams=meal.fat_g,
        time=_format_time(meal.eaten_at),
        meal_type=meal.meal_type.value,
        health_score=meal.health_score,
        is_favorite=meal.is_favorite,
        eaten_at=meal.eaten_at,
    )


@router.get("/meals", response_model=list[MealOut])
async def list_meals(
    user: CurrentUser,
    db: Db,
    day: date | None = Query(
        default=None, description="Omit for full history across all days."
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[MealOut]:
    """Backs the "See all" screen: no `day` means every meal the user has
    logged, newest first, paged via limit/offset. Pass `day` to scope to one
    date (the pre-history behaviour, minus the implicit default of today)."""
    query = select(Meal).where(Meal.user_id == user.id)
    if day is not None:
        query = query.where(Meal.eaten_on == day)
    meals = (
        await db.scalars(
            query.order_by(Meal.eaten_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return [await _meal_response(db, m) for m in meals]


@router.patch("/meals/{meal_id}", response_model=MealOut)
async def update_meal(
    meal_id: uuid.UUID, payload: UpdateMealRequest, user: CurrentUser, db: Db
) -> MealOut:
    meal = await db.scalar(
        select(Meal).where(Meal.id == meal_id, Meal.user_id == user.id)
    )
    if meal is None:
        raise not_found()

    # Quantity and calories both rescale the macros, so they are resolved
    # together against the original analysis — applying them in sequence would
    # let a quantity change overwrite macros an edit had just corrected.
    if payload.quantity is not None or payload.calories is not None:
        analysis = None
        if meal.analysis_id:
            analysis = await db.scalar(
                select(FoodAnalysis).where(FoodAnalysis.id == meal.analysis_id)
            )

        if payload.quantity is not None:
            meal.quantity = round(payload.quantity, 2)

        if payload.calories is not None:
            meal.calories = payload.calories
            meal.calories_edited = True
        elif analysis is not None and not meal.calories_edited:
            # Quantity moved with no edit in play, so the AI figure still
            # governs; an earlier edit is left standing on purpose.
            meal.calories = round(analysis.calories_per_serving * meal.quantity)

        # Without the analysis row (it is SET NULL on delete) there is nothing
        # to rescale from, so the stored macros are left as they are.
        if analysis is not None:
            meal.protein_g, meal.carbs_g, meal.fat_g = _scaled_macros(
                analysis,
                _macro_scale(
                    analysis,
                    meal.quantity,
                    meal.calories,
                    edited=meal.calories_edited,
                ),
            )

    if payload.is_favorite is not None:
        meal.is_favorite = payload.is_favorite

    if payload.meal_type:
        try:
            meal.meal_type = MealType(payload.meal_type.lower())
        except ValueError:
            pass

    await db.flush()
    return await _meal_response(db, meal)


@router.delete("/meals/{meal_id}", response_model=MessageResponse)
async def delete_meal(meal_id: uuid.UUID, user: CurrentUser, db: Db) -> MessageResponse:
    meal = await db.scalar(
        select(Meal).where(Meal.id == meal_id, Meal.user_id == user.id)
    )
    if meal is None:
        raise not_found()

    # Deleting a meal deletes its photo object too — retention promise.
    if meal.photo_id:
        photo = await db.scalar(select(MealPhoto).where(MealPhoto.id == meal.photo_id))
        if photo is not None:
            await get_storage().delete(photo.storage_key)
            await db.delete(photo)

    await db.delete(meal)
    await db.flush()
    return MessageResponse(message="Meal deleted.")


# --------------------------------------------------------------------------
# Home dashboard
# --------------------------------------------------------------------------


@router.get("/home/summary", response_model=DaySummaryOut)
async def day_summary(
    user: CurrentUser,
    db: Db,
    day: date | None = Query(default=None),
) -> DaySummaryOut:
    target = day or datetime.now(UTC).date()

    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(Meal.calories), 0),
                func.coalesce(func.sum(Meal.protein_g), 0),
                func.coalesce(func.sum(Meal.carbs_g), 0),
                func.coalesce(func.sum(Meal.fat_g), 0),
            ).where(Meal.user_id == user.id, Meal.eaten_on == target)
        )
    ).one()
    consumed_cal, consumed_p, consumed_c, consumed_f = (int(v) for v in totals)

    plan = await _active_plan(db, user.id)
    cal_goal = plan.daily_calories if plan else 0
    p_goal = plan.protein_g if plan else 0
    c_goal = plan.carbs_g if plan else 0
    f_goal = plan.fats_g if plan else 0

    # Home "Recently eaten" shows only the latest 3;
    meals = (
        await db.scalars(
            select(Meal)
            .where(Meal.user_id == user.id, Meal.eaten_on == target)
            .order_by(Meal.eaten_at.desc())
            .limit(3)
        )
    ).all()

    return DaySummaryOut(
        date=target,
        calories_left=max(0, cal_goal - consumed_cal),
        calorie_goal=cal_goal,
        calories_consumed=consumed_cal,
        protein_left=max(0, p_goal - consumed_p),
        protein_goal=p_goal,
        carbs_left=max(0, c_goal - consumed_c),
        carbs_goal=c_goal,
        fat_left=max(0, f_goal - consumed_f),
        fat_goal=f_goal,
        meals=[await _meal_response(db, m) for m in meals],
    )


@router.get("/home/week", response_model=list[WeekDayOut])
async def week_strip(
    user: CurrentUser,
    db: Db,
    anchor: date | None = Query(default=None),
) -> list[WeekDayOut]:
    """The week calendar strip, Sunday through Saturday, with per-day
    has-data state so the client can dim empty days."""
    today = datetime.now(UTC).date()
    pivot = anchor or today
    # Python's weekday() is Monday=0; the strip starts on Sunday.
    start = pivot - timedelta(days=(pivot.weekday() + 1) % 7)
    days = [start + timedelta(days=i) for i in range(7)]

    rows = (
        await db.execute(
            select(Meal.eaten_on, func.count(Meal.id))
            .where(
                Meal.user_id == user.id,
                Meal.eaten_on >= days[0],
                Meal.eaten_on <= days[-1],
            )
            .group_by(Meal.eaten_on)
        )
    ).all()
    with_data = {row[0] for row in rows if row[1] > 0}

    return [
        WeekDayOut(
            date=d,
            day_label=d.strftime("%a")[0],
            day_number=d.day,
            has_data=d in with_data,
            is_today=d == today,
        )
        for d in days
    ]


# --------------------------------------------------------------------------
# Favorites
# --------------------------------------------------------------------------


@router.get("/favorites", response_model=list[FavoriteOut])
async def list_favorites(user: CurrentUser, db: Db) -> list[FavoriteOut]:
    """Favourites reference saved meals rather than being standalone records,
    which is why the empty state reads "Save a meal from your history"."""
    meals = (
        await db.scalars(
            select(Meal)
            .where(Meal.user_id == user.id, Meal.is_favorite.is_(True))
            .order_by(Meal.eaten_at.desc())
        )
    ).all()

    out: list[FavoriteOut] = []
    for meal in meals:
        image_url = None
        if meal.photo_id:
            photo = await db.scalar(
                select(MealPhoto).where(MealPhoto.id == meal.photo_id)
            )
            if photo:
                image_url = get_storage().url_for(photo.storage_key)
        out.append(
            FavoriteOut(
                id=meal.id,
                title=meal.title,
                kcal=meal.calories,
                meal_type=meal.meal_type.value.capitalize(),
                tag=meal.tag or _tag_for(meal),
                image_url=image_url,
                is_favorite=True,
            )
        )
    return out


def _tag_for(meal: Meal) -> str:
    """The chip on the favourites card, derived from the macro split."""
    total = meal.protein_g * 4 + meal.carbs_g * 4 + meal.fat_g * 9
    if total <= 0:
        return "BALANCED"
    protein_share = (meal.protein_g * 4) / total
    carb_share = (meal.carbs_g * 4) / total
    if protein_share >= 0.35:
        return "HIGH PROTEIN"
    if carb_share <= 0.25:
        return "LOW CARB"
    if meal.health_score >= 8:
        return "FIBER"
    return "BALANCED"


@router.post("/favorites/{meal_id}/toggle", response_model=FavoriteOut)
async def toggle_favorite(meal_id: uuid.UUID, user: CurrentUser, db: Db) -> FavoriteOut:
    meal = await db.scalar(
        select(Meal).where(Meal.id == meal_id, Meal.user_id == user.id)
    )
    if meal is None:
        raise not_found()

    meal.is_favorite = not meal.is_favorite
    await db.flush()

    image_url = None
    if meal.photo_id:
        photo = await db.scalar(select(MealPhoto).where(MealPhoto.id == meal.photo_id))
        if photo:
            image_url = get_storage().url_for(photo.storage_key)

    return FavoriteOut(
        id=meal.id,
        title=meal.title,
        kcal=meal.calories,
        meal_type=meal.meal_type.value.capitalize(),
        tag=meal.tag or _tag_for(meal),
        image_url=image_url,
        is_favorite=meal.is_favorite,
    )
