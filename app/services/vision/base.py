"""Provider-agnostic contract for food-photo analysis.

Everything downstream depends on this shape, never on a vendor SDK, so the
provider can be swapped (or removed) without touching the API layer.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(slots=True)
class DetectedItemResult:
    """One labelled ingredient.

    `cx`/`cy` are the normalized bounding-box centre in 0..1. The client maps
    them to `Alignment(2*cx - 1, 2*cy - 1)` to place the callout over the
    live camera preview.
    """

    label: str
    cx: float = 0.5
    cy: float = 0.5


@dataclass(slots=True)
class FoodAnalysisResult:
    """Per-serving nutrition for one photo.

    Per-serving, not per-meal: the client multiplies by the quantity stepper
    (`FoodResultCubit`), so returning pre-multiplied totals would double-count.
    """

    name: str
    calories_per_serving: int
    protein_g_per_serving: int
    carbs_g_per_serving: int
    fat_g_per_serving: int
    health_score: int
    health_score_max: int = 10
    detected_items: list[DetectedItemResult] = field(default_factory=list)
    model: str | None = None

    def as_totals(self, quantity: int) -> tuple[int, int, int, int]:
        q = max(1, quantity)
        return (
            self.calories_per_serving * q,
            self.protein_g_per_serving * q,
            self.carbs_g_per_serving * q,
            self.fat_g_per_serving * q,
        )


class VisionAnalysisError(Exception):
    """Raised when analysis fails for a reason the user should hear about."""

    def __init__(self, message_key: str = "food.analysis_failed") -> None:
        self.message_key = message_key
        super().__init__(message_key)


class NotFoodError(VisionAnalysisError):
    """The photo contains no recognisable food."""

    def __init__(self) -> None:
        super().__init__("food.not_food")


class VisionProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def analyze(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> FoodAnalysisResult:
        """Analyse a food photo. Raises VisionAnalysisError on failure."""
        raise NotImplementedError
