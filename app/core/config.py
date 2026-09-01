"""Application settings, loaded from the environment (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- app ---
    APP_NAME: str = "Alluvi AI"
    API_V1_PREFIX: str = "/api"
    DEBUG: bool = False

    # --- database ---
    # Postgres via docker-compose by default; swap to sqlite+aiosqlite for a
    # zero-dependency local run. The driver is psycopg 3 
    DATABASE_URL: str = "postgresql+psycopg://alluviai:alluviai123@localhost:5432/alluviai"

    # --- auth ---
    # The access token is a JWT; the refresh token is opaque and stored hashed.
    JWT_SECRET: str = "change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 30

    # --- OTP ---
    OTP_TTL_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_COOLDOWN_SECONDS: int = 60

    # --- rate limiting ---
    # In-process sliding windows (see app/core/ratelimit.py). Off in tests,
    # which hammer the auth endpoints from a single "client".
    RATE_LIMIT_ENABLED: bool = True

    # --- social sign-in ---
    # Comma-separated because one Google project issues a different OAuth
    # client ID per platform (Android / iOS / web), and the token's `aud` is
    # whichever the app used. Unset means the provider's endpoint answers 501.
    GOOGLE_CLIENT_IDS: str | None = None
    # The iOS bundle ID (plus a Services ID if web sign-in is ever added).
    APPLE_BUNDLE_IDS: str | None = None

    # --- vision provider ---
    # "stub"     -> deterministic fake analysis, no API key, used by tests
    # "claude"   -> Anthropic vision (requires ANTHROPIC_API_KEY)
    # "pipeline" -> Gemini vision + local food DB (requires GEMINI_API_KEY)
    VISION_PROVIDER: Literal["stub", "claude", "pipeline"] = "stub"
    ANTHROPIC_API_KEY: str | None = None
    # Gemini — the self-built analysis pipeline's vision model.
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3-flash"
    # Multiplies every portion estimate before nutrition is computed — the
    # one global tuning knob once eval data shows a systematic bias.
    PORTION_BIAS: float = 1.0
    # --- pipeline resilience ---
    # When Gemini fails twice, the same scan goes to Claude directly via the
    # Anthropic API. Haiku: cheap and fast.
    ANTHROPIC_FALLBACK_MODEL: str = "claude-haiku-4-5"
    # Identical image bytes within this window reuse the logged model output
    # instead of a new model call. Global across users by design.
    SCAN_CACHE_TTL_HOURS: int = 24
    # AI scans (photo + text) allowed per user per UTC day; barcode is free
    # and uncounted.
    DAILY_SCAN_LIMIT: int = 50
    VISION_MODEL: str = "claude-opus-5"
    VISION_EFFORT: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    VISION_MAX_TOKENS: int = 8000

    # --- email ---
    # "console" -> written to the log, no network, no key; the dev default and
    #              what the tests run against.
    # "resend"  -> real delivery via Resend (requires RESEND_API_KEY).
    EMAIL_PROVIDER: Literal["console", "resend"] = "console"
    RESEND_API_KEY: str | None = None
    EMAIL_FROM: str = "Alluvi <onboarding@resend.dev>"
    EMAIL_TIMEOUT_SECONDS: float = 10.0
    # --- push notifications ---
    # "console" -> written to the log, no network, no key; the dev default and
    #              what the tests run against.
    # "fcm"     -> real delivery via Firebase Cloud Messaging (requires
    #              FIREBASE_CREDENTIALS).
    PUSH_PROVIDER: Literal["console", "fcm"] = "console"
    # Path to the Firebase service-account JSON (Project settings → Service
    # accounts → Generate new private key). Never committed.
    FIREBASE_CREDENTIALS: str | None = None
    # The in-process meal-reminder loop (services/push/scheduler.py). Off in
    # tests; harmless but noisy locally with PUSH_PROVIDER=console.
    REMINDERS_ENABLED: bool = True

    # --- storage ---
    # "local" writes under MEDIA_ROOT and serves via /media.
    STORAGE_BACKEND: Literal["local"] = "local"
    MEDIA_ROOT: str = "./media"
    MEDIA_URL_PREFIX: str = "/media"
    # Photos are re-encoded down to this long edge before storage; EXIF is
    # dropped in the process (phone photos carry GPS).
    PHOTO_MAX_EDGE_PX: int = 1568
    PHOTO_JPEG_QUALITY: int = 85

    @model_validator(mode="after")
    def _refuse_placeholder_secret_outside_debug(self) -> "Settings":
        if not self.DEBUG and self.JWT_SECRET == "change-in-production":
            raise ValueError(
                "JWT_SECRET is still the placeholder. Set a real secret "
                '(python -c "import secrets; print(secrets.token_urlsafe(48))") '
                "or set DEBUG=true for local development."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
