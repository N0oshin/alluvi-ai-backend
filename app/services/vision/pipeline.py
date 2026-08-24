"""The self-built analysis pipeline as a drop-in VisionProvider.

vision (names + grams) -> DB matcher (per-100g) -> sanity layer -> result.
The model never produces the final nutrition numbers. The pricing and
logging live in app.services.nutrition.nutri_cal, shared with the text
route; this class is the photo-shaped adapter onto the VisionProvider ABC.

Retries, fallback models and hash-caching arrive in Step 7.
"""

from __future__ import annotations

import hashlib
import time

from app.services.nutrition.nutri_cal import log_scan, price_scan
from app.services.vision.base import (
    FoodAnalysisResult,
    NotFoodError,
    VisionAnalysisError,
    VisionProvider,
)
from app.services.vision.gemini import PROMPT_VERSION, GeminiScanClient


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
            await log_scan(
                route="photo",
                image_sha256=image_sha,
                status="error",
                latency_ms=int((time.perf_counter() - started) * 1000),
                model_used=client.model,
                prompt_version=PROMPT_VERSION,
            )
            raise

        if scan.not_food or not scan.items:
            await log_scan(
                route="photo", image_sha256=image_sha, status="not_food", meta=meta
            )
            raise NotFoodError()

        result, matched_foods = await price_scan(scan, meta)
        await log_scan(
            route="photo",
            image_sha256=image_sha,
            status="ok",
            meta=meta,
            matched_foods=matched_foods,
        )
        return result
