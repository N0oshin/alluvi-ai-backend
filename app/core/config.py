"""Application settings, loaded from the environment (see .env.example)."""

from functools import lru_cache
from typing import Literal

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

    # --- vision provider ---
    # "stub"   -> deterministic fake analysis, no API key, used by tests
    # "claude" -> Anthropic vision (requires ANTHROPIC_API_KEY)
    VISION_PROVIDER: Literal["stub", "claude"] = "stub"
    ANTHROPIC_API_KEY: str | None = None
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
    # The reset link mailed to users. The token is appended as `?token=`.
    PASSWORD_RESET_URL: str = "https://alluvi.dev/reset-password"
    PASSWORD_RESET_TTL_HOURS: int = 1

    # --- storage ---
    # "local" writes under MEDIA_ROOT and serves via /media.
    STORAGE_BACKEND: Literal["local"] = "local"
    MEDIA_ROOT: str = "./media"
    MEDIA_URL_PREFIX: str = "/media"
    # Photos are re-encoded down to this long edge before storage; EXIF is
    # dropped in the process (phone photos carry GPS).
    PHOTO_MAX_EDGE_PX: int = 1568
    PHOTO_JPEG_QUALITY: int = 85


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
