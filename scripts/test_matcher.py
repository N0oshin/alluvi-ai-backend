"""Eyeball the food matcher against the live database.

Usage, from backend/:
    python scripts/test_matcher.py                       # built-in sample names
    python scripts/test_matcher.py "greek yogurt" ...    # your own names
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.services.nutrition.matcher import match_food, normalize  # noqa: E402

# Typical vision-model outputs: plain, cooked, sometimes vague or European.
SAMPLES = [
    "grilled chicken breast",
    "greek yogurt",
    "spaghetti bolognese",
    "french fries",
    "caesar salad",
    "white rice",
    "salmon fillet",
    "porridge",
    "croissant",
    "banana",
]


async def main(names: list[str]) -> None:
    async with SessionLocal() as db:
        for name in names:
            matches = await match_food(db, name, limit=3)
            print(f"\n'{name}'  (normalized: '{normalize(name)}')")
            if not matches:
                print("   -- no match above threshold (would use model fallback)")
            for m in matches:
                print(
                    f"   {m.score:.2f}  [{m.source}] {m.name}"
                    f"  | {m.kcal_100g:.0f} kcal, P {m.protein_100g:.1f}g,"
                    f" C {m.carbs_100g:.1f}g, F {m.fat_100g:.1f}g /100g"
                )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or SAMPLES))
