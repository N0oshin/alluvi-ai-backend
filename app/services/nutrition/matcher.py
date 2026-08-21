"""Fuzzy food-name lookup against the local nutrition databases.

The vision model only *names* foods; this module turns a name into per-100g
nutrition via pg_trgm similarity. Postgres-only by design — the trigram
indexes and `%` operator have no SQLite equivalent, and the pipeline that
calls this never runs in the SQLite test rig.

Search order: custom_foods (curated, always wins when it clears the
threshold) then the USDA nutrition_per_100g view. Open Food Facts joins in
the barcode/text step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Below this trigram similarity a match is considered untrustworthy and the
# caller should fall back to the model's own per-100g estimate.
DEFAULT_THRESHOLD = 0.35

# As-eaten FNDDS dishes are almost always the right answer for a meal photo,
# so they win near-ties against raw/branded entries. Applied to the ranking
# only — the reported score stays the raw similarity.
DATA_TYPE_BONUS = {"survey_fndds_food": 0.05}

# Vocabulary gaps trigrams cannot bridge: different words, same food. Keys
# are whole normalized names; keep this small and grow it from eval misses.
SYNONYMS = {
    "fries": "french fried potatoes",
    "french fries": "french fried potatoes",
    "chips": "potato chips",
    "crisps": "potato chips",
    "coke": "cola soft drink",
    "soda": "soft drink",
    "porridge": "oatmeal",
    "muesli": "granola",
    "courgette": "zucchini",
    "aubergine": "eggplant",
    "rocket": "arugula",
    "mince": "ground beef",
    # USDA/FNDDS wordings that trigrams cannot reach from the common name.
    # Aimed at "Restaurant, Italian, spaghetti with meat sauce" — the plain
    # "spaghetti with meat sauce" wording ties at 1.0 with the sauce-only row
    # ("Spaghetti sauce with meat"): identical words, and trigrams are blind
    # to word order.
    "spaghetti bolognese": "restaurant italian spaghetti with meat sauce",
    "bolognese": "restaurant italian spaghetti with meat sauce",
    "spaghetti and meatballs": "restaurant family style spaghetti and meatballs",
    "caesar salad": "caesar salad with romaine",
    "chicken caesar salad": "caesar garden salad chicken lettuce cheese",
    "white rice": "rice white cooked",
    "brown rice": "rice brown cooked",
}

_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(name: str) -> str:
    """Lowercase, de-punctuate, collapse spaces, then apply synonyms."""
    n = _SPACES.sub(" ", _PUNCT.sub(" ", name.lower())).strip()
    return SYNONYMS.get(n, n)


@dataclass(slots=True)
class FoodMatch:
    source: str  # custom | usda | off
    source_id: str  # custom_foods.id / fdc_id / barcode as text
    name: str
    score: float  # raw trigram similarity, 0..1
    kcal_100g: float
    protein_100g: float
    carbs_100g: float
    fat_100g: float


# custom_foods is small and curated, so it is scanned without the % filter:
# similarity() over every row plus every alias, best score per row.
_CUSTOM_SQL = text(
    """
    SELECT id::text AS source_id,
           name,
           GREATEST(
               similarity(name, :q),
               COALESCE(
                   (SELECT MAX(similarity(a.alias, :q))
                    FROM jsonb_array_elements_text(aliases) AS a(alias)),
                   0
               )
           ) AS score,
           kcal_100g, protein_100g, carbs_100g, fat_100g
    FROM custom_foods
    ORDER BY score DESC
    LIMIT :limit
    """
)

# The % filter is what lets the GIN trigram index prune 13k rows to a
# handful of candidates; similarity() then scores just those. Ranking adds
# the data_type bonus, the returned score does not.
_USDA_SQL = text(
    """
    SELECT fdc_id::text AS source_id,
           description AS name,
           similarity(description, :q) AS score,
           kcal AS kcal_100g,
           COALESCE(protein_g, 0) AS protein_100g,
           COALESCE(carbs_g, 0) AS carbs_100g,
           COALESCE(fat_g, 0) AS fat_100g
    FROM nutrition_per_100g
    WHERE description % :q AND kcal IS NOT NULL
    ORDER BY similarity(description, :q)
             + CASE data_type WHEN 'survey_fndds_food' THEN :fndds_bonus
                              ELSE 0 END DESC
    LIMIT :limit
    """
)


async def match_food(
    db: AsyncSession,
    name: str,
    *,
    limit: int = 3,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[FoodMatch]:
    """Best nutrition-DB matches for a food name, best first.

    Empty list = nothing cleared the threshold; the caller should use the
    vision model's fallback per-100g estimate instead.
    """
    q = normalize(name)
    if not q:
        return []

    # Make `%` agree with our threshold (it defaults to 0.3). Transaction-
    # local, so it cannot leak into other requests sharing the pool.
    await db.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
        {"t": str(threshold)},
    )

    custom = (
        await db.execute(_CUSTOM_SQL, {"q": q, "limit": limit})
    ).mappings().all()
    hits = [
        FoodMatch(source="custom", **row) for row in custom if row["score"] >= threshold
    ]
    if hits:
        return hits

    usda = (
        await db.execute(
            _USDA_SQL,
            {"q": q, "limit": limit, "fndds_bonus": DATA_TYPE_BONUS["survey_fndds_food"]},
        )
    ).mappings().all()
    return [
        FoodMatch(source="usda", **row) for row in usda if row["score"] >= threshold
    ]
