"""Deterministic offline analyser.

Needs no API key, costs nothing, and always returns the same result for the
same bytes — which is what makes the whole food pipeline testable. This is the
default provider so the backend runs out of the box.
"""

from __future__ import annotations

import hashlib

from app.services.vision.base import (
    DetectedItemResult,
    FoodAnalysisResult,
    VisionProvider,
)

_DISHES = [
    ("Garden Power Bowl", ["Lettuce", "Parmesan", "Cherry Tomatoes", "Croutons"]),
    ("Turkey Sandwich", ["Turkey", "Whole Wheat Bread", "Lettuce", "Tomato"]),
    ("Greek Yogurt Bowl", ["Greek Yogurt", "Blueberries", "Granola", "Honey"]),
    ("Taco Bowl", ["Ground Beef", "Black Beans", "Rice", "Avocado"]),
    ("Berry Oats", ["Oats", "Strawberries", "Blueberries", "Almond Milk"]),
    ("Grilled Salmon Plate", ["Salmon", "Asparagus", "Lemon", "Quinoa"]),
]

_CONFIDENCE = ["low", "medium", "high"]

_SCALE_REFERENCES = ["fork", "hand", None]


class StubVisionProvider(VisionProvider):
    name = "stub"

    async def analyze(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        *,
        container: str | None = None,
        user_id: object | None = None,
    ) -> FoodAnalysisResult:
        digest = hashlib.sha256(image_bytes).digest()
        name, ingredients = _DISHES[digest[0] % len(_DISHES)]

        # Derive macros first, then compute calories from them, so the stub's
        # numbers stay internally consistent (4/4/9 kcal per gram).
        protein = 15 + digest[1] % 35
        carbs = 20 + digest[2] % 60
        fat = 8 + digest[3] % 25
        calories = protein * 4 + carbs * 4 + fat * 9

        items = [
            DetectedItemResult(
                label=label,
                cx=round(0.2 + ((digest[4 + i] % 60) / 100.0), 3),
                cy=round(0.2 + ((digest[10 + i] % 60) / 100.0), 3),
            )
            for i, label in enumerate(ingredients)
        ]

        return FoodAnalysisResult(
            name=name,
            calories_per_serving=calories,
            protein_g_per_serving=protein,
            carbs_g_per_serving=carbs,
            fat_g_per_serving=fat,
            health_score=3 + digest[20] % 8,
            health_score_max=10,
            # Roughly food-like: most plated meals land between 150g and 650g.
            estimated_portion_grams=150 + digest[21] % 500,
            portion_confidence=_CONFIDENCE[digest[22] % len(_CONFIDENCE)],
            # None on roughly a third of images, so the client's "include a
            # fork next time" nudge has something to exercise offline.
            scale_reference=_SCALE_REFERENCES[digest[23] % len(_SCALE_REFERENCES)],
            detected_items=items,
            model="stub",
        )
