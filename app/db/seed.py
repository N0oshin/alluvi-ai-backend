"""Seed reference data: achievements and legal copy.

Idempotent — safe to re-run. Run with:  python -m app.db.seed
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

from sqlalchemy import select

from app.db.models import Achievement, LegalDocument
from app.db.session import SessionLocal

# The 12 badges on screen 30, in display order. `icon_key` is a stable slug
# the client maps to an IconData — never an icon code point.
ACHIEVEMENTS = [
    ("first_scan", "First Scan", "camera"),
    ("streak_7", "7-Day Streak", "flame"),
    ("first_kilo", "First Kilo", "scale"),
    ("meals_25", "25 Meals", "ramen"),
    ("streak_30", "30-Day Streak", "flame"),
    ("meals_100", "100 Meals", "restaurant"),
    ("early_bird", "Early Bird", "sunrise"),
    ("protein_pro", "Protein Pro", "egg"),
    ("perfect_week", "Perfect Week", "check_circle"),
    ("halfway", "Halfway There", "timelapse"),
    ("goal_reached", "Goal Reached", "flag"),
    ("legend", "Legend", "medal"),
]

_INTRO = (
    "Welcome to Alluvi AI. This document explains how our service works and "
    "your rights as a user."
)
_FOOTER = "Questions? Reach us at support@alluvi.ai"

TERMS_SECTIONS = [
    {
        "title": "1. Using Alluvi AI",
        "body": (
            "You must be at least 16 years old to use Alluvi AI. You agree to "
            "provide accurate information when logging meals and activity, and "
            "not to misuse the service for any unlawful purpose."
        ),
    },
    {
        "title": "2. Your Data",
        "body": (
            "You own the data you log. We use it only to power your tracking, "
            "insights, and recommendations, and we never sell it to third parties."
        ),
    },
    {
        "title": "3. Subscriptions",
        "body": (
            "Premium subscriptions renew automatically unless cancelled at least "
            "24 hours before the renewal date. You can manage or cancel your "
            "subscription anytime from your device's app store settings."
        ),
    },
    {
        "title": "4. Health Disclaimer",
        "body": (
            "Alluvi AI provides general nutrition and fitness guidance only. It "
            "is not a substitute for professional medical advice — consult a "
            "doctor before making significant changes to your diet or exercise "
            "routine."
        ),
    },
    {
        "title": "5. Contact Us",
        "body": (
            "If you have questions about these terms, reach out to our support "
            "team any time."
        ),
    },
]

PRIVACY_SECTIONS = [
    {
        "title": "1. Using Alluvi AI",
        "body": (
            "When you use Alluvi AI, we collect the details you enter — meals, "
            "weight, and goals — plus basic device and usage information to keep "
            "the app running smoothly."
        ),
    },
    {
        "title": "2. Your Data",
        "body": (
            "Your data is encrypted in transit and at rest. We never sell your "
            "personal information, and you can request a full export or deletion "
            "at any time from Settings."
        ),
    },
    {
        "title": "3. Subscriptions",
        "body": (
            "Billing details for Premium subscriptions are processed securely by "
            "the App Store or Google Play — Alluvi AI never stores your payment "
            "card information."
        ),
    },
    {
        "title": "4. Health Disclaimer",
        "body": (
            "Health and nutrition data you log is treated as sensitive "
            "information and is only used to power your personal insights — it is "
            "never shared with advertisers."
        ),
    },
    {
        "title": "5. Contact Us",
        "body": (
            "For any privacy questions or data requests, contact our privacy team "
            "any time."
        ),
    },
]


async def seed() -> None:
    async with SessionLocal() as db:
        for order, (key, label, icon) in enumerate(ACHIEVEMENTS):
            existing = await db.scalar(
                select(Achievement).where(Achievement.key == key)
            )
            if existing is None:
                db.add(
                    Achievement(
                        key=key, label=label, icon_key=icon, sort_order=order
                    )
                )

        for slug, sections in (("terms", TERMS_SECTIONS), ("privacy", PRIVACY_SECTIONS)):
            existing = await db.scalar(
                select(LegalDocument).where(
                    LegalDocument.slug == slug, LegalDocument.lang == "en"
                )
            )
            if existing is None:
                db.add(
                    LegalDocument(
                        slug=slug,
                        lang="en",
                        last_updated=date(2026, 7, 1),
                        intro=_INTRO,
                        footer_note=_FOOTER,
                        body=json.dumps(sections),
                    )
                )

        await db.commit()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
