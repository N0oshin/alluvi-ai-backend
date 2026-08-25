"""
 retry → Claude fallback chain + image-hash cache lookup

Order of defense for one scan:
    cache hit  -> free, instant (checked by the caller before calling us)
    Gemini     -> the normal path
    Gemini #2  -> one retry after a short backoff
    Claude     -> different provider entirely
    error      -> only now does the user see a failure
"""

from __future__ import annotations

import asyncio

from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.vision_scan import ScanResult
from app.services.vision.base import VisionAnalysisError
from app.services.vision.claude_scan import ClaudeScanClient
from app.services.vision.gemini import PROMPT_VERSION, GeminiScanClient, ScanMeta

RETRY_BACKOFF_SECONDS = 2.0


async def resilient_scan(
    *,
    image_bytes: bytes | None = None,
    text_description: str | None = None,
    container: str | None = None,
) -> tuple[ScanResult, ScanMeta]:
    """Try Gemini twice, then Claude once, then fail."""

    last_error: VisionAnalysisError | None = None

    for attempt in range(2):
        try:
            return await GeminiScanClient().scan(
                image_bytes=image_bytes,
                text_description=text_description,
                container=container,
            )
        except VisionAnalysisError as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)

    if settings.ANTHROPIC_API_KEY:
        try:
            return await ClaudeScanClient().scan(
                image_bytes=image_bytes,
                text_description=text_description,
                container=container,
            )
        except VisionAnalysisError as exc:
            last_error = exc

    assert last_error is not None
    raise last_error


_CACHE_SQL = text(
    """
    SELECT raw_model_output, model_used
    FROM scan_logs
    WHERE image_sha256 = :sha
      AND status = 'ok'
      AND prompt_version = :prompt
      AND raw_model_output IS NOT NULL
      AND created_at > now() - make_interval(hours => :ttl)
    ORDER BY created_at DESC
    LIMIT 1
    """
)


async def cached_scan(image_sha: str) -> tuple[ScanResult, ScanMeta] | None:
    """Reuse the logged model output for byte-identical images.

    Global across users on purpose — the fingerprint only matches identical
    bytes. Keyed on prompt version too, so a prompt upgrade naturally
    ignores older entries. Pricing is NOT cached: the caller re-prices from
    the DB, so food-data fixes apply to cached scans immediately.
    """
    try:
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    _CACHE_SQL,
                    {
                        "sha": image_sha,
                        "prompt": PROMPT_VERSION,
                        "ttl": settings.SCAN_CACHE_TTL_HOURS,
                    },
                )
            ).first()
    except Exception:  # noqa: BLE001 — a broken cache must not break scans
        return None
    if row is None:
        return None

    try:
        scan = ScanResult.model_validate(row.raw_model_output)
    except ValidationError:
        return None

    if scan.not_food or not scan.items:
        return None  # only successful analyses are worth replaying

    meta = ScanMeta(
        model=f"cache:{row.model_used}",
        prompt_version=PROMPT_VERSION,
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
        raw_output=row.raw_model_output,
    )
    return scan, meta
