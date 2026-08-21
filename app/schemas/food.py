"""Food scan, meal log, and analytics payloads.

Field names come from the Dart models verbatim — MealNutritionModel,
MealEntryModel, DayCaloriesModel, WeightPointModel, FavoriteModel.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.profile import AchievementOut


class DetectedItemOut(CamelModel):
    """Drives the labelled callouts on the camera preview.

    The client converts (cx, cy) to `Alignment(2*cx - 1, 2*cy - 1)`.
    """

    label: str
    cx: float
    cy: float


class FoodAnalysisOut(CamelModel):
    """Response for POST /food/analyze — matches MealNutritionModel.

    Per-serving only: `FoodResultCubit` multiplies by the quantity stepper.
    """

    analysis_id: uuid.UUID
    name: str
    time_label: str
    meal_type_label: str
    calories_per_serving: int
    protein_grams_per_serving: int
    carbs_grams_per_serving: int
    fat_grams_per_serving: int
    # 0..1 share of the user's daily target, computed server-side from
    # their active plan.
    protein_progress: float
    carbs_progress: float
    fat_progress: float
    health_score: int
    health_score_max: int = 10
    # The portion the estimate assumes. Shown so the user can check the one
    # assumption that drives the calorie figure; `low` confidence is the
    # client's cue to ask them to confirm rather than accept it silently.
    estimated_portion_grams: int | None = None
    portion_confidence: str = "medium"
    # What the portion was calibrated against, e.g. "fork". Null means nothing
    # of known size was in frame — the cue to ask for one on the next shot.
    scale_reference: str | None = None
    image_url: str | None = None
    detected_items: list[DetectedItemOut] = Field(default_factory=list)


class SaveMealRequest(CamelModel):
    """The "Done" button. Analysis and saving are two separate calls."""

    analysis_id: uuid.UUID
    # Fractional so "I ate half of it" is expressible — the commonest
    # correction there is. The UI offers 0.25 steps; the bounds here only
    # keep absurd values out, they do not enforce the step.
    quantity: float = Field(default=1.0, gt=0, le=99)
    meal_type: str | None = None
    # Set when the user edited the number via the pencil icon.
    calories_override: int | None = Field(default=None, ge=0, le=20000)
    is_favorite: bool = False
    eaten_at: datetime | None = None


class MealOut(CamelModel):
    """Matches MealEntryModel (home "Recently eaten")."""

    id: uuid.UUID
    title: str
    image_url: str = ""
    calories: int
    protein_grams: int
    carbs_grams: int
    fat_grams: int
    time: str
    meal_type: str
    health_score: int
    is_favorite: bool
    eaten_at: datetime
    newly_unlocked: list[AchievementOut] = Field(default_factory=list)


class UpdateMealRequest(CamelModel):
    quantity: float | None = Field(default=None, gt=0, le=99)
    calories: int | None = Field(default=None, ge=0, le=20000)
    is_favorite: bool | None = None
    meal_type: str | None = None


class MacroProgress(CamelModel):
    label: str
    remaining: int
    goal: int
    progress: float


class DaySummaryOut(CamelModel):
    """Home dashboard. Every macro is *remaining* against the daily goal, not
    consumed — the design labels them "Protein left", "Carbs left", "Fat left".
    """

    date: date
    calories_left: int
    calorie_goal: int
    calories_consumed: int
    protein_left: int
    protein_goal: int
    carbs_left: int
    carbs_goal: int
    fat_left: int
    fat_goal: int
    meals: list[MealOut] = Field(default_factory=list)


class WeekDayOut(CamelModel):
    """One cell of the week calendar strip."""

    date: date
    day_label: str
    day_number: int
    has_data: bool
    is_today: bool


class FavoriteOut(CamelModel):
    """Matches FavoriteModel. `imageAsset` is a bundled client asset — the
    backend only ever populates `imageUrl`."""

    id: uuid.UUID
    title: str
    kcal: int
    meal_type: str
    tag: str
    image_url: str | None = None
    is_favorite: bool = True


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------


class DayCaloriesOut(CamelModel):
    """`isActive: false` renders as a faded bar — it means "no data yet",
    not "zero calories"."""

    day_label: str
    calories: float
    is_active: bool = True


class WeightPointOut(CamelModel):
    label: str
    kg: float
    date: date


class GoalProgressOut(CamelModel):
    current_weight_kg: float | None
    starting_weight_kg: float | None
    goal_weight_kg: float | None
    delta_kg: float
    series: list[WeightPointOut] = Field(default_factory=list)


class BmiOut(CamelModel):
    value: float
    # Localised label for display, plus a stable key so the client owns the
    # pill colour rather than parsing the label.
    category: str
    category_key: str


class StreakOut(CamelModel):
    days: int


class AnalyticsOut(CamelModel):
    goal_progress: GoalProgressOut
    current_bmi: BmiOut | None
    streak: StreakOut
    calories_this_week: list[DayCaloriesOut] = Field(default_factory=list)
