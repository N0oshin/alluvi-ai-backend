"""Export PDF Summary Report (Profile → Support & legal).
it answers the web request, gathers data from the database, fills the dataclasses, and calls the drawing function"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.v1.profile import _latest_weight
from app.core.deps import CurrentUser, Db
from app.core.timeutil import ensure_utc, local_today, user_tz, utcnow
from app.db.models import Meal
from app.services.plan import bmi, bmi_category_key
from app.services.report.pdf import ReportData, ReportDay, ReportMeal, build_summary_pdf

router = APIRouter(prefix="/profile", tags=["report"])

# The report is English-only for now; bilingual output needs a shaped Arabic
# font and is deferred with it.
_BMI_LABELS_EN = {
    "underweight": "Underweight",
    "healthy": "Healthy",
    "overweight": "Overweight",
    "obese": "Obese",
}

REPORT_DAYS = 7


@router.get("/export/pdf")
async def export_summary_pdf(user: CurrentUser, db: Db) -> Response:
    tz = user.timezone
    range_end = local_today(tz)
    range_start = range_end - timedelta(days=REPORT_DAYS - 1)

    meals = (
        await db.scalars(
            select(Meal)
            .where(
                Meal.user_id == user.id,
                Meal.eaten_on >= range_start,
                Meal.eaten_on <= range_end,
            )
            .order_by(Meal.eaten_on, Meal.eaten_at)
        )
    ).all()

    by_day: dict = {
        range_start + timedelta(days=i): ReportDay(day=range_start + timedelta(days=i))
        for i in range(REPORT_DAYS)
    }
    for meal in meals:
        by_day[meal.eaten_on].meals.append(
            ReportMeal(
                time=ensure_utc(meal.eaten_at)
                .astimezone(user_tz(tz))
                .strftime("%H:%M"),
                title=meal.title,
                calories=meal.calories,
                protein_g=meal.protein_g,
                carbs_g=meal.carbs_g,
                fat_g=meal.fat_g,
            )
        )

    latest = await _latest_weight(db, user.id)
    current = latest.kg if latest else user.starting_weight_kg

    bmi_value = bmi_category = None
    if current and user.height_cm:
        bmi_value = bmi(current, user.height_cm)
        if bmi_value is not None:
            bmi_category = _BMI_LABELS_EN[bmi_category_key(bmi_value)]

    data = ReportData(
        display_name=user.name or "User",
        range_start=range_start,
        range_end=range_end,
        generated_at=utcnow().astimezone(user_tz(tz)).strftime("%b %d, %Y %H:%M"),
        starting_weight_kg=user.starting_weight_kg,
        current_weight_kg=current,
        goal_weight_kg=user.goal_weight_kg,
        bmi_value=bmi_value,
        bmi_category=bmi_category,
        days=list(by_day.values()),
    )

    # PDF drawing is synchronous CPU work — keep it off the event loop.
    pdf_bytes = await asyncio.to_thread(build_summary_pdf, data)

    filename = f"alluvi-ai-summary-{range_end.isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
