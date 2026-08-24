"""Nutrition calculation: identified items -> priced result, plus scan
logging. Shared by the photo pipeline, the text route, and (partly) the
barcode route.

All arithmetic here follows one rule: nutrition numbers come from the
database (or, on a match miss, the model's per-100g fallback) multiplied by
grams — never from the model directly.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.models import ScanLog
from app.db.session import SessionLocal
from app.schemas.vision_scan import ScanResult
from app.services.nutrition.matcher import match_food
from app.services.nutrition.sanity import (
    clamp_item_grams,
    meal_scale_factor,
    reconcile_kcal,
)
from app.services.vision.base import DetectedItemResult, FoodAnalysisResult
from app.services.vision.gemini import ScanMeta


async def price_scan(
    scan: ScanResult, meta: ScanMeta
) -> tuple[FoodAnalysisResult, list[dict]]:
    bias = settings.PORTION_BIAS

    grams = [clamp_item_grams(i.grams, bias) for i in scan.items]
    scale = meal_scale_factor(sum(grams))
    grams = [g * scale for g in grams]

    total_kcal = total_p = total_c = total_f = 0.0
    detected: list[DetectedItemResult] = []
    matched_foods: list[dict] = []

    async with SessionLocal() as db:
        for item, g in zip(scan.items, grams):
            matches = await match_food(db, item.name, limit=1)
            if matches:
                m = matches[0]
                p100_kcal, p100_p, p100_c, p100_f = (
                    m.kcal_100g,
                    m.protein_100g,
                    m.carbs_100g,
                    m.fat_100g,
                )
                source, matched_name, score = m.source, m.name, m.score
            else:
                f = item.fallback_per_100g
                p100_kcal, p100_p, p100_c, p100_f = (
                    f.kcal,
                    f.protein_g,
                    f.carbs_g,
                    f.fat_g,
                )
                source, matched_name, score = "model_fallback", None, 0.0

            p100_kcal = reconcile_kcal(p100_kcal, p100_p, p100_c, p100_f)

            factor = g / 100.0
            total_kcal += p100_kcal * factor
            total_p += p100_p * factor
            total_c += p100_c * factor
            total_f += p100_f * factor

            detected.append(
                DetectedItemResult(
                    label=item.name,
                    cx=item.cx,
                    cy=item.cy,
                    grams=round(g),
                    confidence=round(item.confidence, 2),
                )
            )
            matched_foods.append(
                {
                    "query": item.name,
                    "source": source,
                    "matched": matched_name,
                    "score": round(score, 3),
                    "grams": round(g, 1),
                    "kcal": round(p100_kcal * factor, 1),
                }
            )

    result = FoodAnalysisResult(
        name=scan.dish_name or scan.items[0].name.title(),
        calories_per_serving=round(total_kcal),
        protein_g_per_serving=round(total_p),
        carbs_g_per_serving=round(total_c),
        fat_g_per_serving=round(total_f),
        health_score=scan.health_score,
        health_score_max=10,
        estimated_portion_grams=round(sum(grams)),
        portion_confidence=scan.portion_confidence,
        scale_reference=scan.scale_reference,
        detected_items=detected,
        model=meta.model,
    )
    return result, matched_foods


def health_score_from_macros(
    kcal_100g: float, protein_100g: float, carbs_100g: float, fat_100g: float
) -> int:
    """Crude 1-10 score for sources with no model in the loop (barcode).

    Rewards protein share, penalises energy density — a rough proxy for
    "whole food vs ultra-processed" that a barcode alone can support.
    """
    score = 5.0
    total = protein_100g * 4 + carbs_100g * 4 + fat_100g * 9
    if total > 0:
        score += 4 * (protein_100g * 4 / total)  # up to +4 for pure protein
    score -= 3 * min(1.0, kcal_100g / 550)  # -3 by ~550 kcal/100g
    return max(1, min(10, round(score)))


async def log_scan(
    *,
    route: str,
    status: str,
    user_id=None,
    image_sha256: str | None = None,
    meta: ScanMeta | None = None,
    matched_foods: list[dict] | None = None,
    latency_ms: int | None = None,
    model_used: str | None = None,
    prompt_version: str | None = None,
) -> None:
    """Best-effort audit row on its own session, so it survives request
    rollbacks and never breaks a scan.

    With no `meta` (call failed before a response, or no model was involved
    at all — barcode), model/prompt fall back to the explicit arguments and
    stay null otherwise."""
    try:
        async with SessionLocal() as db:
            db.add(
                ScanLog(
                    user_id=user_id,
                    route=route,
                    image_sha256=image_sha256,
                    prompt_version=meta.prompt_version if meta else prompt_version,
                    model_used=meta.model if meta else model_used,
                    raw_model_output=meta.raw_output if meta else None,
                    matched_foods=matched_foods,
                    latency_ms=meta.latency_ms if meta else latency_ms,
                    estimated_cost_usd=meta.estimated_cost_usd if meta else None,
                    status=status,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — logging must never mask the scan
        pass
