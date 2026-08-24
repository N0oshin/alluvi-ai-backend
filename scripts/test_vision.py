"""Test the Gemini scan client against a real photo, standalone.

Usage, from backend/:
    python scripts/test_vision.py path\\to\\meal.jpg
    python scripts/test_vision.py path\\to\\meal.jpg bowl      # container hint
    python scripts/test_vision.py --text "chicken salad with a coke"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vision.gemini import GeminiScanClient  # noqa: E402


async def main(argv: list[str]) -> None:
    client = GeminiScanClient()

    if argv and argv[0] == "--text":
        result, meta = await client.scan(text_description=" ".join(argv[1:]))
    else:
        if not argv:
            sys.exit(__doc__)
        image = Path(argv[0]).read_bytes()
        container = argv[1] if len(argv) > 1 else None
        result, meta = await client.scan(image_bytes=image, container=container)

    print(f"\ndish: {result.dish_name!r}   not_food={result.not_food}")
    print(
        f"health {result.health_score}/10, portion confidence "
        f"{result.portion_confidence}, scale ref: {result.scale_reference}"
    )
    for it in result.items:
        f = it.fallback_per_100g
        print(
            f"  - {it.name}: {it.grams:.0f} g (conf {it.confidence:.2f}) "
            f"@({it.cx:.2f},{it.cy:.2f})"
            f"  fallback/100g: {f.kcal:.0f} kcal P{f.protein_g:.0f} "
            f"C{f.carbs_g:.0f} F{f.fat_g:.0f}"
        )
    print(
        f"\n[{meta.model} | {meta.prompt_version}] {meta.latency_ms} ms, "
        f"{meta.input_tokens}+{meta.output_tokens} tok, "
        f"~${meta.estimated_cost_usd}"
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
