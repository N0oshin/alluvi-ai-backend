"""Provider-agnostic contract for food-photo analysis.

Everything downstream depends on this shape, never on a vendor SDK, so the
provider can be swapped (or removed) without touching the API layer.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

# What the user can tell us the food is served in. Deliberately coarse: the
# point is to bound the depth the camera cannot see, and a longer list would
# only make the client's picker slower to use than it is worth.
CONTAINERS = frozenset(
    {"plate", "bowl", "cup", "glass", "packaged", "other"}
)


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
    # Per-item portion data (pipeline provider only; stub/claude leave None).
    grams: int | None = None
    confidence: float | None = None


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
    # Depth is invisible in a photo, so portion is where nearly all the error
    # lives — showing it gives the user something they can actually check,
    # and a low confidence is the cue to ask rather than guess silently.
    estimated_portion_grams: int | None = None
    portion_confidence: str = "medium"
    # What the provider calibrated the portion against — cutlery and hands are
    # near-standard in size, so one in frame is worth more than any prompting.
    # None means nothing usable was visible, which is the client's cue to ask
    # for one next time rather than show a tip nobody reads.
    scale_reference: str | None = None
    detected_items: list[DetectedItemResult] = field(default_factory=list)
    model: str | None = None

    def as_totals(self, quantity: float) -> tuple[int, int, int, int]:
        """Scale per-serving figures to a meal total.

        `quantity` is fractional (0.5 = half the plate), so the products are
        rounded back to whole units — the client renders integers.
        """
        q = max(0.0, float(quantity))
        return (
            round(self.calories_per_serving * q),
            round(self.protein_g_per_serving * q),
            round(self.carbs_g_per_serving * q),
            round(self.fat_g_per_serving * q),
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
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        *,
        container: str | None = None,
        user_id: object | None = None,
    ) -> FoodAnalysisResult:
        """Analyse a food photo. Raises VisionAnalysisError on failure.

        `container` is an optional user-supplied hint from CONTAINERS. Depth is
        what a photo hides, so knowing a dish is a bowl rather than a plate
        bounds the portion before any visual reasoning starts.
        """
        raise NotImplementedError
