"""The self-built analysis pipeline as a drop-in VisionProvider.

vision (names + grams) -> DB matcher (per-100g) -> sanity layer -> result.
The model never produces the final nutrition numbers: every kcal/macro in
the response is DB-per-100g x grams, or — only when no DB row clears the
match threshold — the model's fallback per-100g priced the same way.

Every attempt (success, not-food, failure) writes a scan_logs row using its
own DB session, so the log survives even when the request transaction is
rolled back. Retries, fallback models and hash-caching arrive in Step 7.
"""

from __future__ import annotations

import hashlib
import time

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
from app.services.vision.base import (
    DetectedItemResult,
    FoodAnalysisResult,
    NotFoodError,
    VisionAnalysisError,
    VisionProvider,
)
from app.services.vision.gemini import PROMPT_VERSION, GeminiScanClient, ScanMeta


class PipelineVisionProvider(VisionProvider):
    name = "pipeline"

    async def analyze(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        *,
        container: str | None = None,
    ) -> FoodAnalysisResult:
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        client = GeminiScanClient()
        started = time.perf_counter()

        try:
            scan, meta = await client.scan(
                image_bytes=image_bytes, container=container
            )
        except VisionAnalysisError:
            await self._log(
                route="photo",
                image_sha256=image_sha,
                status="error",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

        if scan.not_food or not scan.items:
            await self._log(
                route="photo",
                image_sha256=image_sha,
                status="not_food",
                meta=meta,
            )
            raise NotFoodError()

        result, matched_foods = await self._price(scan, meta)
        await self._log(
            route="photo",
            image_sha256=image_sha,
            status="ok",
            meta=meta,
            matched_foods=matched_foods,
        )
        return result

    async def _price(
        self, scan: ScanResult, meta: ScanMeta
    ) -> tuple[FoodAnalysisResult, list[dict]]:
        """Turn identified items into nutrition via the DB. Pure pipeline
        arithmetic; the only I/O is the matcher's SELECTs."""
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

    async def _log(
        self,
        *,
        route: str,
        image_sha256: str | None,
        status: str,
        meta: ScanMeta | None = None,
        matched_foods: list[dict] | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Best-effort audit row on its own session; never breaks a scan."""
        try:
            async with SessionLocal() as db:
                db.add(
                    ScanLog(
                        route=route,
                        image_sha256=image_sha256,
                        prompt_version=meta.prompt_version if meta else PROMPT_VERSION,
                        model_used=meta.model if meta else settings.GEMINI_MODEL,
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
