"""Database schema.

Naming note: columns use snake_case; the API layer converts to the camelCase
field names the Flutter models expect. The Dart models are the contract, so
where a name looks odd here it is because the client already ships it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

# JSONB on Postgres, plain JSON elsewhere — the SQLite test schema is built
# with create_all, so every type here must have a portable fallback.
PortableJSON = JSON().with_variant(JSONB(), "postgresql")

# --------------------------------------------------------------------------
# Enums — wire values match the Dart enums in UserInfoModel exactly.
# --------------------------------------------------------------------------


class Gender(str, enum.Enum):
    male = "male"
    female = "female"
    other = "other"


class ActivityLevel(str, enum.Enum):
    """Figma step 2 asks "workouts per week": 0-2 / 3-5 / 6+."""

    low = "low"
    moderate = "moderate"
    high = "high"


class WeightGoal(str, enum.Enum):
    lose = "lose"
    maintain = "maintain"
    gain = "gain"


class AuthProvider(str, enum.Enum):
    email = "email"
    apple = "apple"
    google = "google"


class MealType(str, enum.Enum):
    breakfast = "breakfast"
    lunch = "lunch"
    dinner = "dinner"
    snack = "snack"


class PortionConfidence(str, enum.Enum):
    """How well the photo pinned down the portion weight. Drives whether the
    client shows the estimate quietly or asks the user to confirm it."""

    low = "low"
    medium = "medium"
    high = "high"


class WeightSource(str, enum.Enum):
    """Provenance matters: a Health-synced weight must be distinguishable
    from one the user typed on the Update button."""

    manual = "manual"
    apple_health = "apple_health"
    onboarding = "onboarding"


class OtpPurpose(str, enum.Enum):
    verify_email = "verify_email"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Null for social-only accounts that never set a password.
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    name: Mapped[str] = mapped_column(String(120), default="")
    username: Mapped[str | None] = mapped_column(String(60), unique=True, default=None)

    provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, name="auth_provider"), default=AuthProvider.email
    )
    provider_subject: Mapped[str | None] = mapped_column(
        String(255), index=True, default=None
    )

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)

    # Personal details (screen 21-26 / PersonalDetailsModel)
    gender: Mapped[Gender | None] = mapped_column(
        Enum(Gender, name="gender"), default=None
    )
    birthday: Mapped[date | None] = mapped_column(Date, default=None)
    height_cm: Mapped[float | None] = mapped_column(Float, default=None)

    # Goal inputs (onboarding steps 4-5)
    goal: Mapped[WeightGoal | None] = mapped_column(
        Enum(WeightGoal, name="weight_goal"), default=None
    )
    activity_level: Mapped[ActivityLevel | None] = mapped_column(
        Enum(ActivityLevel, name="activity_level"), default=None
    )
    starting_weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    goal_weight_kg: Mapped[float | None] = mapped_column(Float, default=None)

    # Apple Health (read-only ingest)
    apple_health_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    invite_code: Mapped[str | None] = mapped_column(String(32), unique=True, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    plans: Mapped[list[NutritionPlan]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    meals: Mapped[list[Meal]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    weights: Mapped[list[WeightEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(UUIDPrimaryKey, Timestamps, Base):
    """One row per issued refresh token.

    `family_id` groups every token descended from a single sign-in. Rotation
    consumes a row and issues its successor in the same family; presenting a
    token that is already consumed means it leaked, so the whole family is
    revoked at once.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True, default=uuid.uuid4)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class OtpCode(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "otp_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[OtpPurpose] = mapped_column(
        Enum(OtpPurpose, name="otp_purpose"), default=OtpPurpose.verify_email
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class PasswordResetToken(UUIDPrimaryKey, Timestamps, Base):
    """Password recovery is a *link*, not a code (Figma screen 16)."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class DeviceToken(UUIDPrimaryKey, Timestamps, Base):
    """FCM (Android) or APNs (iOS) push token.

    `user_id` is nullable because `UserNotification/addAnonymousToken` registers
    a token before sign-in — the one genuinely unauthenticated call.
    """

    __tablename__ = "device_tokens"

    token: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, default=None
    )


# --------------------------------------------------------------------------
# Plan & goals
# --------------------------------------------------------------------------


class NutritionPlan(UUIDPrimaryKey, Timestamps, Base):
    """A computed daily target, plus the inputs that produced it.

    `is_override` marks a plan the user edited on Adjust Goals. A later
    recalculation must not silently discard those edits — see the open
    question in BACKEND.md.
    """

    __tablename__ = "nutrition_plans"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Outputs
    daily_calories: Mapped[int] = mapped_column(Integer)
    protein_g: Mapped[int] = mapped_column(Integer)
    carbs_g: Mapped[int] = mapped_column(Integer)
    fats_g: Mapped[int] = mapped_column(Integer)
    weight_delta_kg: Mapped[float] = mapped_column(Float, default=0.0)
    target_date: Mapped[date | None] = mapped_column(Date, default=None)

    # Inputs, snapshotted so a plan can always be explained after the fact.
    input_gender: Mapped[Gender | None] = mapped_column(
        Enum(Gender, name="gender"), default=None
    )
    input_activity: Mapped[ActivityLevel | None] = mapped_column(
        Enum(ActivityLevel, name="activity_level"), default=None
    )
    input_height_cm: Mapped[float | None] = mapped_column(Float, default=None)
    input_weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    input_desired_weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    input_age: Mapped[int | None] = mapped_column(Integer, default=None)
    input_goal: Mapped[WeightGoal | None] = mapped_column(
        Enum(WeightGoal, name="weight_goal"), default=None
    )

    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    user: Mapped[User] = relationship(back_populates="plans")


class WeightEntry(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "weight_entries"
    __table_args__ = (
        # Idempotent Apple Health ingest: re-syncing must not double-count.
        UniqueConstraint("user_id", "external_id", name="uq_weight_external"),
        Index("ix_weight_user_date", "user_id", "recorded_on"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kg: Mapped[float] = mapped_column(Float)
    recorded_on: Mapped[date] = mapped_column(Date)
    source: Mapped[WeightSource] = mapped_column(
        Enum(WeightSource, name="weight_source"), default=WeightSource.manual
    )
    # HealthKit sample UUID — the de-duplication key.
    external_id: Mapped[str | None] = mapped_column(String(128), default=None)

    user: Mapped[User] = relationship(back_populates="weights")


# --------------------------------------------------------------------------
# Food
# --------------------------------------------------------------------------


class MealPhoto(UUIDPrimaryKey, Timestamps, Base):
    """The object key, never the bytes.

    Bytes live in object storage; this row is the pointer. Deleting a meal
    deletes its object, and account deletion purges the user's whole prefix.
    """

    __tablename__ = "meal_photos"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    mime_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)


class FoodAnalysis(UUIDPrimaryKey, Timestamps, Base):
    """One AI scan.

    "Fix Results" is a plain recapture, so an analysis the user never saves is
    throwaway — kept briefly for debugging, not promoted to a meal.
    """

    __tablename__ = "food_analyses"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    photo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meal_photos.id", ondelete="SET NULL"), default=None
    )

    name: Mapped[str] = mapped_column(String(200))
    calories_per_serving: Mapped[int] = mapped_column(Integer)
    protein_g_per_serving: Mapped[int] = mapped_column(Integer)
    carbs_g_per_serving: Mapped[int] = mapped_column(Integer)
    fat_g_per_serving: Mapped[int] = mapped_column(Integer)
    health_score: Mapped[int] = mapped_column(Integer, default=0)
    health_score_max: Mapped[int] = mapped_column(Integer, default=10)

    # Nullable: rows written before portion estimation existed have no value,
    # and a provider is not obliged to supply one.
    estimated_portion_grams: Mapped[int | None] = mapped_column(
        Integer, default=None
    )
    portion_confidence: Mapped[PortionConfidence] = mapped_column(
        Enum(PortionConfidence, name="portion_confidence"),
        default=PortionConfidence.medium,
    )
    # What the model calibrated the portion against ("fork", "hand"), and what
    # the user said the food was served in. Kept because an estimate is only
    # reviewable against the inputs that produced it.
    scale_reference: Mapped[str | None] = mapped_column(String(40), default=None)
    container_hint: Mapped[str | None] = mapped_column(String(20), default=None)

    provider: Mapped[str] = mapped_column(String(40), default="stub")
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    saved_meal_id: Mapped[uuid.UUID | None] = mapped_column(default=None)

    items: Mapped[list[DetectedItem]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class DetectedItem(UUIDPrimaryKey, Base):
    """One labelled ingredient callout on the camera preview.

    `cx`/`cy` are the normalized bounding-box centre in 0..1; the client maps
    them to Alignment(2*cx - 1, 2*cy - 1) per DetectedFoodItem's doc comment.
    """

    __tablename__ = "detected_items"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("food_analyses.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    cx: Mapped[float] = mapped_column(Float, default=0.5)
    cy: Mapped[float] = mapped_column(Float, default=0.5)

    analysis: Mapped[FoodAnalysis] = relationship(back_populates="items")


class Meal(UUIDPrimaryKey, Timestamps, Base):
    """A meal committed to the log by the "Done" button."""

    __tablename__ = "meals"
    __table_args__ = (Index("ix_meals_user_date", "user_id", "eaten_on"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    photo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meal_photos.id", ondelete="SET NULL"), default=None
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("food_analyses.id", ondelete="SET NULL"), default=None
    )

    title: Mapped[str] = mapped_column(String(200))
    meal_type: Mapped[MealType] = mapped_column(
        Enum(MealType, name="meal_type"), default=MealType.snack
    )
    # Servings eaten, as a fraction of the portion in the photo: 0.5 means the
    # user ate half the plate. Float rather than Numeric so the value survives
    # both Postgres and the SQLite fallback without a decimal-conversion
    # warning; the quarter steps the UI offers are exact in binary anyway.
    quantity: Mapped[float] = mapped_column(Float, default=1.0)

    # Totals, already multiplied by quantity and rounded to whole units.
    calories: Mapped[int] = mapped_column(Integer)
    protein_g: Mapped[int] = mapped_column(Integer)
    carbs_g: Mapped[int] = mapped_column(Integer)
    fat_g: Mapped[int] = mapped_column(Integer)
    health_score: Mapped[int] = mapped_column(Integer, default=0)

    # The pencil icon on the calories card lets the user override the AI.
    calories_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    eaten_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    eaten_on: Mapped[date] = mapped_column(Date, index=True)

    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    tag: Mapped[str | None] = mapped_column(String(40), default=None)

    user: Mapped[User] = relationship(back_populates="meals")


# --------------------------------------------------------------------------
# Preferences & content
# --------------------------------------------------------------------------


class NotificationSettings(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    meal_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    streak_reminder: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_report: Mapped[bool] = mapped_column(Boolean, default=True)
    trial_billing_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    tips_and_articles: Mapped[bool] = mapped_column(Boolean, default=False)
    # "HH:mm" — TimeOfDay has no JSON representation.
    quiet_start: Mapped[str] = mapped_column(String(5), default="22:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="07:00")


class Achievement(UUIDPrimaryKey, Base):
    """Catalogue row. `icon_key` is a stable slug the client maps to an
    IconData — never an icon code point."""

    __tablename__ = "achievements"

    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    icon_key: Mapped[str] = mapped_column(String(60))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class UserAchievement(UUIDPrimaryKey, Base):
    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    achievement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE")
    )
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LegalDocument(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "legal_documents"
    __table_args__ = (UniqueConstraint("slug", "lang", name="uq_legal_slug_lang"),)

    slug: Mapped[str] = mapped_column(String(40), index=True)  # terms | privacy
    lang: Mapped[str] = mapped_column(String(5), default="en")
    last_updated: Mapped[date] = mapped_column(Date)
    intro: Mapped[str] = mapped_column(Text)
    footer_note: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)  # JSON: [{title, body}, ...]


class Feedback(UUIDPrimaryKey, Timestamps, Base):
    """"Request a Feature" intake from the Profile screen."""

    __tablename__ = "feedback"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    message: Mapped[str] = mapped_column(Text)


# --------------------------------------------------------------------------
# Food database mirror + scan logging (calorie-analysis pipeline)
#
# The vision model only names foods and estimates grams; the numbers come
# from these tables. The trigram indexes are what the matcher searches; 
# --------------------------------------------------------------------------


class UsdaFood(Base):
    """One food from USDA FoodData Central. Keyed by USDA's own fdc_id so
    re-imports are idempotent upserts."""

    __tablename__ = "usda_foods"
    __table_args__ = (
        Index(
            "ix_usda_foods_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )

    fdc_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    description: Mapped[str] = mapped_column(Text)
    # foundation | sr_legacy | survey_fndds | branded
    data_type: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(128), default=None)


class UsdaFoodNutrient(Base):
    """Per-100g nutrient amounts. Only the four nutrients the app needs are
    imported (1008 kcal, 1003 protein, 1004 fat, 1005 carbs)."""

    __tablename__ = "usda_food_nutrients"

    fdc_id: Mapped[int] = mapped_column(
        ForeignKey("usda_foods.fdc_id", ondelete="CASCADE"), primary_key=True
    )
    nutrient_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[float] = mapped_column(Float)


class OffProduct(Base):
    """Open Food Facts packaged product. Flattened per-100g values because
    the OFF export already ships them as columns — no nutrient indirection."""

    __tablename__ = "off_products"
    __table_args__ = (
        Index(
            "ix_off_products_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text, default=None)
    kcal_100g: Mapped[float | None] = mapped_column(Float, default=None)
    protein_100g: Mapped[float | None] = mapped_column(Float, default=None)
    carbs_100g: Mapped[float | None] = mapped_column(Float, default=None)
    fat_100g: Mapped[float | None] = mapped_column(Float, default=None)


class CustomFood(UUIDPrimaryKey, Timestamps, Base):
    """Hand-curated regional dishes the public databases miss. Searched
    before USDA/OFF so a curated entry always wins the match."""

    __tablename__ = "custom_foods"
    __table_args__ = (
        Index(
            "ix_custom_foods_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    name: Mapped[str] = mapped_column(Text)
    # JSON list of alternate names ("biryani" vs "chicken biryani").
    aliases: Mapped[list] = mapped_column(PortableJSON, default=list)
    kcal_100g: Mapped[float] = mapped_column(Float)
    protein_100g: Mapped[float] = mapped_column(Float)
    carbs_100g: Mapped[float] = mapped_column(Float)
    fat_100g: Mapped[float] = mapped_column(Float)
    region: Mapped[str | None] = mapped_column(String(64), default=None)


class ScanLog(UUIDPrimaryKey, Timestamps, Base):
    """One row per analysis request, whatever the outcome. `image_sha256`
    doubles as the result-cache key; (user_id, created_at) serves the
    daily-quota check."""

    __tablename__ = "scan_logs"
    __table_args__ = (Index("ix_scan_logs_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    route: Mapped[str] = mapped_column(String(16))  # photo | text | barcode
    image_sha256: Mapped[str | None] = mapped_column(
        String(64), index=True, default=None
    )
    prompt_version: Mapped[str | None] = mapped_column(String(32), default=None)
    model_used: Mapped[str | None] = mapped_column(String(64), default=None)
    raw_model_output: Mapped[dict | None] = mapped_column(PortableJSON, default=None)
    matched_foods: Mapped[list | None] = mapped_column(PortableJSON, default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(16), default="ok")
