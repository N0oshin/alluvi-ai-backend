"""Anthropic (Claude) vision provider.

Notes that matter here:
  * Thinking is on by default on Claude Opus 5.
  * Safety classifiers can decline a request: that is an HTTP 200 with
    `stop_reason == "refusal"`, not an exception, so it is checked before
    `content` is read.
  * Structured-output schemas do not support numeric bounds, so ranges are
    clamped in Python after parsing.
"""

from __future__ import annotations

import base64
import json
import logging

from app.core.config import settings
from app.services.vision.base import (
    CONTAINERS,
    DetectedItemResult,
    FoodAnalysisResult,
    NotFoodError,
    VisionAnalysisError,
    VisionProvider,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a nutrition estimator for a calorie-tracking app. You receive one \
photo of a meal and return structured nutrition data for a single serving of \
what is shown.

Estimate for ONE serving of the dish as plated in the photo. The app \
multiplies your numbers by a quantity the user picks, so never pre-multiply.

Macros must be physically consistent with the calorie figure: protein and \
carbohydrate are 4 kcal per gram, fat is 9 kcal per gram, and those three \
should account for the calories you report, within about 10%.

estimated_portion_grams is the total edible weight you assumed for that one \
serving. State the assumption you actually used, so the user can correct it.

portion_confidence is how well the photo pins that weight down. Judge the \
photo, not the dish: a flat plate shot at an angle with a fork or hand for \
scale is "high"; a typical overhead shot of a plated meal is "medium"; a bowl, \
soup, stew, or anything whose depth you cannot see is "low". Say "low" when \
you are unsure — an honest low is more useful than a confident guess.

Before estimating the weight, look for something of known size to calibrate \
against: a fork, spoon, knife, chopstick, a hand or finger, a standard mug or \
drinks can. These vary far less between households than plates and bowls do, \
so anchor the portion to one whenever it is visible. Report which you used in \
scale_reference, or null if nothing usable was in frame.

health_score rates overall nutritional quality out of 10: whole foods, fibre, \
and a balanced macro split score high; ultra-processed, fried, and \
sugar-dominant dishes score low.

detected_items lists the distinct ingredients you can actually see, each with \
the centre of that ingredient in the frame as fractions of the image width and \
height (0.0 to 1.0, origin at the top-left). Include at most 6, and only \
ingredients that are visible.

If the photo contains no food at all, set is_food to false and leave the \
numbers at zero."""

# Structured-output schemas support types, enums, and additionalProperties:false
# but not numeric or length bounds — those are enforced after parsing.
_SCHEMA = {
    "type": "object",
    "properties": {
        "is_food": {
            "type": "boolean",
            "description": "False if the photo contains no recognisable food.",
        },
        "name": {
            "type": "string",
            "description": "Short dish name, e.g. 'Garden Power Bowl'.",
        },
        "calories_per_serving": {"type": "integer"},
        "protein_g_per_serving": {"type": "integer"},
        "carbs_g_per_serving": {"type": "integer"},
        "fat_g_per_serving": {"type": "integer"},
        "health_score": {
            "type": "integer",
            "description": "Nutritional quality, 0-10.",
        },
        "estimated_portion_grams": {
            "type": "integer",
            "description": "Total edible weight assumed for one serving.",
        },
        "portion_confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "How well the photo pins down the portion weight.",
        },
        "scale_reference": {
            "type": ["string", "null"],
            "description": (
                "Known-size object used to calibrate the portion, e.g. 'fork' "
                "or 'hand'. Null if none was visible."
            ),
        },
        "detected_items": {
            "type": "array",
            "description": "Visible ingredients with their centre in the frame.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "cx": {
                        "type": "number",
                        "description": "Horizontal centre, 0.0-1.0 from left.",
                    },
                    "cy": {
                        "type": "number",
                        "description": "Vertical centre, 0.0-1.0 from top.",
                    },
                },
                "required": ["label", "cx", "cy"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "is_food",
        "name",
        "calories_per_serving",
        "protein_g_per_serving",
        "carbs_g_per_serving",
        "fat_g_per_serving",
        "health_score",
        "estimated_portion_grams",
        "portion_confidence",
        "scale_reference",
        "detected_items",
    ],
    "additionalProperties": False,
}

_SUPPORTED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_CONFIDENCE_LEVELS = {"low", "medium", "high"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ClaudeVisionProvider(VisionProvider):
    name = "claude"

    def __init__(self) -> None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "VISION_PROVIDER=claude requires ANTHROPIC_API_KEY. "
                "Set it in .env, or use VISION_PROVIDER=stub for local work."
            )
        # Imported lazily so the package is only needed when this provider runs.
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._model = settings.VISION_MODEL

    async def analyze(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        *,
        container: str | None = None,
        user_id: object | None = None,
    ) -> FoodAnalysisResult:
        import anthropic

        if mime_type not in _SUPPORTED_MIME:
            mime_type = "image/jpeg"

        encoded = base64.standard_b64encode(image_bytes).decode("ascii")

        # The hint rides on the user turn rather than the system prompt so the
        # system prompt stays byte-identical across requests and can be cached.
        prompt = "Analyse this meal photo."
        if container in CONTAINERS:
            prompt += f" The user says it is served in a {container}."

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=settings.VISION_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                output_config={
                    "effort": settings.VISION_EFFORT,
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": encoded,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except anthropic.RateLimitError:
            logger.warning("Anthropic rate limit hit during food analysis")
            raise VisionAnalysisError() from None
        except anthropic.APIError as exc:
            logger.error("Anthropic API error during food analysis: %s", exc)
            raise VisionAnalysisError() from exc

        # A refusal is a 200 with empty/partial content — check before reading.
        if response.stop_reason == "refusal":
            logger.warning(
                "Claude declined the analysis (category=%s)",
                getattr(response.stop_details, "category", None),
            )
            raise VisionAnalysisError()

        if response.stop_reason == "max_tokens":
            logger.warning("Analysis truncated at max_tokens; raise VISION_MAX_TOKENS")
            raise VisionAnalysisError()

        text = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            None,
        )
        if not text:
            raise VisionAnalysisError()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("Structured output was not valid JSON: %s", exc)
            raise VisionAnalysisError() from exc

        if not data.get("is_food", False):
            raise NotFoodError()

        return _to_result(data, model=self._model)


def _to_result(data: dict, *, model: str) -> FoodAnalysisResult:
    """Clamp the model's numbers into ranges the UI can actually render."""
    items: list[DetectedItemResult] = []
    for raw in (data.get("detected_items") or [])[:6]:
        label = str(raw.get("label", "")).strip()
        if not label:
            continue
        items.append(
            DetectedItemResult(
                label=label[:120],
                cx=_clamp(float(raw.get("cx", 0.5)), 0.0, 1.0),
                cy=_clamp(float(raw.get("cy", 0.5)), 0.0, 1.0),
            )
        )

    return FoodAnalysisResult(
        name=(str(data.get("name") or "Meal").strip() or "Meal")[:200],
        # Calories are clamped to a plausible single-serving range; a wild
        # value here would otherwise blow up the day's remaining-calorie math.
        calories_per_serving=int(_clamp(int(data["calories_per_serving"]), 0, 5000)),
        protein_g_per_serving=int(_clamp(int(data["protein_g_per_serving"]), 0, 500)),
        carbs_g_per_serving=int(_clamp(int(data["carbs_g_per_serving"]), 0, 1000)),
        fat_g_per_serving=int(_clamp(int(data["fat_g_per_serving"]), 0, 500)),
        health_score=int(_clamp(int(data.get("health_score", 5)), 0, 10)),
        health_score_max=10,
        estimated_portion_grams=int(
            _clamp(int(data.get("estimated_portion_grams") or 0), 0, 5000)
        )
        or None,
        # The schema constrains this to the three values, but a default here
        # keeps the field safe if the schema is ever loosened.
        portion_confidence=(
            data.get("portion_confidence")
            if data.get("portion_confidence") in _CONFIDENCE_LEVELS
            else "medium"
        ),
        scale_reference=(
            str(data["scale_reference"]).strip()[:40] or None
            if data.get("scale_reference")
            else None
        ),
        detected_items=items,
        model=model,
    )
