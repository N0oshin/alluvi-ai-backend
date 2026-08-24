"""What the vision model is allowed to say.

Deliberately contains no field for total nutrition — the model identifies
and weighs; the food database prices. `fallback_per_100g` is the only
nutrition the model produces, used solely when the DB match fails.

Gemini enforces this shape via constrained decoding (responseSchema); the
Pydantic validation here is the second gate, adding range checks the
decoder cannot express.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FallbackPer100g(BaseModel):
    kcal: float = Field(ge=0, le=900)  # pure fat tops out at ~900
    protein_g: float = Field(ge=0, le=100)
    carbs_g: float = Field(ge=0, le=100)
    fat_g: float = Field(ge=0, le=100)


class ScanItem(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    grams: float = Field(gt=0, le=3000)
    confidence: float = Field(ge=0, le=1)
    cx: float = Field(default=0.5, ge=0, le=1)
    cy: float = Field(default=0.5, ge=0, le=1)
    fallback_per_100g: FallbackPer100g


class ScanResult(BaseModel):
    dish_name: str = Field(max_length=120)
    not_food: bool = False
    items: list[ScanItem] = Field(default_factory=list, max_length=20)
    health_score: int = Field(ge=1, le=10)
    portion_confidence: Literal["low", "medium", "high"]
    scale_reference: str | None = None


# The same shape in Gemini's responseSchema dialect (an OpenAPI subset).
# Kept next to the Pydantic models so the two cannot drift silently.
GEMINI_RESPONSE_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "dish_name": {"type": "STRING"},
        "not_food": {"type": "BOOLEAN"},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "grams": {"type": "NUMBER"},
                    "confidence": {"type": "NUMBER"},
                    "cx": {"type": "NUMBER"},
                    "cy": {"type": "NUMBER"},
                    "fallback_per_100g": {
                        "type": "OBJECT",
                        "properties": {
                            "kcal": {"type": "NUMBER"},
                            "protein_g": {"type": "NUMBER"},
                            "carbs_g": {"type": "NUMBER"},
                            "fat_g": {"type": "NUMBER"},
                        },
                        "required": ["kcal", "protein_g", "carbs_g", "fat_g"],
                    },
                },
                "required": [
                    "name",
                    "grams",
                    "confidence",
                    "cx",
                    "cy",
                    "fallback_per_100g",
                ],
            },
        },
        "health_score": {"type": "INTEGER"},
        "portion_confidence": {
            "type": "STRING",
            "enum": ["low", "medium", "high"],
        },
        "scale_reference": {"type": "STRING", "nullable": True},
    },
    "required": [
        "dish_name",
        "not_food",
        "items",
        "health_score",
        "portion_confidence",
    ],
}
