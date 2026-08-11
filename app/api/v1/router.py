"""Aggregates every v1 router under the client's API prefix."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import analytics, auth, food, notifications, profile, userinfo

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(userinfo.router)
api_router.include_router(food.router)
api_router.include_router(profile.router)
api_router.include_router(profile.legal_router)
api_router.include_router(analytics.router)
api_router.include_router(notifications.router)
