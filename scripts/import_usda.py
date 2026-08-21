
from __future__ import annotations

import asyncio
import csv
import io
import sys
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from app.db.models import UsdaFood, UsdaFoodNutrient  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

BASE = "https://fdc.nal.usda.gov/fdc-datasets"
DATASETS = {
    "foundation": f"{BASE}/FoodData_Central_foundation_food_csv_2025-04-24.zip",
    "sr_legacy": f"{BASE}/FoodData_Central_sr_legacy_food_csv_2018-04.zip",
    "survey": f"{BASE}/FoodData_Central_survey_food_csv_2024-10-31.zip",
}

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "usda"

# Only real catalog entries. The zips also carry USDA's internal lab rows
# (sample_food, sub_sample_food, market_acquisition, agricultural_acquisition)
# which have few nutrients and would pollute fuzzy matching.
KEEP_TYPES = {"foundation_food", "sr_legacy_food", "survey_fndds_food"}

# The four nutrients the app stores, in FDC nutrient ids.
KCAL, PROTEIN, FAT, CARBS = 1008, 1003, 1004, 1005
# Energy fallbacks, in preference order, when 1008 is absent (some
# Foundation rows only carry Atwater energy or kilojoules).
ENERGY_FALLBACKS = [2048, 2047]  # Atwater general, Atwater specific
ENERGY_KJ = 1062
KJ_TO_KCAL = 0.239006

BATCH = 2000


def download(name: str, url: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / url.rsplit("/", 1)[-1]
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[{name}] using cached {dest.name}")
        return dest
    print(f"[{name}] downloading {url} ...")
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    print(f"[{name}] downloaded {dest.stat().st_size / 1e6:.1f} MB")
    return dest


def read_csv(zf: zipfile.ZipFile, filename: str) -> csv.DictReader | None:
    """Open one CSV inside the zip, wherever it sits in the archive tree."""
    member = next(
        (n for n in zf.namelist() if n.rsplit("/", 1)[-1] == filename), None
    )
    if member is None:
        return None
    return csv.DictReader(io.TextIOWrapper(zf.open(member), encoding="utf-8-sig"))


def parse_dataset(path: Path) -> tuple[list[dict], list[dict]]:
    """Return (food rows, nutrient rows) ready for upsert."""
    with zipfile.ZipFile(path) as zf:
        categories: dict[str, str] = {}
        cat_reader = read_csv(zf, "food_category.csv")
        if cat_reader:
            for row in cat_reader:
                categories[row["id"]] = row["description"]

        # The survey zip's food_nutrient.csv identifies nutrients by their
        # legacy *number* (208 = kcal) while the others use the modern id
        # (1008). nutrient.csv in every zip maps both, so translate through
        # it and accept either form.
        nutrient_ids: dict[str, int] = {}
        for row in read_csv(zf, "nutrient.csv"):
            nid = int(float(row["id"]))
            nutrient_ids[row["id"]] = nid
            nbr = row.get("nutrient_nbr", "").strip()
            if nbr:
                nutrient_ids[nbr] = nid

        foods: list[dict] = []
        food_ids: set[int] = set()
        for row in read_csv(zf, "food.csv"):
            if row["data_type"] not in KEEP_TYPES:
                continue
            fdc_id = int(row["fdc_id"])
            food_ids.add(fdc_id)
            foods.append(
                {
                    "fdc_id": fdc_id,
                    "description": row["description"].strip(),
                    "data_type": row["data_type"],
                    "category": categories.get(row.get("food_category_id", ""))
                    or None,
                }
            )

        # (fdc_id, nutrient_id) -> amount; energy candidates kept aside so the
        # best available unit wins, always stored under 1008 kcal.
        amounts: dict[tuple[int, int], float] = {}
        energy: dict[int, dict[int, float]] = {}
        for row in read_csv(zf, "food_nutrient.csv"):
            try:
                nutrient_id = nutrient_ids.get(row["nutrient_id"].strip())
                fdc_id = int(row["fdc_id"])
                amount = float(row["amount"])
            except (KeyError, ValueError):
                continue
            if nutrient_id is None:
                continue
            if fdc_id not in food_ids:
                continue
            if nutrient_id in (PROTEIN, FAT, CARBS):
                amounts[(fdc_id, nutrient_id)] = amount
            elif nutrient_id in (KCAL, ENERGY_KJ, *ENERGY_FALLBACKS):
                energy.setdefault(fdc_id, {})[nutrient_id] = amount

        for fdc_id, cands in energy.items():
            if KCAL in cands:
                kcal = cands[KCAL]
            elif ENERGY_FALLBACKS[0] in cands:
                kcal = cands[ENERGY_FALLBACKS[0]]
            elif ENERGY_FALLBACKS[1] in cands:
                kcal = cands[ENERGY_FALLBACKS[1]]
            elif ENERGY_KJ in cands:
                kcal = cands[ENERGY_KJ] * KJ_TO_KCAL
            else:
                continue
            amounts[(fdc_id, KCAL)] = round(kcal, 2)

        nutrients = [
            {"fdc_id": fdc, "nutrient_id": nid, "amount": amt}
            for (fdc, nid), amt in amounts.items()
        ]
        return foods, nutrients


async def upsert(rows: list[dict], model, index_elements: list[str]) -> None:
    async with SessionLocal() as session:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i : i + BATCH]
            stmt = pg_insert(model).values(chunk)
            update_cols = {
                c: stmt.excluded[c] for c in chunk[0] if c not in index_elements
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=index_elements, set_=update_cols
            )
            await session.execute(stmt)
        await session.commit()


async def run(names: list[str]) -> None:
    for name in names:
        path = download(name, DATASETS[name])
        print(f"[{name}] parsing ...")
        foods, nutrients = parse_dataset(path)
        print(f"[{name}] upserting {len(foods)} foods ...")
        await upsert(foods, UsdaFood, ["fdc_id"])
        print(f"[{name}] upserting {len(nutrients)} nutrient rows ...")
        await upsert(nutrients, UsdaFoodNutrient, ["fdc_id", "nutrient_id"])
        print(f"[{name}] done: {len(foods)} foods, {len(nutrients)} nutrients")


if __name__ == "__main__":
    picked = sys.argv[1:] or list(DATASETS)
    unknown = [n for n in picked if n not in DATASETS]
    if unknown:
        sys.exit(f"unknown dataset(s) {unknown}; choose from {list(DATASETS)}")
    asyncio.run(run(picked))
