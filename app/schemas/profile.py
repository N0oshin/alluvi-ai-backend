"""Profile, onboarding, and settings payloads."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import EmailStr, Field

from app.schemas.common import CamelModel


# --------------------------------------------------------------------------
# Onboarding / plan
# --------------------------------------------------------------------------


class PlanRequest(CamelModel):
    """The six-step questionnaire.

    Metric only. The design's Imperial/Metric toggle is a display concern —
    the client converts, the wire stays in cm/kg.
    """

    gender: str | None = None  # male | female | other
    activity_level: str | None = None  # low | moderate | high  (0-2 / 3-5 / 6+)
    height_cm: float = Field(default=170, gt=50, lt=280)
    weight_kg: float = Field(default=65, gt=20, lt=500)
    goal: str | None = None  # lose | maintain | gain
    desired_weight_kg: float = Field(default=58, gt=20, lt=500)
    birthday: date | None = None


class PlanOut(CamelModel):
    """Plan Ready (screen 07) needs the delta and the date, not just macros."""

    daily_calories: int
    protein_g: int
    carbs_g: int
    fats_g: int
    weight_delta_kg: float
    target_date: date | None
    plan_version: int
    is_override: bool = False
    computed_at: datetime


class NutritionGoalsOut(CamelModel):
    """Matches NutritionGoalsModel — all four are doubles in Dart."""

    calories: float
    protein: float
    carbs: float
    fats: float
    is_override: bool = False


class NutritionGoalsUpdate(CamelModel):
    calories: float = Field(gt=0, lt=20000)
    protein: float = Field(ge=0, lt=2000)
    carbs: float = Field(ge=0, lt=2000)
    fats: float = Field(ge=0, lt=2000)


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


class ProfileOut(CamelModel):
    """Matches ProfileModel. `caloriesLeft` is remaining, not consumed."""

    display_name: str
    username: str
    is_premium: bool
    calories_left: int
    calorie_goal: int
    streak_days: int
    apple_health_connected: bool
    last_synced_at: datetime | None


class ProfileSummaryOut(CamelModel):
    """The compact header on Settings (screen 12) in one call, so the client
    doesn't stitch three endpoints together to paint it."""

    display_name: str
    goal: str | None
    goal_label: str
    current_weight_kg: float | None
    goal_weight_kg: float | None
    daily_calories: int | None


class PersonalDetailsOut(CamelModel):
    name: str
    email: str
    gender: str
    height_cm: float
    birthday: date | None
    # True when the address just changed: a code has been emailed to it, and
    # the account stays locked out of login until `Auth/verifyCode` runs.
    email_verification_required: bool = False


class PersonalDetailsUpdate(CamelModel):
    name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    gender: str | None = None
    height_cm: float | None = Field(default=None, gt=50, lt=280)
    birthday: date | None = None


# --------------------------------------------------------------------------
# Weight
# --------------------------------------------------------------------------


class CurrentWeightOut(CamelModel):
    """Matches CurrentWeightModel. `estimatedGoalDate` is a pre-formatted
    string in the Dart model; the ISO date is sent alongside so the client can
    migrate to formatting it locally."""

    starting_weight_kg: float
    current_weight_kg: float
    goal_weight_kg: float
    estimated_goal_date: str
    target_date: date | None


class WeightUpdateRequest(CamelModel):
    current_weight_kg: float | None = Field(default=None, gt=20, lt=500)
    goal_weight_kg: float | None = Field(default=None, gt=20, lt=500)


class WeightEntryOut(CamelModel):
    date: date
    kg: float
    source: str


class HealthSample(CamelModel):
    """One HealthKit sample. `externalId` is the HealthKit UUID and is the
    de-duplication key — without it, re-syncing double-counts."""

    external_id: str
    kg: float
    recorded_on: date


class HealthSyncRequest(CamelModel):
    samples: list[HealthSample] = Field(default_factory=list, max_length=1000)


class HealthSyncOut(CamelModel):
    imported: int
    skipped: int
    last_synced_at: datetime


# --------------------------------------------------------------------------
# Settings & content
# --------------------------------------------------------------------------


class NotificationSettingsOut(CamelModel):
    meal_reminders: bool
    streak_reminder: bool
    weekly_report: bool
    trial_billing_alerts: bool
    tips_and_articles: bool
    quiet_start: str  # "HH:mm"
    quiet_end: str


class NotificationSettingsUpdate(CamelModel):
    meal_reminders: bool | None = None
    streak_reminder: bool | None = None
    weekly_report: bool | None = None
    trial_billing_alerts: bool | None = None
    tips_and_articles: bool | None = None
    quiet_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


class AchievementOut(CamelModel):
    """`iconKey` is a stable slug the client maps to an IconData."""

    id: uuid.UUID
    key: str
    label: str
    icon_key: str
    unlocked: bool


class AchievementsOut(CamelModel):
    unlocked: int
    total: int
    items: list[AchievementOut] = Field(default_factory=list)


class RingColorOut(CamelModel):
    """`colorToken` names a theme colour, not a hex — a raw hex would break
    dark mode."""

    title: str
    description: str
    value: float
    color_token: str


class InviteInfoOut(CamelModel):
    invite_code: str
    reward_title: str
    reward_subtitle: str
    steps: list[str] = Field(default_factory=list)


class LegalSectionOut(CamelModel):
    title: str
    body: str


class LegalDocumentOut(CamelModel):
    last_updated: str
    intro: str
    sections: list[LegalSectionOut] = Field(default_factory=list)
    footer_note: str


class FeedbackRequest(CamelModel):
    message: str = Field(min_length=1, max_length=5000)


class DeviceTokenRequest(CamelModel):
    token: str = Field(min_length=1, max_length=512)
