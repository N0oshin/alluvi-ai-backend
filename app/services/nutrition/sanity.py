"""Sanity checks between the DB lookup and the response.

Pure functions, no I/O — the arithmetic guardrails of the pipeline:
kcal/macro consistency, portion clamps, and the global bias multiplier.
"""

from __future__ import annotations

KCAL_PER_G_PROTEIN = 4.0
KCAL_PER_G_CARBS = 4.0
KCAL_PER_G_FAT = 9.0

# If DB kcal and macro-derived kcal disagree by more than this share,
# trust the macros: they are three independent measurements, kcal is one
# (and OFF/custom rows especially can carry a stale or mistyped kcal).
KCAL_MISMATCH_TOLERANCE = 0.25

# Per-item clamp: below ~10 g is garnish noise, above 1.5 kg on one plate
# is a mis-scaled estimate.
ITEM_GRAMS_MIN = 10.0
ITEM_GRAMS_MAX = 1500.0

# Whole-meal clamp; totals beyond this get scaled down proportionally.
MEAL_GRAMS_MAX = 3000.0


def macro_kcal(protein_g: float, carbs_g: float, fat_g: float) -> float:
    return (
        protein_g * KCAL_PER_G_PROTEIN
        + carbs_g * KCAL_PER_G_CARBS
        + fat_g * KCAL_PER_G_FAT
    )


def reconcile_kcal(kcal: float, protein_g: float, carbs_g: float, fat_g: float) -> float:
    """DB kcal unless it contradicts its own macros by >25%."""
    derived = macro_kcal(protein_g, carbs_g, fat_g)
    if kcal <= 0:
        return derived
    if derived <= 0:
        return kcal  # water, black coffee: zero macros, kcal may still be right
    if abs(derived - kcal) / kcal > KCAL_MISMATCH_TOLERANCE:
        return derived
    return kcal


def clamp_item_grams(grams: float, bias: float = 1.0) -> float:
    """Apply the global bias multiplier, then clamp to a plausible range."""
    return min(ITEM_GRAMS_MAX, max(ITEM_GRAMS_MIN, grams * bias))


def meal_scale_factor(total_grams: float) -> float:
    """Factor to shrink every item by when the meal total is absurd."""
    if total_grams <= MEAL_GRAMS_MAX:
        return 1.0
    return MEAL_GRAMS_MAX / total_grams
