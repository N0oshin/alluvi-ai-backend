"""Live Open Food Facts lookup for the barcode route.

The off_products table starts empty and grows organically: every barcode a
user scans is fetched here once, cached into the table, and served from the
DB forever after. No bulk import, no storage spent on products nobody scans.

OFF asks API users to identify themselves via User-Agent; their rate limits
(~100 product reads/min) are far above our first-scan-only call pattern.
"""

from __future__ import annotations

import httpx

API_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
USER_AGENT = "AlluviAI/1.0 (nutrition app; backend barcode lookup)"
KJ_TO_KCAL = 0.239006

# Only the fields we store — keeps OFF's response small.
FIELDS = "code,product_name,brands,serving_quantity,nutriments"


def _num(value: object, lo: float, hi: float) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


async def fetch_product(barcode: str) -> dict | None:
    """Product dict shaped for the OffProduct columns, or None.

    None means "can't price this barcode right now" — unknown to OFF,
    missing nutrition data, or OFF unreachable. Callers treat all three the
    same way (BARCODE_UNKNOWN); the cache makes repeats of the transient
    case increasingly rare.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                API_URL.format(code=barcode),
                params={"fields": FIELDS},
                headers={"User-Agent": USER_AGENT},
            )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None

    payload = resp.json()
    product = payload.get("product") or {}
    name = (product.get("product_name") or "").strip()
    if payload.get("status") != 1 or not name:
        return None

    nutr = product.get("nutriments") or {}
    kcal = _num(nutr.get("energy-kcal_100g"), 0, 900)
    if kcal is None:
        kj = _num(nutr.get("energy_100g"), 0, 3800)
        kcal = round(kj * KJ_TO_KCAL, 1) if kj is not None else None
    if kcal is None:
        return None  # unpriceable

    brand = (product.get("brands") or "").split(",")[0].strip() or None
    return {
        "barcode": barcode,
        "name": name[:500],
        "brand": brand[:200] if brand else None,
        "serving_grams": _num(product.get("serving_quantity"), 1, 5000),
        "kcal_100g": kcal,
        "protein_100g": _num(nutr.get("proteins_100g"), 0, 100),
        "carbs_100g": _num(nutr.get("carbohydrates_100g"), 0, 100),
        "fat_100g": _num(nutr.get("fat_100g"), 0, 100),
    }
